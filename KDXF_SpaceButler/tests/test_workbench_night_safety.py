from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock

from spacebutler.workbench import (
    WorkbenchController,
    _illuminance_value,
    _night_device_state,
    _presence_occupied,
)


class WorkbenchNightSafetyTests(unittest.TestCase):
    def test_sensor_helpers_prefer_home_assistant_entity_state(self) -> None:
        source = {
            "state": {"occupied": False, "illuminance": 200},
            "ha_state": {"state": "on", "attributes": {}},
        }
        illuminance = {
            "state": {"illuminance": 200},
            "ha_state": {"state": "8", "attributes": {}},
        }

        self.assertTrue(_presence_occupied(source))
        self.assertEqual(_illuminance_value(illuminance), 8)

    def test_monitor_executes_once_for_false_to_true_edge(self) -> None:
        controller = object.__new__(WorkbenchController)
        controller._lock = threading.RLock()
        controller._night_presence_state = None
        controller._night_monitor_error = "old error"
        controller.night_safety = Mock(
            side_effect=[
                {"occupied": False, "monitoring": True},
                {"occupied": True, "monitoring": True},
                {"occupied": True, "monitoring": True},
            ]
        )
        controller.run_night_safety = Mock(return_value={"response": {"status": "executed"}})

        self.assertIsNone(controller.poll_night_safety())
        self.assertEqual(controller.poll_night_safety(), {"status": "executed"})
        self.assertIsNone(controller.poll_night_safety())
        controller.run_night_safety.assert_called_once_with()
        self.assertIsNone(controller._night_monitor_error)

    def test_registry_power_state_survives_compact_path_light_mapping(self) -> None:
        device = _night_device_state(
            {
                "entity_id": "light.spacebutler_living_room_main",
                "room": "living_room",
                "state": {"power": "ON", "brightness": 140},
                "online": True,
                "discovered": True,
            },
            "elder_01",
            2,
            manual_control=True,
        )

        self.assertEqual(device.state, "on")
        self.assertEqual(device.attributes["brightness_pct"], 55)
        self.assertEqual(device.attributes["night_path_order"], 2)
        self.assertTrue(device.attributes["manual_control"])
        self.assertTrue(device.attributes["available"])


if __name__ == "__main__":
    unittest.main()
