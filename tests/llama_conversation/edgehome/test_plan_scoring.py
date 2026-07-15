"""Tests for deterministic plan scoring and risk evidence."""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.models import EntitySnapshot, PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.planner.scoring import PlanEvaluator


def test_plan_evaluator_scores_verified_plan_and_flags_protected_shutdown() -> None:
    plan = TaskPlan(
        goal="关闭路由器",
        steps=[
            PlanStep(
                id="off_router",
                tool="turn_off_device",
                arguments={"entity_id": "switch.edgehome_router"},
                verify=VerificationRule(entity_id="switch.edgehome_router", state="off"),
            )
        ],
    )
    evaluated = PlanEvaluator().evaluate(
        plan,
        [EntitySnapshot(entity_id="switch.edgehome_router", domain="switch", state="on", friendly_name="路由器")],
    )

    assert evaluated.risk_assessment is not None
    assert evaluated.risk_assessment.blocked is True
    assert evaluated.plan_score is not None
    assert evaluated.plan_score.safety == 0
