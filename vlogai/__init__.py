"""vlogai — turn a folder of travel clips into a story-driven rough cut you can keep editing."""
import re
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "version"


def read_version(default: str = "0.1.0") -> str:
    """读项目根目录 version 文件的第一行，格式：version_number: v 1.0"""
    try:
        first = VERSION_FILE.read_text(encoding="utf-8-sig").splitlines()[0]
    except (OSError, IndexError):
        return default
    m = re.match(r"\s*version_number\s*[:：]\s*(.+?)\s*$", first)
    return m.group(1) if m else default


__version__ = read_version()
