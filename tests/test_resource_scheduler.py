"""资源警戒、CPU 警戒与安全路径调度测试。"""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.benchmark.schemas import ResourceSnapshot
from custom_components.llama_conversation.edgehome.resource.scheduler import ResourceAwareScheduler, ResourceSchedulerConfig


def test_resource_scheduler_selects_safe_fallbacks_from_injected_thresholds() -> None:
    scheduler = ResourceAwareScheduler(ResourceSchedulerConfig(memory_warning_percent=80, cpu_warning_percent=70))

    memory = scheduler.decide(task_is_complex=True, model_available=True, safety_critical=False, resources=ResourceSnapshot(system_memory_percent=81))
    cpu = scheduler.decide(task_is_complex=True, model_available=True, safety_critical=False, resources=ResourceSnapshot(cpu_percent=71))
    unavailable = scheduler.decide(task_is_complex=True, model_available=False, safety_critical=False, resources=ResourceSnapshot())
    safety = scheduler.decide(task_is_complex=True, model_available=True, safety_critical=True, resources=ResourceSnapshot())
    simple = scheduler.decide(task_is_complex=False, model_available=True, safety_critical=False, resources=ResourceSnapshot())

    assert (memory.decision, memory.reason) == ("template_fallback", "memory_warning")
    assert (cpu.decision, cpu.reason) == ("template_fallback", "cpu_warning")
    assert unavailable.reason == "model_unavailable"
    assert safety.decision == "deterministic_safety"
    assert simple.decision == "fast_path"
