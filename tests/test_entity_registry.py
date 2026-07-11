"""第 11 阶段：虚拟实体注册表的中文别名与受保护设备测试。"""

from custom_components.llama_conversation.edgehome.entity_registry import build_demo_entity_registry
from custom_components.llama_conversation.edgehome.errors import EntityResolutionError


def test_entity_registry_resolves_chinese_alias_and_marks_protected_devices() -> None:
    registry = build_demo_entity_registry()

    assert registry.resolve_query("请打开客厅灯", {"light"}) == "light.edgehome_living_room_main"
    assert registry.is_protected("switch.edgehome_refrigerator") is True
    assert registry.is_protected("switch.edgehome_router") is True


def test_entity_registry_requires_clarification_for_ambiguous_main_light() -> None:
    registry = build_demo_entity_registry()

    try:
        registry.resolve_query("打开主灯", {"light"})
    except EntityResolutionError as error:
        assert error.code == "entity_name_ambiguous"
    else:  # pragma: no cover - makes an unexpected unsafe match explicit
        raise AssertionError("不应为“主灯”猜测一个房间")
