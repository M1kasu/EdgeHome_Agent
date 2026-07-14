"""资源采样不伪造不可采集设备数据。"""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.benchmark.resource_monitor import SystemResourceMonitor


def test_resource_monitor_returns_nonnegative_known_metrics_and_nullable_unknowns() -> None:
    snapshot = SystemResourceMonitor().snapshot()

    assert snapshot.process_memory_mb is None or snapshot.process_memory_mb >= 0
    assert snapshot.system_memory_mb is None or snapshot.system_memory_mb >= 0
    assert snapshot.gpu_percent is None
    assert snapshot.device_temperature_c is None
    assert snapshot.power_w is None
