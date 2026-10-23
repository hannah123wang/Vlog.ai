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
    # 性能与高级
    "workers": 4,
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
            if v not in rule:
                raise ValueError(f"{k} 只能是 {'/'.join(sorted(rule))}")
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
