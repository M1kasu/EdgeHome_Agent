"""Tests for plan validation before any Home Assistant service call."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.errors import PlanValidationError
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


def _validator(hass) -> PlanValidator:
    return PlanValidator(
        build_default_tool_registry(),
        ExposedEntityResolver(hass, exposure_checker=lambda *_: True),
        max_steps=2,
    )


def test_validator_normalizes_valid_fixed_plan(hass) -> None:
    hass.states.async_set("light.demo", "off")
    plan = TaskPlan(
        goal="打开灯",
        steps=[
            PlanStep(
                id="step_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.demo"},
                verify=VerificationRule(entity_id="light.demo", state="on"),
            )
        ],
    )

    validated = _validator(hass).validate(plan)

    assert validated.steps[0].arguments == {"entity_id": "light.demo"}


def test_validator_rejects_missing_verification_and_unknown_entity(hass) -> None:
    plan = TaskPlan(
        goal="不安全的计划",
        steps=[PlanStep(id="step_1", tool="turn_on_device", arguments={"entity_id": "light.missing"})],
    )

    with pytest.raises(PlanValidationError) as captured:
        _validator(hass).validate(plan)

    issue_codes = {issue.code for issue in captured.value.issues}
    assert {"entity_not_found", "verification_required"}.issubset(issue_codes)
