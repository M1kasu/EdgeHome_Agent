from __future__ import annotations

from http import HTTPStatus
import unittest

from device_simulator.spacebutler_simulator.fleet_gateway import (
    FleetError,
    FleetGateway,
    RuntimeEndpoint,
    _device_id_from_path,
)


class _FakeCatalog:
    def __init__(self, runtimes: list[RuntimeEndpoint]) -> None:
        self._runtimes = runtimes

    def runtimes(self) -> list[RuntimeEndpoint]:
        return self._runtimes

    def require(self, device_id: str) -> RuntimeEndpoint:
        endpoint = next((item for item in self._runtimes if item.device_id == device_id), None)
        if endpoint is None:
            raise FleetError(HTTPStatus.NOT_FOUND, f"unknown device: {device_id}")
        return endpoint


class DeviceFleetGatewayTest(unittest.TestCase):
    def test_offline_runtime_remains_visible_in_fleet_inventory(self) -> None:
        endpoint = RuntimeEndpoint(
            device_id="bedroom_reading_light",
            source="configured",
            device_type="light",
            name="Bedroom Reading Light",
            room="bedroom",
            container_name="spacebutler-device-bedroom-reading-light",
            status="exited",
            url=None,
            volume_name="spacebutler-device-data-bedroom_reading_light",
        )
        gateway = FleetGateway(_FakeCatalog([endpoint]))  # type: ignore[arg-type]

        health = gateway.health()
        inventory = gateway.devices()

        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["runtime_running"], 0)
        self.assertEqual(health["runtime_healthy"], 0)
        self.assertEqual(inventory["runtime_total"], 1)
        self.assertEqual(inventory["devices"][0]["device_id"], "bedroom_reading_light")
        self.assertFalse(inventory["devices"][0]["online"])
        self.assertEqual(inventory["devices"][0]["fault_mode"], "container_unavailable")
        self.assertEqual(inventory["devices"][0]["runtime"]["isolation"], "dedicated")

    def test_device_request_to_stopped_runtime_is_rejected(self) -> None:
        endpoint = RuntimeEndpoint(
            device_id="bedroom_presence_sensor",
            source="configured",
            device_type="presence",
            name="Bedroom Presence",
            room="bedroom",
            container_name="spacebutler-device-bedroom-presence",
            status="exited",
            url=None,
            volume_name=None,
        )
        gateway = FleetGateway(_FakeCatalog([endpoint]))  # type: ignore[arg-type]

        with self.assertRaises(FleetError) as raised:
            gateway.forward("GET", "/devices/bedroom_presence_sensor")

        self.assertEqual(raised.exception.status, HTTPStatus.SERVICE_UNAVAILABLE)

    def test_device_id_is_extracted_from_read_and_admin_paths(self) -> None:
        self.assertEqual(_device_id_from_path("/devices/study_light"), "study_light")
        self.assertEqual(
            _device_id_from_path("/admin/devices/study_light/fault"),
            "study_light",
        )


if __name__ == "__main__":
    unittest.main()
