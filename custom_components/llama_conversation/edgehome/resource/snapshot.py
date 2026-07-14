"""Runtime resource measurements used by the production scheduler."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResourceSnapshot(BaseModel):
    """Real resource values, with unavailable metrics explicitly represented by null."""

    model_config = ConfigDict(extra="forbid")

    process_memory_mb: float | None = Field(default=None, ge=0.0)
    system_memory_mb: float | None = Field(default=None, ge=0.0)
    system_memory_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    cpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    gpu_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    device_temperature_c: float | None = Field(default=None, ge=-100.0, le=200.0)
    power_w: float | None = Field(default=None, ge=0.0)
