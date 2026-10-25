"""vlogai studio —— 本地桌面应用的后端。只用 Python 标准库，没有任何额外依赖。

  python -m vlogai.studio          # 然后浏览器打开 http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import string
import subprocess
import sys
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__, applog, config
from ..applog import log
from .. import describe as describe_mod
from .. import export_xml, frames, lab, outline, probe, render, shootplan, sketch, story, storyboard, transcribe
import base64
import shutil
import threading
import time
from ..protect import protected
from ..common import VIDEO_EXTS, Project, read_json, require_ffmpeg, write_json
from ..llm import get_llm, key_hint, test_key
from .jobs import JobRunner

WEB = Path(__file__).parent / "web"
PROJECTS = Path(config.get("projects_dir")).expanduser().resolve()   # 启动时按设置定下来
LANGS = {"zh": "Simplified Chinese", "en": "English"}
runner = JobRunner()


# ---------------------------------------------------------------- helpers
def safe_name(name: str) -> str:
    """项目名只留字母数字（含中文）、空格、- _ .，且不能以点开头（挡住 .. 和 .env 这类）。"""
    safe = re.sub(r"[^\w\-. ]", "", name or "").strip().lstrip(".").strip()
    return safe


def project_dir(pid: str) -> Project:
    safe = safe_name(pid)
    if not safe or (PROJECTS / safe).resolve().parent != PROJECTS:
        raise ValueError("项目名无效")
    return Project(PROJECTS / safe)


def flags(p: Project) -> dict:
    """界面里对镜头的标记：must 必用（一定保护）、exclude 排除（AI 看不到）、unprotect 不要自动保护。"""
    f = read_json(p.root / "flags.json", {}) or {}
    return {k: list(f.get(k, [])) for k in ("must", "exclude", "unprotect")}


def media_url(pid: str, p: Project, path: str) -> str:
    return f"/media/{pid}/" + str(Path(path).relative_to(p.root)).replace(os.sep, "/")


# ---------------------------------------------------------------- 从浏览器上传素材（手机相册 / 别的电脑）
# 浏览器拿不到本机文件路径，只能把文件内容传过来。按块传（每块几 MB），断了可以从断点续传。
# 传到哪：项目已经有素材文件夹就放进去（这样一次扫描能看到全部素材），否则放项目里的 footage/。
UPLOAD_CHUNK_MAX = 64 * 1024 * 1024
_upload_lock = threading.Lock()


def upload_target(p: Project) -> Path:
    cur = p.settings().get("footage") or ""
    if cur and Path(cur).is_dir():
        return Path(cur)
    return p.root / "footage"


def upload_filename(name: str) -> str:
    base = Path(str(name).replace("\\", "/")).name
    stem, ext = os.path.splitext(base)
    if ext.lower() not in VIDEO_EXTS:
        raise ValueError(f"不是支持的视频格式：{base}")
    stem = re.sub(r"[^\w\-. ]", "_", stem).strip().lstrip(".") or "video"
    return stem[:120] + ext


def handle_upload(pid: str, q: dict, data: bytes) -> dict:
    """q：name 文件名、total 总字节数、offset 这一块从哪开始、mtime 毫秒、probe=1 只问进度。"""
    p = project_dir(pid)
    arg = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
    total = int(arg("total", "0"))
    if total <= 0:
        raise ValueError("文件大小不对")
    folder = upload_target(p)
    name = upload_filename(arg("name"))
    with _upload_lock:
        if arg("probe") == "1":
            if p.root.is_dir() and not any(p.root.iterdir()):
                p.root.rmdir()          # 只是问进度，不留空项目文件夹（Project() 会自动建目录）
            # 同名同大小：当成已经传过（重复选同一批视频不会传两遍）；同名不同大小：换个名字
            final = folder / name
            n = 1
            while final.exists() and final.stat().st_size != total:
                stem, ext = os.path.splitext(name)
                final = folder / f"{stem}_{n}{ext}"
                n += 1
            if final.exists():
                return {"name": final.name, "have": total, "done": True, "footage": str(folder)}
            part = final.with_name(final.name + ".part")
            have = part.stat().st_size if part.exists() else 0
            disk = next(d for d in (folder, *folder.parents) if d.exists())
            if not have and shutil.disk_usage(disk).free < total + 512 * 1024 * 1024:
                raise ValueError(f"电脑硬盘空间不够了：{folder}")
            return {"name": final.name, "have": have, "done": False, "footage": str(folder)}

        final = folder / name
        part = final.with_name(final.name + ".part")
        offset = int(arg("offset", "0"))
        have = part.stat().st_size if part.exists() else 0
        if final.exists():
            return {"name": name, "have": total, "done": True, "footage": str(folder)}
        if offset != have:              # 断点对不上：告诉浏览器从哪继续
            return {"name": name, "have": have, "done": False, "footage": str(folder), "resync": True}
        folder.mkdir(parents=True, exist_ok=True)
        if not p.settings().get("created"):
            p.save_settings(created=date.today().isoformat())
        with part.open("ab") as f:
            f.write(data)
        have += len(data)
        if have > total:
            part.unlink(missing_ok=True)
            raise ValueError("收到的数据比文件还大，已丢弃，请重新上传")
        if have == total:
            part.replace(final)
            try:
                mt = int(arg("mtime", "0")) / 1000
                if mt > 0:
                    os.utime(final, (mt, mt))   # 保留拍摄 / 修改时间：视频里没有拍摄时间时按它分天
            except (ValueError, OSError):
                pass
            log.info(f"上传完成：{pid}/{final.name}（{total / 1048576:.1f} MB）")
        return {"name": name, "have": have, "done": have == total, "footage": str(folder)}


def mtime(path: Path) -> float:
    return round(path.stat().st_mtime, 3) if path.exists() else 0


def list_versions(p: Project) -> list[dict]:
    out = []
    if p.versions_dir.exists():
        for f in p.versions_dir.glob("story_v*.json"):
            m = re.fullmatch(r"story_v(\d+)\.json", f.name)
            if not m:
                continue
            v = read_json(f, {}) or {}
            out.append({"n": int(m.group(1)), "note": v.get("_note", ""), "created": v.get("_created", ""),
                        "title": v.get("title", ""), "seconds": v.get("total_seconds", 0),
                        "shots": len(v.get("timeline", []))})
    return sorted(out, key=lambda v: -v["n"])


def project_summary(pid: str) -> dict:
    p = project_dir(pid)
    clips = read_json(p.clips_json, []) or []
    shots = read_json(p.shots_json, []) or []
    logs = len(list(p.logs_dir.glob("*.json"))) if p.logs_dir.exists() else 0
    st = read_json(p.story_json, {}) or {}
    ol = read_json(p.out("outline.json"), {}) or {}
    s = p.settings()
    return {
        "id": pid, "name": pid, "footage": s.get("footage", ""),
        "settings": s,
        "clips": len(clips), "shots": len(shots), "logged": logs,
        "duration_min": round(sum(c["duration"] for c in clips) / 60, 1),
        "days": len({c["created"][:10] for c in clips}) if clips else 0,
        "unusable": sum(1 for s_ in shots if s_.get("quality", 1) < float(config.get("bad_quality"))),
        "has_outline": bool(ol.get("beats")),
        "has_plan": p.out("shootplan.json").exists(),
        "beats": len(ol.get("beats", [])),
        "has_story": bool(st.get("timeline")),
        "story_title": st.get("title", ""),
        "story_seconds": st.get("total_seconds", 0),
        "story_shots": len(st.get("timeline", [])),
        "has_preview": p.out("preview.mp4").exists(),
        "has_xml": p.out("timeline.xml").exists(),
        "has_storyboard": p.out("storyboard.html").exists(),
        "path": str(p.root),
        "mtimes": {k: mtime(p.out(k)) for k in ("story.json", "preview.mp4", "timeline.xml")},
        "versions": len(list(p.versions_dir.glob("story_v*.json"))) if p.versions_dir.exists() else 0,
        "flags": {k: len(v) for k, v in flags(p).items()},
    }


def list_projects() -> list[dict]:
    if not PROJECTS.exists():
        return []
    out = []
    for d in sorted(PROJECTS.iterdir(), key=lambda x: -x.stat().st_mtime):
        if d.is_dir() and any((d / f).exists() for f in ("clips.json", "settings.json", "shootplan.json")):
            try:
                out.append(project_summary(d.name))
            except Exception:  # noqa: BLE001
                pass
    return out


def browse(path: str) -> dict:
    """给界面用的文件夹浏览器——浏览器本身拿不到真实路径。"""
    base = Path(path).expanduser() if path else Path.home()
    base = base.resolve()
    if not base.is_dir():
        base = Path.home()
    try:
        entries = sorted(base.iterdir())
    except PermissionError as e:
        raise ValueError(f"没有权限打开：{base}") from e
    dirs = []
    for d in entries:
        try:
            if d.is_dir() and not d.name.startswith("."):
                n = sum(1 for f in d.iterdir()
                        if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."))
                dirs.append({"name": d.name, "path": str(d), "videos": n})
        except PermissionError:
            continue
    here = sum(1 for f in entries
               if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."))
    return {"path": str(base), "parent": str(base.parent) if base.parent != base else None,
            "dirs": dirs[:400], "videos_here": here}


def shots_payload(pid: str) -> list[dict]:
    p = project_dir(pid)
    clips = {c["id"]: c for c in read_json(p.clips_json, []) or []}
    fl = flags(p)
    must, exc = set(fl["must"]), set(fl["exclude"])
    shots_all = read_json(p.shots_json, []) or []
    logs = {s["id"]: read_json(p.logs_dir / f"{s['id']}.json", {}) or {} for s in shots_all}
    target = float(p.settings().get("minutes") or 3) * 60
    # 和剪辑时用同样的规则和上限；quiet：网页轮询时不要往日志里刷屏
    prot = protected(p, [{**s, "log": logs[s["id"]]} for s in shots_all if logs[s["id"]]],
                     target, quiet=True) if shots_all else {}
    out = []
    for s in shots_all:
        lg = logs[s["id"]]
        thumb = ""
        if s.get("frames"):
            f = s["frames"][len(s["frames"]) // 2]["path"]
            thumb = f"/media/{pid}/" + str(Path(f).relative_to(p.root)).replace(os.sep, "/")
        out.append({"id": s["id"], "day": s["day"], "time": s["created"][11:16],
                    "duration": s["duration"], "quality": s["quality"], "thumb": thumb,
                    "file": Path(s["src"]).name,
                    "summary": lg.get("summary", ""), "place": lg.get("place", ""),
                    "shot_type": lg.get("shot_type", ""), "highlight": lg.get("highlight"),
                    "mood": lg.get("mood", ""), "people": lg.get("people", []),
                    "speech": (lg.get("speech") or "")[:120],
                    # 以下是新界面用的字段
                    "frames": [media_url(pid, p, f["path"]) for f in s.get("frames", [])],
                    "created": s["created"], "gps": s.get("gps"), "has_audio": s.get("has_audio"),
                    "vertical": c.get("height", 0) > c.get("width", 0) if (c := clips.get(s["clip_id"])) else False,
                    "fps": (clips.get(s["clip_id"]) or {}).get("fps"),
                    "size": [(clips.get(s["clip_id"]) or {}).get("width"), (clips.get(s["clip_id"]) or {}).get("height")],
                    "tags": lg.get("tags", []), "activity": lg.get("activity", ""),
                    "best_moment": lg.get("best_moment"), "logged": bool(lg),
                    "must": s["id"] in must, "excluded": s["id"] in exc,
                    "unprotected": s["id"] in fl["unprotect"],
                    "protected": prot.get(s["id"], ""),
                    "faces_clear": lg.get("faces_clear"), "subject_present": lg.get("subject_present"),
                    "people_together": lg.get("people_together"), "scenery_value": lg.get("scenery_value")})
    for s in lab.lab_shots(p):          # 镜头工坊的片段：时间线要用，素材页不显示（lab 字段）
        thumb = media_url(pid, p, s["frames"][0]["path"]) if s["frames"] else ""
        out.append({"id": s["id"], "day": s["day"], "time": s["created"][11:16], "duration": s["duration"],
                    "quality": 1.0, "thumb": thumb, "file": Path(s["src"]).name, "summary": s["label"],
                    "frames": [thumb] if thumb else [], "created": s["created"], "has_audio": s["has_audio"],
                    "lab": s["lab"], "label": s["label"], "tags": [], "logged": True,
                    "must": False, "excluded": False, "unprotected": False, "protected": ""})
    return out


def plan_payload(p: Project) -> dict:
    plan = read_json(p.out("shootplan.json"), {}) or {}
    if plan:
        prog = read_json(p.out("shootplan_progress.json"), {}) or {}
        plan["done"] = list(prog.get("done", []))
        plan["has_html"] = p.out("shootplan.html").exists()
        vertical = bool(p.settings().get("vertical"))
        plan["vertical"] = vertical
        pid = p.root.name
        for d in plan.get("days", []):
            for sh in d.get("shots", []):
                sh["sketch"] = sketch.normalize(sh.get("sketch"), sh.get("framing", ""))
                sh["sketch_svg"] = sketch.sketch_svg(sh["sketch"], sh.get("framing", ""), vertical, sh.get("what", ""))
                sh["how"] = sketch.describe(sh["sketch"], sh.get("framing", ""))
                sh["refs"] = [r for r in sh.get("refs", []) or [] if (p.out(shootplan.REFS_DIR) / r).is_file()]
                sh["ref_urls"] = [f"/media/{urllib.parse.quote(pid)}/{shootplan.REFS_DIR}/{urllib.parse.quote(r)}"
                                  for r in sh["refs"]]
    return plan


REF_MAX_BYTES = 4 * 1024 * 1024       # 网页会先把图压到 1600px 左右，正常只有几百 KB
REF_PER_SHOT = 3


def _find_shot(plan: dict, shot_id: str) -> dict:
    for d in plan.get("days", []):
        for sh in d.get("shots", []):
            if sh.get("shot_id") == shot_id:
                return sh
    raise ValueError("拍摄脚本里没有这个镜头，先保存一下脚本再加参考图")


def add_plan_ref(p: Project, body: dict) -> dict:
    """给拍摄脚本的某个镜头附一张参考图（网页传来 data:image/...;base64,...）。"""
    plan = read_json(p.out("shootplan.json"), {}) or {}
    if not plan:
        raise ValueError("还没有拍摄脚本")
    sh = _find_shot(plan, str(body.get("shot_id", "")))
    refs = [r for r in sh.get("refs", []) or [] if (p.out(shootplan.REFS_DIR) / r).is_file()]
    if len(refs) >= REF_PER_SHOT:
        raise ValueError(f"每个镜头最多 {REF_PER_SHOT} 张参考图")
    m = re.fullmatch(r"data:image/(jpeg|jpg|png|webp);base64,(.+)", str(body.get("data", "")), re.S)
    if not m:
        raise ValueError("只支持 JPG / PNG 图片")
    raw = base64.b64decode(m.group(2), validate=False)
    if len(raw) > REF_MAX_BYTES:
        raise ValueError("图片太大了（超过 4MB）")
    if raw[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    else:
        raise ValueError("只支持 JPG / PNG 图片")
    folder = p.out(shootplan.REFS_DIR)
    folder.mkdir(exist_ok=True)
    name = f"{safe_name(sh['shot_id']) or 'shot'}_{int(time.time() * 1000) % 10**9}{ext}"
    (folder / name).write_bytes(raw)
    sh["refs"] = refs + [name]
    write_json(p.out("shootplan.json"), plan)
    shootplan.plan_html(p)
    log.info(f"拍摄脚本 {sh['shot_id']} 加了参考图 {name}（{len(raw) // 1024} KB）")
    return plan_payload(p)


def del_plan_ref(p: Project, body: dict) -> dict:
    plan = read_json(p.out("shootplan.json"), {}) or {}
    sh = _find_shot(plan, str(body.get("shot_id", "")))
    name = str(body.get("name", ""))
    if name in (sh.get("refs") or []):
        sh["refs"] = [r for r in sh["refs"] if r != name]
        f = (p.out(shootplan.REFS_DIR) / name).resolve()
        if f.parent == p.out(shootplan.REFS_DIR).resolve():
            f.unlink(missing_ok=True)
        write_json(p.out("shootplan.json"), plan)
        shootplan.plan_html(p)
    return plan_payload(p)


def save_plan(p: Project, body: dict) -> dict:
    """界面里改过的拍摄脚本：改文字、删镜头、加镜头、标必拍。只接受认识的字段。"""
    plan = read_json(p.out("shootplan.json"), {}) or {}
    if not plan:
        raise ValueError("还没有拍摄脚本")
    days = []
    for d in body.get("days", []):
        shots = []
        for s in d.get("shots", []):
            if not str(s.get("what", "")).strip():
                continue
            row = {k: (bool(s.get(k)) if k == "must_have" else str(s.get(k, "") or "").strip())
                   for k in ("shot_id", "beat", "what", "framing", "hold", "why", "must_have")}
            row["sketch"] = sketch.normalize(s.get("sketch"), row["framing"])
            row["refs"] = [r for r in s.get("refs", []) or []
                           if isinstance(r, str) and "/" not in r and "\\" not in r
                           and (p.out(shootplan.REFS_DIR) / r).is_file()]
            shots.append(row)
        days.append({"day": int(d.get("day") or len(days) + 1), "where": str(d.get("where", "")).strip(),
                     "golden_hours": str(d.get("golden_hours", "")).strip(), "shots": shots})
    plan["days"] = days
    for k in ("title", "logline"):
        if k in body:
            plan[k] = str(body[k])
    write_json(p.out("shootplan.json"), plan)
    shootplan.plan_html(p)
    return plan_payload(p)


def story_payload(pid: str) -> dict:
    p = project_dir(pid)
    st = read_json(p.story_json, {}) or {}
    shots = {s["id"]: s for s in (read_json(p.shots_json, []) or []) + lab.lab_shots(p)}
    items = []
    t = 0.0
    for i, item in enumerate(st.get("timeline", [])):
        s = shots.get(item["shot_id"], {})
        thumb = ""
        if s.get("frames"):
            f = s["frames"][len(s["frames"]) // 2]["path"]
            thumb = f"/media/{pid}/" + str(Path(f).relative_to(p.root)).replace(os.sep, "/")
        lg = read_json(p.logs_dir / f"{item['shot_id']}.json", {}) or {}
        dur = item["out"] - item["in"]
        items.append({**item, "i": i, "at": round(t, 1), "dur": round(dur, 2), "thumb": thumb,
                      "summary": lg.get("summary", "") or s.get("label", ""), "shot_dur": s.get("duration", 0),
                      "lab": s.get("lab", ""),
                      "quality": s.get("quality"), "highlight": lg.get("highlight")})
        t += dur
    return {"title": st.get("title", ""), "logline": st.get("logline", ""),
            "chapters": st.get("chapters", []), "total_seconds": round(t, 1), "timeline": items}


# ---------------------------------------------------------------- jobs
def job_scan(pid: str, footage: str):
    p = project_dir(pid)
    p.save_settings(footage=str(Path(footage).expanduser().resolve()))
    probe.scan(footage, p)


def job_analyze(pid: str, mock: bool, lang: str, whisper: bool, workers: int = 4, subject: str = ""):
    p = project_dir(pid)
    if subject:
        p.save_settings(subject=subject)
    frames.extract(p, workers=workers)
    if whisper:
        transcribe.transcribe(p, config.get("whisper_model"))
    describe_mod.describe_all(p, get_llm(mock), lang=LANGS[lang], workers=workers, subject=subject)


def job_outline(pid: str, idea: str, minutes: float, mock: bool, lang: str):
    p = project_dir(pid)
    p.save_settings(brief=idea, minutes=minutes, lang=lang)
    if mock:
        outline.mock_beats(p, idea, minutes * 60)
    else:
        outline.propose_beats(p, get_llm(False), idea, minutes * 60, LANGS[lang])


def job_story(pid: str, beats: list, minutes: float, mock: bool, lang: str):
    p = project_dir(pid)
    llm = get_llm(mock)
    idea = p.settings().get("brief", "")
    if beats:                                   # 先存下用户改过的分幕（离线模式也存）
        ol = read_json(p.out("outline.json"), {}) or {}
        write_json(p.out("outline.json"), {**ol, "beats": beats})
    if mock or not beats:
        story.build_story(p, llm, idea, minutes * 60, LANGS[lang])
    else:
        outline.story_from_beats(p, llm, beats, minutes * 60, LANGS[lang], idea)
    storyboard.storyboard(p)


def job_revise(pid: str, instruction: str, minutes: float, mock: bool, lang: str):
    p = project_dir(pid)
    story.revise_story(p, get_llm(mock), instruction, minutes * 60, LANGS[lang])
    storyboard.storyboard(p)


def job_plan(pid: str, brief: str, days: int, minutes: float, gear: str, mock: bool, lang: str):
    p = project_dir(pid)
    p.save_settings(plan_brief=brief, plan_days=days, plan_gear=gear, minutes=minutes)
    p.out("shootplan_progress.json").unlink(missing_ok=True)      # 新脚本，勾选记录清零
    for f in (p.out(shootplan.REFS_DIR).glob("*") if p.out(shootplan.REFS_DIR).is_dir() else []):
        f.unlink(missing_ok=True)                                  # 参考图跟着旧镜头编号，一起清掉
    if mock:
        shootplan.mock_plan(p, brief, days)
    else:
        shootplan.make_plan(p, get_llm(False), brief, days, minutes, gear, LANGS[lang])
    shootplan.plan_html(p)


def job_render(pid: str, music: str | None, vertical: bool, hd: bool):
    p = project_dir(pid)
    p.save_settings(music=music or None, vertical=vertical)
    cfg = config.load()
    render.render(p, music=music or None, vertical=vertical, hd=hd, font=cfg["font"] or None,
                  snap=cfg["beat_snap"], music_volume=float(cfg["music_volume"]),
                  captions=cfg["burn_captions"], duck=cfg["duck_music"], workers=int(cfg["render_workers"]))
    export_xml.export_xml(p, music=music or None)


def lab_payload(p: Project) -> dict:
    pid = p.root.name
    ol = read_json(p.out("outline.json"), {}) or {}
    items = []
    for a in lab.assets(p):
        items.append({k: v for k, v in a.items() if k != "path"} | {
            "url": f"/media/{urllib.parse.quote(pid)}/{a['file']}",
            "thumb_url": f"/media/{urllib.parse.quote(pid)}/{a['thumb']}" if a.get("thumb") else ""})
    used = {t["shot_id"] for t in (read_json(p.story_json, {}) or {}).get("timeline", [])}
    for a in items:
        a["in_timeline"] = a["id"] in used
    OW, OH = lab.out_size(p)
    return {"assets": items[::-1], "gaps": ol.get("missing", []), "provider": config.get("lab_provider"),
            "model": config.get("lab_model"), "price_per_sec": config.get("lab_price_per_sec"),
            "fal_key": lab.key_hint(), "out": [OW, OH], "has_story": bool(used)}


def job_lab(pid: str, kind: str, b: dict, mock: bool):
    p = project_dir(pid)
    if kind == "crop":
        lab.crop(p, b["shot_id"], b.get("in"), b.get("out"), b.get("keys"), b.get("label", ""),
                 box=b.get("box"), motion=b.get("motion", "static"))
    elif kind == "slowmo":
        lab.slowmo(p, b["shot_id"], b.get("in"), b.get("out"), float(b.get("speed") or 0.5),
                   b.get("method", "auto"), b.get("label", ""), quality=b.get("quality", "fine"))
    else:
        lab.fill(p, b["shot_id"], float(b.get("t") or 0), b.get("prompt", ""), int(b.get("seconds") or 5),
                 b.get("gap", ""), b.get("label", ""), mock=mock)
    if b.get("insert_at") is not None and (read_json(p.story_json, {}) or {}).get("timeline"):
        newest = lab.assets(p)[-1]["id"]
        lab.insert(p, newest, int(b["insert_at"]))
        storyboard.storyboard(p)
        print(f"[vlogai] 已放进时间线第 {int(b['insert_at']) + 1} 个位置")


def job_export(pid: str):
    p = project_dir(pid)
    s = p.settings()
    render.final_timeline(p, s.get("music"), snap=config.get("beat_snap"))
    export_xml.export_xml(p, music=s.get("music"))


def job_recheck(pid: str):
    """手改 story.json 之后：重新校验，更新故事板和 XML。"""
    p = project_dir(pid)
    story.recheck(p)
    storyboard.storyboard(p)
    s = p.settings()
    render.final_timeline(p, s.get("music") or None, snap=config.get("beat_snap"))
    export_xml.export_xml(p, music=s.get("music") or None)
    print("[vlogai] story.json 校验完成，故事板和 XML 已更新")


def save_timeline(pid: str, items: list, note: str = "手动编辑") -> dict:
    """用界面里的时间线替换 story.json（顺序、删除、入出点、字幕、原声、章节），并存成新版本。
    每段用 i（它在当前 story.json 里的位置）认领原来的数据；旧客户端没有 i 时按 (shot_id, in) 匹配。"""
    p = project_dir(pid)
    st = read_json(p.story_json, {}) or {}
    old = st.get("timeline", [])
    new = []
    for x in items:
        i, base = x.get("i"), None
        if isinstance(i, int) and 0 <= i < len(old) and old[i]["shot_id"] == x.get("shot_id"):
            base = old[i]
        else:
            base = next((t for t in old if t["shot_id"] == x.get("shot_id")
                         and round(t["in"], 2) == round(float(x.get("in", -1)), 2)), None)
        item = dict(base or {"shot_id": x.get("shot_id")})
        item["in"], item["out"] = float(x["in"]), float(x["out"])
        for k in ("text", "keep_audio", "chapter"):
            if k in x:
                item[k] = x[k]
        new.append(item)
    plan = {k: st[k] for k in ("title", "logline", "chapters") if k in st}
    plan["timeline"] = new
    plan = story._validate(plan, read_json(p.shots_json, []) or [], lab.lab_shots(p))
    story._save(p, plan, note)
    storyboard.storyboard(p)
    return story_payload(pid)


def restore_version(pid: str, n: int) -> dict:
    p = project_dir(pid)
    v = read_json(p.versions_dir / f"story_v{int(n)}.json")
    if not v:
        raise ValueError(f"没有 v{n}")
    plan = {k: v[k] for k in ("title", "logline", "chapters", "timeline") if k in v}
    plan = story._validate(plan, read_json(p.shots_json, []) or [], lab.lab_shots(p))
    story._save(p, plan, f"恢复 v{n}")
    storyboard.storyboard(p)
    return story_payload(pid)


def settings_payload() -> dict:
    from importlib.util import find_spec
    return {"settings": config.load(), "defaults": config.DEFAULTS, "key": key_hint(),
            "settings_file": str(config.path()), "env_file": str(Path.cwd() / ".env"),
            "projects_path": str(PROJECTS), "detected_font": render.find_font(None) or "",
            "has_whisper": find_spec("faster_whisper") is not None,
            "running_port": RUNNING.get("port"), "fal_key": lab.key_hint(),
            # 每项的取值范围，网页保存前先检查，出错时能说出是哪一项
            "rules": {k: ([r[1], r[2]] if isinstance(r, tuple) else sorted(r, key=str))
                      for k, r in config._RULES.items()}}


def _open_path(path: Path):
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


RUNNING: dict = {}


# ---------------------------------------------------------------- 安全：谁可以调这个后端
# 后端在本机跑，浏览器里打开的任何网页都能向 127.0.0.1 发请求。以前对所有来源都放行（CORS *），
# 网页可以读到 /media/../.env 里的 API Key，也能偷偷改设置、花你的 API 额度。
# 现在只放行：studio 自己的页面、本机开发服务器、Lovable，以及设置里额外填写的网址。
DEFAULT_ORIGINS = [r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?",
                   r"https://[\w.-]+\.lovable\.app", r"https://[\w.-]+\.lovableproject\.com",
                   r"https://(www\.)?lovable\.dev",
                   r"https://[\w-]+\.[\w-]+\.ts\.net"]          # Tailscale Serve：手机远程访问（只有自己的 tailnet 设备能连）
TAILSCALE_HOST = re.compile(r"[\w-]+\.[\w-]+\.ts\.net")


def origin_allowed(origin: str) -> bool:
    if not origin:
        return True                      # 浏览器直接打开页面、curl 等没有 Origin
    extra = [o.strip().rstrip("/") for o in str(config.get("allowed_origins") or "").split(",") if o.strip()]
    if origin.rstrip("/") in extra:
        return True
    return any(re.fullmatch(p, origin) for p in DEFAULT_ORIGINS)


def host_allowed(host: str) -> bool:
    """挡住 DNS rebinding：请求必须是发给 127.0.0.1 / localhost 的。"""
    h = (host or "").rsplit(":", 1)[0] if not (host or "").startswith("[") else host.split("]")[0] + "]"
    return h in ("127.0.0.1", "localhost", "[::1]", "") or bool(TAILSCALE_HOST.fullmatch(h.lower()))


# ---------------------------------------------------------------- 远程访问（tailscale funnel 公开给别人用）
# 本机：直接在这台电脑上打开（127.0.0.1，没有经过任何转发）→ 什么都能做，不用密码。
# 远程：经过 tailscale / cloudflared 等转发进来的请求 → 要先输入“远程访问密码”，
#       而且碰不到这台电脑本身：不能浏览电脑里的文件夹、不能看/改设置和 API Key、不能看日志、不能打开文件夹。
_FORWARD_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "Cf-Connecting-Ip", "Tailscale-Funnel-Request")
AUTH_COOKIE = "vlogai_auth"
REMOTE_BLOCKED_GET = {"/api/ls", "/api/logs"}
REMOTE_BLOCKED_POST = {"/api/settings", "/api/settings/test", "/api/logs/open", "/api/settings/open_projects"}
REMOTE_BLOCKED_ACTIONS = {"open_folder"}
_login_fails: dict = {}          # ip -> [失败时间…]，挡住暴力猜密码
_secret_cache: list = []


def _secret() -> bytes:
    """本机随机密钥（存在 .vlogai_secret），用来签登录 cookie。改密码后旧 cookie 自动失效。"""
    if not _secret_cache:
        f = Path.cwd() / ".vlogai_secret"
        try:
            s = f.read_text(encoding="utf-8").strip()
        except OSError:
            s = ""
        if len(s) < 32:
            s = base64.urlsafe_b64encode(os.urandom(32)).decode()
            try:
                f.write_text(s, encoding="utf-8")
            except OSError:
                pass
        _secret_cache.append(s.encode())
    return _secret_cache[0]


def auth_token(password: str) -> str:
    import hashlib
    import hmac
    return hmac.new(_secret(), ("vlogai-remote:" + password).encode(), hashlib.sha256).hexdigest()


def remote_password() -> str:
    return str(config.get("remote_password") or "")


def inside_projects(path: str) -> bool:
    try:
        f = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return f == PROJECTS or PROJECTS in f.parents


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>vlogai</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#14110f;color:#f3ede6;
font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif;padding:24px}
.card{width:100%;max-width:380px;background:#1e1a17;border:1px solid #3a332d;border-radius:16px;padding:28px}
h1{margin:0 0 4px;font-size:24px}p{margin:0 0 20px;color:#b3a89c;font-size:14px}
input{width:100%;font-size:18px;padding:14px;border-radius:10px;border:1px solid #4a4038;background:#14110f;color:inherit;margin-bottom:12px}
button{width:100%;font-size:17px;font-weight:700;padding:14px;border:0;border-radius:10px;background:#e8a24a;color:#1a1208}
button:disabled{opacity:.6}.err{color:#ff8a7a;font-size:14px;min-height:22px;margin:4px 0 0}
</style></head><body><form class="card" id="f">
<h1>vlogai</h1>__BODY__
</form><script>
const f=document.getElementById('f');
f&&f.addEventListener('submit',async(e)=>{e.preventDefault();const b=f.querySelector('button'),m=document.getElementById('m');
if(!b)return;b.disabled=true;m.textContent='';
try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({password:document.getElementById('p').value})});const d=await r.json().catch(()=>({}));
if(r.ok&&d.ok){location.replace('/');return;}m.textContent=d.error||('出错了 ('+r.status+')');}
catch(err){m.textContent='连不上，检查一下网络 / Can\\'t connect';}b.disabled=false;});
</script></body></html>"""
LOGIN_FORM = """<p>请输入访问密码 · Enter the access password</p>
<input id="p" type="password" autocomplete="current-password" autofocus placeholder="密码 / Password">
<button type="submit">进入 · Enter</button><div class="err" id="m"></div>"""
LOGIN_CLOSED = """<p>这台电脑还没有开放远程访问。<br>请让主人在 studio 的“设置 → 远程访问密码”里设一个密码。</p>
<p>Remote access isn't enabled yet. Ask the owner to set a remote access password in studio settings.</p>"""


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "vlogai-studio"
    remote = True                        # 没经过 _guard 判断时按“远程”处理（更安全）

    def _guard(self) -> bool:
        """不允许的来源直接 403。返回 True 表示可以继续。"""
        origin = self.headers.get("Origin", "")
        if not host_allowed(self.headers.get("Host", "")):
            log.warning(f"拒绝请求：Host 不是本机（{self.headers.get('Host')}）")
            self.json({"error": "forbidden host"}, 403)
            return False
        if not origin_allowed(origin):
            log.warning(f"拒绝来自 {origin} 的请求：{self.command} {self.path.split('?')[0]}"
                        "（需要的话在设置 → 性能与高级 → 允许访问的网页里添加）")
            self.json({"error": f"origin not allowed: {origin}"}, 403)
            return False
        self.remote = self._is_remote()
        if not self.remote:
            return True
        return self._remote_guard()

    # -- 远程访问
    def _is_remote(self) -> bool:
        if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        if any(self.headers.get(h) for h in _FORWARD_HEADERS):
            return True                  # 经过 tailscale serve/funnel、cloudflared 转发
        h = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        return h not in ("127.0.0.1", "localhost", "[::1]", "")

    def _client_ip(self) -> str:
        fwd = self.headers.get("Cf-Connecting-Ip") or (self.headers.get("X-Forwarded-For") or "").split(",")[0]
        return fwd.strip() or self.client_address[0]

    def _authed(self) -> bool:
        pw = remote_password()
        if not pw:
            return False
        import hmac
        from http.cookies import SimpleCookie
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
        except Exception:  # noqa: BLE001
            return False
        got = c[AUTH_COOKIE].value if AUTH_COOKIE in c else ""
        return bool(got) and hmac.compare_digest(got, auth_token(pw))

    def _login_page(self):
        body = LOGIN_HTML.replace("__BODY__", LOGIN_FORM if remote_password() else LOGIN_CLOSED)
        self._send(200, body.encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    def _do_login(self):
        import hmac
        ip = self._client_ip()
        now = time.time()
        fails = [t for t in _login_fails.get(ip, []) if now - t < 900]
        _login_fails[ip] = fails
        if len(fails) >= 8:
            return self.json({"error": "密码错太多次了，15 分钟后再试 / Too many attempts, try again in 15 minutes"}, 429)
        pw = remote_password()
        if not pw:
            return self.json({"error": "还没有开放远程访问 / Remote access is not enabled"}, 403)
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            got = str(json.loads(self.rfile.read(n) or b"{}").get("password", ""))
        except (ValueError, AttributeError):
            got = ""
        if not hmac.compare_digest(got.encode(), pw.encode()):
            fails.append(now)
            log.warning(f"远程登录失败：{ip}（15 分钟内第 {len(fails)} 次）")
            time.sleep(1)
            return self.json({"error": "密码不对 / Wrong password"}, 401)
        _login_fails.pop(ip, None)
        log.info(f"远程登录成功：{ip}")
        cookie = f"{AUTH_COOKIE}={auth_token(pw)}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax; Secure"
        body = json.dumps({"ok": True}).encode()
        self._send(200, body, "application/json", {"Set-Cookie": cookie})

    def _remote_guard(self) -> bool:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/login" and self.command == "POST":
            self._do_login()
            return False
        if not self._authed():
            if self.command in ("GET", "HEAD") and not path.startswith(("/api/", "/media/", "/static/")):
                self._login_page()
            else:
                self.json({"error": "需要先登录 / Please log in", "login": True}, 401)
            return False
        blocked = (self.command == "GET" and path in REMOTE_BLOCKED_GET) or \
                  (self.command == "POST" and path in REMOTE_BLOCKED_POST) or \
                  (self.command == "POST" and path.rsplit("/", 1)[-1] in REMOTE_BLOCKED_ACTIONS
                   and path.startswith("/api/projects/"))
        if blocked:
            self.json({"error": "这个功能只能在运行 studio 的电脑上用 / Only available on the host computer"}, 403)
            return False
        return True

    # -- 日志：GET 只在 DEBUG 级别记录（网页每秒都在轮询），POST 和出错的请求总会记录
    def log_request(self, code="-", size="-"):
        try:
            code = int(code)
        except (TypeError, ValueError):
            code = 0
        path = urllib.parse.unquote(self.path.split("?")[0])
        msg = f"{self.command} {path} → {code}"
        if code >= 400 and not path.endswith("favicon.ico"):
            log.warning(msg + (f"  {self._err}" if getattr(self, "_err", "") else ""))
        elif self.command == "POST":
            log.info(msg)
        else:
            log.debug(msg)

    def log_error(self, fmt, *args):
        log.warning("HTTP " + (fmt % args))

    def log_message(self, fmt, *args):
        log.debug("HTTP " + (fmt % args))

    # -- plumbing
    def _cors(self):
        """只对允许的来源回 CORS 头（见 origin_allowed）。"""
        origin = self.headers.get("Origin", "")
        if origin and origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):  # noqa: N802
        if not self._guard():
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, data, code: int = 200):
        self._err = data.get("error", "") if isinstance(data, dict) and code >= 400 else ""
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 12 * 1024 * 1024:
            raise ValueError("请求太大了")
        raw = self.rfile.read(n) if n else b""
        if raw and "json" not in (self.headers.get("Content-Type") or "").lower():
            raise ValueError("请求必须是 Content-Type: application/json")
        return json.loads(raw or b"{}")

    def file(self, path: Path):
        if not path.is_file():
            return self.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)) and size:   # 视频拖动进度条要用
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            elif m.group(2):                       # bytes=-500：最后 500 字节
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self._cors()
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if WEB.resolve() in path.resolve().parents:
            # 界面文件（html/js/css）：每次先问服务器有没有更新，代码更新后不会出现“新 JS + 旧 CSS”的错位
            self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.end_headers()
        if self.command == "HEAD" or not size:
            return
        with path.open("rb") as f:                 # 分块发送，大视频不再整个读进内存
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # -- routing
    def do_GET(self):  # noqa: N802
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        try:
            if path == "/" or path == "/index.html":
                return self.file(WEB / "index.html")
            if path.startswith("/static/"):
                f = (WEB / path[len("/static/"):]).resolve()
                if WEB.resolve() in f.parents:
                    return self.file(f)
                return self.json({"error": "nope"}, 403)
            if path.startswith("/media/"):
                parts = path[len("/media/"):].split("/", 1)
                if len(parts) != 2:
                    return self.json({"error": "bad path"}, 400)
                p = project_dir(urllib.parse.unquote(parts[0]))
                f = (p.root / urllib.parse.unquote(parts[1])).resolve()
                if p.root in f.parents or f.parent == p.root:
                    return self.file(f)
                return self.json({"error": "nope"}, 403)

            if path == "/api/env":
                drives = ([f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
                          if os.name == "nt" else [])
                kh = key_hint()
                if self.remote:              # 远程用户：不告诉他电脑上的路径和 Key
                    return self.json({"has_key": kh["has_key"], "key": {"has_key": kh["has_key"]},
                                      "version": __version__, "home": "", "cwd": "", "drives": [], "remote": True})
                return self.json({"has_key": kh["has_key"], "key": kh, "version": __version__,
                                  "home": str(Path.home()), "cwd": str(Path.cwd()), "drives": drives,
                                  "remote": False})
            if path == "/api/settings":
                if self.remote:              # 页面启动时要读几个开关（转写、高清…），路径、Key、密码都不给
                    hide = {"remote_password", "allowed_origins", "base_url", "projects_dir", "font"}
                    pl = settings_payload()
                    pl["settings"] = {k: v for k, v in pl["settings"].items() if k not in hide}
                    pl["defaults"] = {k: v for k, v in pl["defaults"].items() if k not in hide}
                    pl.update(key={"has_key": pl["key"]["has_key"]}, fal_key={"has_key": pl["fal_key"].get("has_key")},
                              settings_file="", env_file="", projects_path="", detected_font="")
                    return self.json(pl)
                return self.json(settings_payload())
            if path == "/api/logs":
                return self.json({"entries": applog.read_entries(q.get("level", ["DEBUG"])[0], q.get("q", [""])[0],
                                                                 int(q.get("limit", ["800"])[0])),
                                  "counts": applog.COUNTS, "file": str(applog.LOG_FILE),
                                  "level": config.get("log_level")})
            if path == "/api/usage":
                from .. import usage
                return self.json(usage.summary(max(1, min(366, int(q.get("days", ["30"])[0])))))
            if path == "/api/logs/summary":
                return self.json({"counts": applog.COUNTS})
            if path == "/api/ls":
                return self.json(browse(q.get("path", [""])[0]))
            if path == "/api/projects":
                return self.json({"projects": list_projects()})
            if path == "/api/job":
                job = runner.current
                since = int(q.get("since", ["0"])[0])
                return self.json(job.as_dict(since) if job else {"status": "idle", "lines": [],
                                                                 "line_count": 0})
            m = re.fullmatch(r"/api/projects/([^/]+)", path)
            if m:
                return self.json(project_summary(urllib.parse.unquote(m.group(1))))
            m = re.fullmatch(r"/api/projects/([^/]+)/shots", path)
            if m:
                return self.json({"shots": shots_payload(urllib.parse.unquote(m.group(1)))})
            m = re.fullmatch(r"/api/projects/([^/]+)/outline", path)
            if m:
                p = project_dir(urllib.parse.unquote(m.group(1)))
                return self.json(read_json(p.out("outline.json"), {}) or {})
            m = re.fullmatch(r"/api/projects/([^/]+)/lab", path)
            if m:
                return self.json(lab_payload(project_dir(urllib.parse.unquote(m.group(1)))))
            m = re.fullmatch(r"/api/projects/([^/]+)/shootplan", path)
            if m:
                return self.json(plan_payload(project_dir(urllib.parse.unquote(m.group(1)))))
            m = re.fullmatch(r"/api/projects/([^/]+)/story", path)
            if m:
                return self.json(story_payload(urllib.parse.unquote(m.group(1))))
            m = re.fullmatch(r"/api/projects/([^/]+)/versions", path)
            if m:
                return self.json({"versions": list_versions(project_dir(urllib.parse.unquote(m.group(1))))})
            return self.json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log.debug(f"浏览器提前断开：GET {path}")
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception(f"接口出错：GET {path}")
            return self.json({"error": f"{e.__class__.__name__}: {e}"}, 500)

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        u = urllib.parse.urlparse(self.path)
        path = u.path
        try:
            m = re.fullmatch(r"/api/projects/([^/]+)/upload", path)
            if m:                               # 上传素材：请求体是视频的一块原始字节，不是 JSON
                n = int(self.headers.get("Content-Length") or 0)
                if n > UPLOAD_CHUNK_MAX:
                    raise ValueError("上传的分块太大")
                data = self.rfile.read(n) if n else b""
                return self.json(handle_upload(urllib.parse.unquote(m.group(1)),
                                               urllib.parse.parse_qs(u.query), data))
            b = self.body()
            if path == "/api/settings":
                if b.get("reset"):
                    config.reset()
                    applog.set_level(config.get("log_level"))
                    log.info("设置已恢复默认")
                if b.get("fal_key"):
                    config.write_env_key(b["fal_key"], lab.FAL_KEY_VAR)
                    log.info(f"视频生成服务 Key 已更新：{lab.key_hint()['hint']}")
                if b.get("api_key"):
                    config.write_env_key(b["api_key"])
                    log.info(f"API Key 已更新：{key_hint()['hint']}")
                if b.get("settings"):
                    config.save(b["settings"])
                    applog.set_level(config.get("log_level"))
                    log.info("设置已保存：" + "，".join(f"{k}={v}" for k, v in b["settings"].items()))
                return self.json(settings_payload())
            if path == "/api/settings/test":
                return self.json(test_key(b.get("api_key") or None))
            if path == "/api/logs/open":
                _open_path(applog.LOG_DIR)
                return self.json({"ok": True})
            if path == "/api/settings/open_projects":
                _open_path(PROJECTS)
                return self.json({"ok": True})
            if path == "/api/projects":
                footage = (b.get("footage") or "").strip()
                name = (b.get("name") or "").strip() or (Path(footage).name if footage else "")
                pid = safe_name(name)
                if not pid:
                    raise ValueError("请填写项目名称")
                if not footage:              # 出发前：只建项目，先写拍摄脚本，素材以后再加
                    if (PROJECTS / pid).exists():
                        raise ValueError(f"已经有同名项目：{pid}")
                    p = project_dir(pid)
                    p.save_settings(created=p.settings().get("created") or date.today().isoformat())
                    log.info(f"新建项目（还没有素材）：{pid}")
                    return self.json({"id": pid, "started": None})
                if self.remote and not inside_projects(footage):
                    raise ValueError("远程只能用上传的视频，不能选电脑里的文件夹")
                if not Path(footage).expanduser().is_dir():
                    raise ValueError(f"素材文件夹不存在：{footage}")
                runner.start("扫描素材", pid, job_scan, pid, footage)
                return self.json({"id": pid, "started": "scan"})

            m = re.fullmatch(r"/api/projects/([^/]+)/(\w+)", path)
            if not m:
                return self.json({"error": "not found"}, 404)
            pid, action = urllib.parse.unquote(m.group(1)), m.group(2)
            lang = b.get("lang") or config.get("lang")
            mock = bool(b.get("mock"))
            minutes = float(b.get("minutes") or project_dir(pid).settings().get("minutes") or 3)
            if self.remote and b.get("music") and not inside_projects(str(b["music"])):
                raise ValueError("远程不能使用电脑里的音乐文件路径")

            if action == "analyze":
                runner.start("看素材", pid, job_analyze, pid, mock, lang,
                             bool(b.get("whisper", config.get("whisper"))),
                             int(b.get("workers") or config.get("workers")), b.get("subject", ""))
            elif action == "plan":
                runner.start("写拍摄脚本", pid, job_plan, pid, b.get("brief", ""),
                             int(b.get("days") or 3), minutes, b.get("gear", ""), mock, lang)
            elif action == "outline":
                runner.start("编故事线", pid, job_outline, pid, b.get("idea", ""), minutes,
                             mock, lang)
            elif action == "story":
                runner.start("铺时间线", pid, job_story, pid, b.get("beats") or [], minutes,
                             mock, lang)
            elif action == "revise":
                runner.start("修改剪辑", pid, job_revise, pid, b.get("instruction", ""),
                             minutes, mock, lang)
            elif action == "render":
                runner.start("渲染预览", pid, job_render, pid, b.get("music"),
                             bool(b.get("vertical")), bool(b.get("hd")))
            elif action == "export":
                runner.start("导出时间线", pid, job_export, pid)
            elif action == "recheck":
                runner.start("重新校验", pid, job_recheck, pid)
            elif action == "lab_make":          # 镜头工坊：慢动作 / 裁切 / 补镜头（后台任务）
                kind = b.get("kind")
                if kind not in lab.KINDS or not b.get("shot_id"):
                    raise ValueError("参数不对：需要 kind（fill / slowmo / crop）和 shot_id")
                name = {"fill": "AI 补镜头", "slowmo": "做慢动作", "crop": "裁切特写"}[kind]
                runner.start(name, pid, job_lab, pid, kind, b, mock)
            elif action in ("timeline", "restore", "flag", "settings", "open_folder", "plan_check", "plan_save",
                            "plan_ref", "plan_ref_del", "lab_preview", "lab_still", "lab_faces", "lab_track", "lab_prompt",
                            "lab_insert", "lab_delete", "lab_rename"):
                return self.json(self._sync_action(pid, action, b))
            else:
                return self.json({"error": f"unknown action {action}"}, 404)
            return self.json({"started": action})
        except RuntimeError as e:
            return self.json({"error": str(e)}, 409)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception(f"接口出错：POST {path}")
            return self.json({"error": f"{e.__class__.__name__}: {e}"}, 500)


def _sync_action(self, pid: str, action: str, b: dict):
    """立即返回结果的小操作（不走后台任务）。"""
    p = project_dir(pid)
    if action in ("timeline", "restore") and runner.busy:
        raise RuntimeError(f"已经有任务在跑：{runner.current.name}，等它结束再保存")
    if action == "timeline":          # 用户手动改完时间线后保存
        return save_timeline(pid, b.get("timeline", []), b.get("note") or "手动编辑")
    if action == "restore":
        return restore_version(pid, int(b.get("n", 0)))
    if action == "flag":
        fl = flags(p)
        sid, kind, on = b.get("shot"), b.get("flag"), bool(b.get("on"))
        if kind not in ("must", "exclude", "unprotect") or not sid:
            raise ValueError("flag 只能是 must、exclude 或 unprotect")
        fl[kind] = [x for x in fl[kind] if x != sid] + ([sid] if on else [])
        if on:                         # 三种标记互斥
            for other in ("must", "exclude", "unprotect"):
                if other != kind:
                    fl[other] = [x for x in fl[other] if x != sid]
        write_json(p.root / "flags.json", fl)
        return {"flags": fl}
    if action == "settings":
        keep = {}
        for k in ("brief", "music", "lang", "subject", "plan_brief", "plan_gear"):
            if k in b and b[k] is not None:
                keep[k] = str(b[k]).strip()
        if keep.get("music") and not Path(keep["music"]).expanduser().is_file():
            raise ValueError(f"找不到音乐文件：{keep['music']}")
        if b.get("minutes") not in (None, ""):
            keep["minutes"] = float(b["minutes"])
        if "vertical" in b:
            keep["vertical"] = bool(b["vertical"])
        if b.get("plan_days") not in (None, ""):
            keep["plan_days"] = max(1, min(30, int(b["plan_days"])))
        s = p.settings()
        s.update(keep)
        write_json(p.settings_json, s)
        return {"settings": s}
    if action == "open_folder":
        _open_path(p.root)
        return {"ok": True}
    if action == "plan_check":         # 拍摄清单打勾
        prog = read_json(p.out("shootplan_progress.json"), {}) or {}
        done = [x for x in prog.get("done", []) if x != b.get("shot_id")]
        if b.get("done"):
            done.append(str(b.get("shot_id")))
        write_json(p.out("shootplan_progress.json"), {"done": done})
        return {"done": done}
    if action == "plan_save":
        return save_plan(p, b)
    media = lambda f: f"/media/{urllib.parse.quote(pid)}/" + str(Path(f).relative_to(p.root)).replace(os.sep, "/")  # noqa: E731
    if action == "lab_preview":        # 工坊里播放用的小预览
        return {"url": media(lab.preview(p, str(b.get("shot_id", ""))))}
    if action == "lab_still":
        return {"url": media(lab.still(p, str(b.get("shot_id", "")), float(b.get("t") or 0)))}
    if action == "lab_faces":
        sid, tt = str(b.get("shot_id", "")), float(b.get("t") or 0)
        found, method = lab.faces(p, sid, tt, None if b.get("mock") else get_llm(False) if key_hint()["has_key"] else None)
        return {"faces": found, "method": method, "url": media(lab.still(p, sid, tt))}
    if action == "lab_track":          # 跟随人脸：算出裁切关键帧
        llm = None if b.get("mock") or not key_hint()["has_key"] else get_llm(False)
        return lab.track(p, str(b.get("shot_id", "")), b.get("in"), b.get("out"), b.get("seed"),
                         b.get("seed_t"), float(b.get("tight") or 0.38), llm)
    if action == "lab_prompt":         # 让 AI 写给视频模型的提示词
        llm = get_llm(bool(b.get("mock")))
        return {"prompt": lab.draft_prompt(p, llm, b.get("gap", ""), b.get("shot_id", ""), b.get("note", ""))}
    if action == "lab_insert":
        if runner.busy:
            raise RuntimeError(f"已经有任务在跑：{runner.current.name}，等它结束再放进时间线")
        lab.insert(p, str(b.get("id", "")), b.get("at"))
        storyboard.storyboard(p)
        return lab_payload(p)
    if action == "lab_delete":
        used = {t["shot_id"] for t in (read_json(p.story_json, {}) or {}).get("timeline", [])}
        if b.get("id") in used:
            raise ValueError("这个片段还在时间线里，先在时间线里删掉它")
        lab.delete(p, str(b.get("id", "")))
        return lab_payload(p)
    if action == "lab_rename":
        items = read_json(p.out(lab.LAB_JSON), []) or []
        for a in items:
            if a.get("id") == b.get("id"):
                a["label"] = str(b.get("label", "")).strip()[:60]
        write_json(p.out(lab.LAB_JSON), items)
        return lab_payload(p)
    if action == "plan_ref":           # 给镜头附参考图
        return add_plan_ref(p, b)
    if action == "plan_ref_del":
        return del_plan_ref(p, b)
    raise ValueError(action)


Handler._sync_action = _sync_action


def _startup_info():
    import platform
    import shutil
    venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    log.info("=" * 60)
    log.info(f"vlogai studio 版本 {__version__}")
    log.info(f"启动 · Python {platform.python_version()} · {sys.executable}"
             + ("" if venv else "  ← 没有在虚拟环境里运行"))
    log.info(f"工作目录 {Path.cwd()} · 设置文件 {config.path().name}"
             + ("" if config.path().exists() else "（未创建，全部用默认值）"))
    log.info(f"日志文件 {applog.LOG_FILE} · 级别 {config.get('log_level')}")
    log.info(f"ffmpeg {shutil.which('ffmpeg') or '未找到'}")
    cfg = config.load()
    log.info(f"抽帧：每 {cfg['frame_every_sec']} 秒，最多 {cfg['max_frames_per_shot']} 帧，{cfg['frame_width']}px · "
             f"并行 {cfg['workers']} · 语音转写 {cfg['whisper_model'] if cfg['whisper'] else '关'}")


class _Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 允许两个程序同时占着同一个端口：旧窗口里的 studio 没关时，
    # 新开的 studio 也能“启动成功”，但请求会被旧的那个接走（网页是新的、后端是旧的）。
    # 所以 Windows 上改用独占端口。
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        import socket
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _studio_already_on(port: int) -> str | None:
    """这个端口上是不是已经有一个 vlogai studio 在跑？是的话返回它的版本号。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/env", timeout=0.6) as r:
            d = json.loads(r.read().decode("utf-8"))
            return str(d.get("version") or "?") if "has_key" in d else None
    except Exception:  # noqa: BLE001
        return None


def serve(port: int | None = None, open_browser: bool = True):
    applog.setup(config.get("log_level"))
    _startup_info()
    require_ffmpeg()
    port = int(port or config.get("port"))
    PROJECTS.mkdir(parents=True, exist_ok=True)
    httpd, chosen = None, port
    log.info(f"代码位置 {Path(__file__).resolve().parents[1]}")
    for cand in range(port, port + 12):          # 端口被占就自动换一个，不让它拦住你
        other = _studio_already_on(cand)
        if other:
            log.warning(f"端口 {cand} 上已经有一个 vlogai studio 在运行（版本 {other}），多半是之前的窗口没关。"
                        f"它跑的可能是旧代码——请关掉那个窗口（或在任务管理器里结束多余的 python.exe）。这次先换一个端口。")
            continue
        try:
            httpd = _Server(("127.0.0.1", cand), Handler)
            chosen = cand
            break
        except OSError as e:
            if e.errno not in (48, 98, 10048):   # 48=macOS 98=Linux 10048=Windows: 端口被占用
                raise
            log.warning(f"端口 {cand} 被占用，换一个…")
    if httpd is None:
        log.error(f"端口 {port}-{port+11} 全都被占用了")
        raise SystemExit(f"{port}-{port+11} 全都被占用了。先关掉其他程序，或者用 --port 指定别的端口。")
    port = chosen
    RUNNING["port"] = port
    url = f"http://127.0.0.1:{port}"
    log.info(f"项目文件夹：{PROJECTS}")
    kh = key_hint()
    if not kh["has_key"]:
        log.warning("没有 ANTHROPIC_API_KEY，只能用离线模拟模式（到设置页填写）")
    else:
        msg = f"API Key：{kh['hint']}（{kh['length']} 位，来自 {kh['source']}）"
        if kh["looks_ok"]:
            log.info(msg)
        else:
            log.warning(msg + "  ← 看起来不对：应以 sk-ant- 开头且不含空格")
        if kh["base_url"] and kh["base_url"].rstrip("/") != "https://api.anthropic.com":
            log.warning(f"设置了 API 地址 {kh['base_url']}，请求不会直接发到 Anthropic")
    log.info(f"已启动：{url}   （关闭：在这个窗口按 Ctrl+C）")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("已停止（Ctrl+C）")
