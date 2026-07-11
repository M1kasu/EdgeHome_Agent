"""Tests for the exposed-entity execution boundary."""

from __future__ import annotations

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.errors import EntityResolutionError
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver


def test_resolver_returns_snapshot_for_exposed_entity(hass) -> None:
    hass.states.async_set("light.demo", "on", {"friendly_name": "演示灯", "brightness": 42})
    resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: True)

    snapshot = resolver.snapshot("light.demo", {"light"})

    assert snapshot.state == "on"
    assert snapshot.friendly_name == "演示灯"
    assert snapshot.attributes["brightness"] == 42


def test_resolver_rejects_unexposed_and_wrong_domain_entities(hass) -> None:
    hass.states.async_set("switch.demo", "off")
    hidden_resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: False)
    allowed_resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: True)

    with pytest.raises(EntityResolutionError, match="未暴露"):
        hidden_resolver.resolve("switch.demo")
    with pytest.raises(EntityResolutionError, match="域不允许"):
        allowed_resolver.resolve("switch.demo", {"light"})


def test_name_resolver_uses_friendly_name_and_registry_alias(hass) -> None:
    entity_registry = er.async_get(hass)
    entry = entity_registry.async_get_or_create(
        "light",
        "test",
        "living-room-main",
        suggested_object_id="living_room_main",
    )
    entity_registry.async_update_entity(entry.entity_id, aliases=["主灯"])
    hass.states.async_set(entry.entity_id, "off", {"friendly_name": "客厅主灯"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)

    resolved = resolver.resolve_name("请打开主灯", {"light"})

    assert resolved.snapshot.entity_id == entry.entity_id


def test_name_resolver_rejects_ambiguous_matches(hass) -> None:
    hass.states.async_set("light.desk_one", "off", {"friendly_name": "书房台灯"})
    hass.states.async_set("light.desk_two", "off", {"friendly_name": "书房台灯"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)

    with pytest.raises(EntityResolutionError) as captured:
        resolver.resolve_name("打开书房台灯", {"light"})

    assert captured.value.code == "entity_name_ambiguous"
