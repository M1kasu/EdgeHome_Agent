from __future__ import annotations

import json

import pytest

from spacebutler.esphome_registry import ESPHOME_DEVICE_RECORDS, discover_entities, resolve_entity_id


@pytest.fixture(autouse=True)
def clear_explicit_bindings(monkeypatch):
    for record in ESPHOME_DEVICE_RECORDS:
        monkeypatch.delenv(record["entity_env"], raising=False)


def state(entity_id, friendly_name, value="on"):
    return {"entity_id": entity_id, "state": value, "attributes": {"friendly_name": friendly_name}}


@pytest.mark.parametrize(("entity_id", "friendly", "device_id"), [
    ("light.living_room_light_living_room_light", "Living-Room-Light Living Room Light", "living_room_main_light"),
    ("cover.living_room_curtain_living_room_curtain", "Living-Room-Curtain Living Room Curtain", "living_room_curtain"),
    ("climate.living_room_ac_living_room_ac", "Living-Room-Ac Living Room AC", "living_room_ac"),
    ("sensor.living_room_ac_living_room_temperature", "Living-Room-Ac Living Room Temperature", "living_room_temperature_sensor"),
    ("binary_sensor.living_room_presence_living_room_presence", "Living-Room-Presence Living Room Presence", "living_room_presence_sensor"),
    ("sensor.living_room_ac_temperature", "Room temperature", "living_room_temperature_sensor"),
    ("light.custom_entity", "  Living-Room-Light  ", "living_room_main_light"),
    ("light.living_room_light_2", "我的灯", "living_room_main_light"),
])
def test_known_id_and_node_prefixed_names_are_resolved(entity_id, friendly, device_id):
    assert resolve_entity_id([state(entity_id, friendly)], device_id) == entity_id


def test_wrong_domains_and_arbitrary_suffixes_never_become_targets():
    states = [state("input_boolean.living_room_presence", "Living Room Presence"),
              state("light.kitchen_light", "Kitchen Living Room Light"),
              state("sensor.temperature", "Outside"), state("light.main", "Other room")]
    bindings, diagnostics = discover_entities(states)
    assert bindings == {}
    assert diagnostics["ha_entity_count"] == 4
    # 没有任何实体可绑定时，注册表里每台设备都必须被报告为缺失。
    assert len(diagnostics["missing_devices"]) == len(ESPHOME_DEVICE_RECORDS)


def test_duplicate_entities_require_explicit_binding_even_if_one_is_unavailable(monkeypatch):
    states = [state("light.living_room_light", "Living Room Light", "unavailable"),
              state("light.living_room_light_2", "Living-Room-Light", "on")]
    bindings, diagnostics = discover_entities(states)
    assert bindings == {}
    assert diagnostics["missing_devices"][0]["reason"] == "ambiguous"
    with pytest.raises(RuntimeError, match="ambiguous"):
        resolve_entity_id(states, "living_room_main_light")
    monkeypatch.setenv("SPACEBUTLER_ENTITY_LIGHT", "light.living_room_light_2")
    assert resolve_entity_id(states, "living_room_main_light") == "light.living_room_light_2"


@pytest.mark.parametrize(("configured", "reason"), [
    ("light.missing", "configured_entity_missing"),
    ("switch.wrong_domain", "invalid_binding"),
    ("light.invalid/name", "invalid_binding"),
])
def test_invalid_explicit_binding_never_falls_back_to_another_device(monkeypatch, configured, reason):
    monkeypatch.setenv("SPACEBUTLER_ENTITY_LIGHT", configured)
    bindings, diagnostics = discover_entities([state("light.living_room_light", "Living Room Light")])
    assert bindings == {}
    assert diagnostics["missing_devices"][0]["reason"] == reason


def test_explicit_binding_supports_custom_entity_and_display_names(monkeypatch):
    monkeypatch.setenv("SPACEBUTLER_ENTITY_LIGHT", "light.custom_lamp")
    assert resolve_entity_id([state("light.custom_lamp", "自定义名称")], "living_room_main_light") == "light.custom_lamp"


def test_diagnostics_distinguish_empty_ha_and_unmatched_entities_without_copying_attributes():
    states = [state("light.unmapped", "未绑定的灯")]
    states[0]["attributes"]["private_value"] = "do-not-expose"
    _, diagnostics = discover_entities(states)
    assert diagnostics["ha_entity_count"] == 1
    assert diagnostics["matched_device_count"] == 0
    assert diagnostics["ha_entities"][0]["friendly_name"] == "未绑定的灯"
    assert "do-not-expose" not in json.dumps(diagnostics)
    _, empty = discover_entities([])
    assert empty["ha_entity_count"] == 0
    assert empty["ha_entities"] == []
