"""全局设置：界面“设置”页里调的参数都存在这里。

保存在项目根目录（启动 vlogai 的当前目录）的 vlogai_settings.json；API Key 单独存在 .env。
各步骤每次运行时现读，所以改完设置不用重启（端口和项目位置除外）。
"""
from __future__ import annotations

import json
from pathlib import Path

SETTINGS_FILE = "vlogai_settings.json"

DEFAULTS: dict = {
    # API 与模型
    "describe_model": "claude-haiku-4-5-20251001",
    "story_model": "claude-sonnet-5",
    "lang": "zh",
    "base_url": "",
    # 抽帧与切镜头
    "frame_every_sec": 4.0,       # 大约每隔几秒抽一帧
    "max_frames_per_shot": 4,
    "frame_width": 512,           # 发给 AI 的静帧宽度（像素）
    "long_clip_sec": 20.0,        # 长于这个时长的片段才做切镜头
    "scene_sensitivity": "mid",   # low / mid / high
    "bad_quality": 0.25,          # 画质分低于它算废片
    "min_clip_sec": 0.5,          # 短于这个时长的文件直接忽略（误触录的一下）
    "min_scene_sec": 2.0,         # 切镜头时，每个镜头至少多长
    "use_proxy": True,            # 长片段先转低分辨率代理再切镜头
    "proxy_width": 320,           # 代理宽度（像素）
    "proxy_fps": 10,              # 代理每秒几帧
    "hwaccel": True,              # 转代理时尝试用显卡解码
    "sharp_ref": 300.0,           # 清晰度达到多少算满分（512px 静帧的拉普拉斯方差）
    "exposure_target": 120,       # 理想的平均亮度（0-255）
    # 关键画面保护
    "protect_enabled": True,
    "protect_faces": True,        # 主人公正脸
    "protect_together": True,     # 关键人物同框
    "protect_scenery": 8,         # 风景分 ≥ 它就保护（0 = 不按风景保护）
    "protect_highlight": 9,       # 精彩度 ≥ 它就保护（0 = 不按精彩度保护）
    "protect_ending": True,       # 适合作结尾的镜头
    "protect_max_share": 0.65,    # 受保护镜头最多占成片多少
    "protect_min_quality": 0.18,  # 画质低于它的镜头再重要也不保护
    # 剪辑节奏
    "longform_min": 6.0,          # 成片长于几分钟按“长片”节奏剪
    "short_seg_sec": 3.5,         # 短片：平均每段几秒（决定让 AI 剪多少段）
    "long_seg_sec": 9.0,          # 长片：平均每段几秒
    "beat_snap_range": 0.4,       # 对齐节拍时，每段最多拉长 / 缩短多少
    "plan_shots_per_day": 0,      # 拍摄脚本每天几个镜头（0 = 按成片长度自动）
    # 语音转写
    "whisper": False,
    "whisper_model": "small",
    # 预览渲染
    "render_hd": False,
    "music_volume": 0.5,
    "beat_snap": True,
    "duck_music": True,
    "burn_captions": True,
    "font": "",
    "render_fps": 30,
    "render_crf": 22,             # 画质：越小越清晰、文件越大
    "caption_size": 6.5,          # 字幕字号，占画面高度的百分比
    "caption_pos": 78,            # 字幕位置，从画面顶部算起的百分比
    "music_fade_in": 1.5,
    "music_fade_out": 3.0,
    "duck_ratio": 12,             # 有人说话时音乐压低的力度（压缩比）
    # 性能与高级
    "workers": 4,
    "render_workers": 2,          # 渲染预览时同时转几段
    "api_retries": 2,             # API 临时出错（超时、过载）时重试几次
    "port": 8765,
    "projects_dir": "projects",
    "log_level": "INFO",          # DEBUG 会额外记录每一个接口请求
    "allowed_origins": "",        # 额外允许调用本地后端的网页，逗号分隔，例如 https://my-app.vercel.app
}

SCENE_THRESHOLDS = {"low": 35.0, "mid": 27.0, "high": 20.0}   # PySceneDetect ContentDetector

# 每项的取值范围：(类型, 最小, 最大) 或 允许值集合
_RULES: dict = {
    "frame_every_sec": (float, 0.5, 30), "max_frames_per_shot": (int, 1, 12),
    "frame_width": (int, 256, 1024), "long_clip_sec": (float, 3, 600),
    "bad_quality": (float, 0, 0.9), "music_volume": (float, 0, 1.5),
    "workers": (int, 1, 32), "port": (int, 1024, 65535),
    "scene_sensitivity": {"low", "mid", "high"}, "lang": {"zh", "en"},
    "whisper_model": {"tiny", "base", "small", "medium", "large-v3"},
    "log_level": {"DEBUG", "INFO", "WARNING"},
    "min_clip_sec": (float, 0, 10), "min_scene_sec": (float, 0.5, 30),
    "proxy_width": (int, 160, 960), "proxy_fps": (int, 2, 30),
    "sharp_ref": (float, 50, 2000), "exposure_target": (int, 40, 220),
    "protect_scenery": (int, 0, 10), "protect_highlight": (int, 0, 10),
    "protect_max_share": (float, 0, 1), "protect_min_quality": (float, 0, 0.9),
    "longform_min": (float, 1, 120), "short_seg_sec": (float, 1, 20), "long_seg_sec": (float, 2, 60),
    "beat_snap_range": (float, 0, 0.9), "plan_shots_per_day": (int, 0, 40),
    "render_fps": {24, 25, 30, 50, 60}, "render_crf": (int, 14, 35),
    "caption_size": (float, 2, 15), "caption_pos": (int, 5, 95),
    "music_fade_in": (float, 0, 10), "music_fade_out": (float, 0, 15), "duck_ratio": (int, 1, 20),
    "render_workers": (int, 1, 16), "api_retries": (int, 0, 6),
}


def path() -> Path:
    return Path.cwd() / SETTINGS_FILE


def load() -> dict:
    data = dict(DEFAULTS)
    f = path()
    if f.is_file():
        try:
            saved = json.loads(f.read_text(encoding="utf-8"))
            data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except (ValueError, OSError):
            pass
    return data


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def validate(patch: dict) -> dict:
    """只保留认识的键，并检查类型和范围。出错抛 ValueError（界面会显示）。"""
    clean = {}
    for k, v in patch.items():
        if k not in DEFAULTS:
            continue
        rule = _RULES.get(k)
        default = DEFAULTS[k]
        if isinstance(rule, set):
            if all(isinstance(x, int) for x in rule):
                try:
                    v = int(float(v))
                except (TypeError, ValueError):
                    pass
            if v not in rule:
                raise ValueError(f"{k} 只能是 {'/'.join(str(x) for x in sorted(rule))}")
        elif isinstance(rule, tuple):
            typ, lo, hi = rule
            try:
                v = typ(v)
            except (TypeError, ValueError) as e:
                raise ValueError(f"{k} 需要是数字") from e
            if not lo <= v <= hi:
                raise ValueError(f"{k} 要在 {lo}–{hi} 之间")
        elif isinstance(default, bool):
            v = bool(v)
        else:
            v = str(v or "").strip()
        clean[k] = v
    if clean.get("font") and not Path(clean["font"]).expanduser().is_file():
        raise ValueError(f"找不到字体文件：{clean['font']}")
    return clean


def save(patch: dict) -> dict:
    data = load()
    data.update(validate(patch))
    changed = {k: v for k, v in data.items() if v != DEFAULTS[k]}   # 只存和默认值不同的
    path().write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def reset() -> dict:
    if path().is_file():
        path().unlink()
    return dict(DEFAULTS)


def write_env_key(key: str) -> None:
    """把 API Key 写进 .env（替换已有那一行，其他行保留）。"""
    key = key.strip().strip("\"'“”‘’").strip()
    f = Path.cwd() / ".env"
    lines = f.read_text(encoding="utf-8-sig").splitlines() if f.is_file() else []
    out, done = [], False
    for line in lines:
        name = line.split("=", 1)[0].strip().removeprefix("export ").strip()
        if name == "ANTHROPIC_API_KEY":
            if key and not done:
                out.append(f"ANTHROPIC_API_KEY={key}")
                done = True
            continue
        out.append(line)
    if key and not done:
        out.append(f"ANTHROPIC_API_KEY={key}")
    f.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
