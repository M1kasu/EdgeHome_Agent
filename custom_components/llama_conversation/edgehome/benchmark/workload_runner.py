"""以指定并发执行工作负载，并让每条请求拥有独立指标记录。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from uuid import uuid4

from .latency_recorder import LatencyRecorder
from .metrics import elapsed_ms, monotonic_ms
from .schemas import BenchmarkMetric, BenchmarkWorkload


WorkloadHandler = Callable[[BenchmarkWorkload, LatencyRecorder], Awaitable[bool]]


class BenchmarkWorkloadRunner:
    """并发测试共享一个有界队列，避免把业务竞争算作基准失败。"""

    def __init__(self, max_concurrency: int = 1) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency 必须至少为 1")
        self._max_concurrency = max_concurrency

    async def run(self, workloads: Iterable[BenchmarkWorkload], handler: WorkloadHandler) -> list[BenchmarkMetric]:
        """执行工作负载；异常变为真实失败记录而不是中断整个实验。"""
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def execute(workload: BenchmarkWorkload) -> BenchmarkMetric:
            queued_at = monotonic_ms()
            async with semaphore:
                queue_wait_ms = elapsed_ms(queued_at)
                recorder = LatencyRecorder(f"req-{uuid4().hex[:12]}", workload.task_type, workload.expected_route)
                try:
                    success = await handler(workload, recorder)
                except Exception as error:
                    return recorder.finish(False, queue_wait_ms=queue_wait_ms, error_code=type(error).__name__)
                return recorder.finish(success, queue_wait_ms=queue_wait_ms)

        return list(await asyncio.gather(*(execute(workload) for workload in workloads)))
