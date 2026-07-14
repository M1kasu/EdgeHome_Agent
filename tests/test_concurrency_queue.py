"""并发队列测量等待时间并在异常时保留失败记录。"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.llama_conversation.edgehome.benchmark.schemas import BenchmarkWorkload
from custom_components.llama_conversation.edgehome.benchmark.workload_runner import BenchmarkWorkloadRunner


@pytest.mark.asyncio
async def test_workload_queue_records_wait_at_single_concurrency() -> None:
    workloads = [BenchmarkWorkload(id=f"job-{index}", task_type="simple", text="查询状态", expected_route="fast_path") for index in range(3)]

    async def handler(_workload, recorder) -> bool:
        recorder.start_stage("routing")
        await asyncio.sleep(0.01)
        recorder.end_stage("routing")
        return True

    metrics = await BenchmarkWorkloadRunner(max_concurrency=1).run(workloads, handler)

    assert len(metrics) == 3
    assert metrics[-1].queue_wait_ms is not None and metrics[-1].queue_wait_ms > 0
