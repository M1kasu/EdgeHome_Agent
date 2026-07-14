"""第 11 阶段：观影场景使用注册工具并为窗帘故障保留继续执行策略。"""

from custom_components.llama_conversation.edgehome.models import EntitySnapshot, OnFailurePolicy
from custom_components.llama_conversation.edgehome.context.home_state import TaskHomeStateBuilder
from custom_components.llama_conversation.edgehome.entity_registry import build_demo_entity_registry
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.planner.demo_scenes import DemoScenePlanFactory
from custom_components.llama_conversation.edgehome.planner.templates import SceneName
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


def test_movie_scene_is_a_structured_registered_tool_plan() -> None:
    draft = DemoScenePlanFactory().build("我要看电影，把客厅调舒服一点，空调不要低于25度", SceneName.MOVIE)

    assert draft is not None
    tool_names = build_default_tool_registry().names()
    assert all(step.tool in tool_names for step in draft.plan.steps)
    curtain = next(step for step in draft.plan.steps if step.tool == "control_curtain")
    assert curtain.on_failure == OnFailurePolicy.SKIP
    assert curtain.verify is not None


def test_movie_scene_uses_only_mqtt_devices_and_request_preferences() -> None:
    snapshots = [
        EntitySnapshot(entity_id="light.edgehome_living_room_main", domain="light", state="on", attributes={"brightness": 204}),
        EntitySnapshot(entity_id="cover.edgehome_living_room_curtain", domain="cover", state="open", attributes={"current_position": 100}),
        EntitySnapshot(entity_id="switch.edgehome_living_room_tv", domain="switch", state="off"),
        EntitySnapshot(entity_id="climate.edgehome_living_room_ac", domain="climate", state="cool", attributes={"temperature": 24.0}),
    ]

    draft = DemoScenePlanFactory().build("启动观影模式，主灯调到20%，空调设为26度", SceneName.MOVIE, snapshots)

    assert draft is not None
    actions = [step for step in draft.plan.steps if step.tool != "get_room_state"]
    assert {step.arguments["entity_id"] for step in actions} == {
        "light.edgehome_living_room_main",
        "cover.edgehome_living_room_curtain",
        "switch.edgehome_living_room_tv",
        "climate.edgehome_living_room_ac",
    }
    assert "light.edgehome_living_room_ambient" not in {step.arguments.get("entity_id") for step in actions}
    assert next(step for step in actions if step.tool == "set_light_brightness").arguments["brightness"] == 20
    assert next(step for step in actions if step.tool == "set_temperature").arguments["temperature"] == 26.0


def test_movie_scene_omits_already_verified_device_actions() -> None:
    snapshots = [
        EntitySnapshot(entity_id="light.edgehome_living_room_main", domain="light", state="on", attributes={"brightness": 76}),
        EntitySnapshot(entity_id="cover.edgehome_living_room_curtain", domain="cover", state="closed", attributes={"current_position": 0}),
        EntitySnapshot(entity_id="switch.edgehome_living_room_tv", domain="switch", state="on"),
        EntitySnapshot(entity_id="climate.edgehome_living_room_ac", domain="climate", state="cool", attributes={"temperature": 25.5}),
    ]

    draft = DemoScenePlanFactory().build("开启观影模式", SceneName.MOVIE, snapshots)

    assert draft is not None
    assert [step.tool for step in draft.plan.steps] == ["get_room_state"]


def test_movie_scene_respects_explicit_climate_and_brightness_constraints() -> None:
    snapshots = [
        EntitySnapshot(entity_id="light.edgehome_living_room_main", domain="light", state="on", attributes={"brightness": 76}),
        EntitySnapshot(entity_id="cover.edgehome_living_room_curtain", domain="cover", state="closed", attributes={"current_position": 0}),
        EntitySnapshot(entity_id="switch.edgehome_living_room_tv", domain="switch", state="on"),
        EntitySnapshot(entity_id="climate.edgehome_living_room_ac", domain="climate", state="off", attributes={"temperature": 23.0}),
    ]

    draft = DemoScenePlanFactory().build("启动观影模式，主灯不低于40%，不要开空调", SceneName.MOVIE, snapshots)

    assert draft is not None
    actions = [step for step in draft.plan.steps if step.tool != "get_room_state"]
    assert [(step.tool, step.arguments["entity_id"]) for step in actions] == [
        ("set_light_brightness", "light.edgehome_living_room_main"),
    ]
    assert actions[0].arguments["brightness"] == 40


def test_bedroom_movie_is_clarified_instead_of_controlling_living_room_devices() -> None:
    draft = DemoScenePlanFactory().build("启动卧室观影", SceneName.MOVIE, [])

    assert draft is not None
    assert draft.plan.need_clarification
    assert draft.plan.steps[0].arguments == {"room": "bedroom"}


def test_registered_scene_context_excludes_legacy_exposed_entities(hass) -> None:
    hass.states.async_set("todo.shopping_list", "0", {"friendly_name": "购物清单"})
    hass.states.async_set("light.edgehome_living_room_main", "on", {"friendly_name": "客厅主灯"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True, registry=build_demo_entity_registry())

    snapshot = TaskHomeStateBuilder(resolver).build("我要看电影，调暗客厅主灯")

    assert [entity.entity_id for entity in snapshot.entities] == ["light.edgehome_living_room_main"]
