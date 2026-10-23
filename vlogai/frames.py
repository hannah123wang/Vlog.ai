"""Step 2 — split long clips into shots, grab low-res keyframes, score technical quality.

Only small JPEGs (~512px) are ever sent to the AI — never the original 4K video.
"""
from __future__ import annotations

import math
import time
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


# ---- 切镜头用的低分辨率代理 -------------------------------------------------------
# PySceneDetect 用 OpenCV 逐帧解码原片：一段 10 分钟的 4K HEVC 要解码 18000 帧 4K 画面，
# 而它检测前本来就会把画面缩到 256px 左右（auto_downscale）。所以先用 ffmpeg（多线程、
# 能用显卡解码）转成 320px、每秒 10 帧的小文件，再在小文件上检测，结果基本一样，快很多。
# 代理文件存在项目的 proxies/ 里，下次直接用；原片改过（修改时间更新）会重做。
PROXY_DIR = "proxies"
# 代理宽度、帧率、是否用显卡解码：设置页可以改（proxy_width / proxy_fps / hwaccel）


def _make_proxy(project: Project, clip: dict) -> Path | None:
    src = Path(clip["path"])
    pw, pf = int(config.get("proxy_width")), int(config.get("proxy_fps"))
    out = project.out(PROXY_DIR) / f"{clip['id']}_{pw}p{pf}.mp4"      # 改了尺寸 / 帧率就是另一个文件
    try:
        if out.is_file() and out.stat().st_size > 0 and out.stat().st_mtime >= src.stat().st_mtime:
            return out
    except OSError:
        return None
    out.parent.mkdir(exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    tail = ["-i", str(src), "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", f"fps={pf},scale={pw}:-2:flags=fast_bilinear,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-g", str(pf * 2),
            "-movflags", "+faststart", str(tmp)]
    for hw in ((["-hwaccel", "auto"], []) if config.get("hwaccel") else ([],)):   # 先试显卡解码，不行就纯 CPU
        try:
            run(["ffmpeg", "-y", "-v", "error", *hw, *tail])
            if tmp.is_file() and tmp.stat().st_size > 0:
                tmp.replace(out)
                return out
        except RuntimeError:
            pass
        tmp.unlink(missing_ok=True)
    return None


def _detect_scenes(path: str, duration: float, proxy: Path | None = None) -> list[tuple[float, float]]:
    """Return [(start, end)] seconds. Falls back to one shot if PySceneDetect is missing.

    proxy：同一段视频的低分辨率代理（时间轴和原片一致），有就在它上面检测。"""
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:
        return [(0.0, duration)]
    try:
        video = open_video(str(proxy or path))
        sm = SceneManager()
        sm.auto_downscale = True
        sm.add_detector(ContentDetector(threshold=config.SCENE_THRESHOLDS[config.get("scene_sensitivity")],
                                        min_scene_len=max(2, int(video.frame_rate * float(config.get("min_scene_sec"))))))
        # 代理已经是每秒 10 帧，不用再跳帧；原片每 3 帧看一帧
        sm.detect_scenes(video, frame_skip=0 if proxy else 2, show_progress=False)
        scenes = [(s.get_seconds(), e.get_seconds()) for s, e in sm.get_scene_list()]
    except Exception as e:  # noqa: BLE001 — never let one odd file kill the run
        log(f"  场景检测失败，整段当作一个镜头: {Path(path).name} ({e})")
        scenes = []
    if not scenes:
        return [(0.0, duration)]
    # 代理和原片的时长可能差几十毫秒：首尾对齐到原片
    scenes = [(max(0.0, a), min(duration, b)) for a, b in scenes if a < duration]
    scenes[0] = (0.0, scenes[0][1])
    scenes[-1] = (scenes[-1][0], duration)
    return scenes


def _scenes_for(project: Project, clip: dict) -> list[tuple[float, float]]:
    dur = clip["duration"]
    if dur < float(config.get("long_clip_sec")):
        return [(0.0, dur)]
    try:
        import scenedetect  # noqa: F401
    except ImportError:
        return [(0.0, dur)]
    t0 = time.time()
    use_proxy = bool(config.get("use_proxy"))
    proxy = _make_proxy(project, clip) if use_proxy else None
    t1 = time.time()
    if use_proxy and proxy is None:
        log(f"  {clip['name']}：转代理失败，直接在原片上切镜头（会慢一些）")
    scenes = _detect_scenes(clip["path"], dur, proxy)
    if t1 - t0 > 2:                     # 新做了代理才报一下用时
        log(f"  {clip['name']}（{dur / 60:.1f} 分钟）切成 {len(scenes)} 个镜头 · "
            f"代理 {t1 - t0:.0f} 秒 + 检测 {time.time() - t1:.0f} 秒")
    return scenes


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


def _quality(frame_paths: list[Path], sharp_ref: float = 300.0, expo_target: float = 120.0) -> dict:
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
    sharp_score = min(1.0, s / sharp_ref)                   # ~300+ = crisp at 512px（设置页：清晰度基准）
    expo_score = 1.0 - min(1.0, abs(b - expo_target) / 110.0)  # penalise very dark / blown out
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

    # 这条素材以前已经切过镜头（同一个文件）：直接沿用，不再重新解码检测。
    # “继续看片”、改了 Key 重跑时，以前每次都要把所有长视频重新切一遍。
    by_clip: dict[str, list[dict]] = {}
    for s in old.values():
        by_clip.setdefault(s["clip_id"], []).append(s)

    def work(clip: dict) -> list[dict]:
        prev = by_clip.get(clip["id"])
        if prev and all(s.get("src") == clip["path"] for s in prev):
            return sorted(prev, key=lambda s: s["start"])
        shots = []
        for k, (st, en) in enumerate(_scenes_for(project, clip), 1):
            sid = f"{clip['id']}_s{k:02d}"
            if sid in old and old[sid].get("src") == clip["path"]:
                shots.append(old[sid])
                continue
            if sid in old:   # 同一个 id 以前对应别的文件（旧版本重新编号造成的）：旧日志作废
                (project.logs_dir / f"{sid}.json").unlink(missing_ok=True)
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
            shot.update(_quality([Path(f["path"]) for f in frames],
                                 float(cfg["sharp_ref"]), float(cfg["exposure_target"])))
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
