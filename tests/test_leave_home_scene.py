"""第 11 阶段：离家场景包含状态读取、普通设备关闭和安防开启。"""

from custom_components.llama_conversation.edgehome.planner.demo_scenes import DemoScenePlanFactory
from custom_components.llama_conversation.edgehome.planner.templates import SceneName


def test_leave_home_scene_contains_window_check_and_security_activation() -> None:
    draft = DemoScenePlanFactory().build("我要出门一天，不要动冰箱和路由器", SceneName.LEAVING)

    assert draft is not None
    tools = [step.tool for step in draft.plan.steps]
    assert "get_home_state" in tools
    assert "get_device_state" in tools
    assert "activate_security_mode" in tools
