"""Vendor-neutral LLM access for the agent layer.

兼容任何 OpenAI-compatible 服务（llama.cpp server / Qwen / OpenAI），
通过环境变量配置：

    SPACEBUTLER_LLM_BASE_URL  e.g. http://127.0.0.1:12881
    SPACEBUTLER_LLM_MODEL     e.g. Home-Llama-3.2-3B (可留空用服务器默认)
    SPACEBUTLER_LLM_API_KEY   可选

未配置时回落到既有 EDGE_LLM_URL 通道；全部不可用时 planner 如实进入
fallback 模式（UI 会真实显示 planner_mode=fallback）。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class LlmClient:
    def __init__(
        self,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._available_until = 0.0
        self._available = False

    @classmethod
    def from_env(cls) -> "LlmClient | None":
        base_url = (
            os.environ.get("SPACEBUTLER_LLM_BASE_URL")
            or os.environ.get("EDGE_LLM_URL")
            or _default_local_url()
        )
        if not base_url:
            return None
        return cls(
            base_url=base_url,
            model=os.environ.get("SPACEBUTLER_LLM_MODEL") or None,
            api_key=os.environ.get("SPACEBUTLER_LLM_API_KEY") or None,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def available(self, force_refresh: bool = False) -> bool:
        """Cheap health probe with a 60s cache so runtime ticks stay cheap."""
        if not force_refresh and time.monotonic() < self._available_until:
            return self._available
        self._available = self._probe()
        self._available_until = time.monotonic() + 60.0
        return self._available

    def _probe(self) -> bool:
        try:
            with urlopen(f"{self._base_url}/health", timeout=3) as response:
                return response.status == 200
        except (OSError, HTTPError, URLError):
            pass
        try:
            with urlopen(f"{self._base_url}/v1/models", timeout=3) as response:
                return response.status == 200
        except (OSError, HTTPError, URLError):
            return False

    def chat_json(self, system: str, user: str, max_attempts: int = 2) -> dict[str, Any] | None:
        """One chat completion, parsed as a JSON object. None on any failure."""
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        if self._model:
            payload["model"] = self._model
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        for _ in range(max(max_attempts, 1)):
            try:
                request = Request(
                    f"{self._base_url}/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=self._timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                return _extract_json_object(str(content))
            except (OSError, HTTPError, URLError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first balanced JSON object; tolerate code fences."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        character = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                try:
                    decoded = json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return decoded if isinstance(decoded, dict) else None
    return None


def _default_local_url() -> str | None:
    """Fallback chain: demo stack llama.cpp, then nothing."""
    for candidate in ("http://127.0.0.1:12881", "http://127.0.0.1:8080"):
        try:
            with urlopen(f"{candidate}/health", timeout=1.5) as response:
                if response.status == 200:
                    return candidate
        except (OSError, HTTPError, URLError):
            continue
    return None
