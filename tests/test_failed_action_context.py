"""Failed actions must not replace the last verified context target."""

from __future__ import annotations

import asyncio

from custom_components.llama_conversation.edgehome.context.service import ConversationContextService
from custom_components.llama_conversation.edgehome.context.store import ConversationStateStore
from custom_components.llama_conversation.edgehome.models import (
    EntitySnapshot,
    PlanStep,
    StepExecutionResult,
    StepStatus,
    TaskExecutionReport,
    TaskPlan,
    TaskStatus,
    VerificationRule,
)
from tests.context_fakes import FakeHass


def _plan(brightness: int) -> TaskPlan:
    return TaskPlan(
        goal="set brightness",
        steps=[
            PlanStep(
                id="step_1",
                tool="set_light_brightness",
                arguments={"entity_id": "light.living", "brightness": brightness},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )


def test_failed_action_appends_audit_but_preserves_last_success(tmp_path) -> None:
    async def exercise() -> None:
        store = ConversationStateStore(FakeHass(tmp_path), tmp_path / "edgehome_context.db")
        service = ConversationContextService(store)
        successful = TaskExecutionReport(
            goal="set brightness",
            status=TaskStatus.SUCCESS,
            step_results=[
                StepExecutionResult(
                    step_id="step_1",
                    tool="set_light_brightness",
                    status=StepStatus.SUCCESS,
                    attempts=1,
                    state_after=EntitySnapshot(
                        entity_id="light.living", domain="light", state="on", attributes={"brightness": 128}
                    ),
                )
            ],
        )
        failed = TaskExecutionReport(
            goal="set brightness",
            status=TaskStatus.FAILED,
            step_results=[
                StepExecutionResult(
                    step_id="step_1",
                    tool="set_light_brightness",
                    status=StepStatus.FAILED,
                    attempts=1,
                    error_message="verification_failed",
                )
            ],
        )

        await service.async_record_success("session-a", _plan(50), successful, user_id="user-a", assistant_id="assistant-a")
        await service.async_record_failure("session-a", _plan(30), failed, user_id="user-a", assistant_id="assistant-a")
        state = await store.async_get("session-a", "user-a", "assistant-a")

        assert state is not None
        assert state.last_entity_id == "light.living"
        assert state.last_parameters["brightness"] == 50
        assert state.recent_results[-1].success is False

    asyncio.run(exercise())
