"""模型不可用时只允许受限目标场景降级。"""

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
async def test_goal_policy_fallback_uses_registered_movie_state(hass) -> None:
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
    assert result.source == PlanningSource.GOAL_POLICY
    assert result.plan is not None
    assert result.plan.goal_spec is not None


@pytest.mark.asyncio
async def test_explicit_scene_constraints_use_goal_policy_not_generic_template(hass) -> None:
    hass.states.async_set("light.edgehome_living_room_main", "off")
    hass.states.async_set("switch.edgehome_living_room_tv", "off")
    hass.states.async_set("climate.edgehome_living_room_ac", "off")
    hass.states.async_set("cover.edgehome_living_room_curtain", "closed")
    tools = build_default_tool_registry()
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    planner = LLMTaskPlanner(_NoModel(), TaskHomeStateBuilder(resolver), StructuredPlanParser(PlanValidator(tools, resolver)), tools)  # type: ignore[arg-type]

    result = await planner.async_plan(
        PlanningRequest(goal="准备观影，但不要打开空调，主灯不低于40%", agent_id="test"),
        {},
    )

    assert result.status == PlanningStatus.ACCEPTED
    assert result.source == PlanningSource.GOAL_POLICY
    assert result.model_attempts == 1
    assert result.plan is not None
    climate_controls = {
        step.tool
        for step in result.plan.steps
        if step.arguments.get("entity_id") == "climate.edgehome_living_room_ac"
    }
    assert climate_controls.isdisjoint({"turn_on_device", "turn_off_device", "set_hvac_mode", "set_temperature"})
