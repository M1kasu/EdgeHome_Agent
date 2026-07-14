"""Tests that untrusted model JSON is parsed and validated before execution."""

from __future__ import annotations

import json

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.planner.parser import PlanParseStatus, StructuredPlanParser
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


def _parser(hass) -> StructuredPlanParser:
    return StructuredPlanParser(
        PlanValidator(
            build_default_tool_registry(),
            ExposedEntityResolver(hass, exposure_checker=lambda *_: True),
        )
    )


def test_parser_accepts_a_valid_plan_for_an_exposed_entity(hass) -> None:
    hass.states.async_set("light.living", "off")
    payload = json.dumps(
        {
            "goal": "打开客厅灯",
            "steps": [
                {
                    "id": "step_1",
                    "tool": "turn_on_device",
                    "arguments": {"entity_id": "light.living"},
                    "verify": {"entity_id": "light.living", "state": "on"},
                }
            ],
        }
    )

    result = _parser(hass).parse(payload)

    assert result.status == PlanParseStatus.ACCEPTED
    assert result.plan is not None
    assert result.plan.steps[0].tool == "turn_on_device"


def test_parser_rejects_invalid_json_schema_and_unregistered_tool(hass) -> None:
    hass.states.async_set("light.living", "off")

    invalid_json = _parser(hass).parse("not json")
    invalid_plan = _parser(hass).parse(
        {"goal": "非法", "steps": [{"id": "step_1", "tool": "shell", "arguments": {"entity_id": "light.living"}}]}
    )

    assert invalid_json.status == PlanParseStatus.INVALID_JSON
    assert invalid_plan.status == PlanParseStatus.INVALID_PLAN
    assert invalid_plan.reason is not None
    assert "tool_not_registered" in invalid_plan.reason
    assert "工具未注册" in invalid_plan.reason


def test_parser_rejects_a_non_topological_plan_dependency(hass) -> None:
    hass.states.async_set("light.living", "off")
    result = _parser(hass).parse(
        {
            "goal": "错误依赖",
            "steps": [
                {
                    "id": "turn_on_living",
                    "tool": "turn_on_device",
                    "arguments": {"entity_id": "light.living"},
                    "depends_on": ["read_living"],
                    "verify": {"entity_id": "light.living", "state": "on"},
                },
                {"id": "read_living", "tool": "get_device_state", "arguments": {"entity_id": "light.living"}},
            ],
        }
    )

    assert result.status == PlanParseStatus.INVALID_PLAN
    assert result.reason is not None and "invalid_step_dependency" in result.reason


def test_parser_recovers_one_json_object_from_a_model_preamble(hass) -> None:
    parser = _parser(hass)
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    payload = "规划如下：\n```json\n" + json.dumps(
        {
            "goal": "打开客厅灯",
            "steps": [
                {
                    "id": "turn_on_living",
                    "tool": "turn_on_device",
                    "arguments": {"entity_id": "light.living"},
                    "verify": {"entity_id": "light.living", "state": "on"},
                }
            ],
        }
    ) + "\n```"

    result = parser.parse(payload)

    assert result.status == PlanParseStatus.ACCEPTED
    assert result.plan is not None
