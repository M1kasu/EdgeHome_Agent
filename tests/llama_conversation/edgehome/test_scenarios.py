"""Tests for capturing and restoring only dynamic scene plan targets."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.scenarios.state import ScenarioStateManager


@pytest.mark.asyncio
async def test_scenario_manager_captures_targets_and_builds_restoration_plan(hass) -> None:
    hass.states.async_set("light.living", "off", {"brightness": 64})
    hass.states.async_set("switch.unrelated", "on")
    resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: True)
    manager = ScenarioStateManager(hass, resolver)
    movie_plan = TaskPlan(
        goal="观影模式",
        steps=[
            PlanStep(
                id="scene_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.living"},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )

    scenario = await manager.async_capture("conversation-1", "movie", movie_plan)
    restoration = await manager.async_restore_plan("conversation-1", "movie")

    assert [snapshot.entity_id for snapshot in scenario.snapshots] == ["light.living"]
    assert restoration is not None
    assert restoration.steps[0].tool == "turn_off_device"
    assert restoration.steps[0].arguments["entity_id"] == "light.living"

    await manager.async_clear("conversation-1", "movie")
    assert await manager.async_restore_plan("conversation-1", "movie") is None


@pytest.mark.asyncio
async def test_scenario_manager_restores_climate_mode_and_target_temperature(hass) -> None:
    hass.states.async_set("climate.living", "heat", {"temperature": 23.5})
    resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: True)
    manager = ScenarioStateManager(hass, resolver)
    movie_plan = TaskPlan(
        goal="观影模式",
        steps=[
            PlanStep(
                id="scene_ac",
                tool="set_temperature",
                arguments={"entity_id": "climate.living", "temperature": 26.0},
                verify=VerificationRule(entity_id="climate.living", attributes={"temperature": 26.0}),
            )
        ],
    )

    await manager.async_capture("conversation-1", "movie", movie_plan)
    restoration = await manager.async_restore_plan("conversation-1", "movie")

    assert restoration is not None
    assert [(step.tool, step.arguments) for step in restoration.steps] == [
        ("set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}),
        ("set_temperature", {"entity_id": "climate.living", "temperature": 23.5}),
    ]
