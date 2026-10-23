"""拍摄脚本里的构图草图：把 AI 给的几个数字（主体在哪、多大、地平线多高、镜头怎么动）画成 SVG。

故意画得很简单——线条小人、方块房子、一条地平线、三分线和一个箭头。草图要说清楚的是
“怎么拍”，不是“那里长什么样”。没有 sketch 字段的镜头（旧脚本、自己加的镜头）按景别给一个默认构图。

输出的 SVG 用 currentColor 画线，放进网页后跟着文字颜色走；箭头用强调色。
"""
from __future__ import annotations

import math
from html import escape

KINDS = ("person", "couple", "group", "face", "landmark", "building", "mountain", "tree",
         "vehicle", "boat", "food", "hands", "object", "road", "sign")
MOVES = ("static", "pan-left", "pan-right", "tilt-up", "tilt-down", "push-in", "pull-out",
         "follow", "orbit", "reveal")
ANGLES = ("eye", "low", "high", "top")

MOVE_LABELS = {"static": "固定机位", "pan-left": "向左摇", "pan-right": "向右摇", "tilt-up": "向上摇",
               "tilt-down": "向下摇", "push-in": "慢慢推近", "pull-out": "慢慢拉远", "follow": "跟拍",
               "orbit": "环绕", "reveal": "升起揭示"}
ANGLE_LABELS = {"eye": "平视", "low": "低角度仰拍", "high": "高角度俯拍", "top": "正上方俯拍"}

ACCENT = "#e9a23b"

# 景别 → 默认构图（AI 没给 sketch 时用）
DEFAULTS = {
    "wide": {"subjects": [{"k": "landmark", "x": .68, "y": .42, "s": .5}, {"k": "couple", "x": .3, "y": .8, "s": .14}],
             "horizon": .66, "move": "static", "angle": "eye"},
    "medium": {"subjects": [{"k": "person", "x": .4, "y": .62, "s": .85}], "horizon": .45, "move": "static", "angle": "eye"},
    "close-up": {"subjects": [{"k": "face", "x": .5, "y": .55, "s": .9}], "horizon": None, "move": "static", "angle": "eye"},
    "detail": {"subjects": [{"k": "object", "x": .5, "y": .55, "s": .5}], "horizon": None, "move": "static", "angle": "eye"},
    "selfie": {"subjects": [{"k": "couple", "x": .5, "y": .66, "s": .75}], "horizon": .34, "move": "static", "angle": "eye"},
    "pov": {"subjects": [{"k": "road", "x": .5, "y": .7, "s": .6}], "horizon": .42, "move": "follow", "angle": "eye"},
    "drone": {"subjects": [{"k": "landmark", "x": .62, "y": .55, "s": .22}], "horizon": .18, "move": "reveal", "angle": "high"},
    "timelapse": {"subjects": [{"k": "building", "x": .35, "y": .62, "s": .3}], "horizon": .66, "move": "static", "angle": "eye"},
}


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(x) else max(lo, min(hi, x))


def normalize(sk, framing: str = "") -> dict:
    """把 AI 或界面给的 sketch 收拾成安全的格式；缺了就按景别给默认值。"""
    base = DEFAULTS.get((framing or "").strip().lower(), DEFAULTS["medium"])
    if not isinstance(sk, dict) or not sk.get("subjects"):
        sk = {**base, **(sk if isinstance(sk, dict) else {})}
        sk["subjects"] = base["subjects"]
    subjects = []
    for s in (sk.get("subjects") or [])[:3]:
        if not isinstance(s, dict):
            continue
        k = str(s.get("k") or s.get("kind") or "object").lower()
        subjects.append({"k": k if k in KINDS else "object",
                         "x": round(_num(s.get("x"), 0, 1, .5), 3),
                         "y": round(_num(s.get("y"), 0, 1, .55), 3),
                         "s": round(_num(s.get("s") or s.get("size"), .04, 1.2, .4), 3)})
    hz = sk.get("horizon")
    move = str(sk.get("move") or "static").lower()
    angle = str(sk.get("angle") or "eye").lower()
    return {"subjects": subjects or base["subjects"],
            "horizon": None if hz in (None, "", "null") else round(_num(hz, 0, 1, .5), 3),
            "move": move if move in MOVES else "static",
            "angle": angle if angle in ANGLES else "eye"}


# ---------------------------------------------------------------- 画各种主体
def _person(cx, cy, h, R, out):
    if h >= .5 * R:                                    # 半身 / 胸像
        r = h * .2
        hy = cy - h * .18
        out.append(f'<circle cx="{cx:.1f}" cy="{hy:.1f}" r="{r:.1f}"/>')
        w = h * .42
        out.append(f'<path d="M{cx - w:.1f} {cy + h * .5:.1f} Q{cx - w * .95:.1f} {hy + r * 1.6:.1f} {cx:.1f} {hy + r * 1.35:.1f}'
                   f' Q{cx + w * .95:.1f} {hy + r * 1.6:.1f} {cx + w:.1f} {cy + h * .5:.1f}"/>')
    else:                                                   # 全身小人
        top = cy - h / 2
        r = h * .1
        out.append(f'<circle cx="{cx:.1f}" cy="{top + r:.1f}" r="{r:.1f}"/>')
        out.append(f'<path d="M{cx:.1f} {top + 2 * r:.1f} V{top + h * .62:.1f} M{cx - h * .14:.1f} {top + h * .36:.1f} H{cx + h * .14:.1f}'
                   f' M{cx:.1f} {top + h * .62:.1f} L{cx - h * .1:.1f} {top + h:.1f} M{cx:.1f} {top + h * .62:.1f} L{cx + h * .1:.1f} {top + h:.1f}"/>')


def _shape(s: dict, W: float, H: float) -> list[str]:
    R = min(H, W * 1.25)          # 大小的参照：横屏就是画面高度；竖屏按宽度算，免得人比画面还宽
    k, cx, cy, h = s["k"], s["x"] * W, s["y"] * H, s["s"] * R
    out: list[str] = []
    if k == "person":
        _person(cx, cy, h, R, out)
    elif k in ("couple", "group"):
        n = 2 if k == "couple" else 3
        gap = h * (.5 if h >= .5 * R else .28)
        for i in range(n):
            _person(cx + (i - (n - 1) / 2) * gap, cy + (h * .04 if i % 2 else 0), h * (1 if i % 2 == 0 else .94), R, out)
    elif k == "face":
        r = h * .34
        out.append(f'<ellipse cx="{cx:.1f}" cy="{cy - h * .08:.1f}" rx="{r * .82:.1f}" ry="{r:.1f}"/>')
        out.append(f'<path d="M{cx - r * 1.5:.1f} {cy + h * .55:.1f} Q{cx - r * 1.3:.1f} {cy + r * .9:.1f} {cx:.1f} {cy + r * .85:.1f}'
                   f' Q{cx + r * 1.3:.1f} {cy + r * .9:.1f} {cx + r * 1.5:.1f} {cy + h * .55:.1f}"/>')
        out.append(f'<path d="M{cx - r * .32:.1f} {cy - h * .12:.1f} h{r * .12:.1f} M{cx + r * .2:.1f} {cy - h * .12:.1f} h{r * .12:.1f}"/>')
    elif k == "landmark":                                   # 塔 / 灯塔 / 纪念碑
        w = h * .16
        top, bot = cy - h / 2, cy + h / 2
        out.append(f'<path d="M{cx - w:.1f} {bot:.1f} L{cx - w * .6:.1f} {top + h * .2:.1f} H{cx + w * .6:.1f} L{cx + w:.1f} {bot:.1f} Z"/>')
        out.append(f'<rect x="{cx - w * .7:.1f}" y="{top + h * .08:.1f}" width="{w * 1.4:.1f}" height="{h * .12:.1f}"/>')
        out.append(f'<path d="M{cx - w * .8:.1f} {top + h * .08:.1f} L{cx:.1f} {top:.1f} L{cx + w * .8:.1f} {top + h * .08:.1f}"/>')
    elif k == "building":
        w = h * .9
        top, bot = cy - h / 2, cy + h / 2
        out.append(f'<path d="M{cx - w / 2:.1f} {bot:.1f} V{top + h * .3:.1f} L{cx:.1f} {top:.1f} L{cx + w / 2:.1f} {top + h * .3:.1f} V{bot:.1f} Z"/>')
        for i in (-1, 1):
            out.append(f'<rect x="{cx + i * w * .22 - w * .08:.1f}" y="{top + h * .45:.1f}" width="{w * .16:.1f}" height="{h * .16:.1f}"/>')
        out.append(f'<rect x="{cx - w * .09:.1f}" y="{bot - h * .3:.1f}" width="{w * .18:.1f}" height="{h * .3:.1f}"/>')
    elif k == "mountain":
        w = h * 1.6
        bot = cy + h / 2
        out.append(f'<path d="M{cx - w:.1f} {bot:.1f} L{cx - w * .3:.1f} {cy - h / 2:.1f} L{cx + w * .1:.1f} {cy - h * .1:.1f}'
                   f' L{cx + w * .45:.1f} {cy - h * .35:.1f} L{cx + w:.1f} {bot:.1f}"/>')
    elif k == "tree":
        out.append(f'<path d="M{cx:.1f} {cy + h / 2:.1f} V{cy + h * .1:.1f}"/>')
        out.append(f'<ellipse cx="{cx:.1f}" cy="{cy - h * .12:.1f}" rx="{h * .28:.1f}" ry="{h * .36:.1f}"/>')
    elif k in ("vehicle", "boat"):
        w = h * 2.2
        bot = cy + h * .3
        if k == "vehicle":
            out.append(f'<path d="M{cx - w / 2:.1f} {bot:.1f} V{cy - h * .05:.1f} L{cx - w * .25:.1f} {cy - h * .45:.1f} H{cx + w * .2:.1f}'
                       f' L{cx + w * .38:.1f} {cy - h * .05:.1f} H{cx + w / 2:.1f} V{bot:.1f} Z"/>')
            for i in (-1, 1):
                out.append(f'<circle cx="{cx + i * w * .28:.1f}" cy="{bot:.1f}" r="{h * .18:.1f}"/>')
        else:
            out.append(f'<path d="M{cx - w / 2:.1f} {cy:.1f} H{cx + w / 2:.1f} L{cx + w * .35:.1f} {bot:.1f} H{cx - w * .35:.1f} Z'
                       f' M{cx:.1f} {cy:.1f} V{cy - h * .7:.1f} L{cx + w * .25:.1f} {cy - h * .1:.1f}"/>')
    elif k == "food":
        w = h * .9
        out.append(f'<ellipse cx="{cx:.1f}" cy="{cy + h * .2:.1f}" rx="{w:.1f}" ry="{h * .22:.1f}"/>')
        out.append(f'<path d="M{cx - w * .55:.1f} {cy + h * .1:.1f} Q{cx:.1f} {cy - h * .45:.1f} {cx + w * .55:.1f} {cy + h * .1:.1f}"/>')
    elif k == "hands":
        for i in (-1, 1):
            hx = cx + i * h * .32
            out.append(f'<rect x="{hx - h * .14:.1f}" y="{cy - h * .05:.1f}" width="{h * .28:.1f}" height="{h * .4:.1f}" rx="{h * .1:.1f}"/>')
            for f in range(4):
                fx = hx - h * .1 + f * h * .068
                out.append(f'<path d="M{fx:.1f} {cy - h * .05:.1f} V{cy - h * .32 + abs(f - 1.5) * h * .05:.1f}"/>')
    elif k == "road":
        w = W * .5
        out.append(f'<path d="M{cx - w:.1f} {H:.1f} L{cx - W * .01:.1f} {cy - h / 2:.1f} M{cx + w:.1f} {H:.1f} L{cx + W * .01:.1f} {cy - h / 2:.1f}"/>')
        out.append(f'<path d="M{cx:.1f} {H:.1f} V{cy - h * .1:.1f}" stroke-dasharray="6 6"/>')
    elif k == "sign":
        w = h * .9
        out.append(f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h * .45:.1f}" rx="2"/>')
        out.append(f'<path d="M{cx:.1f} {cy - h * .05:.1f} V{cy + h / 2:.1f} M{cx - w * .3:.1f} {cy - h * .3:.1f} H{cx + w * .3:.1f}"/>')
    else:                                                   # object
        w = h * .9
        out.append(f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{h * .12:.1f}"/>')
    return out


def _arrow(x1, y1, x2, y2) -> str:
    a = math.atan2(y2 - y1, x2 - x1)
    L = 9
    p1 = (x2 - L * math.cos(a - .5), y2 - L * math.sin(a - .5))
    p2 = (x2 - L * math.cos(a + .5), y2 - L * math.sin(a + .5))
    return (f'<path d="M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f} M{p1[0]:.1f} {p1[1]:.1f} L{x2:.1f} {y2:.1f} '
            f'L{p2[0]:.1f} {p2[1]:.1f}"/>')


def _move(move: str, W: float, H: float, subj: dict | None) -> list[str]:
    m = 16
    if move == "pan-left":
        return [_arrow(W * .62, m, W * .38, m)]
    if move == "pan-right":
        return [_arrow(W * .38, m, W * .62, m)]
    if move in ("tilt-up", "reveal"):
        return [_arrow(W - m, H * .62, W - m, H * .38)]
    if move == "tilt-down":
        return [_arrow(W - m, H * .38, W - m, H * .62)]
    if move == "push-in":
        d = min(W, H) * .12
        return [_arrow(m, m, m + d, m + d), _arrow(W - m, m, W - m - d, m + d),
                _arrow(m, H - m, m + d, H - m - d), _arrow(W - m, H - m, W - m - d, H - m - d)]
    if move == "pull-out":
        d = min(W, H) * .12
        return [_arrow(m + d, m + d, m, m), _arrow(W - m - d, m + d, W - m, m),
                _arrow(m + d, H - m - d, m, H - m), _arrow(W - m - d, H - m - d, W - m, H - m)]
    if move == "follow" and subj:
        x, y = subj["x"] * W, min(H - m, subj["y"] * H + subj["s"] * H * .55)
        return [_arrow(x - W * .12, y, x + W * .12, y)]
    if move == "orbit":
        cx, cy = (subj["x"] * W, subj["y"] * H) if subj else (W / 2, H / 2)
        rx, ry = W * .18, H * .1
        return [f'<path d="M{cx - rx:.1f} {cy + ry * .2:.1f} A{rx:.1f} {ry:.1f} 0 1 0 {cx + rx * .7:.1f} {cy + ry * .9:.1f}"/>',
                _arrow(cx + rx * .4, cy + ry * 1.05, cx + rx * .72, cy + ry * .88)]
    return []



def sketch_svg(sk, framing: str = "", vertical: bool = False, label: str = "") -> str:
    """返回一张构图草图的 SVG（字符串）。sk 可以是 None，会按景别给默认构图。"""
    sk = normalize(sk, framing)
    W, H = (180.0, 320.0) if vertical else (320.0, 180.0)
    parts = [f'<svg class="sketch" viewBox="0 0 {W:.0f} {H:.0f}" xmlns="http://www.w3.org/2000/svg" role="img"'
             f' aria-label="{escape(label or "构图草图")}" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">',
             f'<rect x=".5" y=".5" width="{W - 1:.0f}" height="{H - 1:.0f}" rx="6" stroke-opacity=".35"/>',
             '<g stroke-opacity=".18" stroke-dasharray="3 5" stroke-width="1">'
             f'<path d="M{W / 3:.1f} 0V{H:.0f}M{2 * W / 3:.1f} 0V{H:.0f}M0 {H / 3:.1f}H{W:.0f}M0 {2 * H / 3:.1f}H{W:.0f}"/></g>']
    if sk["horizon"] is not None:
        y = sk["horizon"] * H
        parts.append(f'<path d="M0 {y:.1f} Q{W * .3:.1f} {y - 4:.1f} {W * .55:.1f} {y + 1:.1f} T{W:.0f} {y - 1:.1f}"'
                     ' stroke-opacity=".55" stroke-width="1.4"/>')
    big = sorted(sk["subjects"], key=lambda s: s["s"])       # 小的先画，大的压在上面
    parts.append('<g stroke-width="1.8">')
    for s in big:
        parts.extend(_shape(s, W, H))
    parts.append('</g>')
    arrows = _move(sk["move"], W, H, max(sk["subjects"], key=lambda s: s["s"]) if sk["subjects"] else None)
    if arrows:
        parts.append(f'<g stroke="{ACCENT}" stroke-width="2.2">' + "".join(arrows) + "</g>")
    parts.append("</svg>")
    return "".join(parts)


def describe(sk, framing: str = "") -> str:
    """一句话说明镜头运动和角度，比如“慢慢推近 · 低角度仰拍”（固定机位、平视不写）。"""
    sk = normalize(sk, framing)
    bits = []
    if sk["move"] != "static":
        bits.append(MOVE_LABELS[sk["move"]])
    if sk["angle"] != "eye":
        bits.append(ANGLE_LABELS[sk["angle"]])
    return " · ".join(bits)
