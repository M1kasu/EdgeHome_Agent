"""Vendor-neutral LLM access for the agent layer.

兼容任何 OpenAI-compatible 服务。若服务端支持 JSON Schema 约束解码
（llama.cpp 的 response_format.json_schema，已通过 /props 与实测探针检测），
则启用 grammar 约束生成——这是小模型结构化输出成功率的关键优化；
否则退回严格 prompt + 多级解析 + 单次 repair retry。

配置（全部可选）：
    SPACEBUTLER_LLM_BASE_URL  默认探测本机 llama.cpp
    SPACEBUTLER_LLM_MODEL
    SPACEBUTLER_LLM_API_KEY
    SPACEBUTLER_LLM_TEMPERATURE  默认 0.1（结构化输出低温优先，benchmark 实测选择）
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

_DEFAULT_TEMPERATURE = 0.1


class JsonParseError(Exception):
    """解析失败并携带阶段分类，供失败分布统计。"""

    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(f"{stage}: {detail}")
        self.stage = stage  # json_parse_error | schema_error | missing_field | invalid_enum | http_error


class LlmClient:
    def __init__(
        self,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 40.0,
        temperature: float | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._temperature = temperature if temperature is not None else _DEFAULT_TEMPERATURE
        self._available_until = 0.0
        self._available = False
        self._structured_support: bool | None = None  # None = 未探测
        self.last_retry_count = 0
        self.last_error_stage: str | None = None

    @classmethod
    def from_env(cls) -> "LlmClient | None":
        base_url = (
            os.environ.get("SPACEBUTLER_LLM_BASE_URL")
            or os.environ.get("EDGE_LLM_URL")
            or _default_local_url()
        )
        if not base_url:
            return None
        temperature = os.environ.get("SPACEBUTLER_LLM_TEMPERATURE")
        timeout = os.environ.get("SPACEBUTLER_LLM_TIMEOUT")
        return cls(
            base_url=base_url,
            model=os.environ.get("SPACEBUTLER_LLM_MODEL") or None,
            api_key=os.environ.get("SPACEBUTLER_LLM_API_KEY") or None,
            timeout_seconds=float(timeout) if timeout else 40.0,
            temperature=float(temperature) if temperature else None,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    def available(self, force_refresh: bool = False) -> bool:
        if not force_refresh and time.monotonic() < self._available_until:
            return self._available
        self._available = self._probe()
        self._available_until = time.monotonic() + 60.0
        return self._available

    def _probe(self) -> bool:
        try:
            with urlopen(f"{self._base_url}/health", timeout=3) as response:
                if response.status == 200:
                    return True
        except (OSError, HTTPError, URLError):
            pass
        try:
            with urlopen(f"{self._base_url}/v1/models", timeout=3) as response:
                return response.status == 200
        except (OSError, HTTPError, URLError):
            return False

    # ---- structured output -------------------------------------------
    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any] | None:
        """一次结构化调用：schema 约束（若支持）+ 多级解析 + 单次修复重试。"""
        self.last_retry_count = 0
        self.last_error_stage = None
        use_schema = schema is not None and self._structured_output_supported()
        last_error: JsonParseError | None = None
        for attempt in range(max(max_attempts, 1)):
            self.last_retry_count = attempt
            prompt_user = user if attempt == 0 else (
                f"{user}\n\n上一次输出不是合法 JSON 或不符合 schema。请重新只输出一个符合要求的 JSON 对象。"
            )
            try:
                content = self._completion(system, prompt_user, schema if use_schema else None)
                parsed = _extract_json_object(content)
                if parsed is None:
                    raise JsonParseError("json_parse_error", content[:120])
                if schema is not None:
                    stage = _validate_schema(parsed, schema)
                    if stage is not None:
                        raise JsonParseError(stage, json.dumps(parsed, ensure_ascii=False)[:120])
                return parsed
            except JsonParseError as error:
                last_error = error
                self.last_error_stage = error.stage
            except (OSError, HTTPError, URLError, KeyError, TypeError, ValueError) as error:
                last_error = JsonParseError("http_error", str(error)[:120])
                self.last_error_stage = last_error.stage
        if last_error is not None:
            print(f"[llm] structured call failed ({last_error.stage}): {last_error}", flush=True)
        return None

    def _structured_output_supported(self) -> bool:
        if self._structured_support is not None:
            return self._structured_support
        # 实测探测一次（小请求）；llama.cpp 对不支持的 response_format 会报错。
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        try:
            content = self._completion("只输出 JSON。", '输出 {"ok": true}', schema)
            self._structured_support = isinstance(_extract_json_object(content), dict)
        except (OSError, HTTPError, URLError, ValueError):
            self._structured_support = False
        return self._structured_support

    def _completion(self, system: str, user: str, schema: dict[str, Any] | None) -> str:
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
        }
        if self._model:
            payload["model"] = self._model
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "spacebutler_structured", "schema": schema},
            }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = Request(
            f"{self._base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"])


def _validate_schema(data: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """极简 schema 校验：required 字段 + 顶层类型 + 枚举。返回错误分类或 None。"""
    for key in schema.get("required", []) or []:
        if key not in data:
            return "missing_field"
    properties = schema.get("properties", {}) or {}
    for key, rule in properties.items():
        if key not in data:
            continue
        expected = rule.get("type")
        value = data[key]
        if expected == "object" and not isinstance(value, dict):
            return "schema_error"
        if expected == "array" and not isinstance(value, list):
            return "schema_error"
        if expected == "boolean" and not isinstance(value, bool):
            return "schema_error"
        if expected in {"number", "integer"} and not isinstance(value, (int, float)):
            return "schema_error"
        if expected == "string" and not isinstance(value, str):
            return "schema_error"
        enum = rule.get("enum")
        if enum and value not in enum:
            return "invalid_enum"
    return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """解析链：direct -> fenced -> balanced-object（不做宽松正则猜测）。"""
    cleaned = text.strip()
    try:
        decoded = json.loads(cleaned)
        return decoded if isinstance(decoded, dict) else None
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fenced:
        try:
            decoded = json.loads(fenced.group(1).strip())
            return decoded if isinstance(decoded, dict) else None
        except json.JSONDecodeError:
            pass
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
    for candidate in ("http://127.0.0.1:12881", "http://127.0.0.1:8080"):
        try:
            with urlopen(f"{candidate}/health", timeout=1.5) as response:
                if response.status == 200:
                    return candidate
        except (OSError, HTTPError, URLError):
            continue
    return None
