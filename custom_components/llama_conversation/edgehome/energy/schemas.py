"""第 12 阶段能耗输入、分析、建议和自动化的严格数据契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_ALLOWED_RECOMMENDATION_TARGETS = {
    "unoccupied_ac": "climate.edgehome_living_room_ac",
    "tv_standby": "switch.edgehome_test_socket",
    "night_light": "light.edgehome_living_room_main",
}


class EnergyRecord(BaseModel):
    """一条小时级、非负且可追溯的本地能耗记录。"""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    device_name: str = Field(min_length=1, max_length=100)
    room: Literal["living_room", "bedroom", "home"]
    power_w: float = Field(ge=0.0, le=20_000.0)
    energy_kwh: float = Field(ge=0.0, le=100.0)
    state: str = Field(min_length=1, max_length=32)
    occupancy: bool
    temperature: float = Field(ge=-40.0, le=80.0)
    scene: str = Field(min_length=1, max_length=64)


class EnergyLoadResult(BaseModel):
    """读取失败行不会混入计算，避免 CSV 脏数据驱动建议。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    records: list[EnergyRecord] = Field(default_factory=list)
    rejected_rows: int = Field(ge=0, default=0)
    duplicate_rows: int = Field(ge=0, default=0)
    error_code: str | None = None


class EnergyIntervalFinding(BaseModel):
    """异常或可优化区间，附带可读证据。"""

    model_config = ConfigDict(extra="forbid")

    kind: str
    entity_id: str
    start: datetime
    end: datetime
    duration_minutes: int = Field(ge=0)
    energy_kwh: float = Field(ge=0.0)
    detail: str


class EnergyAnalysis(BaseModel):
    """全部数字由 Python 计算，模型只能把此结构转写为自然语言。"""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    period_start: datetime
    period_end: datetime
    total_kwh: float = Field(ge=0.0)
    device_kwh: dict[str, float] = Field(default_factory=dict)
    room_kwh: dict[str, float] = Field(default_factory=dict)
    daily_kwh: dict[str, float] = Field(default_factory=dict)
    comparison_delta_kwh: float | None = None
    peak_power_w: float = Field(ge=0.0)
    high_consumption_rank: list[str] = Field(default_factory=list)
    unoccupied_intervals: list[EnergyIntervalFinding] = Field(default_factory=list)
    standby_intervals: list[EnergyIntervalFinding] = Field(default_factory=list)
    temperature_ac_correlation: float | None = None
    anomalies: list[EnergyIntervalFinding] = Field(default_factory=list)
    record_count: int = Field(ge=0)


class EnergyRecommendation(BaseModel):
    """一个允许用户单独选择的确定性节能动作。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_-]{3,64}$")
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=500)
    target_entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    rule_type: Literal["unoccupied_ac", "tv_standby", "night_light"]
    parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)
    evidence: list[EnergyIntervalFinding] = Field(min_length=1)
    protected_excluded: bool = True

    @model_validator(mode="after")
    def target_must_match_rule(self) -> "EnergyRecommendation":
        """防止任何计划把节能建议升级为冰箱、路由器或未知实体控制。"""
        if _ALLOWED_RECOMMENDATION_TARGETS[self.rule_type] != self.target_entity_id:
            raise ValueError("节能建议目标不在 EdgeHome Demo 白名单内")
        if not self.protected_excluded:
            raise ValueError("节能建议必须显式排除受保护设备")
        return self


class EnergyPlan(BaseModel):
    """方案仅描述待确认规则；它本身不能创建 Home Assistant 自动化。"""

    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    recommendations: list[EnergyRecommendation] = Field(min_length=1, max_length=10)
    requires_confirmation: bool = True


class EnergyAutomation(BaseModel):
    """持久化的 EdgeHome Demo 自动化记录，目标永远来自白名单。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^edgehome-demo-[a-z0-9-]{8,80}$")
    name: str = Field(pattern=r"^EdgeHome Demo .+", max_length=160)
    rule_type: Literal["unoccupied_ac", "tv_standby", "night_light"]
    target_entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    parameters: dict[str, int | float | str | bool] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime


class EnergyAutomationResult(BaseModel):
    """自动化 API 的统一、可审计返回。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    automation_ids: list[str] = Field(default_factory=list)
    automations: list[EnergyAutomation] = Field(default_factory=list)
    error_code: str | None = None
