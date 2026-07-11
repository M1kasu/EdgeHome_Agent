"""端侧基准的 JSONL/CSV 契约；未采集字段必须为 null。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkWorkload(BaseModel):
    """一条脱离提示词示例的可复现业务工作负载。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_-]{3,96}$")
    task_type: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=8_000)
    expected_route: str = Field(min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkMetric(BaseModel):
    """一条请求的真实测量记录；不以 0 伪装不可采集硬件指标。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    task_type: str = Field(min_length=1, max_length=64)
    route: str = Field(min_length=1, max_length=64)
    success: bool
    start_time: datetime
    end_time: datetime
    total_latency_ms: float = Field(ge=0.0)
    routing_latency_ms: float | None = Field(default=None, ge=0.0)
    context_build_latency_ms: float | None = Field(default=None, ge=0.0)
    retrieval_latency_ms: float | None = Field(default=None, ge=0.0)
    model_ttft_ms: float | None = Field(default=None, ge=0.0)
    model_generation_ms: float | None = Field(default=None, ge=0.0)
    tool_execution_ms: float | None = Field(default=None, ge=0.0)
    verification_ms: float | None = Field(default=None, ge=0.0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    tokens_per_second: float | None = Field(default=None, ge=0.0)
    process_memory_mb: float | None = Field(default=None, ge=0.0)
    system_memory_mb: float | None = Field(default=None, ge=0.0)
    cpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    gpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    device_temperature_c: float | None = Field(default=None, ge=-100.0, le=200.0)
    power_w: float | None = Field(default=None, ge=0.0)
    queue_wait_ms: float | None = Field(default=None, ge=0.0)
    error_code: str | None = Field(default=None, max_length=128)


class ResourceSnapshot(BaseModel):
    """采样器的真实资源数据；无可靠来源时值为 null。"""

    model_config = ConfigDict(extra="forbid")

    process_memory_mb: float | None = Field(default=None, ge=0.0)
    system_memory_mb: float | None = Field(default=None, ge=0.0)
    system_memory_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    cpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    gpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    device_temperature_c: float | None = Field(default=None, ge=-100.0, le=200.0)
    power_w: float | None = Field(default=None, ge=0.0)
