"""Vendor-neutral LLM access for the agent layer.

兼容任何 OpenAI-compatible 服务。若服务端支持 JSON Schema 约束解码
（llama.cpp 的 response_format.json_schema，已通过 /props 与实测探针检测），
则启用 grammar 约束生成——这是小模型结构化输出成功率的关键优化；
否则退回严格 prompt + 多级解析 + 单次 repair retry。

配置（全部可选）：
    SPACEBUTLER_LLM_BASE_URL  本地 llama.cpp 或 OpenAI 兼容 API 地址
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
from urllib.parse import urlparse
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

    @property
    def model(self) -> str:
        return self._model or ""

    @property
    def provider_label(self) -> str:
        host = (urlparse(self._base_url).hostname or "").lower()
        if host == "api.deepseek.com":
            return "DeepSeek"
        if host in {"127.0.0.1", "localhost", "::1"}:
            return "本地边缘模型"
        return host or "OpenAI 兼容 API"

    @property
    def is_remote(self) -> bool:
        host = (urlparse(self._base_url).hostname or "").lower()
        return bool(host and host not in {"127.0.0.1", "localhost", "::1"})

    @property
    def channels(self) -> list[dict[str, object]]:
        """单通道也暴露同一份可观测信息，避免页面把云 API 标成本地模型。"""
        return [{
            "channel": "cloud" if self.is_remote else "local",
            "label": self.provider_label,
            "model": self.model,
            "ready": self.available(),
        }]

    def available(self, force_refresh: bool = False) -> bool:
        if not force_refresh and time.monotonic() < self._available_until:
            return self._available
        self._available = self._probe()
        self._available_until = time.monotonic() + 60.0
        return self._available

    def _probe(self) -> bool:
        try:
            with urlopen(Request(f"{self._base_url}/health", headers=self._headers()), timeout=3) as response:
                if response.status == 200:
                    return True
        except (OSError, HTTPError, URLError):
            pass
        for endpoint in _candidate_endpoints(self._base_url, "models"):
            try:
                with urlopen(Request(endpoint, headers=self._headers()), timeout=3) as response:
                    if response.status == 200:
                        return True
            except (OSError, HTTPError, URLError):
                continue
        return False

    def context_window(self) -> int | None:
        """Per-slot context size, or None when the server does not report it.

        `/health` 和 `/v1/models` 通过不代表能跑规划：llama.cpp 会把 `-c` 均分给各
        slot，槽位上下文装不下带设备清单的 prompt 时结构化调用直接 500，规划会静默
        退回确定性回退。就绪自检需要能提前发现这种情况。
        """
        try:
            with urlopen(Request(f"{self._base_url}/props", headers=self._headers()), timeout=3) as response:
                if response.status != 200:
                    return None
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, HTTPError, URLError, ValueError):
            return None
        settings = payload.get("default_generation_settings")
        value = settings.get("n_ctx") if isinstance(settings, dict) else None
        return int(value) if isinstance(value, (int, float)) and value > 0 else None

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
            except HTTPError as error:
                stage = "auth_error" if error.code in {401, 403} else (
                    "model_not_found" if error.code == 404 else "http_error"
                )
                last_error = JsonParseError(stage, f"HTTP {error.code}")
                self.last_error_stage = last_error.stage
            except (OSError, URLError, KeyError, TypeError, ValueError) as error:
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
        if self.provider_label == "DeepSeek":
            # V4 Flash 默认启用深度思考；家居控制是低延迟结构化任务，显式关闭。
            payload["thinking"] = {"type": "disabled"}
        if schema is not None:
            if self.provider_label == "DeepSeek":
                # DeepSeek 支持 JSON Output；业务 schema 仍由本地校验。
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "spacebutler_structured", "schema": schema},
                }
        request = Request(
            _preferred_endpoint(self._base_url, "chat/completions"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"])

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


def _validate_schema(data: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """极简 schema 校验：required 字段 + 类型 + 枚举，含数组元素递归。

    可选字段给 null 等同于未提供：模型用 null 表达"这一维没有值"是合理输出，
    按类型错误拒掉整次编译会让本来正确的目标失败。required 字段仍不接受 null。
    """
    for key in schema.get("required", []) or []:
        if key not in data or data[key] is None:
            return "missing_field"
    properties = schema.get("properties", {}) or {}
    for key, rule in properties.items():
        if key not in data:
            continue
        value = data[key]
        if value is None:
            continue
        stage = _validate_value(value, rule)
        if stage is not None:
            return stage
    return None


def _validate_value(value: Any, rule: dict[str, Any]) -> str | None:
    """校验单个值：类型、枚举，数组逐元素、对象逐属性递归。"""
    expected = rule.get("type")
    if expected == "object" and not isinstance(value, dict):
        return "schema_error"
    if expected == "array" and not isinstance(value, list):
        return "schema_error"
    if expected == "boolean" and not isinstance(value, bool):
        return "schema_error"
    if expected in {"number", "integer"} and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        return "schema_error"
    if expected == "string" and not isinstance(value, str):
        return "schema_error"
    enum = rule.get("enum")
    if enum and value not in enum:
        return "invalid_enum"
    if expected == "array":
        item_rule = rule.get("items")
        if isinstance(item_rule, dict):
            for item in value:
                stage = _validate_value(item, item_rule)
                if stage is not None:
                    return stage
    if expected == "object" and isinstance(value, dict):
        return _validate_schema(value, rule)
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


def _preferred_endpoint(base_url: str, resource: str) -> str:
    """Build an endpoint for local llama.cpp and official DeepSeek URLs.

    llama.cpp commonly exposes ``/v1/*`` while DeepSeek's documented base URL
    exposes ``/chat/completions`` and ``/models`` directly. A base URL already
    ending in ``/v1`` is also accepted for any OpenAI-compatible provider.
    """
    base = base_url.rstrip("/")
    host = (urlparse(base).hostname or "").lower()
    if base.endswith("/v1") or host == "api.deepseek.com":
        return f"{base}/{resource}"
    return f"{base}/v1/{resource}"


def _candidate_endpoints(base_url: str, resource: str) -> tuple[str, ...]:
    preferred = _preferred_endpoint(base_url, resource)
    base = base_url.rstrip("/")
    alternatives = (preferred, f"{base}/v1/{resource}", f"{base}/{resource}")
    return tuple(dict.fromkeys(alternatives))
