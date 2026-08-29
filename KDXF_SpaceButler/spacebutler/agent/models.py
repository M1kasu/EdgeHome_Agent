"""Serializable dataclasses for the autonomous agent layer.

全部结构保持纯标准库 dataclass + Enum，可直接 JSON 序列化给工作台 UI，
未来可无损替换为 Pydantic 模型或学习型世界模型。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now() -> float:
    return datetime.now(timezone.utc).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%H:%M:%S")


class GoalStatus(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    DEGRADED = "DEGRADED"
    REPLANNING = "REPLANNING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ActionAttribution(str, Enum):
    AGENT_ACTION = "AGENT_ACTION"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    ENVIRONMENT_CHANGE = "ENVIRONMENT_CHANGE"
    UNKNOWN = "UNKNOWN"


@dataclass
class GoalConstraint:
    """One target bound compiled from natural language, e.g. brightness in [20, 45]."""

    metric: str  # brightness | temperature | curtain_position
    lower: float | None = None
    upper: float | None = None
    target: float | None = None
    unit: str = ""

    def satisfied_by(self, value: float | None) -> bool:
        if value is None:
            return False
        if self.lower is not None and value < self.lower:
            return False
        if self.upper is not None and value > self.upper:
            return False
        return True

    def distance(self, value: float | None) -> float:
        """0 inside the bound, positive distance outside; None counts as far away."""
        if value is None:
            return 100.0
        if self.lower is not None and value < self.lower:
            return self.lower - value
        if self.upper is not None and value > self.upper:
            return value - self.upper
        if self.target is not None:
            return abs(value - self.target)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompiledGoal:
    goal_id: str
    activity: str  # watch_movie | thermal_comfort | ...
    room: str
    title: str
    constraints: list[GoalConstraint]
    created_at: float = field(default_factory=now)
    expires_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "activity": self.activity,
            "room": self.room,
            "title": self.title,
            "constraints": [item.to_dict() for item in self.constraints],
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass
class DeviceCapability:
    """What the planner may do with one device; the only vocabulary it sees."""

    device_id: str
    entity_id: str
    domain: str
    name: str
    room: str
    actions: list[str]
    brightness_range: tuple[float, float] | None = None
    temperature_range: tuple[float, float] | None = None
    position_range: tuple[float, float] | None = None
    modes: list[str] = field(default_factory=list)
    readonly: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "entity_id": self.entity_id,
            "domain": self.domain,
            "name": self.name,
            "room": self.room,
            "actions": list(self.actions),
            "brightness_range": list(self.brightness_range) if self.brightness_range else None,
            "temperature_range": list(self.temperature_range) if self.temperature_range else None,
            "position_range": list(self.position_range) if self.position_range else None,
            "modes": list(self.modes),
            "readonly": self.readonly,
        }


@dataclass
class PlannedAction:
    action_id: str
    device_id: str
    device_name: str
    entity_id: str
    capability: str  # set_brightness | set_position | set_temperature | set_hvac_mode | turn_off
    value: float | str | None = None
    mode: str | None = None
    verified: bool | None = None
    observed: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PredictedState:
    """Counterfactual environment prediction for one candidate plan."""

    temperature: float
    temperature_trend: str  # decreasing | increasing | stable
    brightness_pct: float | None = None
    curtain_position: float | None = None
    ac_mode: str = "off"
    horizon_minutes: float = 5.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlanScores:
    goal_satisfaction: float = 0.0
    comfort: float = 0.0
    preference_match: float = 0.5
    safety: float = 1.0
    energy_cost: float = 0.0
    action_cost: float = 0.0
    total: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {key: round(float(value), 4) for key, value in asdict(self).items()}


@dataclass
class CandidatePlan:
    candidate_id: str
    label: str
    actions: list[PlannedAction]
    predicted: PredictedState
    scores: PlanScores
    rationale: str = ""
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "label": self.label,
            "actions": [item.to_dict() for item in self.actions],
            "predicted": self.predicted.to_dict(),
            "scores": self.scores.to_dict(),
            "total_score": round(self.scores.total, 4),
            "rationale": self.rationale,
            "selected": self.selected,
        }


@dataclass
class ActionRecord:
    """One command the agent really sent; used for state-change attribution."""

    action_id: str
    device_id: str
    entity_id: str
    capability: str
    value: object
    sent_at: float
    expected: dict[str, Any]
    verified: bool | None = None
    observed: dict[str, Any] | None = None
    prediction_error: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "device_id": self.device_id,
            "entity_id": self.entity_id,
            "capability": self.capability,
            "value": self.value,
            "sent_at": self.sent_at,
            "expected": self.expected,
            "verified": self.verified,
            "observed": self.observed,
            "prediction_error": self.prediction_error,
        }


@dataclass
class AgentEvent:
    ts: float
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "time": iso(self.ts),
            "kind": self.kind,
            "message": self.message,
            "data": self.data,
        }


@dataclass
class PreferenceEstimate:
    metric: str
    mean: float
    lower: float
    upper: float
    count: int
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RoomState:
    """Perception snapshot of the demo room (living room)."""

    temperature: float | None = None
    presence: bool | None = None
    light_on: bool | None = None
    brightness_pct: float | None = None
    curtain_position: float | None = None
    ac_mode: str = "off"
    ac_target: float | None = None
    captured_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "presence": self.presence,
            "light_on": self.light_on,
            "brightness_pct": self.brightness_pct,
            "curtain_position": self.curtain_position,
            "ac_mode": self.ac_mode,
            "ac_target": self.ac_target,
            "captured_at": self.captured_at,
        }
