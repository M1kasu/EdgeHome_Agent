"""第 11 阶段：离家场景包含状态读取、普通设备关闭和安防开启。"""

from custom_components.llama_conversation.edgehome.planner.demo_scenes import DemoScenePlanFactory
from custom_components.llama_conversation.edgehome.models import EntitySnapshot
from custom_components.llama_conversation.edgehome.planner.templates import SceneName


def test_leave_home_scene_contains_window_check_and_security_activation() -> None:
    draft = DemoScenePlanFactory().build("我要出门一天，不要动冰箱和路由器", SceneName.LEAVING)

    assert draft is not None
    tools = [step.tool for step in draft.plan.steps]
    assert "get_home_state" in tools
    assert "get_device_state" in tools
    assert "activate_security_mode" in tools


def test_leave_home_scene_uses_only_mqtt_devices_and_safety_policy() -> None:
    snapshots = [
        EntitySnapshot(entity_id="light.edgehome_living_room_main", domain="light", state="on", attributes={"brightness": 204}),
        EntitySnapshot(entity_id="cover.edgehome_living_room_curtain", domain="cover", state="open", attributes={"current_position": 100}),
        EntitySnapshot(entity_id="switch.edgehome_living_room_tv", domain="switch", state="on"),
        EntitySnapshot(entity_id="climate.edgehome_living_room_ac", domain="climate", state="cool", attributes={"temperature": 24.0}),
        EntitySnapshot(entity_id="binary_sensor.edgehome_window", domain="binary_sensor", state="off", attributes={"device_class": "window"}),
        EntitySnapshot(entity_id="input_boolean.edgehome_security_mode", domain="input_boolean", state="off"),
    ]

    draft = DemoScenePlanFactory().build("我要出门", SceneName.LEAVING, snapshots)

    assert draft is not None
    targets = {step.arguments.get("entity_id") for step in draft.plan.steps}
    assert targets.issuperset(
        {
            "light.edgehome_living_room_main",
            "cover.edgehome_living_room_curtain",
            "switch.edgehome_living_room_tv",
            "climate.edgehome_living_room_ac",
            "input_boolean.edgehome_security_mode",
        }
    )
    assert "light.edgehome_living_room_ambient" not in targets
    assert "switch.edgehome_refrigerator" not in targets
    assert "switch.edgehome_router" not in targets
