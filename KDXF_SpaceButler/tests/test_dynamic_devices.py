from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from device_simulator.spacebutler_simulator.app import (
    BinarySensorDevice,
    _binary_sensor_state,
    _device_from_definition,
    _entity_id,
    _normalize_device_definition,
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

    def test_presence_report_preserves_since_until_occupancy_changes(self) -> None:
        initial = _binary_sensor_state(
            "presence",
            {"occupied": False, "unoccupied_minutes": 23},
        )
        repeated = _binary_sensor_state("presence", initial)
        occupied = _binary_sensor_state("presence", {**initial, "occupied": True})

        self.assertEqual(repeated["unoccupied_since"], initial["unoccupied_since"])
        self.assertEqual(occupied, {"occupied": True, "unoccupied_since": None})


if __name__ == "__main__":
    unittest.main()
