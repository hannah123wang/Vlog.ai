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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import describe as describe_mod
from .. import export_xml, frames, outline, probe, render, story, storyboard, transcribe
from ..common import VIDEO_EXTS, Project, read_json, require_ffmpeg, write_json
from ..llm import get_llm, key_hint
from .jobs import JobRunner

WEB = Path(__file__).parent / "web"
PROJECTS = Path("projects").resolve()
LANGS = {"zh": "Simplified Chinese", "en": "English"}
runner = JobRunner()


# ---------------------------------------------------------------- helpers
def project_dir(pid: str) -> Project:
    safe = re.sub(r"[^\w\-. ]", "", pid).strip()
    if not safe:
        raise ValueError("项目名无效")
    return Project(PROJECTS / safe)


def flags(p: Project) -> dict:
    """界面里标记的“必用 / 排除”镜头。排除的镜头在 describe.load_logged_shots 里被过滤掉。"""
    f = read_json(p.root / "flags.json", {}) or {}
    return {"must": list(f.get("must", [])), "exclude": list(f.get("exclude", []))}


def media_url(pid: str, p: Project, path: str) -> str:
    return f"/media/{pid}/" + str(Path(path).relative_to(p.root)).replace(os.sep, "/")


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
        "unusable": sum(1 for s_ in shots if s_.get("quality", 1) < 0.25),
        "has_outline": bool(ol.get("beats")),
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
        if d.is_dir() and (d / "clips.json").exists():
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
    dirs = []
    for d in sorted(base.iterdir()):
        try:
            if d.is_dir() and not d.name.startswith("."):
                n = sum(1 for f in d.iterdir()
                        if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."))
                dirs.append({"name": d.name, "path": str(d), "videos": n})
        except PermissionError:
            continue
    here = sum(1 for f in base.iterdir()
               if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."))
    return {"path": str(base), "parent": str(base.parent) if base.parent != base else None,
            "dirs": dirs[:400], "videos_here": here}


def shots_payload(pid: str) -> list[dict]:
    p = project_dir(pid)
    clips = {c["id"]: c for c in read_json(p.clips_json, []) or []}
    fl = flags(p)
    must, exc = set(fl["must"]), set(fl["exclude"])
    out = []
    for s in read_json(p.shots_json, []) or []:
        lg = read_json(p.logs_dir / f"{s['id']}.json", {}) or {}
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
                    "tags": lg.get("tags", []), "activity": lg.get("activity", ""),
                    "best_moment": lg.get("best_moment"), "logged": bool(lg),
                    "must": s["id"] in must, "excluded": s["id"] in exc})
    return out


def story_payload(pid: str) -> dict:
    p = project_dir(pid)
    st = read_json(p.story_json, {}) or {}
    shots = {s["id"]: s for s in read_json(p.shots_json, []) or []}
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
                      "summary": lg.get("summary", ""), "shot_dur": s.get("duration", 0),
                      "quality": s.get("quality"), "highlight": lg.get("highlight")})
        t += dur
    return {"title": st.get("title", ""), "logline": st.get("logline", ""),
            "chapters": st.get("chapters", []), "total_seconds": round(t, 1), "timeline": items}


# ---------------------------------------------------------------- jobs
def job_scan(pid: str, footage: str):
    p = project_dir(pid)
    p.save_settings(footage=str(Path(footage).expanduser().resolve()))
    probe.scan(footage, p)


def job_analyze(pid: str, mock: bool, lang: str, whisper: bool, workers: int = 4):
    p = project_dir(pid)
    frames.extract(p, workers=workers)
    if whisper:
        transcribe.transcribe(p)
    describe_mod.describe_all(p, get_llm(mock), lang=LANGS[lang], workers=workers)


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


def job_render(pid: str, music: str | None, vertical: bool, hd: bool):
    p = project_dir(pid)
    p.save_settings(music=music or None, vertical=vertical)
    render.render(p, music=music or None, vertical=vertical, hd=hd)
    export_xml.export_xml(p, music=music or None)


def job_export(pid: str):
    p = project_dir(pid)
    s = p.settings()
    render.final_timeline(p, s.get("music"))
    export_xml.export_xml(p, music=s.get("music"))


def job_recheck(pid: str):
    """手改 story.json 之后：重新校验，更新故事板和 XML。"""
    p = project_dir(pid)
    story.recheck(p)
    storyboard.storyboard(p)
    s = p.settings()
    render.final_timeline(p, s.get("music") or None)
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
    plan = story._validate(plan, read_json(p.shots_json, []) or [])
    story._save(p, plan, note)
    storyboard.storyboard(p)
    return story_payload(pid)


def restore_version(pid: str, n: int) -> dict:
    p = project_dir(pid)
    v = read_json(p.versions_dir / f"story_v{int(n)}.json")
    if not v:
        raise ValueError(f"没有 v{n}")
    plan = {k: v[k] for k in ("title", "logline", "chapters", "timeline") if k in v}
    plan = story._validate(plan, read_json(p.shots_json, []) or [])
    story._save(p, plan, f"恢复 v{n}")
    storyboard.storyboard(p)
    return story_payload(pid)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "vlogai-studio"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # -- plumbing
    def _cors(self):
        """允许 Lovable / 任何前端从浏览器直接调这个本地后端。"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):  # noqa: N802
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
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def file(self, path: Path):
        if not path.is_file():
            return self.json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        size = path.stat().st_size
        rng = self.headers.get("Range")
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):   # 视频拖动进度条要用
            start = int(m.group(1) or 0)
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            with path.open("rb") as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
            self.send_response(206)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
            return
        self._send(200, path.read_bytes(), ctype, {"Accept-Ranges": "bytes"})

    # -- routing
    def do_GET(self):  # noqa: N802
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
                return self.json({"has_key": kh["has_key"], "key": kh,
                                  "home": str(Path.home()), "cwd": str(Path.cwd()), "drives": drives})
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
            m = re.fullmatch(r"/api/projects/([^/]+)/story", path)
            if m:
                return self.json(story_payload(urllib.parse.unquote(m.group(1))))
            m = re.fullmatch(r"/api/projects/([^/]+)/versions", path)
            if m:
                return self.json({"versions": list_versions(project_dir(urllib.parse.unquote(m.group(1))))})
            return self.json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self.json({"error": f"{e.__class__.__name__}: {e}"}, 500)

    def do_POST(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        path = u.path
        try:
            b = self.body()
            if path == "/api/projects":
                name = (b.get("name") or "").strip() or Path(b["footage"]).name
                pid = re.sub(r"[^\w\-. ]", "", name).strip() or "project"
                runner.start("扫描素材", pid, job_scan, pid, b["footage"])
                return self.json({"id": pid})

            m = re.fullmatch(r"/api/projects/([^/]+)/(\w+)", path)
            if not m:
                return self.json({"error": "not found"}, 404)
            pid, action = urllib.parse.unquote(m.group(1)), m.group(2)
            lang = b.get("lang", "zh")
            mock = bool(b.get("mock"))
            minutes = float(b.get("minutes") or project_dir(pid).settings().get("minutes") or 3)

            if action == "analyze":
                runner.start("看素材", pid, job_analyze, pid, mock, lang,
                             bool(b.get("whisper")), int(b.get("workers") or 4))
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
            elif action in ("timeline", "restore", "flag", "settings", "open_folder"):
                return self.json(self._sync_action(pid, action, b))
            else:
                return self.json({"error": f"unknown action {action}"}, 404)
            return self.json({"started": action})
        except RuntimeError as e:
            return self.json({"error": str(e)}, 409)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
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
        if kind not in ("must", "exclude") or not sid:
            raise ValueError("flag 只能是 must 或 exclude")
        other = "exclude" if kind == "must" else "must"
        fl[kind] = [x for x in fl[kind] if x != sid] + ([sid] if on else [])
        if on:
            fl[other] = [x for x in fl[other] if x != sid]
        write_json(p.root / "flags.json", fl)
        return {"flags": fl}
    if action == "settings":
        keep = {}
        for k in ("brief", "music", "lang"):
            if k in b and b[k] is not None:
                keep[k] = str(b[k]).strip()
        if keep.get("music") and not Path(keep["music"]).expanduser().is_file():
            raise ValueError(f"找不到音乐文件：{keep['music']}")
        if b.get("minutes") not in (None, ""):
            keep["minutes"] = float(b["minutes"])
        if "vertical" in b:
            keep["vertical"] = bool(b["vertical"])
        s = p.settings()
        s.update(keep)
        write_json(p.settings_json, s)
        return {"settings": s}
    if action == "open_folder":
        if os.name == "nt":
            os.startfile(str(p.root))  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p.root)])
        return {"ok": True}
    raise ValueError(action)


Handler._sync_action = _sync_action


def serve(port: int = 8765, open_browser: bool = True):
    require_ffmpeg()
    PROJECTS.mkdir(exist_ok=True)
    httpd, chosen = None, port
    for cand in range(port, port + 12):          # 端口被占就自动换一个，不让它拦住你
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", cand), Handler)
            chosen = cand
            break
        except OSError as e:
            if e.errno not in (48, 98, 10048):   # 48=macOS 98=Linux 10048=Windows: 端口被占用
                raise
            print(f"[vlogai studio] 端口 {cand} 被占用，换一个…")
    if httpd is None:
        raise SystemExit(f"{port}-{port+11} 全都被占用了。先关掉其他程序，或者用 --port 指定别的端口。")
    port = chosen
    url = f"http://127.0.0.1:{port}"
    print(f"[vlogai studio] 已启动：{url}")
    print(f"[vlogai studio] 项目文件夹：{PROJECTS}")
    kh = key_hint()
    if not kh["has_key"]:
        print("[vlogai studio] 注意：没有 ANTHROPIC_API_KEY，只能用离线模拟模式")
    else:
        print(f"[vlogai studio] API Key：{kh['hint']}（{kh['length']} 位，来自 {kh['source']}）"
              + ("" if kh["looks_ok"] else "  ← 看起来不对：应以 sk-ant- 开头且不含空格"))
        if kh["base_url"]:
            print(f"[vlogai studio] 注意：设置了 ANTHROPIC_BASE_URL={kh['base_url']}，请求不会直接发到 Anthropic")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[vlogai studio] 已停止")
