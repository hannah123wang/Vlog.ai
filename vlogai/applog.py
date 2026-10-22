"""运行日志：终端实时显示 + 写进 logs/vlogai.log（自动滚动，最多 5 个 × 5MB）。

  from .applog import log
  log.info("…") / log.warning("…") / log.exception("…")   # exception 会带上完整 traceback

studio 的后台任务会把 pipeline 打印的每一行转发到这里（见 studio/jobs.py），
所以终端和日志文件里能看到和网页进度条一样的内容，外加接口请求和报错。
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.cwd() / "logs"
LOG_FILE = LOG_DIR / "vlogai.log"
FILE_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("vlogai")           # 终端 + 文件
_file_only = logging.getLogger("vlogai.cli")  # 只写文件（命令行模式下，终端已经由 print 显示过了）

_lock = threading.Lock()
_ready = False
import time as _time
COUNTS = {"error": 0, "warning": 0, "started": _time.time()}   # 本次启动以来的报错/警告数，给界面角标用
RECENT = deque(maxlen=50)                   # 最近的报错，方便接口直接取


class _Counter(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            COUNTS["error"] += 1
            RECENT.append({"time": self.formatter.formatTime(record, DATE_FMT) if self.formatter else "",
                           "message": record.getMessage()})
        elif record.levelno >= logging.WARNING:
            COUNTS["warning"] += 1


def setup(level: str = "INFO", console: bool = True) -> None:
    """进程启动时调用一次。再次调用只调整级别。"""
    global _ready
    with _lock:
        if _ready:
            set_level(level)
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(logging.Formatter(FILE_FMT, DATE_FMT))
        counter = _Counter()
        counter.setFormatter(logging.Formatter(FILE_FMT, DATE_FMT))
        log.handlers.clear()
        log.addHandler(fh)
        log.addHandler(counter)
        if console:
            _enable_ansi()
            # 用进程原始的 stderr：任务运行时 stdout 会被重定向给网页进度条，stderr 不会
            ch = logging.StreamHandler(sys.__stderr__ or sys.stderr)
            ch.setFormatter(_ConsoleFormatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
            log.addHandler(ch)
        log.propagate = False
        _file_only.handlers.clear()
        _file_only.addHandler(fh)
        _file_only.addHandler(counter)
        _file_only.propagate = False
        set_level(level)
        _install_hooks()
        _ready = True


def set_level(level: str) -> None:
    lv = getattr(logging, str(level).upper(), logging.INFO)
    log.setLevel(lv)
    _file_only.setLevel(lv)


def _enable_ansi() -> None:
    import os
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-12)                 # STD_ERROR_HANDLE
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:  # noqa: BLE001
            pass


class _ConsoleFormatter(logging.Formatter):
    """终端里给警告和报错加颜色（Windows 10+ 的终端也支持）。"""
    COLORS = {"WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[31m", "DEBUG": "\033[90m"}

    def format(self, record):
        s = super().format(record)
        c = self.COLORS.get(record.levelname)
        return f"{c}{s}\033[0m" if c else s


def _install_hooks() -> None:
    """没被捕获的异常（主线程、后台线程）也记下来，不再悄悄消失。"""
    old = sys.excepthook

    def hook(tp, val, tb):
        if not issubclass(tp, KeyboardInterrupt):
            log.error("未捕获的异常", exc_info=(tp, val, tb))
        old(tp, val, tb)

    sys.excepthook = hook

    def thook(args):
        if args.exc_type is not SystemExit:
            log.error(f"线程 {args.thread.name if args.thread else '?'} 出错",
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = thook


_ERR = re.compile(r"✗|Traceback|ERROR|出错|Error code: [45]\d\d|Exception:")
_WARN = re.compile(r"失败|重试|跳过|找不到|忽略|注意|警告|没有安装|未安装|看起来不对")


def level_of(line: str) -> int:
    """pipeline 打印的是普通文字，这里按内容判断级别。"""
    if _ERR.search(line):
        return logging.ERROR
    if _WARN.search(line):
        return logging.WARNING
    return logging.INFO


def pipeline_line(line: str, context: str = "", console: bool = True) -> None:
    """记录 pipeline 打印的一行。context 例如 “看素材·海洋三省”。"""
    line = line.replace("[vlogai] ", "").rstrip()
    if not line.strip():
        return
    msg = f"[{context}] {line}" if context else line
    (log if console else _file_only).log(level_of(line), msg)


class TeeStdout:
    """命令行模式：print 照常显示在终端，同时逐行写进日志文件。"""

    def __init__(self, orig):
        self.orig, self.buf = orig, ""

    def write(self, s):
        n = self.orig.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            pipeline_line(line, "cli", console=False)
        return n

    def flush(self):
        self.orig.flush()

    def __getattr__(self, name):
        return getattr(self.orig, name)


# ---------------------------------------------------------------- 给界面读日志
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\w+)\s+(\S+): (.*)$")
_RANK = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def read_entries(min_level: str = "DEBUG", q: str = "", limit: int = 500) -> list[dict]:
    """读日志文件末尾，解析成条目（多行的 traceback 合并到上一条）。"""
    if not LOG_FILE.is_file():
        return []
    with LOG_FILE.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 3_000_000))
        text = f.read().decode("utf-8", errors="replace")
    if size > 3_000_000:
        text = text.split("\n", 1)[-1]          # 丢掉被截断的第一行
    entries: list[dict] = []
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if m:
            entries.append({"time": m.group(1), "level": m.group(2), "source": m.group(3),
                            "message": m.group(4)})
        elif entries:
            entries[-1]["message"] += "\n" + raw
    floor = _RANK.get(min_level.upper(), 10)
    ql = q.strip().lower()
    out = [e for e in entries if _RANK.get(e["level"], 20) >= floor
           and (not ql or ql in e["message"].lower() or ql in e["source"].lower())]
    return out[-limit:]
