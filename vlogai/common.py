"""Shared helpers: paths, JSON IO, ffmpeg wrappers, logging."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".avi", ".mkv", ".insv", ".lrv"}


def log(msg: str) -> None:
    print(f"[vlogai] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"[vlogai] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def require_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if not missing:
        return
    # 没装 Homebrew 也没关系：static-ffmpeg 会下载一份自带的 ffmpeg/ffprobe
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths(weak=True)
        log("使用 static-ffmpeg 自带的 ffmpeg/ffprobe（首次运行会下载约 100MB）")
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        log(f"static-ffmpeg 启动失败：{e}")
    still = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if still:
        die(f"找不到 {', '.join(still)}。二选一：\n"
            "  A) brew install ffmpeg\n"
            "  B) 不用 Homebrew: pip install static-ffmpeg （然后重新运行本命令即可）")


def run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    """Run a command; raise with stderr tail on failure."""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"Command failed: {' '.join(cmd[:6])} ...\n{tail}")
    if not quiet:
        print(res.stdout)
    return res


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Project:
    """All intermediate files for one vlog live in one work folder."""

    def __init__(self, work_dir: Path):
        self.root = Path(work_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # --- files -------------------------------------------------------------
    @property
    def clips_json(self) -> Path:  # probe results
        return self.root / "clips.json"

    @property
    def shots_json(self) -> Path:  # shots + keyframes + quality
        return self.root / "shots.json"

    @property
    def logs_dir(self) -> Path:  # one AI description per shot (cache)
        return self.root / "logs"

    @property
    def transcripts_dir(self) -> Path:
        return self.root / "transcripts"

    @property
    def frames_dir(self) -> Path:
        return self.root / "frames"

    @property
    def story_json(self) -> Path:  # the edit decision list you can hand-edit
        return self.root / "story.json"

    @property
    def versions_dir(self) -> Path:
        return self.root / "story_versions"

    @property
    def settings_json(self) -> Path:
        return self.root / "settings.json"

    def out(self, name: str) -> Path:
        return self.root / name

    # --- settings persisted between steps ---------------------------------
    def settings(self) -> dict:
        return read_json(self.settings_json, {}) or {}

    def save_settings(self, **kw) -> dict:
        s = self.settings()
        s.update({k: v for k, v in kw.items() if v is not None})
        write_json(self.settings_json, s)
        return s
