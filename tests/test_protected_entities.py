"""第 11 阶段：冰箱和路由器不得被离家计划关闭。"""

from custom_components.llama_conversation.edgehome.planner.demo_scenes import DemoScenePlanFactory
from custom_components.llama_conversation.edgehome.planner.templates import SceneName


def test_leave_home_plan_keeps_protected_refrigerator_and_router_on() -> None:
    plan = DemoScenePlanFactory().build("我要出门一天", SceneName.LEAVING)

    assert plan is not None
    shutdown_targets = {
        str(step.arguments.get("entity_id")) for step in plan.plan.steps if step.tool == "turn_off_device"
    }
    assert "switch.edgehome_refrigerator" not in shutdown_targets
    assert "switch.edgehome_router" not in shutdown_targets
