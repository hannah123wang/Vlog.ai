"""拍摄前：AI 根据你要去的地方和想要的感觉，给出一份分镜脚本。

这是整个闭环的前半段——拍之前有脚本，拍完之后 AI 按同一套结构剪，
匹配率比"回来之后对着一堆素材硬凑"高得多。

脚本里每一条都带 shot_id，拍的时候记一下（或者事后在界面里认领），
剪辑阶段 story.py 会优先把它放到对应的幕里。
"""
from __future__ import annotations

import json
from html import escape

from .common import Project, log, read_json, write_json

PLAN_SYSTEM = """You are a documentary director planning a shoot with a traveller who is not a
professional. They tell you where they are going, with whom, and the feeling they want. You write a
shot list they can actually execute on a phone or a drone while on holiday.

Rules:
- Think in STORY first, shots second: the piece needs an opening hook, a sense of place, human
  moments, a peak, and an ending. Name the beat each shot serves.
- Every shot must be concrete and shootable: what is in frame, how long to hold, and what to do
  (walk through the doorway, let them walk out of frame, hold five seconds after the action ends).
- Include the unglamorous connective shots people always forget: hands, feet walking, doors, signs,
  food arriving, the ride between places, someone looking back at the camera.
- Call out the shots that make editing possible: a clean wide establisher for each location, at
  least one shot where both people are in frame together, and a quiet ending shot.
- Respect reality: a 4-hour layover is not a day of shooting. Match the volume to the time they have.
- If they mention drone, plan drone shots that are legal and simple (a slow reveal beats a complex
  orbit). If they don't, don't invent gear.
Reply with ONLY JSON."""

PLAN_PROMPT = """The trip:
<brief>{brief}</brief>

Days of shooting: {days}
Target length of the finished film: about {minutes} minutes.
Gear mentioned: {gear}
Write all text in {lang}.

Return JSON:
{{
 "title": "a working title for the film",
 "logline": "one sentence: the story this trip should tell",
 "beats": [
   {{"id": "b1", "title": "beat name", "intent": "what this beat does for the viewer"}}
 ],
 "days": [
   {{"day": 1,
     "where": "place / activity",
     "golden_hours": "when the light will be best here, and what to shoot then",
     "shots": [
       {{"shot_id": "d1s01",
         "beat": "beat title it serves",
         "what": "what is in frame and what happens",
         "framing": "wide | medium | close-up | detail | selfie | pov | drone",
         "hold": "how long to roll, e.g. '8-10s, keep rolling after the action'",
         "why": "why the edit will need this",
         "must_have": true/false}}
     ]}}
 ],
 "must_haves": ["the handful of shots the film cannot be cut without"],
 "reminders": ["practical warnings: batteries, verticals for Reels, record 10s of room tone, etc."]
}}
Aim for {shots_per_day} shots per day — enough to cut from, few enough to actually shoot."""


def make_plan(project: Project, llm, brief: str, days: int, minutes: float,
              gear: str, lang: str) -> dict:
    shots_per_day = 10 if minutes <= 3 else 16
    log(f"AI 写拍摄脚本：{days} 天，目标成片 {minutes:.0f} 分钟…")
    prompt = PLAN_PROMPT.format(brief=brief, days=days, minutes=minutes,
                                gear=gear or "(not specified — assume a phone)",
                                lang=lang, shots_per_day=shots_per_day)
    plan = llm.plan(PLAN_SYSTEM, prompt, max_tokens=8000)
    plan["brief"] = brief
    plan["days_count"] = days
    write_json(project.out("shootplan.json"), plan)
    n = sum(len(d.get("shots", [])) for d in plan.get("days", []))
    log(f"脚本完成：{len(plan.get('beats', []))} 幕、{n} 个镜头 → shootplan.json")
    for d in plan.get("days", []):
        log(f"  Day {d.get('day')} · {d.get('where')} · {len(d.get('shots', []))} 个镜头")
    return plan


def plan_html(project: Project) -> str:
    """手机上能看的拍摄清单——每条可以勾掉。"""
    p = read_json(project.out("shootplan.json"))
    if not p:
        raise SystemExit("还没有拍摄脚本，先运行 plan")
    days = ""
    for d in p.get("days", []):
        shots = "".join(
            f'<label class="shot{" must" if s.get("must_have") else ""}">'
            f'<input type="checkbox">'
            f'<div><b>{escape(s.get("framing",""))}</b> · {escape(s.get("hold",""))}'
            f'{" · <span class=m>必拍</span>" if s.get("must_have") else ""}'
            f'<div class="what">{escape(s.get("what",""))}</div>'
            f'<div class="why">{escape(s.get("beat",""))} — {escape(s.get("why",""))}</div>'
            f'</div></label>' for s in d.get("shots", []))
        days += (f'<section><h2>Day {d.get("day")} · {escape(d.get("where",""))}</h2>'
                 f'<p class="gold">☀️ {escape(d.get("golden_hours",""))}</p>{shots}</section>')
    must = "".join(f"<li>{escape(m)}</li>" for m in p.get("must_haves", []))
    rem = "".join(f"<li>{escape(r)}</li>" for r in p.get("reminders", []))
    html = f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(p.get('title',''))} — 拍摄脚本</title>
<style>
body{{font:16px/1.6 -apple-system,"PingFang SC",sans-serif;max-width:720px;margin:0 auto;
padding:20px;background:#0c0d10;color:#e8eaf0}}
h1{{font-size:1.5rem;margin:0 0 .2rem}}.log{{color:#8c93a3;margin:0 0 1.5rem}}
h2{{font-size:1.05rem;margin:1.6rem 0 .4rem;color:#ff8a5b}}
.gold{{color:#8c93a3;font-size:.85rem;margin:.2rem 0 .8rem}}
.shot{{display:flex;gap:.7rem;align-items:flex-start;background:#15171c;border:1px solid #272b34;
border-radius:10px;padding:.7rem .8rem;margin:.4rem 0;cursor:pointer}}
.shot.must{{border-color:#ff8a5b55}}
.shot input{{margin-top:.35rem;width:18px;height:18px;flex:none}}
.shot b{{color:#5bc8ff;font-size:.8rem;text-transform:uppercase}}
.what{{margin:.2rem 0}}.why{{font-size:.82rem;color:#8c93a3}}
.m{{color:#ff8a5b;font-size:.78rem}}
.box{{background:#1c1f26;border-radius:10px;padding:.8rem 1.2rem;margin-top:1.5rem}}
input:checked + div .what{{text-decoration:line-through;opacity:.5}}
</style>
<h1>{escape(p.get('title',''))}</h1>
<p class="log">{escape(p.get('logline',''))}</p>
{days}
<div class="box"><h2>非拍不可</h2><ul>{must}</ul></div>
<div class="box"><h2>别忘了</h2><ul>{rem}</ul></div>"""
    out = project.out("shootplan.html")
    out.write_text(html, encoding="utf-8")
    log(f"拍摄清单：{out}（用手机打开，拍一条勾一条）")
    return str(out)


def mock_plan(project: Project, brief: str, days: int) -> dict:
    plan = {"title": "拍摄脚本 (mock)", "logline": "离线模拟", "brief": brief, "days_count": days,
            "beats": [{"id": "b1", "title": "出发", "intent": "建立期待"}],
            "days": [{"day": i + 1, "where": f"第 {i+1} 天", "golden_hours": "日出后一小时",
                      "shots": [{"shot_id": f"d{i+1}s{j:02d}", "beat": "出发",
                                 "what": "（离线模拟）一个镜头", "framing": "wide",
                                 "hold": "8-10s", "why": "剪辑需要", "must_have": j == 1}
                                for j in range(1, 4)]} for i in range(days)],
            "must_haves": ["（离线模拟）"], "reminders": ["（离线模拟）"]}
    write_json(project.out("shootplan.json"), plan)
    return plan
