"""Step 1 — scan the footage folder and read metadata (time, GPS, duration, fps)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .common import VIDEO_EXTS, Project, log, run, write_json


def _parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s[:19], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_iso6709(s: str | None):
    """'+43.6532-079.3832+076.000/' -> (lat, lon)."""
    if not s:
        return None
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", s)
    return (float(m.group(1)), float(m.group(2))) if m else None


def _fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key, "0/0")
        try:
            n, d = v.split("/")
            if float(d):
                f = float(n) / float(d)
                if 1 < f < 241:
                    return round(f, 3)
        except ValueError:
            pass
    return 30.0


def probe_file(path: Path) -> dict | None:
    try:
        res = run(["ffprobe", "-v", "error", "-print_format", "json",
                   "-show_format", "-show_streams", str(path)])
    except RuntimeError as e:
        log(f"跳过无法读取的文件 {path.name}: {e}")
        return None
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError:
        log(f"跳过读不出信息的文件 {path.name}（可能已损坏）")
        return None
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        return None
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    vtags = {k.lower(): val for k, val in (v.get("tags") or {}).items()}

    # rotation (phones record landscape pixels + a rotation flag)
    rot = 0
    try:
        rot = int(vtags.get("rotate", 0))
    except ValueError:
        pass
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    if abs(rot) % 180 == 90:
        w, h = h, w

    created = (_parse_time(tags.get("com.apple.quicktime.creationdate"))
               or _parse_time(tags.get("creation_time"))
               or _parse_time(vtags.get("creation_time")))
    time_source = "metadata"
    if created is None:
        created = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        time_source = "file_mtime"

    gps = _parse_iso6709(tags.get("com.apple.quicktime.location.iso6709") or tags.get("location"))

    return {
        "path": str(path.resolve()),
        "name": path.name,
        "duration": round(float(fmt.get("duration") or v.get("duration") or 0), 3),
        "width": w,
        "height": h,
        "fps": _fps(v),
        "has_audio": a is not None,
        "created": created.isoformat(),
        "time_source": time_source,
        "gps": gps,
        "device": tags.get("com.apple.quicktime.model") or tags.get("encoder") or "",
    }


def scan(footage_dir: Path, project: Project) -> list[dict]:
    footage_dir = Path(footage_dir).expanduser().resolve()
    files = sorted(p for p in footage_dir.rglob("*")
                   if p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("."))
    log(f"在 {footage_dir} 找到 {len(files)} 个视频文件，读取元数据…")
    clips = [c for c in (probe_file(p) for p in files) if c and c["duration"] >= 0.5]
    clips.sort(key=lambda c: c["created"])

    # group by calendar day (local-ish: uses the stored offset) -> Day 1, Day 2 ...
    days = sorted({c["created"][:10] for c in clips})
    day_index = {d: i + 1 for i, d in enumerate(days)}
    for i, c in enumerate(clips, 1):
        c["id"] = f"c{i:04d}"
        c["day"] = day_index[c["created"][:10]]

    write_json(project.clips_json, clips)
    total = sum(c["duration"] for c in clips)
    log(f"共 {len(clips)} 条素材，总时长 {total/60:.1f} 分钟，跨 {len(days)} 天 → {project.clips_json.name}")
    return clips
