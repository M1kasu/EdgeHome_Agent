from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from spacebutler.proactive import ENERGY_RULE_ID, ProactiveRuleStore
from spacebutler.workbench import _resolve_rule_config, _unoccupied_minutes


class ProactiveBindingTest(unittest.TestCase):
    def test_default_binding_selects_a_room_with_all_required_sources(self) -> None:
        devices = [
            {"device_id": "bedroom_ac", "type": "climate", "room": "bedroom"},
            {"device_id": "study_presence", "type": "presence", "room": "study"},
            {"device_id": "study_window", "type": "contact", "room": "study"},
            {"device_id": "study_ac", "type": "climate", "room": "study"},
        ]

        config = _resolve_rule_config(None, devices)

        self.assertEqual(config["room"], "study")
        self.assertEqual(config["presence_device_id"], "study_presence")
        self.assertEqual(config["contact_device_id"], "study_window")
        self.assertEqual(config["climate_device_id"], "study_ac")

    def test_rule_binding_persists_separately_from_preferences(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "household.db"
            config = {
                "enabled": True,
                "room": "study",
                "presence_device_id": "study_presence",
                "contact_device_id": "study_window",
                "climate_device_id": "study_ac",
            }
            ProactiveRuleStore(database_path).save(ENERGY_RULE_ID, config)

            restored = ProactiveRuleStore(database_path).load(ENERGY_RULE_ID)

            self.assertEqual(restored, config)

    def test_unoccupied_duration_comes_from_sensor_timestamp(self) -> None:
        since = datetime.now(timezone.utc) - timedelta(minutes=27, seconds=5)
        source = {
            "state": {
                "occupied": False,
                "unoccupied_since": since.isoformat(),
            }
        }

        self.assertGreaterEqual(_unoccupied_minutes(source), 27)
        self.assertEqual(_unoccupied_minutes({"state": {"occupied": True}}), 0)


if __name__ == "__main__":
    unittest.main()
