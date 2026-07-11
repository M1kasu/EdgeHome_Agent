"""Revalidate a fixed plan when the environmental baseline changed before execution."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..entity_resolver import ExposedEntityResolver
from ..errors import PlanValidationError
from ..models import EntitySnapshot, TaskPlan
from ..plan_validator import PlanValidator


class PlanEnvironmentBaseline(BaseModel):
    """The exact target/precondition states observed when a plan was accepted."""

    model_config = ConfigDict(extra="forbid")

    entities: dict[str, EntitySnapshot] = Field(default_factory=dict)


class PlanRevalidationResult(BaseModel):
    """A non-executable result describing whether the plan must be regenerated."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    reason: str | None = None


class EnvironmentPlanGuard:
    """Reject a stale plan before execution instead of applying it to a changed home."""

    def __init__(self, validator: PlanValidator, resolver: ExposedEntityResolver) -> None:
        self._validator = validator
        self._resolver = resolver

    def capture(self, plan: TaskPlan) -> PlanEnvironmentBaseline:
        """Capture only plan targets and declared preconditions, not unrelated state."""
        entity_ids = {
            entity_id
            for step in plan.steps
            for entity_id in [step.arguments.get("entity_id"), *(condition.entity_id for condition in step.preconditions)]
            if isinstance(entity_id, str)
        }
        return PlanEnvironmentBaseline(entities={entity_id: self._resolver.snapshot(entity_id) for entity_id in entity_ids})

    def revalidate(self, plan: TaskPlan, baseline: PlanEnvironmentBaseline) -> PlanRevalidationResult:
        """Run deterministic validation and reject changed or unavailable plan targets."""
        try:
            self._validator.validate(plan)
        except PlanValidationError as error:
            return PlanRevalidationResult(accepted=False, reason=",".join(issue.code for issue in error.issues))
        for entity_id, before in baseline.entities.items():
            after = self._resolver.snapshot(entity_id)
            if after.state in {"unavailable", "unknown"}:
                return PlanRevalidationResult(accepted=False, reason="environment_entity_unavailable")
            if after.state != before.state:
                return PlanRevalidationResult(accepted=False, reason="environment_state_changed")
        return PlanRevalidationResult(accepted=True)
