"""vlogai studio —— 本地桌面应用的后端。只用 Python 标准库，没有任何额外依赖。

  python -m vlogai.studio          # 然后浏览器打开 http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import describe as describe_mod
from .. import export_xml, frames, outline, probe, render, story, storyboard, transcribe
from ..common import VIDEO_EXTS, Project, read_json, require_ffmpeg, write_json
from ..llm import get_llm
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
                    "speech": (lg.get("speech") or "")[:120]})
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
    if mock or not beats:
        story.build_story(p, llm, idea, minutes * 60, LANGS[lang])
    else:
        ol = read_json(p.out("outline.json"), {}) or {}
        write_json(p.out("outline.json"), {**ol, "beats": beats})
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
                return self.json({"has_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                  "home": str(Path.home()), "cwd": str(Path.cwd())})
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
            elif action == "timeline":      # 用户手动改完时间线后保存
                p = project_dir(pid)
                st = read_json(p.story_json, {}) or {}
                keep = {(t["shot_id"], round(t["in"], 2)) for t in b.get("timeline", [])}
                order = {(t["shot_id"], round(t["in"], 2)): i for i, t in
                         enumerate(b.get("timeline", []))}
                new = [t for t in st.get("timeline", []) if (t["shot_id"], round(t["in"], 2)) in keep]
                for t in new:
                    upd = next((x for x in b["timeline"]
                                if x["shot_id"] == t["shot_id"]
                                and round(x["in"], 2) == round(t["in"], 2)), None)
                    if upd:
                        t["in"], t["out"] = float(upd["in"]), float(upd["out"])
                new.sort(key=lambda t: order[(t["shot_id"], round(t["in"], 2))])
                st["timeline"] = new
                write_json(p.story_json, st)
                story.recheck(p)
                return self.json(story_payload(pid))
            else:
                return self.json({"error": f"unknown action {action}"}, 404)
            return self.json({"started": action})
        except RuntimeError as e:
            return self.json({"error": str(e)}, 409)
        except Exception as e:  # noqa: BLE001
            return self.json({"error": f"{e.__class__.__name__}: {e}"}, 500)


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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[vlogai studio] 注意：没有 ANTHROPIC_API_KEY，只能用离线模拟模式")
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
