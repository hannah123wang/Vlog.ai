"""Token 用量记账：每次调用 Claude API 记一行到 logs/usage.jsonl，网页“运行日志 → Token 用量”汇总显示。

一行一条，格式：
  {"t": "2026-10-23T11:58:02", "task": "2026-10-23T11:57:40", "job": "看素材", "project": "h3",
   "step": "describe", "model": "claude-haiku-4-5-20251001", "in": 2310, "out": 412,
   "cache_read": 0, "cache_write": 0}

任务信息（job / project / task）由 studio 的任务队列或命令行在开始时设置；同一时间只跑一个任务，
所以用一个进程级的“当前任务”就够了（看片的并行线程也能拿到）。
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .applog import LOG_DIR

USAGE_FILE = LOG_DIR / "usage.jsonl"
_lock = threading.Lock()
_current = {"job": "", "project": "", "task": ""}


def set_task(job: str, project: str, task: str | None = None) -> None:
    _current.update(job=job or "", project=project or "",
                    task=task or datetime.now().isoformat(timespec="seconds"))


def clear_task() -> None:
    _current.update(job="", project="", task="")


def record(model: str, step: str, usage) -> None:
    """usage：anthropic 返回的 resp.usage。记账失败不影响正常流程。"""
    try:
        row = {"t": datetime.now().isoformat(timespec="seconds"), **_current, "step": step, "model": model,
               "in": int(getattr(usage, "input_tokens", 0) or 0),
               "out": int(getattr(usage, "output_tokens", 0) or 0),
               "cache_read": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
               "cache_write": int(getattr(usage, "cache_creation_input_tokens", 0) or 0)}
        with _lock:
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _rows() -> list[dict]:
    if not USAGE_FILE.is_file():
        return []
    out = []
    with USAGE_FILE.open(encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _add(acc: dict, r: dict) -> None:
    acc["calls"] += 1
    acc["video_sec"] = acc.get("video_sec", 0) + r.get("video_sec", 0)
    acc["cost"] = round(acc.get("cost", 0) + r.get("cost", 0), 2)
    acc["in"] += r.get("in", 0) + r.get("cache_read", 0) + r.get("cache_write", 0)
    acc["out"] += r.get("out", 0)


def _new() -> dict:
    return {"calls": 0, "in": 0, "out": 0, "video_sec": 0, "cost": 0.0}


def summary(days: int = 30) -> dict:
    """网页用的汇总：今天 / 近 7 天 / 本月 / 全部；按天、按项目、按模型、按步骤；最近的任务。"""
    rows = _rows()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    since = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    totals = {k: _new() for k in ("today", "week", "month", "all")}
    by_day: dict = defaultdict(_new)
    by_project: dict = defaultdict(_new)
    by_model: dict = defaultdict(_new)
    by_step: dict = defaultdict(_new)
    tasks: dict = {}
    for r in rows:
        d = r.get("t", "")[:10]
        _add(totals["all"], r)
        if d == today:
            _add(totals["today"], r)
        if d >= week_start:
            _add(totals["week"], r)
        if d.startswith(month):
            _add(totals["month"], r)
        if d >= since:
            _add(by_day[d], r)
        _add(by_project[r.get("project") or "—"], r)
        _add(by_model[r.get("model") or "—"], r)
        _add(by_step[r.get("job") or r.get("step") or "—"], r)
        key = (r.get("task") or r.get("t", ""), r.get("job", ""), r.get("project", ""))
        t = tasks.setdefault(key, {"started": key[0], "job": key[1], "project": key[2],
                                   "models": set(), **_new()})
        _add(t, r)
        t["models"].add(r.get("model", ""))
    days_list = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    recent = sorted(tasks.values(), key=lambda x: x["started"], reverse=True)[:60]
    for t in recent:
        t["models"] = sorted(m for m in t["models"] if m)

    def ranked(d: dict) -> list:
        return [{"name": k, **v} for k, v in sorted(d.items(), key=lambda kv: -(kv[1]["in"] + kv[1]["out"]))]

    return {"totals": totals, "by_day": [{"day": d, **by_day.get(d, _new())} for d in days_list],
            "by_project": ranked(by_project), "by_model": ranked(by_model), "by_step": ranked(by_step),
            "tasks": recent, "file": str(USAGE_FILE), "records": len(rows)}


def task_total(task: str) -> dict:
    """某一次任务一共用了多少（任务结束时写进运行日志）。"""
    acc = _new()
    for r in _rows():
        if task and r.get("task") == task:
            _add(acc, r)
    return acc


def record_video(model: str, seconds: float, cost: float) -> None:
    """视频生成按秒计费，不是 token：单独记秒数和估算花费。"""
    try:
        row = {"t": datetime.now().isoformat(timespec="seconds"), **_current, "step": "video", "model": model,
               "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "video_sec": seconds, "cost": cost}
        with _lock:
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with USAGE_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
