"""Validated intermediate representations for the deterministic fast path."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..models import EntitySnapshot, TaskPlan


class FastIntent(StrEnum):
    """Small, explicit intent set eligible for direct device execution."""

    UNKNOWN = "unknown"
    QUERY_STATE = "query_state"
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_BRIGHTNESS = "set_brightness"
    SET_TEMPERATURE = "set_temperature"
    CONTROL_CURTAIN = "control_curtain"


class FastIntentMatch(BaseModel):
    """A deterministic classification result with an auditable confidence value."""

    model_config = ConfigDict(extra="forbid")

    intent: FastIntent
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class FastSlots(BaseModel):
    """Slots extracted from one user utterance before any service call is planned."""

    model_config = ConfigDict(extra="forbid")

    entity_query: str = Field(min_length=1, max_length=300)
    value: float | None = None
    errors: list[str] = Field(default_factory=list)


class FastRouteStatus(StrEnum):
    """Whether the fast path can execute, needs clarification, or must defer."""

    READY = "ready"
    CLARIFICATION = "clarification"
    FALLBACK = "fallback"


class FastRouteDecision(BaseModel):
    """Auditable route decision; only READY may contain an executable fixed plan."""

    model_config = ConfigDict(extra="forbid")

    status: FastRouteStatus
    intent: FastIntent
    reason: str
    entity: EntitySnapshot | None = None
    plan: TaskPlan | None = None
