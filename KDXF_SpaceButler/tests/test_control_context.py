from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from spacebutler.control_context import ControlContextRegistry


class ControlContextRegistryTests(unittest.TestCase):
    def test_manual_context_persists_source_and_expiry(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "household.db"
            created = ControlContextRegistry(database_path).set_manual(
                "bedroom_path_light",
                source="wall_switch",
                ttl_seconds=900,
            )

            restored = ControlContextRegistry(database_path).get("bedroom_path_light")

            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.source, "wall_switch")
            self.assertGreater(restored.remaining_seconds, 0)
            self.assertEqual(restored.expires_at, created.expires_at)

    def test_clearing_manual_context_is_persistent(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "household.db"
            registry = ControlContextRegistry(database_path)
            registry.set_manual("bedroom_path_light")

            registry.clear_manual("bedroom_path_light")

            self.assertIsNone(ControlContextRegistry(database_path).get("bedroom_path_light"))


if __name__ == "__main__":
    unittest.main()
