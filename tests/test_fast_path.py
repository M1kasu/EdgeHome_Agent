"""第 11 阶段：单设备中文指令必须走无模型的快速路径。"""

from custom_components.llama_conversation.edgehome.entity_registry import build_demo_entity_registry
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.router import ChineseFastRouter
from custom_components.llama_conversation.edgehome.understanding.models import FastIntent, FastRouteStatus


def test_fast_path_builds_verified_light_plan_without_model(hass) -> None:
    hass.states.async_set("light.edgehome_living_room_main", "off", {"friendly_name": "客厅主灯"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True, registry=build_demo_entity_registry())

    decision = ChineseFastRouter(resolver).route("把客厅主灯调到50%")

    assert decision.status == FastRouteStatus.READY
    assert decision.intent == FastIntent.SET_BRIGHTNESS
    assert decision.plan is not None
    assert decision.plan.steps[0].tool == "set_light_brightness"
    assert decision.plan.steps[0].arguments["brightness"] == 50


def test_fast_path_supports_dedicated_security_mode(hass) -> None:
    hass.states.async_set("input_boolean.edgehome_security_mode", "off", {"friendly_name": "安防模式"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True, registry=build_demo_entity_registry())

    decision = ChineseFastRouter(resolver).route("打开安防模式")

    assert decision.status == FastRouteStatus.READY
    assert decision.plan is not None
    assert decision.plan.steps[0].tool == "activate_security_mode"


def test_registry_is_the_per_agent_boundary_for_virtual_security_mode(hass) -> None:
    hass.states.async_set("input_boolean.edgehome_security_mode", "off", {"friendly_name": "安防模式"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: False, registry=build_demo_entity_registry())

    assert resolver.snapshot("input_boolean.edgehome_security_mode").state == "off"
