from __future__ import annotations

from pathlib import Path
import unittest

import yaml

from spacebutler.workbench import _dashboard_projection, _device_ui, _room_summaries


def _device(
    device_id: str,
    device_type: str,
    room: str,
    state: dict[str, object],
    capabilities: list[str],
) -> dict[str, object]:
    device: dict[str, object] = {
        "device_id": device_id,
        "type": device_type,
        "name": device_id.replace("_", " ").title(),
        "room": room,
        "room_name": room.replace("_", " ").title(),
        "state": state,
        "online": True,
        "fault_mode": "none",
        "capabilities": capabilities,
    }
    device["ui"] = _device_ui(device)
    return device


class DashboardProjectionTests(unittest.TestCase):
    def test_layout_and_favorites_follow_arbitrary_inventory(self) -> None:
        devices = [
            _device("garage_lamp", "light", "garage", {"power": "ON", "brightness": 128}, ["turn_on", "turn_off", "set_brightness"]),
            _device("study_socket", "switch", "study", {"power": "OFF", "power_w": 1.5}, ["turn_on", "turn_off"]),
            _device("garage_presence", "presence", "garage", {"occupied": False}, ["report_occupied", "report_unoccupied"]),
        ]
        rooms = _room_summaries(devices)

        dashboard = _dashboard_projection(devices, rooms)

        self.assertEqual({item["room"] for item in dashboard["rooms"]}, {"garage", "study"})
        self.assertEqual(set(dashboard["device_positions"]), {item["device_id"] for item in devices})
        self.assertEqual(dashboard["favorite_device_ids"], ["garage_lamp", "study_socket"])
        self.assertNotIn("garage_presence", dashboard["favorite_device_ids"])

    def test_control_descriptors_contain_payloads_not_device_specific_buttons(self) -> None:
        climate = _device(
            "loft_climate",
            "climate",
            "loft",
            {"mode": "cool", "temperature": 25, "current_temperature": 27},
            ["set_mode", "set_temperature", "turn_off"],
        )

        ui = climate["ui"]

        self.assertEqual([control["kind"] for control in ui["controls"]], ["toggle", "segmented", "stepper"])
        self.assertEqual(ui["primary_action"]["payload"], {"mode": "off", "temperature": 25.0})
        self.assertEqual(ui["controls"][1]["options"][1]["payload"]["mode"], "cool")
        self.assertEqual(ui["floorplan"], {"shape": "thermostat", "readout": "27°"})

    def test_climate_controls_follow_live_supported_modes(self) -> None:
        climate = {
            "device_id": "loft_climate",
            "type": "climate",
            "name": "Loft Climate",
            "room": "loft",
            "room_name": "Loft",
            "state": {"mode": "cool", "temperature": 25, "current_temperature": 27},
            "online": True,
            "fault_mode": "none",
            "capabilities": ["set_mode", "set_temperature", "turn_off"],
            "ha_state": {"attributes": {"hvac_modes": ["off", "cool"]}},
        }

        ui = _device_ui(climate)
        mode_control = next(control for control in ui["controls"] if control["kind"] == "segmented")

        self.assertEqual([option["value"] for option in mode_control["options"]], ["off", "cool"])
        self.assertEqual(ui["primary_action"]["payload"], {"mode": "off", "temperature": 25.0})
        self.assertNotIn("heat", {option["value"] for option in mode_control["options"]})

    def test_semantic_device_icons_are_decided_by_backend_descriptor(self) -> None:
        television = _device("living_tv", "switch", "living_room", {"power": "OFF"}, ["turn_on", "turn_off"])
        reading_light = _device(
            "reading_light",
            "light",
            "bedroom",
            {"power": "ON", "brightness": 120},
            ["turn_on", "turn_off", "set_brightness"],
        )

        self.assertEqual(television["ui"]["icon"], "monitor")
        self.assertEqual(reading_light["ui"]["icon"], "lamp-desk")

    def test_frontend_contains_no_seed_device_ids_or_device_specific_actions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        configured = yaml.safe_load(
            (root / "device_simulator" / "config" / "devices.yaml").read_text(encoding="utf-8")
        )["devices"]
        frontend = "\n".join(
            (root / "workbench" / filename).read_text(encoding="utf-8")
            for filename in ("index.html", "app.js", "styles.css")
        )

        for device_id in configured:
            with self.subTest(device_id=device_id):
                self.assertNotIn(device_id, frontend)
        self.assertNotIn('data-device-action="toggle"', frontend)
        self.assertIn("data-control-payload", frontend)
        self.assertIn("data-floor-shape", frontend)
        self.assertNotIn(
            "state.floorSelectedId = state.dashboard?.favorite_device_ids?.[0]",
            frontend,
        )
        self.assertIn(
            "if (state.floorSelectedId && !state.devices.some",
            frontend,
        )


if __name__ == "__main__":
    unittest.main()
