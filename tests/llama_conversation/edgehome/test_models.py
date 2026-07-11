"""Schema tests for fixed EdgeHome plans."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule


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
