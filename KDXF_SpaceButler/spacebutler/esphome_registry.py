"""Real ESPHome node endpoints and Home Assistant entity aliases.

This module describes deployed hardware identities only. It does not create
simulated devices or choose scene actions.
"""

from __future__ import annotations

import os
from typing import Final


DEFAULT_ESPHOME_HOST: Final = "192.168.111.134"

# role, port, entity metadata. Every container listens on 6053 internally;
# these are the Ubuntu VM host ports published by its Compose project.
ESPHOME_NODE_PORTS: Final[tuple[tuple[str, int], ...]] = (
    ("light", 6053),
    ("curtain", 6054),
    ("ac", 6055),
    ("presence", 6056),
)


def esphome_nodes(host: str | None = None) -> tuple[tuple[str, str, int], ...]:
    """Return configured ESPHome endpoints, defaulting to the Ubuntu VM."""
    resolved_host = host or os.environ.get("ESPHOME_HOST") or DEFAULT_ESPHOME_HOST
    return tuple((role, resolved_host, port) for role, port in ESPHOME_NODE_PORTS)


ESPHOME_DEVICE_RECORDS: Final[tuple[dict[str, object], ...]] = (
    {
        "aliases": (
            "Living Room Light",
            "SpaceButler Living Room Main",
            "SpaceButler Living Room Light",
            "Main",
        ),
        "domain": "light",
        "device_id": "living_room_main_light",
        "type": "light",
        "name": "客厅主灯",
        "room": "living_room",
        "capabilities": ("power", "brightness"),
    },
    {
        "aliases": (
            "Living Room Curtain",
            "SpaceButler Living Room Curtain",
            "Curtain",
        ),
        "domain": "cover",
        "device_id": "living_room_curtain",
        "type": "curtain",
        "name": "客厅窗帘",
        "room": "living_room",
        "capabilities": ("position",),
    },
    {
        "aliases": (
            "Living Room AC",
            "SpaceButler Living Room AC",
            "AC",
        ),
        "domain": "climate",
        "device_id": "living_room_ac",
        "type": "climate",
        "name": "客厅空调",
        "room": "living_room",
        "capabilities": ("mode", "temperature"),
    },
    {
        "aliases": (
            "Living Room Presence",
            "SpaceButler Living Room Presence",
            "Presence",
        ),
        "domain": "binary_sensor",
        "device_id": "living_room_presence_sensor",
        "type": "presence",
        "name": "客厅人体存在传感器",
        "room": "living_room",
        "capabilities": (),
    },
    {
        "aliases": (
            "Living Room Temperature",
            "SpaceButler Living Room Temperature",
            "Temperature",
        ),
        "domain": "sensor",
        "device_id": "living_room_temperature_sensor",
        "type": "temperature",
        "name": "客厅温度传感器",
        "room": "living_room",
        "capabilities": (),
    },
)


ESPHOME_ALIAS_REGISTRY: Final[dict[str, dict[str, object]]] = {
    str(alias): record
    for record in ESPHOME_DEVICE_RECORDS
    for alias in record["aliases"]
}


def device_record(device_id: str) -> dict[str, object] | None:
    """Look up one real ESPHome entity definition by stable device id."""
    for record in ESPHOME_DEVICE_RECORDS:
        if record["device_id"] == device_id:
            return dict(record)
    return None


def entity_matches(entity_id: str, friendly_name: str, record: dict[str, object]) -> bool:
    """Return whether one HA state belongs to a deployed ESPHome entity."""
    aliases = tuple(str(item) for item in record.get("aliases", ()))
    return entity_id.startswith(f"{record['domain']}.") and friendly_name in aliases


def resolve_entity_id(states: list[dict[str, object]], device_id: str) -> str:
    """Resolve an HA entity id using domain plus every known friendly-name alias."""
    record = device_record(device_id)
    if record is None:
        raise RuntimeError(f"unknown ESPHome device id: {device_id}")
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        attributes = item.get("attributes")
        friendly = str(attributes.get("friendly_name", "")) if isinstance(attributes, dict) else ""
        if entity_matches(entity_id, friendly, record):
            return entity_id
    raise RuntimeError(
        f"Home Assistant entity not found for {device_id}; accepted aliases={record['aliases']}"
    )
