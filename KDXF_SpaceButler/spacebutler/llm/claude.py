"""Claude cloud channel for the agent's structured planning calls.

本地 7B 一次规划要 75–145 秒，现场演示撑不住。本模块提供云端通道：
用 Anthropic 官方 SDK 调 Claude，接口与 :class:`spacebutler.llm.client.LlmClient`
的 ``chat_json`` 保持一致，因此规划器、目标编译器无需改动即可切换通道。

三个降低延迟的手段，按影响排序：
1. Fast Mode（``speed="fast"``）——同一个模型，输出吞吐最高 2.5×。
2. ``output_config.format`` 约束解码——直接产出合法 JSON，省掉重试往返。
3. ``effort`` 可调——本项目的规划是"按 schema 填空"的有界任务，
   不需要最高档推理深度；默认 medium，可用环境变量覆盖。

配置：
    SPACEBUTLER_CLOUD_ENABLED    设为 false 可关闭云通道
    ANTHROPIC_API_KEY            官方 SDK 标准变量
    SPACEBUTLER_CLOUD_MODEL      默认 claude-opus-5
    SPACEBUTLER_CLOUD_EFFORT     low | medium | high | xhigh | max（默认 medium）
    SPACEBUTLER_CLOUD_FAST       默认 true，设为 false 关闭 Fast Mode
    SPACEBUTLER_CLOUD_TIMEOUT    单次调用超时秒数，默认 25
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

from .client import JsonParseError, _extract_json_object, _validate_schema

_DEFAULT_MODEL = "claude-opus-5"
_DEFAULT_EFFORT = "medium"
_DEFAULT_TIMEOUT = 25.0
_FAST_MODE_BETA = "fast-mode-2026-02-01"
# Fast Mode 目前只在这两个模型上可用；其它模型必须走普通通道，否则请求会被拒绝。
_FAST_MODE_MODELS = {"claude-opus-5", "claude-opus-4-8"}
_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Add ``additionalProperties: false`` everywhere constrained decoding needs it.

    项目内的 schema 是给 llama.cpp 写的，没有这个字段；Claude 的约束解码要求
    每个 object 层级都显式禁止额外键。这里只补这一个字段，不改动
    ``required``——放宽必填项是业务决策，不能由适配层偷偷替代。
    """
    node = copy.deepcopy(schema)

    def walk(item: object) -> None:
        if isinstance(item, dict):
            if item.get("type") == "object":
                item.setdefault("additionalProperties", False)
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(node)
    return node


class ClaudeClient:
    """Cloud planning channel with the same ``chat_json`` contract as the local client."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        fast: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._model = model or _DEFAULT_MODEL
        requested_effort = (effort or _DEFAULT_EFFORT).lower()
        self._effort = requested_effort if requested_effort in _VALID_EFFORT else _DEFAULT_EFFORT
        self._fast = bool(fast) and self._model in _FAST_MODE_MODELS
        self._timeout = timeout_seconds or _DEFAULT_TIMEOUT
        self.last_retry_count = 0
        self.last_error_stage: str | None = None
        self._client: Any = None
        self._init_error: str | None = None
        try:
            import anthropic
        except ImportError:  # pragma: no cover - 依赖缺失时退回本地通道
            self._init_error = "anthropic SDK 未安装（pip install anthropic）"
            return
        key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""
        if not key.strip():
            self._init_error = "未配置 ANTHROPIC_API_KEY"
            return
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=key, timeout=self._timeout, max_retries=1)

    @classmethod
    def from_env(cls) -> "ClaudeClient | None":
        if os.environ.get("SPACEBUTLER_CLOUD_ENABLED", "true").strip().lower() in {"false", "0", "no"}:
            return None
        timeout = os.environ.get("SPACEBUTLER_CLOUD_TIMEOUT")
        fast = os.environ.get("SPACEBUTLER_CLOUD_FAST", "true").strip().lower() not in {"false", "0", "no"}
        return cls(
            model=os.environ.get("SPACEBUTLER_CLOUD_MODEL") or None,
            effort=os.environ.get("SPACEBUTLER_CLOUD_EFFORT") or None,
            fast=fast,
            timeout_seconds=float(timeout) if timeout else None,
        )

    @property
    def channel(self) -> str:
        return "cloud"

    @property
    def model(self) -> str:
        return self._model

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def available(self, force_refresh: bool = False) -> bool:
        """Configured-and-constructible. 真实可用性由首次调用结果决定。"""
        return self._client is not None

    def context_window(self) -> int | None:
        """Claude 的上下文远超规划 prompt 所需，返回 None 表示无需本地容量检查。"""
        return None

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any] | None:
        """One structured call. Returns None on failure so the router can fall back."""
        self.last_retry_count = 0
        self.last_error_stage = None
        if self._client is None:
            self.last_error_stage = "not_configured"
            return None
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": self._effort},
        }
        if schema is not None:
            request["output_config"]["format"] = {
                "type": "json_schema",
                "schema": _strict_schema(schema),
            }
        try:
            if self._fast:
                response = self._client.beta.messages.create(
                    speed="fast", betas=[_FAST_MODE_BETA], **request)
            else:
                response = self._client.messages.create(**request)
        except Exception as error:  # SDK 抛型很多，任何失败都应让路由器回落本地
            self.last_error_stage = _error_stage(self._anthropic, error)
            print(f"[llm/cloud] call failed ({self.last_error_stage}): {str(error)[:160]}", flush=True)
            return None
        # 安全分类器可能拒答；此时 content 不含可用文本，必须先看 stop_reason。
        if getattr(response, "stop_reason", None) == "refusal":
            self.last_error_stage = "refusal"
            print("[llm/cloud] request refused by safety classifier", flush=True)
            return None
        text = next((block.text for block in response.content if block.type == "text"), "")
        parsed = _extract_json_object(text)
        if parsed is None:
            self.last_error_stage = "json_parse_error"
            return None
        if schema is not None:
            stage = _validate_schema(parsed, schema)
            if stage is not None:
                self.last_error_stage = stage
                print(f"[llm/cloud] schema rejected ({stage}): "
                      f"{json.dumps(parsed, ensure_ascii=False)[:120]}", flush=True)
                return None
        return parsed


def _error_stage(anthropic_module: Any, error: Exception) -> str:
    """Classify an SDK failure so the timeline can distinguish quota from network."""
    for name, stage in (
        ("RateLimitError", "rate_limited"),
        ("AuthenticationError", "auth_error"),
        ("PermissionDeniedError", "auth_error"),
        ("NotFoundError", "model_not_found"),
        ("APITimeoutError", "timeout"),
        ("APIConnectionError", "network_error"),
        ("BadRequestError", "bad_request"),
    ):
        cls = getattr(anthropic_module, name, None)
        if cls is not None and isinstance(error, cls):
            return stage
    return "http_error"


__all__ = ["ClaudeClient", "JsonParseError"]
