"""Tests for model-backed planning with no model tool or service permissions."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.llama_conversation.edgehome.context.home_state import TaskHomeStateBuilder
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.entity import TextGenerationResult
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningStatus
from custom_components.llama_conversation.edgehome.planner.parser import StructuredPlanParser
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


class DummyPlanningClient:
    """Minimal LocalLLMClient-compatible fake that captures the isolated planning call."""

    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.messages: list[Any] = []
        self.options: dict[str, Any] = {}

    async def _generate(self, messages, _llm_api, _agent_id, options):
        self.messages = messages
        self.options = options
        if self.error:
            raise self.error
        return TextGenerationResult(response=self.response)


def _planner(hass, client: DummyPlanningClient) -> LLMTaskPlanner:
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    tools = build_default_tool_registry()
    return LLMTaskPlanner(
        client,  # type: ignore[arg-type]
        TaskHomeStateBuilder(resolver),
        StructuredPlanParser(PlanValidator(tools, resolver)),
        tools,
    )


@pytest.mark.asyncio
async def test_llm_planner_accepts_valid_json_but_exposes_only_relevant_snapshot(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    hass.states.async_set("switch.bedroom", "off", {"friendly_name": "卧室开关"})
    response = json.dumps(
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
    client = DummyPlanningClient(response=response)

    result = await _planner(hass, client).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})

    assert result.status == PlanningStatus.ACCEPTED
    assert result.plan is not None
    prompt = client.messages[0].content
    assert "light.living" in prompt
    assert "switch.bedroom" not in prompt
    assert client.options["enable_streaming"] is False
    assert "response_json_schema" in client.options


@pytest.mark.asyncio
async def test_llm_planner_rejects_illegal_model_plan_and_backend_failure(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    illegal = DummyPlanningClient(
        response=json.dumps({"goal": "非法", "steps": [{"id": "step_1", "tool": "shell", "arguments": {"entity_id": "light.living"}}]})
    )
    unavailable = DummyPlanningClient(error=RuntimeError("backend offline"))

    illegal_result = await _planner(hass, illegal).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})
    unavailable_result = await _planner(hass, unavailable).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})

    assert illegal_result.status == PlanningStatus.REJECTED
    assert illegal_result.reason is not None
    assert "tool_not_registered" in illegal_result.reason
    assert unavailable_result.status == PlanningStatus.UNAVAILABLE
    assert unavailable_result.reason == "RuntimeError"
