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


@pytest.mark.asyncio
async def test_coordinator_uses_dialogue_reference_and_manages_delayed_tasks(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    hass.states.async_set("light.bedroom", "off", {"friendly_name": "卧室灯"})

    async def turn_on(call) -> None:
        previous = hass.states.get(call.data["entity_id"])
        attributes = dict(previous.attributes) if previous else {}
        attributes["brightness"] = round(call.data.get("brightness_pct", 100) * 255 / 100)
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            attributes,
            context=call.context,
        )

    hass.services.async_register("light", "turn_on", turn_on)
    coordinator = _coordinator(hass)

    first = await coordinator.async_handle_with_dialogue("把客厅灯亮度调到50%", "conversation-1")
    reference = await coordinator.async_handle_with_dialogue("卧室的也一样", "conversation-1")
    delayed = await coordinator.async_handle_with_dialogue("10分钟后打开客厅灯", "conversation-1")
    listed = await coordinator.async_handle_with_dialogue("查询延时任务", "conversation-1")
    task = (await coordinator._delayed_tasks.async_list("conversation-1"))[0]
    cancelled = await coordinator.async_handle_with_dialogue(f"取消延时任务 {task.id[:8]}", "conversation-1")

    assert first.handled is True
    assert reference.speech == "已按上一条设置完成操作。"
    assert hass.states.get("light.bedroom").attributes["brightness"] == 128
    assert delayed.speech is not None
    assert delayed.speech.startswith("已安排延时任务")
    assert listed.speech is not None
    assert listed.speech.startswith("待执行任务：")
    assert cancelled.speech == "已取消延时任务。"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_coordinator_resumes_pending_clarification_on_next_turn(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(call) -> None:
        previous = hass.states.get(call.data["entity_id"])
        attributes = dict(previous.attributes) if previous else {}
        attributes["brightness"] = round(call.data.get("brightness_pct", 100) * 255 / 100)
        hass.states.async_set(call.data["entity_id"], "on", attributes, context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    coordinator = _coordinator(hass)

    clarification = await coordinator.async_handle_with_dialogue("把客厅灯调亮", "conversation-1")
    resumed = await coordinator.async_handle_with_dialogue("调到50%", "conversation-1")

    assert clarification.speech is not None
    assert clarification.speech.startswith("暂时无法安全执行")
    assert resumed.speech == "已设置亮度客厅灯。"
    assert hass.states.get("light.living").attributes["brightness"] == 128
