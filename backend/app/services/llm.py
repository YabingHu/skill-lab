"""OpenAI 兼容的 LLM 客户端（本地 vLLM / Ollama / 内部网关等）。"""
from __future__ import annotations

import json
import re

import httpx

from ..config import Config


class LLMError(Exception):
    pass


_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_FENCE_RE = re.compile(r"^\s*```[\w-]*\n(.*?)\n```\s*$", re.S)


def strip_reasoning(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def extract_json(text: str):
    text = strip_reasoning(text)
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise LLMError("LLM 没有返回 JSON")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start:end + 1])


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @property
    def conf(self) -> dict:
        return self.cfg["llm"]

    def chat(self, messages: list[dict], **kw) -> str:
        c = self.conf
        url = c["base_url"].rstrip("/") + "/chat/completions"
        body = {
            "model": c["model"],
            "messages": messages,
            "temperature": kw.get("temperature", c.get("temperature", 0.1)),
            "max_tokens": kw.get("max_tokens", c.get("max_tokens", 8192)),
            "stream": False,
            **(c.get("extra_body") or {}),
        }
        headers = {"Content-Type": "application/json", **(c.get("extra_headers") or {})}
        if c.get("api_key"):
            headers["Authorization"] = f"Bearer {c['api_key']}"
        try:
            with httpx.Client(timeout=c.get("timeout", 300), trust_env=False) as client:
                r = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"调用 LLM 失败（{url}）：{e}") from e
        if r.status_code >= 400:
            raise LLMError(f"LLM 返回 {r.status_code}：{r.text[:500]}")
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"无法解析 LLM 响应：{r.text[:500]}") from e
        return strip_reasoning(content)

    def ping(self) -> dict:
        try:
            out = self.chat([{"role": "user", "content": "只回复 OK"}], max_tokens=16)
            return {"ok": True, "reply": out, "model": self.conf["model"], "base_url": self.conf["base_url"]}
        except LLMError as e:
            return {"ok": False, "error": str(e), "model": self.conf["model"], "base_url": self.conf["base_url"]}
