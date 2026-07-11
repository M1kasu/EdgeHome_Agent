"""Structured EdgeHome dialogue state kept alongside Home Assistant ChatLog."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import TaskPlan
from ..energy.schemas import EnergyPlan, EnergyRecommendation


class PendingClarification(BaseModel):
    """The original goal and question required before planning may resume."""

    model_config = ConfigDict(extra="forbid")

    original_goal: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=300)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingProtectedAction(BaseModel):
    """A one-turn approval record for a previously requested protected shutdown."""

    model_config = ConfigDict(extra="forbid")

    plan: TaskPlan
    entity_ids: list[str] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingEnergyPlan(BaseModel):
    """待确认的节能方案；未确认前绝不创建长期自动化。"""

    model_config = ConfigDict(extra="forbid")

    plan: EnergyPlan
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DialogueState(BaseModel):
    """Persisted per-conversation memory used only by EdgeHome orchestration."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=256)
    last_plan: TaskPlan | None = None
    last_entity_id: str | None = None
    last_arguments: dict[str, Any] = Field(default_factory=dict)
    pending_clarification: PendingClarification | None = None
    pending_protected_action: PendingProtectedAction | None = None
    last_energy_analysis_id: str | None = None
    last_energy_recommendations: list[EnergyRecommendation] = Field(default_factory=list)
    pending_energy_plan: PendingEnergyPlan | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=12))

    def refreshed(self, ttl_hours: int = 12) -> "DialogueState":
        """Return a copy with a bounded expiry to avoid permanent conversational memory."""
        now = datetime.now(timezone.utc)
        return self.model_copy(update={"updated_at": now, "expires_at": now + timedelta(hours=ttl_hours)})
