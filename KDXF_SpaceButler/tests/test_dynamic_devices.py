from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest

from device_simulator.spacebutler_simulator.app import (
    BinarySensorDevice,
    DeviceSimulator,
    IlluminanceSensorDevice,
    SwitchDevice,
    _availability_config,
    _binary_sensor_state,
    _binary_sensor_discovery,
    _climate_discovery,
    _curtain_discovery,
    _device_from_definition,
    _entity_id,
    _feedback_discovery,
    _illuminance_discovery,
    _illuminance_state,
    _light_discovery,
    _normalize_device_definition,
    _power_discovery,
    _switch_discovery,
)
from device_simulator.spacebutler_simulator.store import DeviceStateStore


class DynamicDeviceRegistryTest(unittest.TestCase):
    def test_runtime_device_definition_and_state_survive_store_reconstruction(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "devices.db"
            device_id, definition = _normalize_device_definition(
                {
                    "device_id": "bedroom_reading_light",
                    "type": "light",
                    "name": "Bedroom Reading Light",
                    "room": "bedroom",
                    "initial_state": {"power": "ON", "brightness": 128},
                }
            )
            first_store = DeviceStateStore(database_path)
            first_store.save_definition(device_id, definition, source="runtime")
            first_store.ensure_device(device_id, _device_from_definition(device_id, definition).initial_state)

            reconstructed_store = DeviceStateStore(database_path)
            saved = reconstructed_store.definitions()
            state = reconstructed_store.get_device(device_id)

            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["source"], "runtime")
            self.assertEqual(saved[0]["definition"]["room"], "bedroom")
            self.assertEqual(state["state"], {"power": "ON", "brightness": 128})

    def test_dynamic_entity_id_is_stable_and_seed_ids_remain_compatible(self) -> None:
        dynamic_id, dynamic_definition = _normalize_device_definition(
            {
                "device_id": "study_desk_light",
                "type": "light",
                "name": "Study Desk Light",
                "room": "study",
            }
        )
        seed_id, seed_definition = _normalize_device_definition(
            {
                "device_id": "living_room_main_light",
                "type": "light",
                "name": "Living Room Main Light",
                "room": "living_room",
            }
        )

        self.assertEqual(
            _entity_id(_device_from_definition(dynamic_id, dynamic_definition)),
            "light.spacebutler_study_desk_light",
        )
        self.assertEqual(
            _entity_id(_device_from_definition(seed_id, seed_definition)),
            "light.spacebutler_living_room_main",
        )

    def test_only_runtime_devices_can_be_deleted(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = DeviceStateStore(Path(temporary_directory) / "devices.db")
            seed_id, seed_definition = _normalize_device_definition(
                {
                    "device_id": "living_room_tv",
                    "type": "switch",
                    "name": "Living Room TV",
                    "room": "living_room",
                }
            )
            runtime_id, runtime_definition = _normalize_device_definition(
                {
                    "device_id": "study_socket",
                    "type": "switch",
                    "name": "Study Socket",
                    "room": "study",
                }
            )
            for device_id, definition, source in (
                (seed_id, seed_definition, "configured"),
                (runtime_id, runtime_definition, "runtime"),
            ):
                store.save_definition(device_id, definition, source=source)
                store.ensure_device(device_id, _device_from_definition(device_id, definition).initial_state)

            with self.assertRaises(ValueError):
                store.delete_device(seed_id)
            removed = store.delete_device(runtime_id)

            self.assertEqual(removed["device_id"], runtime_id)
            self.assertIsNone(store.get_device(runtime_id))
            self.assertEqual([item["device_id"] for item in store.definitions()], [seed_id])

    def test_presence_and_contact_devices_use_binary_sensor_entities(self) -> None:
        presence_id, presence_definition = _normalize_device_definition(
            {
                "device_id": "study_presence",
                "type": "presence",
                "name": "Study Presence",
                "room": "study",
                "initial_state": {"occupied": False, "unoccupied_minutes": 25},
            }
        )
        contact_id, contact_definition = _normalize_device_definition(
            {
                "device_id": "study_window",
                "type": "contact",
                "name": "Study Window",
                "room": "study",
                "initial_state": {"open": True},
            }
        )

        presence = _device_from_definition(presence_id, presence_definition)
        contact = _device_from_definition(contact_id, contact_definition)

        self.assertIsInstance(presence, BinarySensorDevice)
        self.assertEqual(_entity_id(presence), "binary_sensor.spacebutler_study_presence")
        self.assertFalse(presence.initial_state["occupied"])
        self.assertIsInstance(presence.initial_state["unoccupied_since"], str)
        self.assertEqual(_entity_id(contact), "binary_sensor.spacebutler_study_window")
        self.assertEqual(contact.initial_state, {"open": True})

    def test_illuminance_sensor_uses_numeric_mqtt_discovery(self) -> None:
        device_id, definition = _normalize_device_definition(
            {
                "device_id": "bedroom_illuminance",
                "type": "illuminance",
                "name": "Bedroom Illuminance",
                "room": "bedroom",
                "initial_state": {"illuminance": 8},
            }
        )

        device = _device_from_definition(device_id, definition)
        discovery = _illuminance_discovery(device)

        self.assertIsInstance(device, IlluminanceSensorDevice)
        self.assertEqual(_entity_id(device), "sensor.spacebutler_bedroom_illuminance")
        self.assertEqual(_illuminance_state({"illuminance": "42"}), {"illuminance": 42})
        self.assertEqual(discovery["device_class"], "illuminance")
        self.assertEqual(discovery["unit_of_measurement"], "lx")
        self.assertEqual(discovery["state_class"], "measurement")

    def test_presence_report_preserves_since_until_occupancy_changes(self) -> None:
        initial = _binary_sensor_state(
            "presence",
            {"occupied": False, "unoccupied_minutes": 23},
        )
        repeated = _binary_sensor_state("presence", initial)
        occupied = _binary_sensor_state("presence", {**initial, "occupied": True})

        self.assertEqual(repeated["unoccupied_since"], initial["unoccupied_since"])
        self.assertEqual(occupied, {"occupied": True, "unoccupied_since": None})

    def test_discovery_entities_require_process_and_device_availability(self) -> None:
        cases = (
            ("study_light", "light", _light_discovery),
            ("study_curtain", "curtain", _curtain_discovery),
            ("study_ac", "climate", _climate_discovery),
            ("study_presence", "presence", _binary_sensor_discovery),
            ("study_window", "contact", _binary_sensor_discovery),
            ("study_illuminance", "illuminance", _illuminance_discovery),
            ("study_socket", "switch", _switch_discovery),
        )
        for device_id, device_type, discovery_factory in cases:
            with self.subTest(device_type=device_type):
                normalized_id, definition = _normalize_device_definition(
                    {
                        "device_id": device_id,
                        "type": device_type,
                        "name": f"Study {device_type}",
                        "room": "study",
                    }
                )
                device = _device_from_definition(normalized_id, definition)
                expected = _availability_config(device)

                for discovery in (discovery_factory(device), _feedback_discovery(device)):
                    self.assertNotIn("availability_topic", discovery)
                    self.assertEqual(discovery["availability_mode"], "all")
                    self.assertEqual(discovery["availability"], expected["availability"])
                    self.assertEqual(discovery["availability"][0]["topic"], "spacebutler/simulator/availability")
                    self.assertEqual(
                        discovery["availability"][1]["topic"],
                        f"spacebutler/devices/{device_id}/availability",
                    )

                if isinstance(device, SwitchDevice):
                    power_discovery = _power_discovery(device)
                    self.assertEqual(power_discovery["availability"], expected["availability"])

    def test_slow_command_preserves_device_fifo_without_blocking_other_devices(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            store = DeviceStateStore(Path(temporary_directory) / "devices.db")
            curtain_id, curtain_definition = _normalize_device_definition(
                {
                    "device_id": "study_curtain",
                    "type": "curtain",
                    "name": "Study Curtain",
                    "room": "study",
                }
            )
            light_id, light_definition = _normalize_device_definition(
                {
                    "device_id": "study_light",
                    "type": "light",
                    "name": "Study Light",
                    "room": "study",
                }
            )
            curtain = _device_from_definition(curtain_id, curtain_definition)
            light = _device_from_definition(light_id, light_definition)
            simulator = DeviceSimulator(
                {curtain.device_id: curtain, light.device_id: light},
                store,
                "127.0.0.1",
                1883,
            )
            curtain_started = threading.Event()
            curtain_release = threading.Event()
            curtain_second = threading.Event()
            light_handled = threading.Event()

            def handle_curtain(_device: object, raw: str, _topic: str) -> None:
                if raw == "25":
                    curtain_started.set()
                    self.assertTrue(curtain_release.wait(2))
                else:
                    curtain_second.set()

            def handle_light(_device: object, _payload: dict[str, object]) -> None:
                light_handled.set()

            simulator._handle_curtain_command = handle_curtain  # type: ignore[method-assign]
            simulator._handle_light_command = handle_light  # type: ignore[method-assign]
            try:
                simulator._on_message(None, None, SimpleNamespace(topic=curtain.set_position_topic, payload=b"25"))
                self.assertTrue(curtain_started.wait(1))

                simulator._on_message(None, None, SimpleNamespace(topic=curtain.set_position_topic, payload=b"75"))
                simulator._on_message(None, None, SimpleNamespace(topic=light.command_topic, payload=b'{"state":"ON"}'))

                self.assertTrue(light_handled.wait(0.5))
                self.assertFalse(curtain_second.wait(0.1))
                curtain_release.set()
                self.assertTrue(curtain_second.wait(1))
            finally:
                curtain_release.set()
                simulator.stop()


if __name__ == "__main__":
    unittest.main()
