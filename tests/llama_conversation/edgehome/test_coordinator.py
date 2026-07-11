"""Tests for execution and safe fallback behavior of the Chinese fast path."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.coordinator import EdgeHomeCoordinator
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.execution.executor import FixedPlanExecutor


def _coordinator(hass) -> EdgeHomeCoordinator:
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    return EdgeHomeCoordinator(hass, resolver=resolver, executor=FixedPlanExecutor(hass, resolver=resolver))


@pytest.mark.asyncio
async def test_coordinator_executes_simple_light_command_without_model(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(call) -> None:
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)

    result = await _coordinator(hass).async_handle("打开客厅灯")

    assert result.handled is True
    assert result.speech == "已打开客厅灯。"
    assert hass.states.get("light.living").state == "on"


@pytest.mark.asyncio
async def test_coordinator_reports_query_and_never_executes_complex_request(hass) -> None:
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅灯", "brightness": 128})

    query = await _coordinator(hass).async_handle("客厅灯状态？")
    complex_request = await _coordinator(hass).async_handle("打开客厅灯然后关闭卧室灯")

    assert query.handled is True
    assert query.speech == "客厅灯当前状态为 on，亮度约 50%。"
    assert complex_request.handled is False


@pytest.mark.asyncio
async def test_coordinator_does_not_fallback_after_a_direct_execution_failure(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(_call) -> None:
        return None

    hass.services.async_register("light", "turn_on", turn_on)

    result = await _coordinator(hass).async_handle("打开客厅灯")

    assert result.handled is True
    assert result.speech is not None
    assert result.speech.startswith("未能完成对客厅灯的操作：")
