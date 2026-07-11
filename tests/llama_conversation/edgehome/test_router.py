"""Tests for Chinese fast-path routing into fixed EdgeHome plans."""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.router import ChineseFastRouter
from custom_components.llama_conversation.edgehome.understanding.models import FastIntent, FastRouteStatus


def _router(hass) -> ChineseFastRouter:
    return ChineseFastRouter(EntityNameResolver(hass, exposure_checker=lambda *_: True))


def test_router_builds_brightness_fixed_plan_for_exposed_light(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    decision = _router(hass).route("把客厅灯亮度调到百分之五十")

    assert decision.status == FastRouteStatus.READY
    assert decision.intent == FastIntent.SET_BRIGHTNESS
    assert decision.plan is not None
    step = decision.plan.steps[0]
    assert step.tool == "set_light_brightness"
    assert step.arguments == {"entity_id": "light.living", "brightness": 50}
    assert step.verify is not None
    assert step.verify.attributes == {"brightness": 128}


def test_router_builds_query_plan_without_service_verification(hass) -> None:
    hass.states.async_set("sensor.living_temperature", "23", {"friendly_name": "客厅温度"})

    decision = _router(hass).route("查询客厅温度状态")

    assert decision.status == FastRouteStatus.READY
    assert decision.plan is not None
    assert decision.plan.steps[0].tool == "get_device_state"
    assert decision.plan.steps[0].verify is None


def test_router_requires_clarification_for_missing_value_or_ambiguous_name(hass) -> None:
    hass.states.async_set("light.one", "off", {"friendly_name": "台灯"})
    hass.states.async_set("light.two", "off", {"friendly_name": "台灯"})

    missing_value = _router(hass).route("把台灯调亮")
    ambiguous = _router(hass).route("打开台灯")

    assert missing_value.status == FastRouteStatus.CLARIFICATION
    assert missing_value.plan is None
    assert ambiguous.status == FastRouteStatus.CLARIFICATION
    assert ambiguous.reason == "entity_name_ambiguous"


def test_router_defers_complex_request_without_building_a_plan(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    decision = _router(hass).route("打开客厅灯然后关闭卧室灯")

    assert decision.status == FastRouteStatus.FALLBACK
    assert decision.plan is None
