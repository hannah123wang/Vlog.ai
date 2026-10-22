"""后台任务：一次跑一个，边跑边把日志和进度喂给前端。

复用 vlogai 各步骤现成的 print 输出——把 stdout 重定向进来当作进度日志，
不用改动已经跑通的 pipeline 代码。
"""
from __future__ import annotations

import io
import re
import threading
import traceback
from contextlib import redirect_stdout
from datetime import datetime

PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


class _LineStream(io.TextIOBase):
    """把 print 的每一行转交给 job。"""

    def __init__(self, job: "Job"):
        self.job = job
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.job.add_line(line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self.job.add_line(self._buf.strip())
            self._buf = ""


class Job:
    def __init__(self, name: str, project: str):
        self.name = name
        self.project = project
        self.status = "running"      # running | done | error
        self.lines: list[str] = []
        self.done = 0
        self.total = 0
        self.error = ""
        self.started = datetime.now().isoformat(timespec="seconds")
        self._lock = threading.Lock()

    def add_line(self, line: str) -> None:
        with self._lock:
            self.lines.append(line)
            if len(self.lines) > 500:
                del self.lines[:-400]
        m = PROGRESS_RE.search(line)
        if m:
            self.done, self.total = int(m.group(1)), int(m.group(2))

    def as_dict(self, since: int = 0) -> dict:
        with self._lock:
            lines = self.lines[since:]
            n = len(self.lines)
        pct = round(100 * self.done / self.total) if self.total else None
        return {"name": self.name, "project": self.project, "status": self.status,
                "lines": lines, "line_count": n, "done": self.done, "total": self.total,
                "pct": pct, "error": self.error, "started": self.started}


class JobRunner:
    """一次只跑一个任务（stdout 重定向是进程级的，并行会串台）。"""

    def __init__(self):
        self.current: Job | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.status == "running"

    def start(self, name: str, project: str, fn, *args, **kwargs) -> Job:
        with self._lock:
            if self.busy:
                raise RuntimeError(f"已经有任务在跑：{self.current.name}")
            job = Job(name, project)
            self.current = job

        def run():
            stream = _LineStream(job)
            try:
                with redirect_stdout(stream):
                    fn(*args, **kwargs)
                stream.flush()
                job.status = "done"
                job.add_line("✓ 完成")
            except Exception as e:  # noqa: BLE001
                stream.flush()
                job.status = "error"
                job.error = f"{e.__class__.__name__}: {e}"
                job.add_line("✗ " + job.error)
                job.add_line(traceback.format_exc()[-1500:])

        threading.Thread(target=run, daemon=True).start()
        return job
