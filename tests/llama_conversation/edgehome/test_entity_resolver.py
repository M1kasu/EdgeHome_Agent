"""Tests for the exposed-entity execution boundary."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.errors import EntityResolutionError


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
