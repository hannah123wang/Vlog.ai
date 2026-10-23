"""拍摄前：AI 根据你要去的地方和想要的感觉，给出一份分镜脚本。

这是整个闭环的前半段——拍之前有脚本，拍完之后 AI 按同一套结构剪，
匹配率比"回来之后对着一堆素材硬凑"高得多。

脚本里每一条都带 shot_id，拍的时候记一下（或者事后在界面里认领），
剪辑阶段 story.py 会优先把它放到对应的幕里。
"""
from __future__ import annotations

import base64
import json
from html import escape

from . import sketch
from .common import Project, log, read_json, write_json

REFS_DIR = "plan_refs"          # 你给镜头附的参考图，存在项目文件夹的这个子目录里

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
- Give every shot a composition sketch: where the main subjects sit in the frame (use the rule of
  thirds; a person looking or walking into the frame has space in front of them), how big they are,
  where the horizon falls, and how the camera moves. Keep it to the 1-3 things that matter.
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
         "must_have": true/false,
         "sketch": {{"subjects": [{{"k": "person | couple | group | face | landmark | building | mountain | tree | vehicle | boat | food | hands | object | road | sign",
                                  "x": 0.0-1.0, "y": 0.0-1.0, "s": 0.0-1.0}}],
                    "horizon": 0.0-1.0 or null,
                    "move": "static | pan-left | pan-right | tilt-up | tilt-down | push-in | pull-out | follow | orbit | reveal",
                    "angle": "eye | low | high | top"}}}}
     ]}}
 ],
 "must_haves": ["the handful of shots the film cannot be cut without"],
 "reminders": ["practical warnings: batteries, verticals for Reels, record 10s of room tone, etc."]
}}
In "sketch": x, y = centre of the subject as a fraction of the frame (0,0 = top-left); s = its height as a
fraction of the frame height; horizon = height of the horizon / skyline from the top (null indoors or for
details). The frame is {aspect}.
Aim for {shots_per_day} shots per day — enough to cut from, few enough to actually shoot."""


def make_plan(project: Project, llm, brief: str, days: int, minutes: float,
              gear: str, lang: str) -> dict:
    shots_per_day = 10 if minutes <= 3 else 16
    log(f"AI 写拍摄脚本：{days} 天，目标成片 {minutes:.0f} 分钟…")
    vertical = bool(project.settings().get("vertical"))
    prompt = PLAN_PROMPT.format(brief=brief, days=days, minutes=minutes,
                                gear=gear or "(not specified — assume a phone)",
                                lang=lang, shots_per_day=shots_per_day,
                                aspect="vertical 9:16" if vertical else "horizontal 16:9")
    # 每个镜头连草图大约 250 tokens；天数多时 8000 不够，会被截断成坏 JSON
    plan = llm.plan(PLAN_SYSTEM, prompt, max_tokens=min(16000, 3000 + days * shots_per_day * 260))
    for d in plan.get("days", []):
        for sh in d.get("shots", []):
            sh["sketch"] = sketch.normalize(sh.get("sketch"), sh.get("framing", ""))
    plan["brief"] = brief
    plan["days_count"] = days
    write_json(project.out("shootplan.json"), plan)
    n = sum(len(d.get("shots", [])) for d in plan.get("days", []))
    log(f"脚本完成：{len(plan.get('beats', []))} 幕、{n} 个镜头 → shootplan.json")
    for d in plan.get("days", []):
        log(f"  Day {d.get('day')} · {d.get('where')} · {len(d.get('shots', []))} 个镜头")
    return plan


def _ref_data_uri(project: Project, name: str) -> str:
    f = project.out(REFS_DIR) / name
    if not f.is_file():
        return ""
    mime = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()


def plan_html(project: Project) -> str:
    """手机上能看的拍摄清单：每条带构图草图和你附的参考图，可以勾掉（勾选记在手机浏览器里）。

    图片都直接嵌在这一个文件里，传到手机上没网也能看。"""
    p = read_json(project.out("shootplan.json"))
    if not p:
        raise SystemExit("还没有拍摄脚本，先运行 plan")
    vertical = bool(project.settings().get("vertical"))
    done = set((read_json(project.out("shootplan_progress.json"), {}) or {}).get("done", []))
    days = ""
    for d in p.get("days", []):
        shots = ""
        for s in d.get("shots", []):
            sid = escape(str(s.get("shot_id", "")))
            how = sketch.describe(s.get("sketch"), s.get("framing", ""))
            refs = "".join(f'<img src="{uri}" alt="参考图" loading="lazy">'
                           for uri in (_ref_data_uri(project, r) for r in s.get("refs", []) or []) if uri)
            shots += (
                f'<label class="shot{" must" if s.get("must_have") else ""}">'
                f'<input type="checkbox" data-id="{sid}"{" checked" if s.get("shot_id") in done else ""}>'
                f'<div class="body"><div class="pics{" v" if vertical else ""}">'
                f'{sketch.sketch_svg(s.get("sketch"), s.get("framing", ""), vertical, s.get("what", ""))}{refs}</div>'
                f'<b>{escape(s.get("framing",""))}</b> · {escape(s.get("hold",""))}'
                f'{" · " + escape(how) if how else ""}'
                f'{" · <span class=m>必拍</span>" if s.get("must_have") else ""}'
                f'<div class="what">{escape(s.get("what",""))}</div>'
                f'<div class="why">{escape(s.get("beat",""))} — {escape(s.get("why",""))}</div>'
                f'</div></label>')
        days += (f'<section><h2>Day {d.get("day")} · {escape(d.get("where",""))}</h2>'
                 f'<p class="gold">☀️ {escape(d.get("golden_hours",""))}</p>{shots}</section>')
    must = "".join(f"<li>{escape(m)}</li>" for m in p.get("must_haves", []))
    rem = "".join(f"<li>{escape(r)}</li>" for r in p.get("reminders", []))
    key = json.dumps("vlogai.plan." + project.root.name)
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
border-radius:10px;padding:.7rem .8rem;margin:.5rem 0;cursor:pointer}}
.shot.must{{border-color:#ff8a5b55}}
.shot input{{margin-top:.35rem;width:20px;height:20px;flex:none}}
.body{{flex:1;min-width:0}}
.pics{{display:flex;gap:.5rem;overflow-x:auto;margin-bottom:.5rem}}
.pics svg,.pics img{{flex:none;width:min(240px,70vw);height:auto;border-radius:6px}}
.pics.v svg,.pics.v img{{width:min(130px,38vw)}}
.pics img{{object-fit:cover;aspect-ratio:16/9}}.pics.v img{{aspect-ratio:9/16}}
.sketch{{color:#cfd3dc;background:#0c0d10}}
.shot b{{color:#5bc8ff;font-size:.8rem;text-transform:uppercase}}
.what{{margin:.2rem 0}}.why{{font-size:.82rem;color:#8c93a3}}
.m{{color:#ff8a5b;font-size:.78rem}}
.box{{background:#1c1f26;border-radius:10px;padding:.8rem 1.2rem;margin-top:1.5rem}}
input:checked + .body{{opacity:.5}} input:checked + .body .what{{text-decoration:line-through}}
</style>
<h1>{escape(p.get('title',''))}</h1>
<p class="log">{escape(p.get('logline',''))}</p>
{days}
<div class="box"><h2>非拍不可</h2><ul>{must}</ul></div>
<div class="box"><h2>别忘了</h2><ul>{rem}</ul></div>
<script>
(function () {{
  var KEY = {key}, saved = {{}};
  try {{ saved = JSON.parse(localStorage.getItem(KEY)) || {{}}; }} catch (e) {{}}
  document.querySelectorAll('input[data-id]').forEach(function (c) {{
    if (c.dataset.id in saved) c.checked = saved[c.dataset.id];
    c.addEventListener('change', function () {{
      saved[c.dataset.id] = c.checked;
      try {{ localStorage.setItem(KEY, JSON.stringify(saved)); }} catch (e) {{}}
    }});
  }});
}}());
</script>"""
    out = project.out("shootplan.html")
    out.write_text(html, encoding="utf-8")
    log(f"拍摄清单：{out}（用手机打开，拍一条勾一条）")
    return str(out)


MOCK_SHOTS = [
    ("wide", "（离线模拟）远景：地标在右三分线，人很小", {"subjects": [{"k": "landmark", "x": .68, "y": .42, "s": .5},
     {"k": "couple", "x": .28, "y": .8, "s": .14}], "horizon": .68, "move": "pan-right", "angle": "eye"}),
    ("selfie", "（离线模拟）两人自拍同框", None),
    ("detail", "（离线模拟）手和食物的特写", {"subjects": [{"k": "food", "x": .5, "y": .6, "s": .45},
     {"k": "hands", "x": .5, "y": .28, "s": .3}], "horizon": None, "move": "push-in", "angle": "top"}),
    ("pov", "（离线模拟）路上的第一人称", None),
]


def mock_plan(project: Project, brief: str, days: int) -> dict:
    plan = {"title": "拍摄脚本 (mock)", "logline": "离线模拟", "brief": brief, "days_count": days,
            "beats": [{"id": "b1", "title": "出发", "intent": "建立期待"},
                      {"id": "b2", "title": "路上", "intent": "节奏和地方感"}],
            "days": [{"day": i + 1, "where": f"第 {i+1} 天", "golden_hours": "日出后一小时",
                      "shots": [{"shot_id": f"d{i+1}s{j + 1:02d}", "beat": "出发" if j < 2 else "路上",
                                 "what": what, "framing": fr, "hold": "8-10s", "why": "剪辑需要",
                                 "must_have": j == 0, "sketch": sketch.normalize(sk, fr)}
                                for j, (fr, what, sk) in enumerate(MOCK_SHOTS)]} for i in range(days)],
            "must_haves": ["（离线模拟）"], "reminders": ["（离线模拟）"]}
    write_json(project.out("shootplan.json"), plan)
    return plan
