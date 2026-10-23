"""Model access. Two back-ends:

* ClaudeLLM — real, uses the Anthropic API (needs ANTHROPIC_API_KEY).
* MockLLM   — offline stand-in with simple rules, so the whole pipeline can be
              tested for free before spending any API credits.

Swap models with env vars:
  VLOGAI_DESCRIBE_MODEL  (bulk per-shot logging; cheap + fast model recommended)
  VLOGAI_STORY_MODEL     (one big call that writes the story; strongest model you like)
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

from . import config

class FatalError(RuntimeError):
    """重试也没用、剩下的镜头也不用再跑的错误（Key 不对、余额不足、模型名写错……）。
    任务面板只显示这句话，不附带 Python 报错堆栈。"""


class AuthError(FatalError):
    """API Key 不对：没必要重试，也没必要继续跑剩下的镜头。"""


class BillingError(FatalError):
    """账户余额不足（400 credit balance is too low）。"""


BILLING_URL = "https://platform.claude.com/settings/billing"


def _api_message(e) -> str:
    """从 anthropic 的异常里取出服务器给的那句话。"""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if err.get("message"):
            return str(err["message"])
    return str(e)


def fatal_from(e, model: str = ""):
    """把 anthropic 的异常翻成一句能看懂、知道下一步怎么做的话；不是致命错误就返回 None。"""
    try:
        import anthropic
    except ImportError:
        return None
    msg = _api_message(e)
    if isinstance(e, anthropic.AuthenticationError):
        h = key_hint()
        return AuthError(f"API Key 无效（401）。程序用的是 {h['hint']}（{h['length']} 位，来自 "
                         f"{h['source']}）。检查这个 Key 是否完整、是否已被删除；可以在设置页换一个 Key 再重试。")
    if isinstance(e, anthropic.BadRequestError) and "credit balance" in msg.lower():
        h = key_hint()
        return BillingError(f"API 余额不足。这个 Key（{h['hint']}）所在组织的额度用完了，到 {BILLING_URL} 充值后再点重试；"
                            "claude.ai 的订阅和 API 额度是分开的。已经做完的部分（比如看过的镜头）不会重复计费。")
    if isinstance(e, anthropic.PermissionDeniedError):
        return FatalError(f"没有权限（403）：{msg}。检查这个 Key 所在的组织 / 工作区是否能用这个模型。")
    if isinstance(e, anthropic.NotFoundError):
        return FatalError(f"找不到模型 {model}（404）。到设置页换一个看片 / 编故事模型再重试。")
    return None


def _read_dotenv() -> dict:
    """读项目根目录（当前工作目录）下的 .env，只认 KEY=VALUE 这种简单格式。"""
    f = Path.cwd() / ".env"
    out = {}
    if f.is_file():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip()
    return out


def _clean(key: str) -> str:
    # 去掉复制时常带进来的空格、换行、引号（包括中文引号）
    return (key or "").strip().strip("\"'“”‘’").strip()


def api_key() -> tuple[str, str]:
    """返回 (key, 来源)。项目里的 .env 优先于系统环境变量——这样不会被别的窗口/setx 留下的旧值干扰。"""
    env_file = _read_dotenv()
    if env_file.get("ANTHROPIC_API_KEY"):
        return _clean(env_file["ANTHROPIC_API_KEY"]), f"{Path.cwd() / '.env'}"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _clean(os.environ["ANTHROPIC_API_KEY"]), "环境变量 ANTHROPIC_API_KEY"
    return "", ""


def base_url() -> str:
    return config.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL", "")


def model_for(kind: str) -> str:
    """kind = describe / story。设置页里选过的优先，其次环境变量，最后默认值。"""
    key, env = f"{kind}_model", f"VLOGAI_{kind.upper()}_MODEL"
    v = config.get(key)
    if v != config.DEFAULTS[key]:
        return v
    return os.environ.get(env) or v


def make_client(key: str | None = None, max_retries: int = 4):
    import anthropic
    key = _clean(key) if key else api_key()[0]
    kw = {"api_key": key, "max_retries": max_retries}
    if config.get("base_url"):
        kw["base_url"] = config.get("base_url")
    return anthropic.Anthropic(**kw)


def test_key(key: str | None = None) -> dict:
    """设置页“测试连接”：列一下模型（不产生费用）。"""
    try:
        import anthropic
    except ImportError:
        return {"ok": False, "reason": "没有安装 anthropic：pip install anthropic"}
    if not (key or api_key()[0]):
        return {"ok": False, "reason": "还没有填 API Key"}
    try:
        client = make_client(key, max_retries=0)
        try:
            ids = [m.id for m in client.models.list(limit=50).data]
        except AttributeError:            # 很老的 SDK 没有 models.list
            client.messages.create(model=model_for("describe"), max_tokens=1,
                                   messages=[{"role": "user", "content": "hi"}])
            ids = []
        missing = [m for m in (model_for("describe"), model_for("story")) if ids and m not in ids]
        return {"ok": True, "models": ids, "missing": missing}
    except anthropic.AuthenticationError:
        return {"ok": False, "reason": "401：Key 无效。检查是否复制完整、是否已在 console 里删除"}
    except Exception as e:  # noqa: BLE001
        fatal = fatal_from(e, model_for("describe"))
        if fatal:
            return {"ok": False, "reason": str(fatal)}
        return {"ok": False, "reason": f"{e.__class__.__name__}: {e}"}


def key_hint() -> dict:
    """给界面和日志看的 Key 摘要（不暴露完整 Key）。"""
    key, src = api_key()
    if not key:
        return {"has_key": False, "hint": "", "source": "", "length": 0, "base_url": base_url()}
    return {"has_key": True, "hint": f"{key[:14]}…{key[-4:]}", "length": len(key), "source": src,
            "base_url": base_url(),
            "looks_ok": key.startswith("sk-ant-") and " " not in key}


DEFAULT_DESCRIBE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_STORY_MODEL = "claude-sonnet-5"


def extract_json(text: str):
    """Pull the first JSON object/array out of a model reply."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("模型没有返回 JSON")
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    return json.loads(text[start:end + 1])


class ClaudeLLM:
    name = "claude"

    def __init__(self):
        try:
            import anthropic
        except ImportError as e:
            raise SystemExit("请先 pip install anthropic") from e
        key, src = api_key()
        if not key:
            raise SystemExit("没有设置 ANTHROPIC_API_KEY。在项目根目录建一个 .env 文件写上 "
                             "ANTHROPIC_API_KEY=sk-ant-...，或者加 --mock 先用离线模式测试流程。")
        h = key_hint()
        print(f"[vlogai] 使用 API Key {h['hint']}（{h['length']} 位，来自 {src}）"
              + (f"，ANTHROPIC_BASE_URL={h['base_url']}" if h["base_url"] else ""))
        self._bad_request = anthropic.BadRequestError
        self.client = make_client(key)
        self.describe_model = model_for("describe")
        self.story_model = model_for("story")
        print(f"[vlogai] 模型：看片 {self.describe_model} · 编故事 {self.story_model}")
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def _call(self, model: str, system: str, content: list, max_tokens: int) -> str:
        for attempt in range(3):
            try:
                resp = self.client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": content}])
                self.usage["input_tokens"] += resp.usage.input_tokens
                self.usage["output_tokens"] += resp.usage.output_tokens
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            except Exception as e:  # noqa: BLE001
                fatal = fatal_from(e, model)
                if fatal:
                    raise fatal from e
                if attempt == 2 or isinstance(e, self._bad_request):   # 400：请求本身有问题，重试也一样
                    raise
                time.sleep(3 * (attempt + 1))
                print(f"[vlogai] API 重试 ({e.__class__.__name__}: {str(e)[:200]})")
        return ""

    def describe(self, system: str, prompt: str, image_paths: list[str]) -> dict:
        content = []
        for p in image_paths:
            data = base64.standard_b64encode(Path(p).read_bytes()).decode()
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        content.append({"type": "text", "text": prompt})
        return extract_json(self._call(self.describe_model, system, content, 900))

    def plan(self, system: str, prompt: str, max_tokens: int = 16000) -> dict:
        text = self._call(self.story_model, system, [{"type": "text", "text": prompt}], max_tokens)
        return extract_json(text)


class MockLLM:
    """Deterministic fake — lets you test scan → render → XML with zero API cost."""
    name = "mock"
    usage = {"input_tokens": 0, "output_tokens": 0}

    def describe(self, system: str, prompt: str, image_paths: list[str]) -> dict:
        meta = json.loads(prompt.split("<meta>")[1].split("</meta>")[0])
        q = meta.get("quality", 0.5)
        name = Path(meta["file"]).stem
        return {
            "summary": f"（离线模拟）{name} 的画面",
            "place": f"Day {meta['day']}",
            "people": [], "activity": "旅行片段", "mood": "平静",
            "shot_type": "wide" if meta["duration"] > 8 else "medium",
            "story_roles": ["b-roll"], "highlight": round(3 + 6 * q),
            "best_moment": {"start": 0.0, "end": min(meta["duration"], 4.0)},
            "keep_audio": bool(meta.get("speech")), "tags": [name],
            # 关键画面保护要用的几项（离线模拟：按文件名和画质给个确定的答案）
            "faces_clear": "selfie" in name.lower(), "subject_present": "selfie" in name.lower(),
            "people_together": bool(meta.get("speech")),
            "scenery_value": round(2 + 8 * q),
        }

    def plan(self, system: str, prompt: str, max_tokens: int = 16000) -> dict:
        data = json.loads(prompt.split("<shots_json>")[1].split("</shots_json>")[0])
        target = float(prompt.split("<target_seconds>")[1].split("</target_seconds>")[0])
        shots = sorted(data, key=lambda s: s["time"])
        good = [s for s in shots if s["q"] >= 0.25] or shots
        per = max(2.0, target / max(1, len(good)))
        timeline, total = [], 0.0
        for s in good:
            if total >= target:
                break
            ln = min(per, s["dur"])
            chap = f"Day {s['day']}"
            timeline.append({"shot_id": s["id"], "in": 0.0, "out": round(ln, 2), "chapter": chap,
                             "role": "moment", "why": "离线模拟：按时间顺序挑选清晰镜头",
                             "keep_audio": False, "text": chap if not timeline or
                             timeline[-1]["chapter"] != chap else ""})
            total += ln
        chapters = []
        for t in timeline:
            if not chapters or chapters[-1]["name"] != t["chapter"]:
                chapters.append({"name": t["chapter"], "purpose": "按天记录"})
        return {"title": "我的旅行 (mock)", "logline": "离线模拟生成的粗剪，用来测试流程",
                "chapters": chapters, "timeline": timeline}


def get_llm(mock: bool):
    return MockLLM() if mock else ClaudeLLM()
