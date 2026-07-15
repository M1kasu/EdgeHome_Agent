"""Schema tests for fixed EdgeHome plans."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from custom_components.llama_conversation.edgehome.models import (
    ConstraintType,
    GoalConstraint,
    GoalSpec,
    GoalType,
    PlanStep,
    TaskPlan,
    VerificationRule,
)


def test_fixed_plan_accepts_structured_verified_step() -> None:
    plan = TaskPlan(
        goal="打开客厅灯",
        steps=[
            PlanStep(
                id="step_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.living_room"},
                verify=VerificationRule(entity_id="light.living_room", state="on"),
            )
        ],
    )

    assert plan.steps[0].verify is not None
    assert plan.steps[0].verify.state == "on"


def test_plan_rejects_clarification_without_question() -> None:
    with pytest.raises(ValidationError, match="clarification_question"):
        TaskPlan(
            goal="澄清设备",
            need_clarification=True,
            steps=[PlanStep(id="step_1", tool="get_device_state", arguments={"entity_id": "light.demo"})],
        )


def test_verification_rule_requires_an_expected_state_or_attribute() -> None:
    with pytest.raises(ValidationError, match="状态条件"):
        VerificationRule(entity_id="light.demo")


def test_task_plan_accepts_structured_goal_spec_and_typed_constraints() -> None:
    spec = GoalSpec(
        raw_goal="准备观影但不要开空调，主灯不低于40%",
        goal_type=GoalType.SCENE,
        scene="movie",
        target_rooms=["living_room"],
        constraints=[
            GoalConstraint(
                statement="禁止控制climate",
                constraint_type=ConstraintType.PROHIBITION,
                target="climate",
                operator="not_control",
                conflict_policy="reject",
            )
        ],
        forbidden_actions=["禁止控制climate"],
    )
    plan = TaskPlan(
        goal=spec.raw_goal,
        scene="movie",
        goal_spec=spec,
        steps=[PlanStep(id="read_room", tool="get_room_state", arguments={"room": "living_room"})],
    )

    assert plan.goal_spec is not None
    assert plan.goal_spec.constraints[0].constraint_type == ConstraintType.PROHIBITION
