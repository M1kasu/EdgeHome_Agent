"""Business rules for updating structured context only after verified execution."""

from __future__ import annotations

from typing import Any

from ..models import PlanStep, StepExecutionResult, TaskExecutionReport, TaskPlan, TaskStatus
from .models import ConversationState, ExecutionResult
from .store import ConversationStateStore


_CONTROL_TOOLS = frozenset(
    {
        "turn_on_device",
        "turn_off_device",
        "set_light_brightness",
        "set_temperature",
        "control_curtain",
        "activate_security_mode",
    }
)
_SENSITIVE_KEYS = frozenset({"token", "password", "secret", "api_key", "authorization"})


class ConversationContextService:
    """Keep successful state trustworthy and preserve failures only as audit history."""

    def __init__(self, store: ConversationStateStore) -> None:
        self._store = store

    async def async_record_success(
        self,
        conversation_id: str,
        plan: TaskPlan,
        report: TaskExecutionReport,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Record a context target only after executor verification has succeeded."""
        if report.status != TaskStatus.SUCCESS:
            raise ValueError("Only verified successful execution may update conversation context")
        state = await self._load_or_new(conversation_id, user_id, assistant_id)
        step, outcome = _primary_outcome(plan, report)
        result = _execution_result(step, outcome, success=True)
        updates: dict[str, Any] = {
            "last_plan": plan,
            "current_goal": None,
            "pending_clarification": None,
            "pending_plan": None,
            "recent_results": _bounded_results(state, result),
        }
        if step is not None and step.tool in _CONTROL_TOOLS:
            entity_id = _entity_id(step, outcome)
            updates.update(
                {
                    "last_room": _room_for(step, outcome),
                    "last_entity_id": entity_id,
                    "last_device_type": entity_id.split(".", maxsplit=1)[0] if entity_id else None,
                    "last_action": step.tool,
                    "last_parameters": _safe_mapping(step.arguments),
                    "last_successful_action": step.tool,
                    "last_requested_parameters": _safe_mapping(step.arguments),
                    "last_actual_state": _safe_mapping(result.actual),
                }
            )
        return await self._store.async_save(state.model_copy(update=updates))

    async def async_record_failure(
        self,
        conversation_id: str,
        plan: TaskPlan,
        report: TaskExecutionReport,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Append a failed attempt without changing the last verified target or parameters."""
        if report.status == TaskStatus.SUCCESS:
            raise ValueError("Successful execution must use async_record_success")
        state = await self._load_or_new(conversation_id, user_id, assistant_id)
        step, outcome = _primary_outcome(plan, report)
        result = _execution_result(step, outcome, success=False)
        return await self._store.async_save(
            state.model_copy(update={"recent_results": _bounded_results(state, result), "pending_plan": None})
        )

    async def async_set_pending_plan(
        self,
        conversation_id: str,
        plan: TaskPlan,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Keep a validated pending plan separate from short textual clarification state."""
        state = await self._load_or_new(conversation_id, user_id, assistant_id)
        return await self._store.async_save(state.model_copy(update={"current_goal": plan.goal, "pending_plan": plan}))

    async def _load_or_new(
        self,
        conversation_id: str,
        user_id: str | None,
        assistant_id: str | None,
    ) -> ConversationState:
        return (await self._store.async_get(conversation_id, user_id, assistant_id)) or self._store.new_state(
            conversation_id,
            user_id,
            assistant_id,
        )


def _primary_outcome(plan: TaskPlan, report: TaskExecutionReport) -> tuple[PlanStep | None, StepExecutionResult | None]:
    """Choose the final device step, not a preceding room-read step in a scene."""
    results_by_id = {result.step_id: result for result in report.step_results}
    for step in reversed(plan.steps):
        if step.tool in _CONTROL_TOOLS or "entity_id" in step.arguments:
            return step, results_by_id.get(step.id)
    return None, report.step_results[-1] if report.step_results else None


def _execution_result(step: PlanStep | None, outcome: StepExecutionResult | None, *, success: bool) -> ExecutionResult:
    entity_id = _entity_id(step, outcome)
    actual = dict(outcome.state_after.attributes) if outcome and outcome.state_after else {}
    if outcome and outcome.state_after:
        actual["state"] = outcome.state_after.state
    return ExecutionResult(
        entity_id=entity_id,
        action=step.tool if step is not None else "unknown",
        requested=_safe_mapping(step.arguments) if step is not None else {},
        actual=_safe_mapping(actual),
        success=success,
        reason=outcome.error_message if outcome and not success else None,
    )


def _entity_id(step: PlanStep | None, outcome: StepExecutionResult | None) -> str | None:
    if outcome and outcome.state_after:
        return outcome.state_after.entity_id
    if outcome and outcome.state_before:
        return outcome.state_before.entity_id
    entity_id = step.arguments.get("entity_id") if step is not None else None
    return entity_id if isinstance(entity_id, str) else None


def _room_for(step: PlanStep, outcome: StepExecutionResult | None) -> str | None:
    if isinstance(step.arguments.get("room"), str):
        return step.arguments["room"]
    snapshot = outcome.state_after if outcome and outcome.state_after else outcome.state_before if outcome else None
    if snapshot and snapshot.area_id:
        return snapshot.area_id
    entity_id = _entity_id(step, outcome)
    if entity_id and "living_room" in entity_id:
        return "living_room"
    if entity_id and "bedroom" in entity_id:
        return "bedroom"
    return None


def _bounded_results(state: ConversationState, result: ExecutionResult) -> list[ExecutionResult]:
    return [*state.recent_results, result][-5:]


def _safe_mapping(values: dict[str, Any]) -> dict[str, Any]:
    """Exclude credentials even if a future caller accidentally passes one into context."""
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if key.lower() in _SENSITIVE_KEYS:
            continue
        safe[key] = value
    return safe
