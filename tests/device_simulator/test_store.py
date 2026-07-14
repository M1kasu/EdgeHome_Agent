"""Tests for the simulator-owned SQLite state boundary."""

from __future__ import annotations

from device_simulator.edgehome_simulator.store import DeviceStateStore


def test_device_state_is_persistent_and_reset_by_the_simulator(tmp_path) -> None:
    store = DeviceStateStore(tmp_path / "device_state.db")
    initial = {"power": "ON", "brightness": 204}

    created = store.ensure_device("living_room_main_light", initial)
    changed = store.update_state("living_room_main_light", {"power": "ON", "brightness": 128}, "req-1")
    reopened = DeviceStateStore(tmp_path / "device_state.db").get_device("living_room_main_light")
    reset = store.reset_device("living_room_main_light", initial)

    assert created["state"] == initial
    assert changed["state"] == {"power": "ON", "brightness": 128}
    assert reopened is not None and reopened["state"] == {"power": "ON", "brightness": 128}
    assert reset["state"] == initial
    assert reset["online"] is True
    assert reset["fault_mode"] == "none"


def test_fault_injection_is_persisted_and_audited(tmp_path) -> None:
    store = DeviceStateStore(tmp_path / "device_state.db")
    store.ensure_device("living_room_main_light", {"power": "OFF", "brightness": 0})

    offline = store.set_fault("living_room_main_light", "offline")
    acknowledgement = store.set_fault("living_room_main_light", "ack_without_state_change")
    events = store.events()

    assert offline["online"] is False
    assert acknowledgement["online"] is True
    assert acknowledgement["fault_mode"] == "ack_without_state_change"
    assert [event["event_type"] for event in events].count("fault_changed") == 2
