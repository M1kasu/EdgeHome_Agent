"""Real MQTT device identities exposed through Home Assistant discovery.

These records describe device containers that are actually deployed and online.
This module does not create simulated devices; it only lets the workbench show
MQTT-backed entities that Home Assistant already reports, alongside the
ESPHome nodes described in :mod:`spacebutler.esphome_registry`.
"""

from __future__ import annotations

from typing import Final


# Each record maps one deployed MQTT device container to its Home Assistant
# entity. Matching is by domain plus friendly name, mirroring the ESPHome
# registry so both sources share one projection path.
MQTT_DEVICE_RECORDS: Final[tuple[dict[str, object], ...]] = (
    {
        "aliases": ("卧室阅读灯",),
        "domain": "light",
        "device_id": "bedroom_reading_light",
        "type": "light",
        "name": "卧室阅读灯",
        "room": "bedroom",
        "capabilities": ("power", "brightness"),
    },
    {
        "aliases": ("客厅电视",),
        "domain": "switch",
        "device_id": "living_room_tv",
        "type": "switch",
        "name": "客厅电视",
        "room": "living_room",
        "capabilities": ("power",),
        # 电视功耗是独立的 MQTT sensor 实体，投影时读它填充 power_w。
        "power_entity_id": "sensor.spacebutler_living_room_tv_power",
    },
    {
        "aliases": ("客厅窗户传感器",),
        "domain": "binary_sensor",
        "device_id": "living_room_window_sensor",
        "type": "contact",
        "name": "客厅窗户传感器",
        "room": "living_room",
        "capabilities": (),
    },
    {
        "aliases": ("卧室起身传感器",),
        "domain": "binary_sensor",
        "device_id": "bedroom_presence_sensor",
        "type": "presence",
        "name": "卧室起身传感器",
        "room": "bedroom",
        "capabilities": (),
    },
    {
        "aliases": ("卧室照度传感器",),
        "domain": "sensor",
        "device_id": "bedroom_illuminance_sensor",
        "type": "illuminance",
        "name": "卧室照度传感器",
        "room": "bedroom",
        "capabilities": (),
    },
)


MQTT_ALIAS_REGISTRY: Final[dict[str, dict[str, object]]] = {
    str(alias): record
    for record in MQTT_DEVICE_RECORDS
    for alias in record["aliases"]
}


def device_record(device_id: str) -> dict[str, object] | None:
    """Look up one deployed MQTT device definition by stable device id."""
    for record in MQTT_DEVICE_RECORDS:
        if record["device_id"] == device_id:
            return dict(record)
    return None
