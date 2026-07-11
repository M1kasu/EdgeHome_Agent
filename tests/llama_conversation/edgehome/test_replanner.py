"""Tests for bounded failure-replanning decisions."""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.execution.replanner import FailureReplanner
from custom_components.llama_conversation.edgehome.models import (
    OnFailurePolicy,
    PlanStep,
    StepExecutionResult,
    StepStatus,
    TaskExecutionReport,
    TaskPlan,
    TaskStatus,
    VerificationRule,
)


def _plan() -> TaskPlan:
    return TaskPlan(
        goal="观影模式",
        steps=[
            PlanStep(
                id="step_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.demo"},
                verify=VerificationRule(entity_id="light.demo", state="on"),
                on_failure=OnFailurePolicy.REPLAN,
            )
        ],
    )


def test_failure_replanner_allows_exactly_a_recoverable_replan() -> None:
    report = TaskExecutionReport(
        goal="观影模式",
        status=TaskStatus.NEEDS_REPLAN,
        needs_replan=True,
        step_results=[
            StepExecutionResult(
                step_id="step_1",
                tool="turn_on_device",
                status=StepStatus.FAILED,
                attempts=2,
                error_code="service_timeout",
            )
        ],
    )

    decision = FailureReplanner().decide("打开客厅灯", _plan(), report)

    assert decision.allowed is True
    assert decision.prompt_goal is not None
    assert "不得重复失败步骤" in decision.prompt_goal


def test_failure_replanner_refuses_protected_or_unmarked_failures() -> None:
    protected = TaskExecutionReport(
        goal="关闭冰箱",
        status=TaskStatus.NEEDS_REPLAN,
        needs_replan=True,
        step_results=[
            StepExecutionResult(
                step_id="step_1",
                tool="turn_off_device",
                status=StepStatus.FAILED,
                attempts=0,
                error_code="protected_entity_confirmation_required",
            )
        ],
    )
    unmarked = TaskExecutionReport(goal="观影模式", status=TaskStatus.FAILED)

    assert FailureReplanner().decide("关闭冰箱", _plan(), protected).allowed is False
    assert FailureReplanner().decide("观影", _plan(), unmarked).reason == "report_not_marked_for_replan"
