"""Strongly typed, bounded state for one EdgeHome conversation session."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..energy.schemas import EnergyPlan, EnergyRecommendation
from ..models import TaskPlan


class PendingClarification(BaseModel):
    """A question that must be resolved before a later turn can execute."""

    model_config = ConfigDict(extra="forbid")

    original_text: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=300)
    intent: str | None = Field(default=None, max_length=64)
    candidate_entity_id: str | None = Field(default=None, max_length=256)
    missing_slots: list[str] = Field(default_factory=list, max_length=8)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def original_goal(self) -> str:
        """Keep legacy callers readable while the stored field uses clearer wording."""
        return self.original_text


class PendingProtectedAction(BaseModel):
    """A one-turn approval record for a previously requested protected shutdown."""

    model_config = ConfigDict(extra="forbid")

    plan: TaskPlan
    entity_ids: list[str] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingEnergyPlan(BaseModel):
    """A bounded energy proposal awaiting an explicit follow-up choice."""

    model_config = ConfigDict(extra="forbid")

    plan: EnergyPlan
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionResult(BaseModel):
    """The small, auditable result of one verified or failed device action."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str | None = Field(default=None, max_length=256)
    action: str = Field(min_length=1, max_length=96)
    requested: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    success: bool
    reason: str | None = Field(default=None, max_length=500)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationState(BaseModel):
    """Persisted state keyed by a conversation, agent, and optional HA user."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=16, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=256)
    user_id: str | None = Field(default=None, max_length=256)
    assistant_id: str | None = Field(default=None, max_length=256)
    last_room: str | None = Field(default=None, max_length=128)
    last_entity_id: str | None = Field(default=None, max_length=256)
    last_device_type: str | None = Field(default=None, max_length=64)
    last_action: str | None = Field(default=None, max_length=96)
    last_parameters: dict[str, Any] = Field(default_factory=dict)
    current_goal: str | None = Field(default=None, max_length=300)
    pending_clarification: PendingClarification | None = None
    pending_plan: TaskPlan | None = None
    pending_protected_action: PendingProtectedAction | None = None
    last_plan: TaskPlan | None = None
    last_energy_analysis_id: str | None = Field(default=None, max_length=128)
    last_energy_recommendations: list[EnergyRecommendation] = Field(default_factory=list)
    pending_energy_plan: PendingEnergyPlan | None = None
    recent_results: list[ExecutionResult] = Field(default_factory=list, max_length=5)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=30))

    @property
    def last_arguments(self) -> dict[str, Any]:
        """Compatibility name for callers that still read the prior field."""
        return self.last_parameters

    def refreshed(self, ttl: timedelta = timedelta(minutes=30)) -> "ConversationState":
        """Refresh expiry without extending the amount or sensitivity of stored data."""
        now = datetime.now(timezone.utc)
        return self.model_copy(update={"updated_at": now, "expires_at": now + ttl})
