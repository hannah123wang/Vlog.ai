"""Step 4 — the AI "watches" every shot (keyframes + transcript) and writes a log entry.

Results are cached per shot in work/logs/, so re-running only pays for new shots.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import Project, log, read_json, write_json
from .llm import FatalError
from .transcribe import speech_for

SYSTEM = """You are an assistant editor logging raw travel-vlog footage.
You see a few keyframes from ONE shot (in time order, timestamps relative to the shot start)
plus metadata and any transcribed speech. Describe what is actually visible/audible — do not invent.
Reply with ONLY a JSON object, no prose."""

PROMPT = """<meta>{meta}</meta>
Keyframe times (s, relative to shot start): {times}
Transcribed speech in this shot: {speech}
The creator's main subject(s) — the people this film is about: {subject}

Return JSON with exactly these keys:
{{
 "summary": "one vivid sentence of what happens",
 "place": "best guess of the kind of place / landmark if recognisable, else ''",
 "people": ["who appears, e.g. 'me (selfie)', 'friend', 'street vendor'"],
 "activity": "what is happening",
 "mood": "emotional tone, 1-3 words",
 "shot_type": "one of: wide, medium, close-up, detail, selfie, pov, drone, timelapse, screen",
 "story_roles": ["any of: opener, establishing, travel-transition, arrival, food, moment, reaction, reflection, climax, ending, b-roll"],
 "highlight": 1-10 integer (how much a viewer would want to see this; blurry/shaky/nothing happening = low),
 "best_moment": {{"start": seconds, "end": seconds}}  (best continuous 2-8s part, relative to shot start, within 0..{dur}),
 "keep_audio": true if the original sound/speech is worth hearing in the final vlog,
 "faces_clear": true only if a human face is clearly visible, in focus and facing the camera enough to recognise,
 "subject_present": true if one of the creator's main subjects (above) appears in this shot,
 "people_together": true if two or more people share the frame,
 "scenery_value": 1-10 how striking the landscape, seascape, forest, landmark or architecture is (1 if there is none),
 "tags": ["3-6 short search tags"]
}}
Write all free-text values in {lang}."""


def describe_all(project: Project, llm, lang: str = "Chinese", workers: int = 4,
                 subject: str = "") -> None:
    shots = read_json(project.shots_json)
    if not shots:
        raise SystemExit("先运行 frames")
    todo = [s for s in shots if not (project.logs_dir / f"{s['id']}.json").exists()]
    log(f"AI 看素材：{len(shots)} 个镜头，其中 {len(todo)} 个需要新分析（{llm.name}）…")
    if not todo:
        return

    stop = threading.Event()             # 一个镜头遇到致命错误（Key 不对、余额不足）后，其余镜头直接跳过

    def work(s: dict):
        if stop.is_set():
            return
        speech = speech_for(project, s["clip_id"], s["start"], s["end"])
        meta = {"file": Path(s["src"]).name, "day": s["day"], "recorded": s["created"],
                "gps": s["gps"], "duration": s["duration"], "quality": s["quality"],
                "speech": bool(speech)}
        prompt = PROMPT.format(
            meta=json.dumps(meta, ensure_ascii=False),
            times=[round(f["t"] - s["start"], 1) for f in s["frames"]],
            speech=speech or "(none)", dur=round(s["duration"], 1), lang=lang,
            subject=subject or project.settings().get("subject")
                    or "(not specified — treat the person who recurs most as the main subject)")
        try:
            d = llm.describe(SYSTEM, prompt, [f["path"] for f in s["frames"]])
        except FatalError:
            stop.set()
            raise                     # Key 不对 / 余额不足：整步停下，不要假装“完成”
        except Exception as e:  # noqa: BLE001
            log(f"  {s['id']} 分析失败，稍后重跑会再试: {e}")
            return
        d["speech"] = speech
        write_json(project.logs_dir / f"{s['id']}.json", d)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, _ in enumerate(ex.map(work, todo), 1):
            if i % 20 == 0 or i == len(todo):
                log(f"  {i}/{len(todo)}")
    if llm.usage["input_tokens"]:
        log(f"  本步用量：输入 {llm.usage['input_tokens']:,} tokens，输出 {llm.usage['output_tokens']:,} tokens")


def load_logged_shots(project: Project) -> list[dict]:
    """Shots merged with their AI log — the material the story step works from."""
    excluded = set((read_json(project.root / "flags.json", {}) or {}).get("exclude", []))
    out = []
    for s in read_json(project.shots_json, []):
        if s["id"] in excluded:          # 用户在界面里排除的镜头，AI 编故事时看不到
            continue
        d = read_json(project.logs_dir / f"{s['id']}.json")
        if d:
            out.append({**s, "log": d})
    return out
