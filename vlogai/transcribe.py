"""Step 3 (optional) — transcribe speech with Whisper, locally.

Uses faster-whisper if installed (pip install faster-whisper). Skipped silently otherwise.
Chinese + English are auto-detected per clip.
"""
from __future__ import annotations

from .common import Project, log, read_json, write_json


def transcribe(project: Project, model_size: str = "small") -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("未安装 faster-whisper，跳过语音转写（pip install faster-whisper 可开启）")
        return
    clips = [c for c in read_json(project.clips_json, []) if c["has_audio"]]
    todo = [c for c in clips if not (project.transcripts_dir / f"{c['id']}.json").exists()]
    if not todo:
        log("语音转写：已全部完成（缓存）")
        return
    log(f"语音转写 {len(todo)} 条素材（模型 {model_size}，首次运行会下载模型）…")
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    for i, c in enumerate(todo, 1):
        try:
            segments, info = model.transcribe(c["path"], vad_filter=True, beam_size=1)
            segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                    for s in segments if s.text.strip()]
            data = {"language": info.language, "segments": segs}
        except Exception as e:  # noqa: BLE001
            data = {"language": None, "segments": [], "error": str(e)}
        write_json(project.transcripts_dir / f"{c['id']}.json", data)
        if i % 10 == 0 or i == len(todo):
            log(f"  {i}/{len(todo)}")


def speech_for(project: Project, clip_id: str, start: float, end: float) -> str:
    """Transcript text that falls inside [start, end] of a clip."""
    data = read_json(project.transcripts_dir / f"{clip_id}.json")
    if not data:
        return ""
    parts = [s["text"] for s in data["segments"] if s["end"] > start and s["start"] < end]
    return " ".join(parts)[:600]


def speech_segments(project: Project, clip_id: str, start: float, end: float) -> list[dict]:
    data = read_json(project.transcripts_dir / f"{clip_id}.json")
    if not data:
        return []
    return [s for s in data["segments"] if s["end"] > start and s["start"] < end]
