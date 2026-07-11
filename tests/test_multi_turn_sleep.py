"""第 11 阶段：睡眠澄清、槽位继承和测试模式延时任务计划。"""

from custom_components.llama_conversation.edgehome.planner.demo_scenes import DemoScenePlanFactory
from custom_components.llama_conversation.edgehome.planner.templates import SceneName
from custom_components.llama_conversation.edgehome.coordinator import EdgeHomeCoordinator
import pytest


def test_sleep_scene_asks_for_missing_ac_timing_then_builds_delayed_plan() -> None:
    factory = DemoScenePlanFactory()
    first = factory.build("我要睡觉了", SceneName.SLEEPING)
    second = factory.build("我要睡觉了，测试模式半小时后关客厅空调，卧室调到26度，电视现在关闭", SceneName.SLEEPING)

    assert first is not None and first.plan.need_clarification is True
    assert first.plan.clarification_question is not None
    assert second is not None
    assert second.delay_seconds == 30
    assert second.delayed_plan is not None
    bedroom_temperature = next(step for step in second.plan.steps if step.tool == "set_temperature")
    assert bedroom_temperature.arguments == {"entity_id": "climate.edgehome_bedroom_ac", "temperature": 26.0}


@pytest.mark.asyncio
async def test_sleep_follow_up_preserves_the_original_goal_for_complex_planning(hass) -> None:
    coordinator = EdgeHomeCoordinator(hass)
    await coordinator._dialogue_store.async_set_clarification(
        "sleep-conversation",
        "我要睡觉了。",
        "客厅空调是否关闭？",
    )

    follow_up = await coordinator.async_handle_with_dialogue(
        "测试模式半小时后关，卧室调到26度，客厅电视现在关闭。",
        "sleep-conversation",
    )

    assert follow_up.handled is False
    assert follow_up.planning_text == "我要睡觉了。 测试模式半小时后关，卧室调到26度，客厅电视现在关闭。"
    await coordinator.async_shutdown()
