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
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("没有设置 ANTHROPIC_API_KEY。先 export ANTHROPIC_API_KEY=sk-ant-...，"
                             "或者加 --mock 先用离线模式测试流程。")
        self.client = anthropic.Anthropic(max_retries=4)
        self.describe_model = os.environ.get("VLOGAI_DESCRIBE_MODEL", DEFAULT_DESCRIBE_MODEL)
        self.story_model = os.environ.get("VLOGAI_STORY_MODEL", DEFAULT_STORY_MODEL)
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
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))
                print(f"[vlogai] API 重试 ({e.__class__.__name__})")
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
