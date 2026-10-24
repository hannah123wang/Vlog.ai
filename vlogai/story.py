"""Step 5 — the AI reads every shot log and writes the story: an edit decision list (story.json).

story.json is plain JSON you can open and hand-edit (reorder, delete, change in/out),
or change with a sentence via `vlogai revise "..."`. Every version is kept in story_versions/.
"""
from __future__ import annotations

import json
from datetime import datetime

from . import config
from .common import Project, log, read_json, write_json
from .describe import load_logged_shots
from .lab import lab_shots
from .protect import enforce, must_include_block, protected

SYSTEM = """You are a senior documentary/travel-vlog editor. From a log of raw shots you build a
rough cut that feels like a story, not a slideshow. Craft rules:
- COLD OPEN: 5-12 s hook built from the most striking or emotional moments (may be out of order).
- Then a title beat, then the trip in broadly chronological order, grouped into chapters
  (by day/place/theme). Each chapter: establish the place (wide) -> the moments (medium/close)
  -> a reaction or small human beat. Give the trip an emotional arc (anticipation, discovery,
  a peak, reflection) and a real ending (quiet, reflective, or a callback to the opening).
- Rhythm: follow the pacing note in the request. Vary shot sizes; never put two near-identical
  shots back to back. Prefer high 'hl' and 'q'.
  Shots with q < 0.25 are technically bad — use only if irreplaceable.
- Use shots with worthwhile speech as anchors (keep_audio=true) — they carry the narrative.
- Hit the target length within ±10%.
- MUST-INCLUDE: the request lists shots that are protected (the creator's face, the two of them
  together, signature scenery). Every one of them has to appear somewhere it fits. Place them where
  they belong in the story — do not dump them at the end.
Reply with ONLY JSON, no prose."""

PROMPT = """Trip brief from the creator: {brief}
Target length: <target_seconds>{target}</target_seconds> seconds.
Pacing: {pacing}
Aim for roughly {n_segments} segments in the timeline.
Write titles, captions and 'why' notes in {lang}.

Shot log. Fields: id, day, time, dur (s), q (technical quality 0-1), hl (highlight 1-10),
type, roles, place, mood, sum, best (best in/out, s relative to shot start), speech.
<shots_json>{shots}</shots_json>

Shots that MUST appear in the cut (protected — the creator will notice if they are missing):
<must_include>{must_include}</must_include>

Return JSON:
{{
 "title": "vlog title",
 "logline": "one sentence: the story this vlog tells",
 "chapters": [{{"name": "...", "purpose": "what this chapter does emotionally"}}],
 "timeline": [
   {{"shot_id": "c0001_s01", "in": 0.0, "out": 3.2,
     "chapter": "chapter name", "role": "hook|title|establishing|moment|reaction|transition|ending",
     "why": "short editor note", "keep_audio": false,
     "text": "optional on-screen caption, usually empty"}}
 ]
}}
'in'/'out' are seconds RELATIVE TO THE SHOT START and must lie within 0..dur of that shot.
A shot may be used at most twice (e.g. once in the cold open, once in its chapter)."""

REVISE_PROMPT = """Here is the current cut (story.json) and the shot log it was built from.
Revise the cut according to the creator's instruction, keeping everything else that works.
Instruction: {instruction}

Target length: <target_seconds>{target}</target_seconds> seconds (unless the instruction changes it).
Pacing: {pacing}
Write text in {lang}.

<current_story>{story}</current_story>
<shots_json>{shots}</shots_json>

Shots that MUST still appear after your revision:
<must_include>{must_include}</must_include>

Return the full revised story in the same JSON format (title, logline, chapters, timeline)."""


def _pacing(target_seconds: float) -> tuple[str, int]:
    """短片和长片的剪辑节奏完全不同——这里按目标时长切换。"""
    cfg = config.load()     # 设置页“剪辑节奏”：长片从几分钟开始、短片 / 长片平均每段几秒
    if target_seconds <= float(cfg["longform_min"]) * 60:
        return ("tight vlog cutting: b-roll shots 1.5-4 s, speech/reaction moments as long as the "
                "sentence needs. Cut anything that repeats.",
                max(8, int(target_seconds / float(cfg["short_seg_sec"]))))
    return ("long-form edit: let shots breathe (6-15 s each, the best ones longer), keep most of the "
            "trip rather than only the highlights, but still drop dead air, repeats and soft/shaky "
            "material, and keep a varied rhythm so it never feels like a slideshow.",
            max(12, int(target_seconds / float(cfg["long_seg_sec"]))))


def _compact(shots: list[dict]) -> list[dict]:
    rows = []
    for s in shots:
        lg = s["log"]
        rows.append({
            "id": s["id"], "day": s["day"], "time": s["created"][5:16].replace("T", " "),
            "dur": round(s["duration"], 1), "q": s["quality"], "hl": lg.get("highlight"),
            "type": lg.get("shot_type"), "roles": lg.get("story_roles"), "place": lg.get("place"),
            "mood": lg.get("mood"), "sum": lg.get("summary"), "best": lg.get("best_moment"),
            "speech": (lg.get("speech") or "")[:200],
        })
    return rows


def _max_tokens(n_segments: int) -> int:
    """长片的时间线本身就很长，输出上限要跟着涨。"""
    return max(8000, min(48000, 4000 + 140 * n_segments))


def _validate(plan: dict, shots: list[dict], extra: list[dict] | None = None) -> dict:
    # extra：镜头工坊做出的片段（不交给 AI 看，但时间线里可以用，AI 修改时也要保住）
    by_id = {s["id"]: s for s in list(shots) + list(extra or [])}
    clean, dropped = [], 0
    for item in plan.get("timeline", []):
        s = by_id.get(item.get("shot_id"))
        if not s:
            dropped += 1
            continue
        dur = s["duration"]
        a = max(0.0, min(float(item.get("in", 0) or 0), dur))
        b = max(0.0, min(float(item.get("out", dur) or dur), dur))
        if b - a < 0.8:  # too short to read — widen around the chosen point
            a = max(0.0, min(a, dur - 1.5))
            b = min(dur, a + 1.5)
        if b - a < 0.5:
            dropped += 1
            continue
        clean.append({
            "shot_id": s["id"], "in": round(a, 2), "out": round(b, 2),
            "chapter": item.get("chapter", ""), "role": item.get("role", ""),
            "why": item.get("why", ""), "keep_audio": bool(item.get("keep_audio")) and s["has_audio"],
            "text": item.get("text", "") or "",
            # absolute positions in the source file (used by render/export)
            "src": s["src"], "src_in": round(s["start"] + a, 3), "src_out": round(s["start"] + b, 3),
        })
    if dropped:
        log(f"  已忽略 {dropped} 个无效镜头引用")
    plan["timeline"] = clean
    plan["total_seconds"] = round(sum(t["out"] - t["in"] for t in clean), 1)
    return plan


def _save(project: Project, plan: dict, note: str) -> None:
    plan["_note"] = note
    plan["_created"] = datetime.now().isoformat(timespec="seconds")
    project.versions_dir.mkdir(exist_ok=True)
    nums = [int(f.stem[7:]) for f in project.versions_dir.glob("story_v*.json") if f.stem[7:].isdigit()]
    n = max(nums, default=0) + 1          # 删过某个版本文件也不会覆盖已有版本
    write_json(project.versions_dir / f"story_v{n}.json", plan)
    write_json(project.story_json, plan)
    log(f"故事版本 v{n}：《{plan.get('title','')}》 {len(plan['timeline'])} 个镜头，"
        f"约 {plan['total_seconds']:.0f} 秒 → {project.story_json.name}")
    if plan.get("logline"):
        log(f"  故事线：{plan['logline']}")


def build_story(project: Project, llm, brief: str, target_seconds: float, lang: str) -> dict:
    shots = load_logged_shots(project)
    if not shots:
        raise SystemExit("还没有镜头日志，先运行 describe")
    pacing, n_seg = _pacing(target_seconds)
    prot = protected(project, shots, target_seconds)
    log(f"AI 编故事：{len(shots)} 个镜头 → 目标 {target_seconds/60:.1f} 分钟，约 {n_seg} 个片段…")
    prompt = PROMPT.format(brief=brief or "(none — infer the story from the footage)",
                           target=int(target_seconds), lang=lang, pacing=pacing, n_segments=n_seg,
                           shots=json.dumps(_compact(shots), ensure_ascii=False),
                           must_include=must_include_block(prot, shots))
    plan = _validate(llm.plan(SYSTEM, prompt, max_tokens=_max_tokens(n_seg)), shots, lab_shots(project))
    plan = enforce(plan, prot, shots)
    _save(project, plan, f"initial; brief={brief!r}")
    return plan


def revise_story(project: Project, llm, instruction: str, target_seconds: float, lang: str) -> dict:
    shots = load_logged_shots(project)
    current = read_json(project.story_json)
    if not current:
        raise SystemExit("还没有 story.json，先运行 story")
    slim = {k: current[k] for k in ("title", "logline", "chapters") if k in current}
    slim["timeline"] = [{k: t[k] for k in ("shot_id", "in", "out", "chapter", "role", "why",
                                            "keep_audio", "text")} for t in current["timeline"]]
    if llm.name == "mock":
        log("  离线模式无法理解修改指令，仅重新校验现有 story.json")
        plan = _validate(slim, shots, lab_shots(project))
    else:
        prot = protected(project, shots, target_seconds)
        pacing, n_seg = _pacing(target_seconds)
        prompt = REVISE_PROMPT.format(instruction=instruction, target=int(target_seconds), lang=lang,
                                      pacing=pacing,
                                      story=json.dumps(slim, ensure_ascii=False),
                                      shots=json.dumps(_compact(shots), ensure_ascii=False),
                                      must_include=must_include_block(prot, shots))
        plan = _validate(llm.plan(SYSTEM, prompt, max_tokens=_max_tokens(n_seg)), shots, lab_shots(project))
        plan = enforce(plan, prot, shots)
    _save(project, plan, f"revise: {instruction}")
    return plan


def recheck(project: Project) -> dict:
    """After hand-editing story.json: re-validate and recompute source timecodes."""
    shots = read_json(project.shots_json, [])
    plan = _validate(read_json(project.story_json), shots, lab_shots(project))
    write_json(project.story_json, plan)
    return plan
