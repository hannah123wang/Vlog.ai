"""镜头工坊：在已有素材的基础上做出新的片段，可以直接放进时间线。

三种工具：
  补镜头（fill）   用一张参考静帧 + 一句描述，让视频生成服务补几秒素材里没有的画面；
                   离线模拟时用静帧做慢推，不花钱，用来测流程。
  慢动作（slowmo）  原片帧率够高（比如 iPhone 120fps）就直接放慢；不够就补帧（帧混合 / 光流）。
  裁切特写（crop）  在画面里框一块（可以自动找脸），固定裁切，或者从全景慢慢推到这一块 / 反过来拉开。

做出来的片段存在项目的 lab/ 文件夹，记录在 lab.json。它们在时间线、预览渲染和 XML 导出里
都当作普通镜头用（id 以 lab 开头），但不会出现在“素材”页，也不会交给 AI 看片。
"""
from __future__ import annotations

import base64
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from . import config
from .common import Project, log, read_json, run, write_json

LAB_DIR = "lab"
LAB_JSON = "lab.json"
KINDS = ("fill", "slowmo", "crop")
FAL_KEY_VAR = "FAL_KEY"


# ---------------------------------------------------------------- 记录
def assets(project: Project) -> list[dict]:
    out = []
    for a in read_json(project.out(LAB_JSON), []) or []:
        f = project.root / a.get("file", "")
        if a.get("file") and f.is_file():
            a["path"] = str(f)
            out.append(a)
    return out


def _save(project: Project, items: list[dict]) -> None:
    write_json(project.out(LAB_JSON), [{k: v for k, v in a.items() if k != "path"} for a in items])


def _new_id(project: Project) -> str:
    used = [a.get("id", "") for a in read_json(project.out(LAB_JSON), []) or []]
    n = max([int(i[3:]) for i in used if i[3:].isdigit()] + [0]) + 1
    return f"lab{n:03d}"


def lab_shots(project: Project) -> list[dict]:
    """把工坊里做出的片段包装成“镜头”，时间线校验、渲染、导出都按普通镜头处理。"""
    shots = {s["id"]: s for s in read_json(project.shots_json, []) or []}
    out = []
    for a in assets(project):
        src = shots.get(a.get("src_shot") or "", {})
        thumb = project.root / a.get("thumb", "")
        out.append({
            "id": a["id"], "clip_id": a["id"], "src": a["path"], "start": 0.0, "end": a["duration"],
            "duration": a["duration"], "day": src.get("day", 0), "created": src.get("created") or a["created"],
            "gps": src.get("gps"), "has_audio": bool(a.get("has_audio")),
            "frames": [{"t": round(a["duration"] / 2, 2), "path": str(thumb)}] if thumb.is_file() else [],
            "quality": 1.0, "lab": a["kind"], "label": a.get("label", ""),
        })
    return out


def lab_clips(project: Project) -> list[dict]:
    """和 clips.json 同样格式的条目（渲染、导出按文件路径查分辨率 / 帧率用）。"""
    return [{"id": a["id"], "path": a["path"], "name": Path(a["path"]).name, "duration": a["duration"],
             "width": a["width"], "height": a["height"], "fps": a["fps"], "has_audio": bool(a.get("has_audio")),
             "created": a["created"], "day": 0} for a in assets(project)]


def all_clips(project: Project) -> dict[str, dict]:
    clips = {c["path"]: c for c in read_json(project.clips_json, []) or []}
    clips.update({c["path"]: c for c in lab_clips(project)})
    return clips


def delete(project: Project, asset_id: str) -> None:
    items = read_json(project.out(LAB_JSON), []) or []
    keep = [a for a in items if a.get("id") != asset_id]
    for a in items:
        if a.get("id") == asset_id:
            for k in ("file", "thumb"):
                if a.get(k):
                    (project.root / a[k]).unlink(missing_ok=True)
    _save(project, [{**a, "path": ""} for a in keep])


# ---------------------------------------------------------------- 工具函数
def _source(project: Project, shot_id: str) -> tuple[dict, dict]:
    shots = {s["id"]: s for s in read_json(project.shots_json, []) or []}
    clips = {c["id"]: c for c in read_json(project.clips_json, []) or []}
    s = shots.get(shot_id)
    if not s:
        raise ValueError(f"找不到镜头 {shot_id}")
    if not Path(s["src"]).is_file():
        raise ValueError(f"原片不在了：{s['src']}")
    return s, clips.get(s["clip_id"], {})


def out_size(project: Project) -> tuple[int, int]:
    return (1080, 1920) if project.settings().get("vertical") else (1920, 1080)


def _range(s: dict, a, b) -> tuple[float, float]:
    a = max(0.0, min(float(a or 0), s["duration"] - 0.3))
    b = float(b) if b not in (None, "") else s["duration"]
    b = max(a + 0.3, min(b, s["duration"]))
    return a, b


def _probe(path: Path) -> dict:
    from .probe import probe_file
    info = probe_file(path) or {}
    return {"duration": info.get("duration", 0), "width": info.get("width", 0), "height": info.get("height", 0),
            "fps": info.get("fps", 30.0), "has_audio": info.get("has_audio", False)}


def _thumb(video: Path, dst: Path) -> None:
    try:
        d = _probe(video)["duration"]
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, d / 2):.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(dst)])
    except RuntimeError:
        pass


def _finish(project: Project, aid: str, kind: str, tmp: Path, label: str, **extra) -> dict:
    folder = project.out(LAB_DIR)
    final = folder / f"{aid}.mp4"
    tmp.replace(final)
    thumb = folder / f"{aid}.jpg"
    _thumb(final, thumb)
    rec = {"id": aid, "kind": kind, "label": label, "auto_label": bool(extra.pop("auto_label", False)),
           "file": f"{LAB_DIR}/{final.name}",
           "thumb": f"{LAB_DIR}/{thumb.name}" if thumb.is_file() else "",
           "created": datetime.now().isoformat(timespec="seconds"), **_probe(final), **extra}
    items = read_json(project.out(LAB_JSON), []) or []
    items.append(rec)
    _save(project, items)
    log(f"镜头工坊：做好了 {aid}（{label}，{rec['duration']:.1f} 秒）→ {rec['file']}")
    return rec


def _tmp(project: Project, aid: str) -> Path:
    folder = project.out(LAB_DIR)
    folder.mkdir(exist_ok=True)
    return folder / f"{aid}.part.mp4"


# 工坊做的是中间片段（导出时还会再编码一次）：veryfast 比 medium 快一倍多，crf 18 画质看不出差别
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def preview(project: Project, shot_id: str) -> Path:
    """工坊页里播放用的小预览（640px，只含这个镜头），第一次打开时做，之后直接用。"""
    s, _ = _source(project, shot_id)
    out = project.out(LAB_DIR) / "_preview" / f"{shot_id}.mp4"
    if out.is_file() and out.stat().st_mtime >= Path(s["src"]).stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start']:.3f}", "-t", f"{s['duration']:.3f}", "-i", s["src"],
         "-vf", "scale='min(640,iw)':-2,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)])
    tmp.replace(out)
    return out


def still(project: Project, shot_id: str, t: float, width: int = 1280) -> Path:
    """镜头里某一时刻的一张大一点的静帧（找脸、裁切预览、补镜头参考图都用它）。"""
    s, _ = _source(project, shot_id)
    t = max(0.0, min(float(t or 0), s["duration"] - 0.05))
    out = project.out(LAB_DIR) / "_stills" / f"{shot_id}_{int(t * 10):05d}_{width}.jpg"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start'] + t:.3f}", "-i", s["src"], "-frames:v", "1",
             "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", str(out)])
    return out


def faces(project: Project, shot_id: str, t: float, llm=None) -> tuple[list[dict], str]:
    """在这一帧里找人脸，返回 ([{x, y, w, h}], 方法)。坐标都是 0-1 的比例，从大到小。

    先用本机的 OpenCV（不花钱）；OpenCV 5 去掉了人脸检测器，那样就请 AI 看一眼这张图（约 1-2k tokens）。"""
    img_path = still(project, shot_id, t)
    found = _faces_opencv(img_path)
    if found is not None:
        return found, "opencv"
    if llm is not None and hasattr(llm, "describe"):
        return _faces_ai(img_path, llm), "ai"
    return [], "none"


def _faces_opencv(img_path: Path) -> list[dict] | None:
    try:
        import cv2
    except ImportError:
        return None
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        return None
    img = cv2.imread(str(img_path))
    if img is None:
        return []
    H, W = img.shape[:2]
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    found = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        casc = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, name))
        if casc.empty():
            continue
        for (x, y, w, h) in casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                                   minSize=(max(24, W // 40), max(24, W // 40))):
            found.append((int(x), int(y), int(w), int(h)))
    found.sort(key=lambda b: -b[2] * b[3])
    keep: list = []
    for b in found:                              # 去掉重叠的
        if all(_iou(b, k) < 0.3 for k in keep):
            keep.append(b)
    return [{"x": round(x / W, 4), "y": round(y / H, 4), "w": round(w / W, 4), "h": round(h / H, 4)}
            for x, y, w, h in keep[:8]]


FACES_PROMPT = """Find every clearly visible human face in this image. Return ONLY JSON:
{"faces": [{"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}]}
x, y = top-left corner, w, h = size, all as fractions of the image width / height (0-1), largest face first.
Return {"faces": []} if there are none."""


def _faces_ai(img_path: Path, llm) -> list[dict]:
    try:
        d = llm.describe("You locate faces in photos precisely.", FACES_PROMPT, [str(img_path)])
    except Exception as e:  # noqa: BLE001
        from .llm import FatalError
        if isinstance(e, FatalError):
            raise
        log(f"  AI 找脸失败：{e}")
        return []
    out = []
    for f in (d or {}).get("faces", [])[:8] if isinstance(d, dict) else []:
        try:
            box = {k: round(min(1.0, max(0.0, float(f[k]))), 4) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            continue
        if box["w"] > 0.01 and box["h"] > 0.01:
            out.append(box)
    return out


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(0, min(ax + aw, bx + bw) - max(ax, bx)), max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / float(aw * ah + bw * bh - inter) if inter else 0.0


# ---------------------------------------------------------------- 裁切特写
# 框用“关键帧”描述：[{t, x, y, w, h}]，t 是相对镜头开头的秒数，x/y/w/h 是 0-1 的比例（左上角 + 宽高）。
#   一个关键帧 = 固定特写；两个 = 从一个框平滑推拉到另一个；很多个 = 跟着人脸走（track 算出来的）。
# 渲染时逐帧按时间插值出框，用 OpenCV 做亚像素的裁切 + 缩放，推拉和跟随都很顺，不会一顿一顿。
def _full_box(W: int, H: int, aspect: float, cx: float = 0.5, cy: float = 0.5) -> dict:
    """画面里能放下的最大输出比例区域（0-1），尽量以 (cx, cy) 为中心。"""
    if W / H > aspect:
        h, w = 1.0, (H * aspect) / W
    else:
        w, h = 1.0, (W / aspect) / H
    return {"x": min(max(0.0, cx - w / 2), 1 - w), "y": min(max(0.0, cy - h / 2), 1 - h), "w": w, "h": h}


def _fix_box(b: dict, W: int, H: int, aspect: float) -> dict:
    """修正成输出宽高比、不超出画面（以框中心为准）。"""
    cx = float(b.get("x", 0)) + float(b.get("w", 1)) / 2
    cy = float(b.get("y", 0)) + float(b.get("h", 1)) / 2
    full = _full_box(W, H, aspect)
    w = min(max(0.02, float(b.get("w", 1))), full["w"])
    h = w * W / aspect / H
    if h > full["h"]:
        h = full["h"]
        w = h * H * aspect / W
    return {"x": min(max(0.0, cx - w / 2), 1 - w), "y": min(max(0.0, cy - h / 2), 1 - h), "w": w, "h": h}


def _keys(keys: list, W: int, H: int, aspect: float, a: float, b: float) -> list[dict]:
    out = []
    for k in keys or []:
        try:
            box = _fix_box(k, W, H, aspect)
            out.append({"t": min(max(float(k.get("t", a)), a), b), **box})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda k: k["t"])
    if not out:
        raise ValueError("没有裁切框")
    return out


def _box_at(keys: list[dict], t: float) -> dict:
    """t 时刻的框：两个关键帧之间插值。只有两个关键帧（推拉）时用缓入缓出，多个（跟随）时线性。"""
    if t <= keys[0]["t"]:
        return keys[0]
    if t >= keys[-1]["t"]:
        return keys[-1]
    for k0, k1 in zip(keys, keys[1:]):
        if k0["t"] <= t <= k1["t"]:
            p = (t - k0["t"]) / max(1e-6, k1["t"] - k0["t"])
            if len(keys) == 2:
                p = p * p * (3 - 2 * p)
            return {c: k0[c] + (k1[c] - k0[c]) * p for c in ("x", "y", "w", "h")}
    return keys[-1]


def crop(project: Project, shot_id: str, a, b, keys: list | None = None, label: str = "",
         box: dict | None = None, motion: str = "static") -> dict:
    s, clip = _source(project, shot_id)
    a, b = _range(s, a, b)
    OW, OH = out_size(project)
    aspect = OW / OH
    W, H = int(clip.get("width") or 1920), int(clip.get("height") or 1080)
    if not keys:                                 # 旧的调用方式：一个框 + 推近 / 拉远
        bx = _fix_box(box or {}, W, H, aspect)
        full = _full_box(W, H, aspect, bx["x"] + bx["w"] / 2, bx["y"] + bx["h"] / 2)
        keys = {"push_in": [{"t": a, **full}, {"t": b, **bx}],
                "pull_out": [{"t": a, **bx}, {"t": b, **full}]}.get(motion, [{"t": a, **bx}])
    keys = _keys(keys, W, H, aspect, a, b)
    smallest = min(k["w"] for k in keys) * W
    if smallest < OW * 0.35:
        log(f"  提醒：最小的框只有约 {smallest:.0f} 像素宽，放大到 {OW}×{OH} 会比较糊")
    mode = "fixed" if len(keys) == 1 else "push" if len(keys) == 2 else "track"
    fps = int(config.get("render_fps"))
    aid = _new_id(project)
    tmp = _tmp(project, aid)
    log(f"镜头工坊：裁切 {shot_id} {a:.1f}–{b:.1f} 秒（{ {'fixed': '固定', 'push': '推拉', 'track': '跟随人脸'}[mode] }，"
        f"{len(keys)} 个关键帧）→ {OW}×{OH}…")
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        _crop_render_cv(s, clip, a, b, keys, W, H, OW, OH, fps, tmp)
    except ImportError:
        _crop_render_ff(s, a, b, keys, W, H, OW, OH, fps, tmp)
    k0 = keys[0]
    return _finish(project, aid, "crop", tmp, label or f"{ {'fixed': '特写', 'push': '推拉', 'track': '跟随'}[mode] } · {shot_id}",
                   auto_label=not label, src_shot=shot_id, src_in=round(a, 2), src_out=round(b, 2),
                   params={"mode": mode, "keys": [{c: round(v, 4) for c, v in k.items()} for k in keys],
                           "box": {c: round(k0[c], 4) for c in ("x", "y", "w", "h")}})


def _crop_render_cv(s, clip, a, b, keys, W, H, OW, OH, fps, tmp: Path) -> None:
    """ffmpeg 解码原片 → 每一帧按时间算框，OpenCV 亚像素裁切缩放 → ffmpeg 编码（带原声）。"""
    import subprocess
    import cv2
    import numpy as np
    dur = b - a
    # 解码时先把画面缩到“刚好够用”的大小：最小的框放大到输出尺寸不再糊就行，4K 原片不用全解
    need = max(OW / max(1e-6, min(k["w"] for k in keys)), OH / max(1e-6, min(k["h"] for k in keys)) * W / H)
    DW = int(min(W, max(OW, need)) // 2 * 2)
    DH = int(round(DW * H / W / 2) * 2)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{s['start'] + a:.3f}", "-t", f"{dur:.3f}", "-i", s["src"],
                            "-vf", f"fps={fps},scale={DW}:{DH}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OW}x{OH}",
                            "-r", str(fps), "-i", "-", "-ss", f"{s['start'] + a:.3f}", "-t", f"{dur:.3f}", "-i", s["src"],
                            "-map", "0:v", "-map", "1:a?", *ENC, "-c:a", "aac", "-b:a", "160k", "-shortest", str(tmp)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes, n_total, i = DW * DH * 3, max(1, int(round(dur * fps))), 0
    try:
        while True:
            buf = dec.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            img = np.frombuffer(buf, np.uint8).reshape(DH, DW, 3)
            bx = _box_at(keys, a + i / fps)
            sx = OW / (bx["w"] * DW)
            sy = OH / (bx["h"] * DH)
            M = np.float32([[sx, 0, -bx["x"] * DW * sx], [0, sy, -bx["y"] * DH * sy]])
            out = cv2.warpAffine(img, M, (OW, OH), flags=cv2.INTER_AREA if sx < 1 else cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
            enc.stdin.write(out.tobytes())
            i += 1
            if i % 30 == 0 or i == n_total:
                print(f"[vlogai]   {min(i, n_total)}/{n_total} 帧")
    finally:
        enc.stdin.close()
        dec.stdout.close()
        dec.wait()
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RuntimeError(f"裁切编码失败：{err[-400:]}")
    if i == 0:
        raise RuntimeError("裁切失败：没读到画面（原片可能坏了）")


def _crop_render_ff(s, a, b, keys, W, H, OW, OH, fps, tmp: Path) -> None:
    """没有 OpenCV 时的退路：只用 ffmpeg。固定框用 crop；推拉 / 跟随用首尾两个框做 zoompan。"""
    dur = b - a
    px = lambda k: (int(k["x"] * W) // 2 * 2, int(k["y"] * H) // 2 * 2, max(16, int(k["w"] * W) // 2 * 2), max(16, int(k["h"] * H) // 2 * 2))  # noqa: E731
    if len(keys) == 1:
        x, y, w, h = px(keys[0])
        vf = f"crop={w}:{h}:{x}:{y},scale={OW}:{OH}:flags=lanczos,setsar=1,fps={fps},format=yuv420p"
    else:
        k0, k1 = keys[0], keys[-1]
        # 外框：包住首尾两个框、且是输出比例的区域；在外框里用 zoompan 推拉
        ux0, uy0 = min(k0["x"], k1["x"]), min(k0["y"], k1["y"])
        ux1, uy1 = max(k0["x"] + k0["w"], k1["x"] + k1["w"]), max(k0["y"] + k0["h"], k1["y"] + k1["h"])
        U = _fix_box({"x": ux0, "y": uy0, "w": max(ux1 - ux0, (uy1 - uy0) * H * (OW / OH) / W), "h": 1}, W, H, OW / OH)
        x0, y0, w0, h0 = px(U)
        n = max(2, int(round(dur * fps)))
        e = f"((on/{n - 1})*(on/{n - 1})*(3-2*(on/{n - 1})))"
        kk = 2
        ax, ay, aw = (k0["x"] * W - x0) * kk, (k0["y"] * H - y0) * kk, k0["w"] * W * kk
        bx, by, bw = (k1["x"] * W - x0) * kk, (k1["y"] * H - y0) * kk, k1["w"] * W * kk
        Wk = w0 * kk
        vf = (f"fps={fps},crop={w0}:{h0}:{x0}:{y0},scale={w0 * kk}:{h0 * kk},"
              f"zoompan=z='{Wk}/({aw}+({bw}-{aw})*{e})':x='{ax}+({bx}-{ax})*{e}':y='{ay}+({by}-{ay})*{e}':d=1:s={OW}x{OH}:fps={fps},"
              f"setsar=1,format=yuv420p")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start'] + a:.3f}", "-t", f"{dur:.3f}", "-i", s["src"],
         "-vf", vf, "-map", "0:v:0", "-map", "0:a?", *ENC, "-c:a", "aac", "-b:a", "160k", str(tmp)])


def track(project: Project, shot_id: str, a, b, seed: dict | None = None, seed_t: float | None = None,
          tight: float = 0.38, llm=None) -> dict:
    """在 a–b 这段里跟着一张脸走，返回裁切关键帧。

    每秒取 3 帧找脸（本机 OpenCV；没有的话用 AI，只看 6 帧）；从你选的那张脸（seed，没有就用最大的）开始，
    每一帧挑离上一帧最近的那张脸；中间没找到的按前后插值，再平滑一下，免得画面抖。
    tight：脸占画面高度的比例（0.25 宽松 – 0.6 很紧）。"""
    import shutil
    import tempfile
    s, clip = _source(project, shot_id)
    a, b = _range(s, a, b)
    OW, OH = out_size(project)
    aspect = OW / OH
    W, H = int(clip.get("width") or 1920), int(clip.get("height") or 1080)
    use_ai = _faces_opencv_available() is False
    if use_ai and llm is None:
        raise ValueError("这台电脑的 OpenCV 不支持找脸，又没有 API Key 可以让 AI 找。可以改用“固定”或“推拉”手动框。")
    rate = (6 / max(0.5, b - a)) if use_ai else 3.0
    tmpdir = Path(tempfile.mkdtemp(prefix="vlogai_track_"))
    try:
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start'] + a:.3f}", "-t", f"{b - a:.3f}", "-i", s["src"],
             "-vf", f"fps={rate:.4f},scale='min(960,iw)':-2", "-q:v", "3", str(tmpdir / "f_%04d.jpg")])
        files = sorted(tmpdir.glob("f_*.jpg"))
        samples = []
        for i, f in enumerate(files):
            t = a + (i + 0.5) / rate
            found = _faces_ai(f, llm) if use_ai else (_faces_opencv(f) or [])
            samples.append((min(t, b), found))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not any(f for _, f in samples):
        raise ValueError("这一段里没找到清楚的脸。可以换一段，或者改用“固定”“推拉”手动框。")
    ctr = lambda f: (f["x"] + f["w"] / 2, f["y"] + f["h"] / 2)  # noqa: E731
    # 起点：离 seed 最近的那一帧里离 seed 最近的脸；没有 seed 就用脸最大的那一帧
    if seed:
        st = seed_t if seed_t is not None else a
        si = min(range(len(samples)), key=lambda i: (not samples[i][1], abs(samples[i][0] - st)))
        sc = (seed["x"] + seed["w"] / 2, seed["y"] + seed["h"] / 2)
    else:
        si = max(range(len(samples)), key=lambda i: max([f["w"] * f["h"] for f in samples[i][1]] or [0]))
        sc = ctr(max(samples[si][1], key=lambda f: f["w"] * f["h"]))
    chosen: list = [None] * len(samples)

    def pick(i, ref):
        best = min(samples[i][1], key=lambda f: math.dist(ctr(f), ref), default=None)
        return best if best and math.dist(ctr(best), ref) < 0.22 else None

    chosen[si] = pick(si, sc) or (min(samples[si][1], key=lambda f: math.dist(ctr(f), sc)) if samples[si][1] else None)
    for rng in (range(si + 1, len(samples)), range(si - 1, -1, -1)):
        ref = ctr(chosen[si]) if chosen[si] else sc
        for i in rng:
            f = pick(i, ref)
            if f:
                chosen[i] = f
                ref = ctr(f)
    pts = [(samples[i][0], chosen[i]) for i in range(len(samples)) if chosen[i]]
    if not pts:
        raise ValueError("没能跟上这张脸，换一段试试。")
    # 平滑：中心做 5 点滑动平均；大小用中位数（画面不忽大忽小）
    xs = [ctr(f)[0] for _, f in pts]
    ys = [ctr(f)[1] for _, f in pts]
    sm = lambda v, i: sum(v[max(0, i - 2): i + 3]) / len(v[max(0, i - 2): i + 3])  # noqa: E731
    fh = sorted(f["h"] for _, f in pts)[len(pts) // 2]
    tight = min(0.7, max(0.15, float(tight)))
    h = min(1.0, fh / tight)
    w = h * H * aspect / W
    keys = []
    for i, (t, _) in enumerate(pts):
        cx, cy = sm(xs, i), sm(ys, i)
        box = _fix_box({"x": cx - w / 2, "y": cy - h * 0.42, "w": w, "h": h}, W, H, aspect)   # 眼睛在画面上三分之一附近
        keys.append({"t": round(t, 3), **{k: round(v, 4) for k, v in box.items()}})
    if keys[0]["t"] > a:
        keys.insert(0, {**keys[0], "t": round(a, 3)})
    if keys[-1]["t"] < b:
        keys.append({**keys[-1], "t": round(b, 3)})
    found = sum(1 for c in chosen if c)
    log(f"镜头工坊：跟随人脸 {shot_id} {a:.1f}–{b:.1f} 秒，{len(samples)} 帧里有 {found} 帧跟上了（{'AI' if use_ai else 'OpenCV'}）")
    return {"keys": keys, "samples": len(samples), "found": found, "method": "ai" if use_ai else "opencv"}


def _faces_opencv_available() -> bool | None:
    try:
        import cv2
    except ImportError:
        return False
    return hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data")


# ---------------------------------------------------------------- 慢动作
# 光流补帧（minterpolate）只能用一个核，1080p 每秒原片要算半分钟左右。
# 所以把要放慢的那段切成几块，同时开几个 ffmpeg 各算一块，最后无缝拼起来：
# 每块前后多读几帧原片当“边角料”（补帧要看前后帧），算完再按统一的输出帧网格精确剪掉，
# 所以接缝处和一口气算出来的是同一帧，不会多一帧或少一帧。
FLOW = {"fine": "mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",     # 标准：最稳
        "fast": "mi_mode=mci:mc_mode=obmc:me_mode=bilat:me=ds"}        # 快速：约快一半，复杂运动偶尔有块状瑕疵
_MEM_PER_1080P = 0.9           # GB：一个 1080p 光流进程大约占这么多内存


def _avail_mem_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class _M(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _M(); m.dwLength = ctypes.sizeof(_M)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullAvailPhys / 2 ** 30
            return None
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2 ** 20
    except Exception:  # noqa: BLE001 — 拿不到就按核数来
        pass
    return None


def _slowmo_workers(seconds: float, width: int, height: int) -> int:
    """同时算几块：设置里填了就用设置的；0 = 按 CPU 核数和空闲内存自动定。每块至少 0.5 秒原片。"""
    want = int(config.get("lab_slowmo_workers") or 0)
    if want <= 0:
        want = os.cpu_count() or 2
        mem = _avail_mem_gb()
        if mem is not None:
            per = _MEM_PER_1080P * max(0.15, (width * height) / (1920 * 1080))
            want = min(want, max(1, int((mem - 1.5) / per)))     # 给系统和别的程序留 1.5 GB
    return max(1, min(want, int(seconds / 0.5) or 1, 32))


def _ffmpeg_bg(cmd: list[str]) -> None:
    """后台算块：Windows 上用“低于正常”优先级，算慢动作时电脑别的事不卡。"""
    import subprocess
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if os.name == "nt" else 0
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg 出错：\n{tail}")


def _src_size(clip: dict) -> tuple[int, int]:
    w, h = (clip.get("width") or 1920), (clip.get("height") or 1080)
    if w > 1920:                                  # 处理前先缩到 1920 宽
        h, w = round(h * 1920 / w), 1920
    return int(w), int(h)


def slowmo(project: Project, shot_id: str, a, b, speed: float = 0.5, method: str = "auto", label: str = "",
           quality: str = "fine") -> dict:
    s, clip = _source(project, shot_id)
    a, b = _range(s, a, b)
    speed = max(0.1, min(1.0, float(speed)))
    out_fps = int(config.get("render_fps"))
    src_fps = float(clip.get("fps") or 30)
    need = out_fps / speed                      # 放慢后要每秒这么多原始帧才不卡
    q = Fraction(speed).limit_denominator(100)  # 用分数：补出来的帧正好落在成片的帧位上，不用再丢帧/重复帧
    rate = Fraction(out_fps) / q
    rate_s, slow = f"{rate.numerator}/{rate.denominator}", f"settb=AVTB,setpts=PTS*{q.denominator}/{q.numerator}"
    if method == "auto":
        method = "native" if src_fps >= need * 0.9 else "flow"
    quality = quality if quality in FLOW else "fine"
    scale = "scale='min(1920,iw)':-2"
    if method == "native":                      # 高帧率原片：直接放慢，最自然
        interp = ""
    elif method == "blend":                     # 帧混合：快，动作快时有重影
        interp = f"framerate=fps={rate_s},"
    else:                                       # 光流补帧：更顺，但最费时间
        method = "flow"
        interp = f"minterpolate=fps={rate_s}:{FLOW[quality]},"
    aid = _new_id(project)
    tmp = _tmp(project, aid)
    t0 = time.time()
    dur = b - a
    total = max(1, round(dur / speed * out_fps))                # 成片一共多少帧
    w, h = _src_size(clip)
    n = 1 if method == "native" else _slowmo_workers(dur, w, h)
    tag = {"native": "直接放慢", "blend": "帧混合", "flow": "光流补帧" + ("（快速）" if quality == "fast" else "")}[method]
    log(f"镜头工坊：慢动作 {shot_id} {a:.1f}–{b:.1f} 秒 × {speed:g}（{tag}，原片 {src_fps:g}fps）"
        + (f"，分 {n} 块同时算…" if n > 1 else "…"))
    if n == 1:
        vf = f"{scale},{interp}{slow},fps={out_fps},setsar=1,format=yuv420p"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start'] + a:.3f}", "-t", f"{dur:.3f}", "-i", s["src"],
             "-vf", vf, "-an", *ENC, str(tmp)])
    else:
        _slowmo_chunks(s, a, speed, out_fps, src_fps, total, n, f"{scale},{interp}{slow}", tmp,
                       file_dur=float(clip.get("duration") or 0) or None)
    log(f"  用时 {time.time() - t0:.0f} 秒")
    return _finish(project, aid, "slowmo", tmp, label or f"慢动作 ×{speed:g} · {shot_id}", auto_label=not label,
                   src_shot=shot_id, src_in=round(a, 2), src_out=round(b, 2),
                   params={"speed": speed, "method": method, "src_fps": src_fps,
                           **({"quality": quality} if method == "flow" else {})})


def _slowmo_chunks(s: dict, a: float, speed: float, out_fps: int, src_fps: float,
                   total: int, n: int, pre: str, tmp: Path, file_dur: float | None = None) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    pad = 2 / src_fps                                     # 每块前后多读 2 帧原片（补帧要看前后各 2 帧）
    a_abs = s["start"] + a
    step = speed / out_fps                                # 成片一帧对应多少秒原片
    m = min(math.ceil(pad / step), int(a_abs / step + 1e-9))
    origin = a_abs - m * step                             # 所有块共用的时间原点 → 共用一张输出帧网格，
    lead = m / out_fps                                    # 而且成片第 0 帧正好落在 a 上（和一口气算的对齐）
    parts = [tmp.with_name(f"{tmp.stem}.{i:02d}.mp4") for i in range(n)]

    def one(i: int) -> int:
        k0, k1 = round(total * i / n), round(total * (i + 1) / n)
        t0, t1 = a_abs + k0 * speed / out_fps, a_abs + k1 * speed / out_fps
        r0 = max(origin, t0 - pad)
        r1 = t1 + pad if file_dur is None else min(file_dur, t1 + pad)
        vf = (f"setpts=PTS+{r0 - origin:.6f}/TB,{pre},fps={out_fps},"
              f"trim=start={lead + k0 / out_fps:.6f}:end={lead + k1 / out_fps:.6f},setpts=PTS-STARTPTS,"
              f"setsar=1,format=yuv420p")
        _ffmpeg_bg(["ffmpeg", "-y", "-v", "error", "-ss", f"{r0:.4f}", "-t", f"{r1 - r0:.4f}", "-i", s["src"],
                    "-vf", vf, "-an", "-threads", "2", *ENC, str(parts[i])])
        return i

    try:
        done = 0
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(one, i) for i in range(n)]
            for f in as_completed(futs):
                f.result()                                # 有一块出错就整体报错
                done += 1
                log(f"  已算完 {done}/{n} 块")
        lst = tmp.with_name(f"{tmp.stem}.list.txt")
        lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        try:
            run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c", "copy", "-movflags", "+faststart", str(tmp)])
        finally:
            lst.unlink(missing_ok=True)
    finally:
        for p in parts:
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------- 补镜头
PROMPT_SYSTEM = """You write prompts for an image-to-video model that will animate ONE reference still from a
traveller's own footage into a short, believable shot that fills a gap in their travel vlog.
Keep the place, light and people consistent with the still; describe gentle, realistic camera motion and
natural movement only. No text, no logos, no new people who are not in the still. 40-70 words, English,
present tense, one paragraph. Reply with ONLY the prompt."""


def draft_prompt(project: Project, llm, gap: str, shot_id: str = "", note: str = "") -> str:
    """让编故事的模型根据“缺什么”和参考镜头的日志，写一段给视频模型的提示词。"""
    lg = read_json(project.logs_dir / f"{shot_id}.json", {}) if shot_id else {}
    prompt = (f"Gap in the edit: {gap or '(not specified)'}\n"
              f"Reference shot log: {json.dumps({k: lg.get(k) for k in ('summary', 'place', 'mood', 'shot_type', 'tags')}, ensure_ascii=False)}\n"
              f"Creator's note: {note or '(none)'}\nAspect: {'9:16 vertical' if project.settings().get('vertical') else '16:9'}")
    text = llm._call(llm.story_model, PROMPT_SYSTEM, [{"type": "text", "text": prompt}], 400, step="lab_prompt") \
        if hasattr(llm, "_call") else ""
    return (text or f"Slow cinematic push-in on the scene, gentle natural motion, {gap}").strip().strip('"')


def _fal_key() -> str:
    from .llm import _clean, _read_dotenv
    v = _read_dotenv().get(FAL_KEY_VAR) or os.environ.get(FAL_KEY_VAR, "")
    return _clean(v) if v else ""


def key_hint() -> dict:
    k = _fal_key()
    return {"has_key": bool(k), "hint": f"{k[:6]}…{k[-4:]}" if len(k) > 12 else ("已设置" if k else "")}


def _http(method: str, url: str, key: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Key {key}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        from .llm import FatalError
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise FatalError(f"视频生成服务拒绝了这个 Key（{e.code}）。到设置页“镜头工坊”检查 FAL_KEY。{detail}") from None
        if e.code == 402 or "balance" in detail.lower() or "credit" in detail.lower():
            raise FatalError(f"视频生成服务余额不足（{e.code}）：{detail}") from None
        if e.code in (404, 422):
            raise FatalError(f"视频生成服务不认这个请求（{e.code}）。检查设置页里的模型名和额外参数：{detail}") from None
        raise RuntimeError(f"视频生成服务出错（{e.code}）：{detail}") from None


def _generate_fal(ref: Path, prompt: str, seconds: int, aspect: str, dst: Path) -> str:
    key = _fal_key()
    if not key:
        from .llm import FatalError
        raise FatalError("还没有填视频生成服务的 Key（FAL_KEY）。到设置页“镜头工坊”填写，或者先用离线模拟。")
    model = str(config.get("lab_model")).strip().strip("/")
    base = str(config.get("lab_base_url") or "https://queue.fal.run").rstrip("/")
    body = {"prompt": prompt, "image_url": "data:image/jpeg;base64," + base64.b64encode(ref.read_bytes()).decode(),
            "duration": str(seconds), "aspect_ratio": aspect}
    if config.get("lab_extra"):
        body.update(json.loads(config.get("lab_extra")))
    log(f"  提交给 {model}（{seconds} 秒，{aspect}）…")
    sub = _http("POST", f"{base}/{model}", key, body)
    status_url = sub.get("status_url") or f"{base}/{model}/requests/{sub.get('request_id')}/status"
    result_url = sub.get("response_url") or f"{base}/{model}/requests/{sub.get('request_id')}"
    t0, last = time.time(), ""
    while True:
        st = _http("GET", status_url, key)
        state = st.get("status", "")
        if state != last:
            pos = st.get("queue_position")
            log(f"  状态：{ {'IN_QUEUE': '排队中', 'IN_PROGRESS': '生成中', 'COMPLETED': '完成'}.get(state, state)}"
                + (f"（前面还有 {pos} 个）" if pos and state == "IN_QUEUE" else ""))
            last = state
        if state == "COMPLETED":
            break
        if state in ("FAILED", "ERROR", "CANCELLED"):
            raise RuntimeError(f"视频生成失败：{json.dumps(st, ensure_ascii=False)[:300]}")
        if time.time() - t0 > 20 * 60:
            raise RuntimeError("视频生成等了 20 分钟还没好，先停下。可以稍后在服务商后台查看这个请求。")
        time.sleep(float(os.environ.get("VLOGAI_POLL", "4")))   # 测试时可以调短
    res = _http("GET", result_url, key)
    video = res.get("video") or (res.get("videos") or [{}])[0]
    url = video.get("url") if isinstance(video, dict) else video
    if not url:
        raise RuntimeError(f"服务商没有返回视频地址：{json.dumps(res, ensure_ascii=False)[:300]}")
    with urllib.request.urlopen(url, timeout=300) as r, dst.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return model


def _generate_mock(ref: Path, seconds: int, dst: Path, OW: int, OH: int) -> str:
    """离线模拟：静帧慢推 + 角落标一行字。不花钱，只用来测流程。"""
    from .render import _ff_path, find_font
    fps = int(config.get("render_fps"))
    n = seconds * fps
    font = find_font(config.get("font") or None)
    txt = (f",drawtext=fontfile='{_ff_path(font)}':text='AI 补镜头（离线模拟）':fontsize={OH // 30}:"
           f"fontcolor=white@0.85:borderw=2:bordercolor=black@0.5:x=w*0.04:y=h*0.06") if font else ""
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(ref), "-t", str(seconds),
         "-vf", f"scale={OW * 2}:{OH * 2}:force_original_aspect_ratio=increase,crop={OW * 2}:{OH * 2},"
                f"zoompan=z='1+0.12*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={OW}x{OH}:fps={fps}"
                f"{txt},format=yuv420p", *ENC, str(dst)])
    return "mock"


def estimate(seconds: int) -> float:
    return round(float(config.get("lab_price_per_sec")) * seconds, 2)


def fill(project: Project, ref_shot: str, ref_t: float, prompt: str, seconds: int = 5,
         gap: str = "", label: str = "", mock: bool | None = None) -> dict:
    seconds = 10 if int(seconds) >= 8 else 5
    provider = "mock" if (mock or config.get("lab_provider") == "mock") else "fal"
    ref = still(project, ref_shot, ref_t, width=1280)
    OW, OH = out_size(project)
    aid = _new_id(project)
    tmp = _tmp(project, aid)
    log(f"镜头工坊：补镜头 {seconds} 秒（{'离线模拟' if provider == 'mock' else config.get('lab_model')}），参考 {ref_shot} @ {ref_t:.1f} 秒")
    if provider == "mock":
        model = _generate_mock(ref, seconds, tmp, OW, OH)
        cost = 0.0
    else:
        model = _generate_fal(ref, prompt, seconds, "9:16" if OH > OW else "16:9", tmp)
        cost = estimate(seconds)
        from . import usage
        usage.record_video(model, seconds, cost)
    return _finish(project, aid, "fill", tmp, label or (gap[:24] if gap else f"补镜头 · {ref_shot}"), auto_label=not label and not gap,
                   src_shot=ref_shot, src_in=round(float(ref_t), 2), src_out=round(float(ref_t), 2),
                   params={"prompt": prompt, "gap": gap, "seconds": seconds, "provider": provider, "model": model},
                   cost=cost)


# ---------------------------------------------------------------- 放进时间线
def insert(project: Project, asset_id: str, at: int | None = None) -> dict:
    """把一个片段插进当前时间线（at = 插在第几个之前；不填放到最后），存成新版本。"""
    from . import story
    st = read_json(project.story_json, {}) or {}
    if not st.get("timeline"):
        raise ValueError("还没有时间线，先在“故事线”里剪一版")
    a = next((x for x in assets(project) if x["id"] == asset_id), None)
    if not a:
        raise ValueError(f"找不到片段 {asset_id}")
    tl = list(st["timeline"])
    at = len(tl) if at is None else max(0, min(int(at), len(tl)))
    near = tl[at - 1] if at > 0 else tl[0]
    tl.insert(at, {"shot_id": asset_id, "in": 0.0, "out": a["duration"], "chapter": near.get("chapter", ""),
                   "role": "lab", "why": a.get("label", ""), "keep_audio": False, "text": ""})
    shots = (read_json(project.shots_json, []) or []) + lab_shots(project)
    plan = {k: st[k] for k in ("title", "logline", "chapters") if k in st}
    plan["timeline"] = tl
    plan = story._validate(plan, shots)
    story._save(project, plan, f"镜头工坊：插入 {asset_id}")
    return plan
