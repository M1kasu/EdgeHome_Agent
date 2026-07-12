"""资源感知的 EdgeHome 路径选择与复杂任务并发队列。"""

from __future__ import annotations

import asyncio
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from .snapshot import ResourceSnapshot


class ResourceSchedulerConfig(BaseModel):
    """可注入的运行配置，避免在调度逻辑中写死机器阈值。"""

    model_config = ConfigDict(extra="forbid")

    memory_warning_percent: float = Field(default=90.0, ge=1.0, le=100.0)
    cpu_warning_percent: float = Field(default=90.0, ge=1.0, le=100.0)
    max_llm_concurrency: int = Field(default=1, ge=1, le=16)
    max_context_tokens: int = Field(default=2048, ge=128, le=32_768)
    fallback_context_tokens: int = Field(default=512, ge=64, le=32_768)
    max_generation_tokens: int = Field(default=256, ge=16, le=4_096)


class SchedulingDecision(BaseModel):
    """每项选择保留可审计原因及可用资源快照。"""

    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str
    memory_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    cpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    context_tokens: int = Field(ge=1)
    max_generation_tokens: int = Field(ge=1)


class ResourceAwareScheduler:
    """优先让简单、安全任务继续可用，资源紧张时只降级复杂规划。"""

    def __init__(self, config: ResourceSchedulerConfig | None = None) -> None:
        self.config = config or ResourceSchedulerConfig()
        self._semaphore = asyncio.Semaphore(self.config.max_llm_concurrency)
        self._decisions: list[SchedulingDecision] = []

    def decide(
        self,
        *,
        task_is_complex: bool,
        model_available: bool,
        safety_critical: bool,
        resources: ResourceSnapshot,
    ) -> SchedulingDecision:
        """返回快速路径、模型路径或模板降级；不调用模型做调度。"""
        memory = resources.system_memory_percent
        cpu = resources.cpu_percent
        if safety_critical:
            return self._remember(self._decision("deterministic_safety", "safety_critical", memory, cpu, True))
        if not task_is_complex:
            return self._remember(self._decision("fast_path", "simple_task", memory, cpu, True))
        if not model_available:
            return self._remember(self._decision("template_fallback", "model_unavailable", memory, cpu, True))
        if memory is not None and memory >= self.config.memory_warning_percent:
            return self._remember(self._decision("template_fallback", "memory_warning", memory, cpu, True))
        if cpu is not None and cpu >= self.config.cpu_warning_percent:
            return self._remember(self._decision("template_fallback", "cpu_warning", memory, cpu, True))
        return self._remember(self._decision("llm_path", "resources_available", memory, cpu, False))

    async def async_acquire_llm(self) -> float:
        """等待一个复杂规划槽位并返回真实队列等待时间。"""
        started = perf_counter()
        await self._semaphore.acquire()
        return max(0.0, (perf_counter() - started) * 1000)

    def release_llm(self) -> None:
        """释放复杂模型规划槽位。"""
        self._semaphore.release()

    @property
    def decisions(self) -> tuple[SchedulingDecision, ...]:
        """返回有界审计记录。"""
        return tuple(self._decisions)

    def _decision(
        self,
        decision: str,
        reason: str,
        memory: float | None,
        cpu: float | None,
        fallback: bool,
    ) -> SchedulingDecision:
        return SchedulingDecision(
            decision=decision,
            reason=reason,
            memory_percent=memory,
            cpu_percent=cpu,
            context_tokens=self.config.fallback_context_tokens if fallback else self.config.max_context_tokens,
            max_generation_tokens=self.config.max_generation_tokens,
        )

    def _remember(self, decision: SchedulingDecision) -> SchedulingDecision:
        self._decisions.append(decision)
        del self._decisions[:-200]
        return decision
