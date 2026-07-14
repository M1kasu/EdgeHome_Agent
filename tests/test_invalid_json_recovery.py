"""无效模型 JSON 只能被拒绝或模板降级。"""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.context.home_state import TaskHomeStateBuilder
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningStatus
from custom_components.llama_conversation.edgehome.planner.parser import StructuredPlanParser
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry
from custom_components.llama_conversation.entity import TextGenerationResult


class _InvalidJsonModel:
    async def _generate(self, *_args, **_kwargs):
        return TextGenerationResult(response="not a json plan")


@pytest.mark.asyncio
async def test_invalid_json_never_becomes_an_executable_plan(hass) -> None:
    tools = build_default_tool_registry()
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    planner = LLMTaskPlanner(_InvalidJsonModel(), TaskHomeStateBuilder(resolver), StructuredPlanParser(PlanValidator(tools, resolver)), tools)  # type: ignore[arg-type]

    result = await planner.async_plan(PlanningRequest(goal="打开客厅灯", agent_id="test"), {})

    assert result.status == PlanningStatus.REJECTED
    assert result.plan is None
