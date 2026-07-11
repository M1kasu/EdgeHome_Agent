"""模型不可用时只允许模板场景降级。"""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.context.home_state import TaskHomeStateBuilder
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningStatus, PlanningSource
from custom_components.llama_conversation.edgehome.planner.parser import StructuredPlanParser
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


class _NoModel:
    async def _generate(self, *_args, **_kwargs):
        raise RuntimeError("offline")


@pytest.mark.asyncio
async def test_template_fallback_uses_only_registered_movie_plan(hass) -> None:
    hass.states.async_set("light.edgehome_living_room_main", "off")
    hass.states.async_set("light.edgehome_living_room_ambient", "off")
    hass.states.async_set("switch.edgehome_living_room_tv", "off")
    hass.states.async_set("climate.edgehome_living_room_ac", "off")
    hass.states.async_set("cover.edgehome_living_room_curtain", "closed")
    tools = build_default_tool_registry()
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    planner = LLMTaskPlanner(_NoModel(), TaskHomeStateBuilder(resolver), StructuredPlanParser(PlanValidator(tools, resolver)), tools)  # type: ignore[arg-type]

    result = await planner.async_template_fallback(PlanningRequest(goal="我要看电影", agent_id="test"), "memory_warning")

    assert result.status == PlanningStatus.ACCEPTED
    assert result.source == PlanningSource.DEMO_SCENE_FALLBACK
