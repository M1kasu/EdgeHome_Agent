"""将一个请求的分段单调计时和资源快照组合为 BenchmarkMetric。"""

from __future__ import annotations

from datetime import datetime, timezone

from .metrics import elapsed_ms, monotonic_ms
from .resource_monitor import SystemResourceMonitor
from .schemas import BenchmarkMetric


class LatencyRecorder:
    """请求级别记录器；调用方只需标记真实发生的阶段。"""

    def __init__(self, request_id: str, task_type: str, route: str, monitor: SystemResourceMonitor | None = None) -> None:
        self._request_id = request_id
        self._task_type = task_type
        self._route = route
        self._monitor = monitor or SystemResourceMonitor()
        self._started_monotonic_ms = monotonic_ms()
        self._started_at = datetime.now(timezone.utc)
        self._starts: dict[str, float] = {}
        self._durations: dict[str, float] = {}

    def start_stage(self, stage: str) -> None:
        """标记一个实际阶段的起点。"""
        self._starts[stage] = monotonic_ms()

    def end_stage(self, stage: str) -> float | None:
        """结束一个阶段；未开始阶段返回 null。"""
        started = self._starts.pop(stage, None)
        if started is None:
            return None
        duration = elapsed_ms(started)
        self._durations[stage] = duration
        return duration

    def set_stage_duration(self, stage: str, duration_ms: float | None) -> None:
        """记录由被测服务自身单调计时提供的阶段耗时；未知值保持 null。"""
        if duration_ms is not None:
            self._durations[stage] = max(0.0, duration_ms)

    def finish(
        self,
        success: bool,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        tokens_per_second: float | None = None,
        queue_wait_ms: float | None = None,
        error_code: str | None = None,
    ) -> BenchmarkMetric:
        """结束并生成不可伪造未采集指标的结果记录。"""
        snapshot = self._monitor.snapshot()
        return BenchmarkMetric(
            request_id=self._request_id,
            task_type=self._task_type,
            route=self._route,
            success=success,
            start_time=self._started_at,
            end_time=datetime.now(timezone.utc),
            total_latency_ms=elapsed_ms(self._started_monotonic_ms),
            routing_latency_ms=self._durations.get("routing"),
            context_build_latency_ms=self._durations.get("context_build"),
            retrieval_latency_ms=self._durations.get("retrieval"),
            model_ttft_ms=self._durations.get("model_ttft"),
            model_generation_ms=self._durations.get("model_generation"),
            tool_execution_ms=self._durations.get("tool_execution"),
            verification_ms=self._durations.get("verification"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_per_second=tokens_per_second,
            process_memory_mb=snapshot.process_memory_mb,
            system_memory_mb=snapshot.system_memory_mb,
            cpu_percent=snapshot.cpu_percent,
            gpu_percent=snapshot.gpu_percent,
            device_temperature_c=snapshot.device_temperature_c,
            power_w=snapshot.power_w,
            queue_wait_ms=queue_wait_ms,
            error_code=error_code,
        )
