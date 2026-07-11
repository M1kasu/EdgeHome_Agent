"""Tests for bounded edge planning concurrency and aggregate metrics."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.resource.monitor import ResourceMonitor


@pytest.mark.asyncio
async def test_resource_monitor_limits_concurrency_and_records_aggregate_metrics() -> None:
    monitor = ResourceMonitor(max_concurrent_plans=1)

    lease = await monitor.async_try_acquire("complex_planning")
    busy = await monitor.async_try_acquire("complex_planning")
    assert lease is not None
    assert busy is None

    record = await monitor.async_release(lease)

    assert record.route == "complex_planning"
    assert record.concurrent_at_start == 1
    assert len(monitor.records) == 1
