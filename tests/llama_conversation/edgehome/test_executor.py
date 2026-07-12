"""Tests for fixed-plan service execution, verification, timeout, and retry."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.execution.executor import FixedPlanExecutor
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, TaskStatus, VerificationRule
from custom_components.llama_conversation.edgehome.tools.registry import ToolRegistry, build_default_tool_registry


def _executor(hass, registry: ToolRegistry | None = None) -> FixedPlanExecutor:
    resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: True)
    return FixedPlanExecutor(hass, tools=registry, resolver=resolver)


@pytest.mark.asyncio
async def test_executor_calls_service_and_verifies_fresh_light_state(hass) -> None:
    entity_id = "light.demo"
    hass.states.async_set(entity_id, "off", {"brightness": 0})

    async def turn_on(call) -> None:
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            {"brightness": call.data.get("brightness_pct", 100)},
            context=call.context,
        )

    hass.services.async_register("light", "turn_on", turn_on)
    plan = TaskPlan(
        goal="调亮客厅灯",
        steps=[
            PlanStep(
                id="step_1",
                tool="set_light_brightness",
                arguments={"entity_id": entity_id, "brightness": 45},
                verify=VerificationRule(entity_id=entity_id, state="on", attributes={"brightness": 45}),
            )
        ],
    )

    report = await _executor(hass).async_execute(plan)

    assert report.status == TaskStatus.SUCCESS
    assert report.step_results[0].attempts == 1
    assert report.step_results[0].verification is not None
    assert report.step_results[0].verification.passed is True
    assert hass.states.get(entity_id).state == "on"


@pytest.mark.asyncio
async def test_executor_retries_when_service_returns_without_state_change(hass) -> None:
    entity_id = "light.retry"
    hass.states.async_set(entity_id, "off")
    calls = 0

    async def turn_on(_call) -> None:
        nonlocal calls
        calls += 1

    hass.services.async_register("light", "turn_on", turn_on)
    defaults = build_default_tool_registry()
    turn_on_definition = defaults.get("turn_on_device")
    assert turn_on_definition is not None
    registry = ToolRegistry()
    registry.register(replace(turn_on_definition, max_retries=1))
    plan = TaskPlan(
        goal="打开未响应的灯",
        steps=[
            PlanStep(
                id="step_1",
                tool="turn_on_device",
                arguments={"entity_id": entity_id},
                verify=VerificationRule(entity_id=entity_id, state="on"),
            )
        ],
    )

    report = await _executor(hass, registry).async_execute(plan)

    assert report.status == TaskStatus.FAILED
    assert calls == 2
    assert report.step_results[0].error_code == "verification_failed"
    assert report.step_results[0].attempts == 2


@pytest.mark.asyncio
async def test_executor_waits_for_mqtt_device_feedback_before_retrying(hass) -> None:
    entity_id = "light.edgehome_living_room_main"
    hass.states.async_set(entity_id, "off", {"brightness": 0})

    async def turn_on(call) -> None:
        async def publish_feedback() -> None:
            await asyncio.sleep(0.05)
            hass.states.async_set(call.data["entity_id"], "on", {"brightness": 128}, context=call.context)

        hass.async_create_task(publish_feedback())

    hass.services.async_register("light", "turn_on", turn_on)
    plan = TaskPlan(
        goal="等待 MQTT 灯反馈",
        steps=[
            PlanStep(
                id="step_1",
                tool="set_light_brightness",
                arguments={"entity_id": entity_id, "brightness": 50},
                verify=VerificationRule(entity_id=entity_id, state="on", attributes={"brightness": 128}),
            )
        ],
    )

    report = await _executor(hass).async_execute(plan)

    assert report.status == TaskStatus.SUCCESS
    assert report.step_results[0].attempts == 1


@pytest.mark.asyncio
async def test_executor_times_out_a_slow_service(hass) -> None:
    entity_id = "light.slow"
    hass.states.async_set(entity_id, "off")

    async def turn_on(_call) -> None:
        await asyncio.sleep(0.1)

    hass.services.async_register("light", "turn_on", turn_on)
    defaults = build_default_tool_registry()
    turn_on_definition = defaults.get("turn_on_device")
    assert turn_on_definition is not None
    registry = ToolRegistry()
    registry.register(replace(turn_on_definition, timeout_seconds=0.01, max_retries=0))
    plan = TaskPlan(
        goal="超时保护",
        steps=[
            PlanStep(
                id="step_1",
                tool="turn_on_device",
                arguments={"entity_id": entity_id},
                verify=VerificationRule(entity_id=entity_id, state="on"),
            )
        ],
    )

    report = await _executor(hass, registry).async_execute(plan)

    assert report.status == TaskStatus.FAILED
    assert report.step_results[0].error_code == "service_timeout"


@pytest.mark.asyncio
async def test_executor_rejects_unregistered_tool_before_service_call(hass) -> None:
    hass.states.async_set("light.demo", "off")
    plan = TaskPlan(
        goal="非法工具",
        steps=[
            PlanStep(
                id="step_1",
                tool="arbitrary_rest_request",
                arguments={"entity_id": "light.demo"},
            )
        ],
    )

    report = await _executor(hass).async_execute(plan)

    assert report.status == TaskStatus.FAILED
    assert report.step_results[0].error_code == "tool_not_registered"
