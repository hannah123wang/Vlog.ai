"""关键画面保护：哪些镜头无论如何都不能被剪掉。

不需要训练，也不需要人脸识别库。AI 在看每个镜头时已经在回答"有没有清晰人脸"、
"主人公在不在"、"几个人同框"、"风景分值"，这里只是把这些答案变成硬规则。

两层防线：
  1. 生成时——把受保护的镜头 ID 显式列给模型，要求必须出现
  2. 生成后——程序校验，缺了就自动插回去（模型会忘，程序不会）
"""
from __future__ import annotations

from .common import log, read_json

# 规则：(判断函数, 原因, 优先级)  优先级高的在裁剪时后被放弃
RULES = [
    (lambda lg, s: lg.get("subject_present") and lg.get("faces_clear"), "主人公正脸", 100),
    (lambda lg, s: lg.get("people_together"), "关键人物同框", 95),
    (lambda lg, s: (lg.get("scenery_value") or 0) >= 8, "重要风景/地标", 80),
    (lambda lg, s: (lg.get("highlight") or 0) >= 9, "AI 评为极精彩", 70),
    (lambda lg, s: "ending" in (lg.get("story_roles") or []), "适合作结尾", 60),
]

MIN_QUALITY = 0.18          # 糊成一片的镜头，再重要也救不回来
MAX_SHARE = 0.65            # 受保护镜头最多占成片的六成，否则等于没得剪


def protected(project, shots: list[dict], target_seconds: float | None = None) -> dict[str, str]:
    """返回 {shot_id: 保护原因}。shots 需要带 log 字段（load_logged_shots 的输出）。"""
    hits: list[tuple[int, str, str, float]] = []   # (优先级, id, 原因, 时长)
    for s in shots:
        lg = s.get("log") or read_json(project.logs_dir / f"{s['id']}.json", {}) or {}
        if s.get("quality", 1) < MIN_QUALITY:
            continue
        for test, reason, prio in RULES:
            try:
                ok = test(lg, s)
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                best = lg.get("best_moment") or {}
                dur = max(1.5, float(best.get("end", 4)) - float(best.get("start", 0)))
                hits.append((prio, s["id"], reason, min(dur, s["duration"])))
                break

    hits.sort(key=lambda h: -h[0])
    # 保护太多等于没剪：超过上限就按优先级截断
    if target_seconds:
        cap, total, kept = MAX_SHARE * target_seconds, 0.0, []
        for prio, sid, reason, dur in hits:
            if total + dur > cap:
                continue
            total += dur
            kept.append((prio, sid, reason, dur))
        if len(kept) < len(hits):
            log(f"  受保护镜头超出上限，按优先级保留 {len(kept)}/{len(hits)} 个")
        hits = kept

    out = {sid: reason for _, sid, reason, _ in hits}
    if out:
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
        rows.append(f'{{"id":"{sid}","reason":"{reason}","sum":"{(lg.get("summary") or "")[:60]}"}}')
    return "[" + ",".join(rows) + "]"


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
        a = max(0.0, min(float(best.get("start", 0) or 0), s["duration"] - 1.5))
        b = min(s["duration"], max(a + 1.5, float(best.get("end", a + 4) or a + 4)))
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
