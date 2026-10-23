"""关键画面保护：哪些镜头无论如何都不能被剪掉。

不需要训练，也不需要人脸识别库。AI 在看每个镜头时已经在回答"有没有清晰人脸"、
"主人公在不在"、"几个人同框"、"风景分值"，这里只是把这些答案变成硬规则。

两层防线：
  1. 生成时——把受保护的镜头 ID 显式列给模型，要求必须出现
  2. 生成后——程序校验，缺了就自动插回去（模型会忘，程序不会）
"""
from __future__ import annotations

import json

from . import config
from .common import log, read_json


def _rules(cfg: dict) -> list:
    """规则：(判断函数, 原因, 优先级)。优先级高的在超出上限时后被放弃。开关和门槛在设置页“关键画面保护”里改。"""
    out = []
    if cfg["protect_faces"]:
        out.append((lambda lg, s: lg.get("subject_present") and lg.get("faces_clear"), "主人公正脸", 100))
    if cfg["protect_together"]:
        out.append((lambda lg, s: lg.get("people_together"), "关键人物同框", 95))
    if int(cfg["protect_scenery"]) > 0:
        n = int(cfg["protect_scenery"])
        out.append((lambda lg, s: (lg.get("scenery_value") or 0) >= n, "重要风景/地标", 80))
    if int(cfg["protect_highlight"]) > 0:
        h = int(cfg["protect_highlight"])
        out.append((lambda lg, s: (lg.get("highlight") or 0) >= h, "AI 评为极精彩", 70))
    if cfg["protect_ending"]:
        out.append((lambda lg, s: "ending" in (lg.get("story_roles") or []), "适合作结尾", 60))
    return out


RULES = _rules(config.DEFAULTS)     # 默认规则（给外部参考用）；实际每次按当前设置生成

MIN_QUALITY = 0.18          # 糊成一片的镜头，再重要也救不回来
MAX_SHARE = 0.65            # 受保护镜头最多占成片的六成，否则等于没得剪


USER_MUST = "你标为必用"     # 界面里点了“标为必用”的镜头，优先级最高


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def user_flags(project) -> dict:
    f = read_json(project.root / "flags.json", {}) or {}
    return {k: set(f.get(k, [])) for k in ("must", "exclude", "unprotect")}


def protected(project, shots: list[dict], target_seconds: float | None = None,
              quiet: bool = False) -> dict[str, str]:
    """返回 {shot_id: 保护原因}。shots 需要带 log 字段（load_logged_shots 的输出）。

    用户在界面里的选择优先于自动规则：标为必用 → 一定保护；点了“不保护” → 自动规则不再保护它；
    排除的镜头不会出现在这里（load_logged_shots 已经把它们去掉了）。"""
    fl = user_flags(project)
    cfg = config.load()
    rules = _rules(cfg) if cfg["protect_enabled"] else []      # 关掉自动保护后，只剩你标的“必用”
    min_q, max_share = float(cfg["protect_min_quality"]), float(cfg["protect_max_share"])
    hits: list[tuple[int, str, str, float]] = []   # (优先级, id, 原因, 时长)
    for s in shots:
        if s["id"] in fl["exclude"]:
            continue
        lg = s.get("log") or read_json(project.logs_dir / f"{s['id']}.json", {}) or {}
        best = lg.get("best_moment") or {}
        dur = min(max(1.5, _num(best.get("end"), 4) - _num(best.get("start"), 0)), s["duration"])
        if s["id"] in fl["must"]:
            hits.append((120, s["id"], USER_MUST, dur))
            continue
        if s["id"] in fl["unprotect"] or s.get("quality", 1) < min_q:
            continue
        for test, reason, prio in rules:
            try:
                ok = test(lg, s)
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                hits.append((prio, s["id"], reason, dur))
                break

    hits.sort(key=lambda h: -h[0])
    # 保护太多等于没剪：超过上限就按优先级截断
    if target_seconds:
        cap, total, kept = max_share * target_seconds, 0.0, []
        for prio, sid, reason, dur in hits:
            if total + dur > cap:
                continue
            total += dur
            kept.append((prio, sid, reason, dur))
        if len(kept) < len(hits) and not quiet:
            log(f"  受保护镜头超出上限，按优先级保留 {len(kept)}/{len(hits)} 个")
        hits = kept

    out = {sid: reason for _, sid, reason, _ in hits}
    if out and not quiet:
        by_reason: dict[str, int] = {}
        for r in out.values():
            by_reason[r] = by_reason.get(r, 0) + 1
        log("  关键画面保护：" + "、".join(f"{k} {v} 个" for k, v in by_reason.items()))
    return out


def must_include_block(prot: dict[str, str], shots: list[dict]) -> str:
    """喂给模型的那段"这些必须出现"。"""
    if not prot:
        return "(none)"
    by_id = {s["id"]: s for s in shots}
    rows = []
    for sid, reason in prot.items():
        s = by_id.get(sid)
        if not s:
            continue
        lg = s.get("log") or {}
        rows.append({"id": sid, "reason": reason, "sum": (lg.get("summary") or "")[:60]})
    return json.dumps(rows, ensure_ascii=False)     # 摘要里有引号也不会把 JSON 弄坏


def enforce(plan: dict, prot: dict[str, str], shots: list[dict]) -> dict:
    """生成后校验：受保护但没进时间线的镜头，自动插回最合适的位置。"""
    if not prot:
        return plan
    tl = plan.get("timeline", [])
    present = {t["shot_id"] for t in tl}
    missing = [sid for sid in prot if sid not in present]
    if not missing:
        log("  ✓ 所有受保护镜头都在成片里")
        return plan

    by_id = {s["id"]: s for s in shots}
    added = 0
    for sid in missing:
        s = by_id.get(sid)
        if not s:
            continue
        lg = s.get("log") or {}
        best = lg.get("best_moment") or {}
        a = max(0.0, min(_num(best.get("start"), 0), s["duration"] - 1.5))
        b = min(s["duration"], max(a + 1.5, _num(best.get("end"), a + 4)))
        # 插在同一天的最后一个片段之后，没有同天的就放在结尾前
        pos, chapter = len(tl), (tl[-1]["chapter"] if tl else "")
        for i, t in enumerate(tl):
            ts = by_id.get(t["shot_id"])
            if ts and ts.get("day") == s.get("day"):
                pos, chapter = i + 1, t.get("chapter", chapter)
        tl.insert(pos, {
            "shot_id": sid, "in": round(a, 2), "out": round(b, 2), "chapter": chapter,
            "role": "protected", "why": f"关键画面保护：{prot[sid]}（AI 原本漏掉了，已自动补回）",
            "keep_audio": False, "text": "",
            "src": s["src"], "src_in": round(s["start"] + a, 3), "src_out": round(s["start"] + b, 3),
        })
        added += 1
    plan["timeline"] = tl
    plan["total_seconds"] = round(sum(t["out"] - t["in"] for t in tl), 1)
    log(f"  ⚠️ AI 漏掉了 {added} 个受保护镜头，已自动补回（见时间线里标注 protected 的片段）")
    return plan
