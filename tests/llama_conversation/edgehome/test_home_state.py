"""Tests for task-scoped, exposure-aware planning snapshots."""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.context.home_state import TaskHomeStateBuilder
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver


def test_home_state_only_contains_entities_relevant_to_goal(hass) -> None:
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅灯"})
    hass.states.async_set("switch.bedroom", "off", {"friendly_name": "卧室开关"})
    builder = TaskHomeStateBuilder(EntityNameResolver(hass, exposure_checker=lambda *_: True))

    snapshot = builder.build("把客厅灯调暗")

    assert [entity.entity_id for entity in snapshot.entities] == ["light.living"]
    assert snapshot.truncated is False


def test_home_state_never_includes_unexposed_entities(hass) -> None:
    hass.states.async_set("light.visible", "on", {"friendly_name": "客厅灯"})
    hass.states.async_set("switch.hidden", "off", {"friendly_name": "客厅开关"})
    builder = TaskHomeStateBuilder(
        EntityNameResolver(hass, exposure_checker=lambda _hass, _domain, entity_id: entity_id != "switch.hidden")
    )

    snapshot = builder.build("查看客厅灯和客厅开关")

    assert [entity.entity_id for entity in snapshot.entities] == ["light.visible"]
