"""Unit coverage for M4's device-owned MQTT TV protocol."""

from device_simulator.edgehome_simulator.app import SwitchDevice, _power_discovery, _switch_discovery, _switch_state


def _tv() -> SwitchDevice:
    return SwitchDevice(
        device_id="living_room_tv",
        name="客厅电视",
        room="living_room",
        initial_state={"power": "OFF", "power_w": 1.5},
        command_delay_ms=150,
        failure_rate=0,
    )


def test_tv_switch_discovery_has_a_stable_mqtt_identity() -> None:
    discovery = _switch_discovery(_tv())

    assert discovery["default_entity_id"] == "switch.edgehome_living_room_tv"
    assert discovery["command_topic"] == "edgehome/devices/living_room_tv/command"
    assert discovery["state_topic"] == "edgehome/devices/living_room_tv/state"
    assert discovery["payload_on"] == "ON"
    assert discovery["payload_off"] == "OFF"


def test_tv_power_sensor_is_read_only_and_tracks_availability() -> None:
    discovery = _power_discovery(_tv())

    assert discovery["default_entity_id"] == "sensor.edgehome_living_room_tv_power"
    assert discovery["state_topic"] == "edgehome/devices/living_room_tv/state"
    assert "command_topic" not in discovery
    assert discovery["payload_available"] == "online"
    assert discovery["payload_not_available"] == "offline"


def test_tv_power_state_rejects_invalid_values() -> None:
    assert _switch_state({"power": "ON", "power_w": 95}) == {"power": "ON", "power_w": 95.0}

    try:
        _switch_state({"power": "BROKEN", "power_w": 95})
    except ValueError as error:
        assert "invalid switch state" in str(error)
    else:
        raise AssertionError("invalid switch state must be rejected")
