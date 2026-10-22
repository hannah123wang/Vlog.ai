"""A one-page storyboard (storyboard.html) to review the AI's cut before rendering."""
from __future__ import annotations

import base64
from html import escape
from pathlib import Path

from .common import Project, log, read_json


def _thumb(path: str) -> str:
    try:
        return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()
    except OSError:
        return ""


def storyboard(project: Project) -> Path:
    story = read_json(project.story_json)
    shots = {s["id"]: s for s in read_json(project.shots_json, [])}
    rows, last_ch, t0 = [], None, 0.0
    for i, t in enumerate(story["timeline"], 1):
        s = shots.get(t["shot_id"], {})
        if t.get("chapter") != last_ch:
            rows.append(f'<h2>{escape(t.get("chapter") or "")}</h2>')
            last_ch = t.get("chapter")
        frames = s.get("frames", [])
        img = _thumb(frames[len(frames) // 2]["path"]) if frames else ""
        lg = read_json(project.logs_dir / f"{t['shot_id']}.json", {}) or {}
        dur = t["out"] - t["in"]
        rows.append(
            f'<div class="row"><img src="{img}"><div>'
            f'<div class="meta">#{i} · {int(t0//60)}:{t0%60:04.1f} · {dur:.1f}s · {escape(t["shot_id"])}'
            f' · {escape(t.get("role",""))}{" · 🔊原声" if t.get("keep_audio") else ""}</div>'
            f'<div class="sum">{escape(lg.get("summary",""))}</div>'
            f'<div class="why">✂️ {escape(t.get("why",""))}</div>'
            + (f'<div class="txt">字幕：{escape(t["text"])}</div>' if t.get("text") else "")
            + "</div></div>")
        t0 += dur
    html = f"""<!doctype html><meta charset="utf-8"><title>{escape(story.get('title',''))}</title>
<style>body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#222}}
h1{{margin-bottom:4px}}.log{{color:#666;margin-bottom:24px}}h2{{margin-top:32px;border-bottom:1px solid #ddd}}
.row{{display:flex;gap:14px;margin:10px 0}}img{{width:200px;border-radius:6px;flex:none}}
.meta{{font-size:12px;color:#888}}.sum{{margin:4px 0}}.why{{font-size:13px;color:#555}}.txt{{font-size:13px;color:#b35c00}}</style>
<h1>{escape(story.get('title',''))}</h1><div class="log">{escape(story.get('logline',''))} · 共 {t0/60:.1f} 分钟 · {len(story['timeline'])} 个镜头</div>
{''.join(rows)}"""
    out = project.out("storyboard.html")
    out.write_text(html, encoding="utf-8")
    log(f"故事板：{out}（用浏览器打开先看看 AI 的剪辑思路）")
    return out
