"""Step 2 — split long clips into shots, grab low-res keyframes, score technical quality.

Only small JPEGs (~512px) are ever sent to the AI — never the original 4K video.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .common import Project, log, read_json, run, write_json

# 默认值在 config.DEFAULTS，界面“设置”页可以改：
#   long_clip_sec        长于这个时长的片段才做切镜头
#   max_frames_per_shot  每个镜头最多抽几帧
#   frame_every_sec      大约每隔几秒抽一帧
#   frame_width          静帧宽度（发给 AI 的就是它）
#   scene_sensitivity    切镜头灵敏度 low/mid/high


def _detect_scenes(path: str, duration: float) -> list[tuple[float, float]]:
    """Return [(start, end)] seconds. Falls back to one shot if PySceneDetect is missing."""
    if duration < float(config.get("long_clip_sec")):
        return [(0.0, duration)]
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        return [(0.0, duration)]
    try:
        video = open_video(path)
        sm = SceneManager()
        sm.auto_downscale = True
        sm.add_detector(ContentDetector(threshold=config.SCENE_THRESHOLDS[config.get("scene_sensitivity")], min_scene_len=int(video.frame_rate * 2)))
        sm.detect_scenes(video, frame_skip=2, show_progress=False)
        scenes = [(s.get_seconds(), e.get_seconds()) for s, e in sm.get_scene_list()]
    except Exception as e:  # noqa: BLE001 — never let one odd file kill the run
        log(f"  场景检测失败，整段当作一个镜头: {Path(path).name} ({e})")
        scenes = []
    return scenes or [(0.0, duration)]


def _grab_frame(src: str, t: float, dst: Path, width: int = 512) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return True
    try:
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", src,
             "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", "4", str(dst)])
        return dst.exists()
    except RuntimeError:
        return False


def _quality(frame_paths: list[Path]) -> dict:
    """Sharpness (Laplacian variance), exposure and 'boring/static' score from keyframes."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return {"quality": 0.6, "sharpness": None, "brightness": None}
    sharp, bright, grays = [], [], []
    for p in frame_paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # sharpness of the sharpest regions (4x4 grid, 80th pct), so a crisp horizon
        # against a smooth sky/sea isn't mistaken for an out-of-focus shot
        lap = cv2.Laplacian(g, cv2.CV_64F)
        th, tw = lap.shape[0] // 4, lap.shape[1] // 4
        tiles = [lap[r*th:(r+1)*th, c*tw:(c+1)*tw].var() for r in range(4) for c in range(4)]
        sharp.append(float(np.percentile(tiles, 80)))
        bright.append(float(g.mean()))
        grays.append(cv2.resize(g, (64, 36)).astype("float32"))
    if not sharp:
        return {"quality": 0.0, "sharpness": 0, "brightness": 0}
    s = float(np.median(sharp))
    b = float(np.mean(bright))
    change = float(np.mean([abs(a - b_).mean() for a, b_ in zip(grays, grays[1:])])) if len(grays) > 1 else 0.0
    sharp_score = min(1.0, s / 300.0)                       # ~300+ = crisp at 512px
    expo_score = 1.0 - min(1.0, abs(b - 120) / 110.0)        # penalise very dark / blown out
    q = round((sharp_score ** 0.5) * (0.5 + 0.5 * expo_score), 3)  # blur dominates
    return {"quality": q, "sharpness": round(s, 1), "brightness": round(b, 1),
            "visual_change": round(change, 2)}


def extract(project: Project, workers: int = 4) -> list[dict]:
    clips = read_json(project.clips_json)
    if not clips:
        raise SystemExit("先运行 scan")
    old = {s["id"]: s for s in (read_json(project.shots_json, []) or [])}
    cfg = config.load()
    every, max_n, width = float(cfg["frame_every_sec"]), int(cfg["max_frames_per_shot"]), int(cfg["frame_width"])
    log(f"切分镜头并抽取关键帧（{len(clips)} 条素材）…")

    def work(clip: dict) -> list[dict]:
        shots = []
        for k, (st, en) in enumerate(_detect_scenes(clip["path"], clip["duration"]), 1):
            sid = f"{clip['id']}_s{k:02d}"
            if sid in old:
                shots.append(old[sid])
                continue
            dur = en - st
            n = max(1, min(max_n, math.ceil(dur / every)))
            times = [st + dur * (i + 0.5) / n for i in range(n)]
            frames = []
            for i, t in enumerate(times):
                p = project.frames_dir / clip["id"] / f"{sid}_{i}.jpg"
                if _grab_frame(clip["path"], t, p, width):
                    frames.append({"t": round(t, 2), "path": str(p)})
            shot = {"id": sid, "clip_id": clip["id"], "src": clip["path"],
                    "start": round(st, 3), "end": round(en, 3), "duration": round(dur, 3),
                    "day": clip["day"], "created": clip["created"], "gps": clip["gps"],
                    "has_audio": clip["has_audio"], "frames": frames}
            shot.update(_quality([Path(f["path"]) for f in frames]))
            shots.append(shot)
        return shots

    all_shots: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, shots in enumerate(ex.map(work, clips), 1):
            all_shots.extend(shots)
            if i % 25 == 0 or i == len(clips):
                log(f"  {i}/{len(clips)} 条素材完成")
    write_json(project.shots_json, all_shots)
    bad = sum(1 for s in all_shots if s["quality"] < float(cfg["bad_quality"]))
    log(f"得到 {len(all_shots)} 个镜头（其中约 {bad} 个模糊/过暗）→ {project.shots_json.name}")
    return all_shots
