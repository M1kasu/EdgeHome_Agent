"""Dual-channel LLM routing: cloud first for speed, local edge as the fallback.

为什么需要两条通道：
- 云端（Claude）快，现场演示的等待时间从 75–145 秒降到个位数秒级；
- 本地（llama.cpp）在断网、限流或没配 Key 时仍能完成规划，保住"边缘可用"这条底线。

路由规则很简单，也刻意保持简单——录制现场最不需要的就是难以解释的调度行为：
    云端可用 → 用云端；失败/超时/被拒 → 立刻回落本地；两边都不行 → 返回 None。

返回 None 时，上层规划器按既有逻辑零命令安全退出，绝不退回固定脚本。
路由器记录每次调用实际走了哪条通道（``last_channel``），供工作台展示与自检使用。
"""

from __future__ import annotations

import threading
import time
from typing import Any


class DualChannelRouter:
    """Route one structured call to the fastest healthy channel.

    对外暴露的接口与单通道 ``LlmClient`` 完全一致，因此
    :class:`spacebutler.agent.runtime.AgentRuntime` 无需感知通道数量。
    """

    def __init__(
        self,
        cloud: Any | None = None,
        local: Any | None = None,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._cloud = cloud if (cloud is not None and cloud.available()) else None
        self._local = local
        self._cooldown = cooldown_seconds
        self._lock = threading.Lock()
        # 云端连续失败后短暂静默，避免每次规划都先白等一个超时。
        self._cloud_muted_until = 0.0
        self.last_channel: str | None = None
        self.last_error_stage: str | None = None
        self.last_retry_count = 0
        self.last_latency_seconds: float | None = None
        self.cloud_failures = 0

    @property
    def base_url(self) -> str:
        """Keep the attribute the local client exposed; used only for diagnostics."""
        return getattr(self._local, "base_url", "") if self._local is not None else ""

    @property
    def channels(self) -> list[dict[str, object]]:
        """Describe both channels for the workbench and the readiness self-check."""
        described: list[dict[str, object]] = []
        if self._cloud is not None:
            described.append({
                "channel": "cloud",
                "label": "云端 Claude",
                "model": getattr(self._cloud, "model", ""),
                "ready": self._cloud_ready(),
                "muted_seconds": max(0.0, round(self._cloud_muted_until - time.monotonic(), 1)),
                "failures": self.cloud_failures,
            })
        if self._local is not None:
            described.append({
                "channel": "local",
                "label": "本地边缘模型",
                "model": getattr(self._local, "_model", "") or "",
                "ready": bool(self._local.available()),
                "context_window": self._local.context_window(),
            })
        return described

    def _cloud_ready(self) -> bool:
        return self._cloud is not None and time.monotonic() >= self._cloud_muted_until

    def available(self, force_refresh: bool = False) -> bool:
        """Ready when either channel can serve a planning call."""
        if self._cloud is not None:
            return True
        return self._local is not None and self._local.available(force_refresh)

    def context_window(self) -> int | None:
        """Report the local slot context; the cloud channel has no such limit.

        云端可用时返回 None，让就绪自检跳过本地容量检查——那条检查是为
        llama.cpp 槽位设计的，对云通道没有意义。
        """
        if self._cloud_ready():
            return None
        return self._local.context_window() if self._local is not None else None

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any] | None:
        started = time.monotonic()
        if self._cloud_ready():
            result = self._cloud.chat_json(system, user, schema, max_attempts)
            if result is not None:
                with self._lock:
                    self.cloud_failures = 0
                self._record("cloud", getattr(self._cloud, "last_error_stage", None), started)
                return result
            stage = getattr(self._cloud, "last_error_stage", None)
            with self._lock:
                self.cloud_failures += 1
                # 鉴权和模型不存在属于配置问题，重试没有意义，静默久一些。
                mute = self._cooldown * (5 if stage in {"auth_error", "model_not_found"} else 1)
                self._cloud_muted_until = time.monotonic() + mute
            print(f"[llm/router] cloud channel failed ({stage}); falling back to local "
                  f"for {mute:.0f}s", flush=True)
        if self._local is None:
            self._record(None, "no_channel", started)
            return None
        result = self._local.chat_json(system, user, schema, max_attempts)
        self._record("local" if result is not None else None,
                     getattr(self._local, "last_error_stage", None), started)
        self.last_retry_count = getattr(self._local, "last_retry_count", 0)
        return result

    def _record(self, channel: str | None, stage: str | None, started: float) -> None:
        self.last_channel = channel
        self.last_error_stage = stage
        self.last_latency_seconds = round(time.monotonic() - started, 2)


def build_router(cloud: Any | None, local: Any | None) -> Any | None:
    """Return a router, or the single available client when only one channel exists.

    只有一条通道时不包一层路由器，保持调用栈和既有行为一致。
    """
    if cloud is not None and cloud.available():
        return DualChannelRouter(cloud=cloud, local=local)
    return local


__all__ = ["DualChannelRouter", "build_router"]
