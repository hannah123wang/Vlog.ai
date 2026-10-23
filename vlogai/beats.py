"""Optional — nudge cut points onto the music's beats (needs `pip install librosa`)."""
from __future__ import annotations

import bisect

from .common import log


def detect_beats(music_path: str) -> list[float]:
    try:
        import librosa
    except ImportError:
        log("未安装 librosa，跳过卡点（pip install librosa 可开启）")
        return []
    y, sr = librosa.load(music_path, sr=22050, mono=True, duration=900)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    beats = librosa.frames_to_time(frames, sr=sr).tolist()
    try:
        bpm = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
    except (TypeError, IndexError):
        bpm = 0.0
    log(f"音乐节拍：约 {bpm:.0f} BPM，{len(beats)} 个节拍点")
    return beats


def snap_to_beats(timeline: list[dict], shot_len: dict[str, tuple[float, float]],
                  beats: list[float], max_shift: float = 0.4) -> list[dict]:
    """Stretch/trim each b-roll segment (±40%) so its cut lands on the nearest beat.
    Segments with kept speech are never changed. Uses spare footage inside the shot."""
    if not beats:
        return timeline
    out, cur = [], 0.0
    for t in timeline:
        t = dict(t)
        length = t["src_out"] - t["src_in"]
        if not t.get("keep_audio"):
            shot_start, shot_end = shot_len[t["shot_id"]]
            lo = cur + max(0.8, length * (1 - max_shift))
            hi = cur + min(length * (1 + max_shift), shot_end - t["src_in"])
            i = bisect.bisect_left(beats, lo)
            cands = [b for b in beats[max(0, i - 1): i + 8] if lo <= b <= hi]
            if cands:
                target = min(cands, key=lambda b: abs(b - (cur + length)))
                length = target - cur
                t["src_out"] = round(t["src_in"] + length, 3)
                t["out"] = round(t["in"] + length, 2)
        out.append(t)
        cur += length
    return out
