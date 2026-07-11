"""第 11 阶段：观影场景使用注册工具并为窗帘故障保留继续执行策略。"""

from custom_components.llama_conversation.edgehome.models import OnFailurePolicy
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


def test_registered_scene_context_excludes_legacy_exposed_entities(hass) -> None:
    hass.states.async_set("todo.shopping_list", "0", {"friendly_name": "购物清单"})
    hass.states.async_set("light.edgehome_living_room_main", "on", {"friendly_name": "客厅主灯"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True, registry=build_demo_entity_registry())

    snapshot = TaskHomeStateBuilder(resolver).build("我要看电影，调暗客厅主灯")

    assert [entity.entity_id for entity in snapshot.entities] == ["light.edgehome_living_room_main"]
