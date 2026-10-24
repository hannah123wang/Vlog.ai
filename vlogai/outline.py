"""故事线共创：你写个大概，AI 看完素材把它补成真正能剪的结构。

两步：
  propose_beats  —— 你的想法 + 全部镜头日志 → AI 提出分幕（每一幕都注明用哪些素材支撑）
  story_from_beats —— 你改完/确认分幕 → AI 按这个结构铺出完整时间线
这是和"一键成片"最不一样的地方：结构由你拍板，AI 只负责用真实素材把它填满。
"""
from __future__ import annotations

import json

from .common import Project, log, read_json, write_json
from .describe import load_logged_shots
from .protect import enforce, must_include_block, protected
from .lab import lab_shots
from .story import _compact, _max_tokens, _pacing, _save, _validate

BEATS_SYSTEM = """You are a documentary editor in the story-planning conversation with a creator.
They tell you roughly what the piece should be. You have already watched every shot (you are given
the log). Your job is to turn their rough idea into a concrete beat structure that THIS footage can
actually support — never propose a beat the footage can't carry, and tell them honestly what is
missing. Reply with ONLY JSON."""

BEATS_PROMPT = """The creator's rough idea for the piece:
<idea>{idea}</idea>

Target length: {target} seconds. Pacing: {pacing}
Write all text in {lang}.

These shots are protected — the creator will notice if the film doesn't contain them. Make sure your
beats give every one of them a home:
<must_include>{must_include}</must_include>

Shot log (id, day, time, dur, q=technical quality 0-1, hl=highlight 1-10, type, roles, place, mood,
sum=what happens, best=best in/out, speech):
<shots_json>{shots}</shots_json>

Return JSON:
{{
 "title": "a title for the piece",
 "logline": "one sentence: the story this tells",
 "beats": [
   {{"id": "b1",
     "title": "short name of this beat",
     "intent": "what this beat does for the viewer emotionally, 1-2 sentences",
     "evidence": "the actual footage that supports it — name real moments from the log",
     "shot_ids": ["c0012_s01", "..."],   // candidate shots, best first
     "seconds": 45}}                      // roughly how long this beat should run
 ],
 "missing": ["anything the creator's idea asks for that the footage does NOT contain — be specific and honest, [] if nothing"],
 "notes": "one or two sentences of editorial advice: what is strongest here, what you'd protect"
}}
The beats' seconds should add up to roughly the target length. Order them as they will appear."""


def propose_beats(project: Project, llm, idea: str, target_seconds: float, lang: str) -> dict:
    shots = load_logged_shots(project)
    if not shots:
        raise SystemExit("还没有镜头日志，先跑 describe")
    pacing, n_seg = _pacing(target_seconds)
    prot = protected(project, shots, target_seconds)
    log(f"AI 读你的想法 + {len(shots)} 个镜头，提出分幕结构…")
    prompt = BEATS_PROMPT.format(idea=idea or "(the creator hasn't said — propose what this footage "
                                              "most naturally becomes)",
                                 target=int(target_seconds), pacing=pacing, lang=lang,
                                 shots=json.dumps(_compact(shots), ensure_ascii=False),
                                 must_include=must_include_block(prot, shots))
    data = llm.plan(BEATS_SYSTEM, prompt, max_tokens=8000)
    by_id = {s["id"] for s in shots}
    for b in data.get("beats", []):
        b["shot_ids"] = [s for s in b.get("shot_ids", []) if s in by_id]
    data["idea"] = idea
    data["target_seconds"] = target_seconds
    write_json(project.out("outline.json"), data)
    total = sum(float(b.get("seconds") or 0) for b in data.get("beats", []))
    log(f"分幕：{len(data.get('beats', []))} 幕，合计约 {total/60:.1f} 分钟 → outline.json")
    for b in data.get("beats", []):
        log(f"  · {b.get('title')} ({b.get('seconds')}s)")
    if data.get("missing"):
        log("  素材里没有的：" + "；".join(data["missing"]))
    return data


STORY_FROM_BEATS_SYSTEM = """You are a senior documentary/travel editor. The creator has APPROVED a
beat structure. Build the actual cut that realises it, using only the logged shots. The structure is
theirs — follow the beats in order, honour each beat's intent and length, and do not invent new
beats. Within a beat you decide shot order, in/out points and rhythm: establish, then the moments,
then a human beat; vary shot sizes; never two near-identical shots back to back; prefer high 'hl'
and 'q'; use shots with worthwhile speech as anchors. Reply with ONLY JSON."""

STORY_FROM_BEATS_PROMPT = """Approved structure (follow it in this order):
<beats_json>{beats}</beats_json>

The creator's own words about the piece: {idea}
Total target: <target_seconds>{target}</target_seconds> seconds. Pacing: {pacing}
Aim for roughly {n_segments} segments overall. Write text in {lang}.

Shot log:
<shots_json>{shots}</shots_json>

Shots that MUST appear in the cut (protected — the creator will notice if they are missing):
<must_include>{must_include}</must_include>

Return JSON:
{{
 "title": "...", "logline": "...",
 "chapters": [{{"name": "beat title", "purpose": "..."}}],
 "timeline": [
   {{"shot_id": "c0001_s01", "in": 0.0, "out": 3.2, "chapter": "beat title",
     "role": "hook|establishing|moment|reaction|transition|ending",
     "why": "short editor note", "keep_audio": false, "text": ""}}
 ]
}}
'in'/'out' are seconds RELATIVE TO THE SHOT START, within 0..dur of that shot.
'chapter' must be one of the approved beat titles. A shot may be used at most twice."""


def story_from_beats(project: Project, llm, beats: list[dict], target_seconds: float,
                     lang: str, idea: str = "") -> dict:
    shots = load_logged_shots(project)
    pacing, n_seg = _pacing(target_seconds)
    prot = protected(project, shots, target_seconds)
    log(f"按你确认的 {len(beats)} 幕结构铺时间线（目标 {target_seconds/60:.1f} 分钟）…")
    prompt = STORY_FROM_BEATS_PROMPT.format(
        beats=json.dumps(beats, ensure_ascii=False), idea=idea or "(none)",
        target=int(target_seconds), pacing=pacing, n_segments=n_seg, lang=lang,
        shots=json.dumps(_compact(shots), ensure_ascii=False),
        must_include=must_include_block(prot, shots))
    plan = _validate(llm.plan(STORY_FROM_BEATS_SYSTEM, prompt,
                              max_tokens=_max_tokens(n_seg)), shots, lab_shots(project))
    plan = enforce(plan, prot, shots)
    _save(project, plan, "from approved outline")
    return plan


def mock_beats(project: Project, idea: str, target_seconds: float) -> dict:
    """离线模式：按天分幕，用来调界面。"""
    shots = load_logged_shots(project)
    days: dict[int, list] = {}
    for s in shots:
        days.setdefault(s["day"], []).append(s)
    beats = []
    for i, (day, group) in enumerate(sorted(days.items()), 1):
        group = sorted(group, key=lambda s: -s["quality"])[:12]
        beats.append({"id": f"b{i}", "title": f"Day {day}", "intent": "（离线模拟）这一天的片段",
                      "evidence": f"{len(group)} 个清晰镜头",
                      "shot_ids": [s["id"] for s in group],
                      "seconds": round(target_seconds / max(1, len(days)))})
    data = {"title": "我的旅行 (mock)", "logline": "离线模拟的分幕，用来测界面",
            "beats": beats, "missing": [], "notes": "离线模式不会真的理解你的想法",
            "idea": idea, "target_seconds": target_seconds}
    write_json(project.out("outline.json"), data)
    return data
