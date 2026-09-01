"""Real ESPHome node endpoints and Home Assistant entity aliases.

This module describes deployed hardware identities only. It does not create
simulated devices or choose scene actions.
"""

from __future__ import annotations

import os
import re
from typing import Final
import unicodedata


DEFAULT_ESPHOME_HOST: Final = "192.168.111.134"

# role, port, entity metadata. Every container listens on 6053 internally;
# these are the Ubuntu VM host ports published by its Compose project.
ESPHOME_NODE_PORTS: Final[tuple[tuple[str, int], ...]] = (
    ("light", 6053),
    ("curtain", 6054),
    ("ac", 6055),
    ("presence", 6056),
    ("bedroom", 6057),
    ("living_room_aux", 6058),
)


def esphome_nodes(host: str | None = None) -> tuple[tuple[str, str, int], ...]:
    """Return configured ESPHome endpoints, defaulting to the Ubuntu VM."""
    resolved_host = host or os.environ.get("ESPHOME_HOST") or DEFAULT_ESPHOME_HOST
    return tuple((role, resolved_host, port) for role, port in ESPHOME_NODE_PORTS)


# 哪台设备由哪个节点提供。一个节点可以承载多台设备（卧室节点有灯和两个传感器），
# 所以不能按设备类型反推节点角色。
NODE_ROLE_BY_DEVICE: Final[dict[str, str]] = {
    "living_room_main_light": "light",
    "living_room_curtain": "curtain",
    "living_room_ac": "ac",
    "living_room_temperature_sensor": "ac",
    "living_room_presence_sensor": "presence",
    "bedroom_reading_light": "bedroom",
    "bedroom_presence_sensor": "bedroom",
    "bedroom_illuminance_sensor": "bedroom",
    "living_room_tv": "living_room_aux",
    "living_room_window_sensor": "living_room_aux",
}


ESPHOME_DEVICE_RECORDS: Final[tuple[dict[str, object], ...]] = (
    {
        "aliases": (
            "Living Room Light",
            "SpaceButler Living Room Main",
            "SpaceButler Living Room Light",
            "Main",
        ),
        "domain": "light",
        "entity_env": "SPACEBUTLER_ENTITY_LIGHT",
        "node_names": ("living_room_light",),
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
        "entity_env": "SPACEBUTLER_ENTITY_CURTAIN",
        "node_names": ("living_room_curtain",),
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
        "entity_env": "SPACEBUTLER_ENTITY_CLIMATE",
        "node_names": ("living_room_ac",),
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
        "entity_env": "SPACEBUTLER_ENTITY_PRESENCE",
        "node_names": ("living_room_presence",),
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
        "entity_env": "SPACEBUTLER_ENTITY_TEMPERATURE",
        "node_names": ("living_room_ac", "living_room_temperature"),
        "device_id": "living_room_temperature_sensor",
        "type": "temperature",
        "name": "客厅温度传感器",
        "room": "living_room",
        "capabilities": (),
    },
    {
        "aliases": ("Bedroom Reading Light",),
        "domain": "light",
        "entity_env": "SPACEBUTLER_ENTITY_BEDROOM_LIGHT",
        "node_names": ("bedroom",),
        "device_id": "bedroom_reading_light",
        "type": "light",
        "name": "卧室阅读灯",
        "room": "bedroom",
        "capabilities": ("power", "brightness"),
    },
    {
        "aliases": ("Bedroom Presence",),
        "domain": "binary_sensor",
        "entity_env": "SPACEBUTLER_ENTITY_BEDROOM_PRESENCE",
        "node_names": ("bedroom",),
        "device_id": "bedroom_presence_sensor",
        "type": "presence",
        "name": "卧室起身传感器",
        "room": "bedroom",
        "capabilities": (),
    },
    {
        "aliases": ("Bedroom Illuminance",),
        "domain": "sensor",
        "entity_env": "SPACEBUTLER_ENTITY_BEDROOM_ILLUMINANCE",
        "node_names": ("bedroom",),
        "device_id": "bedroom_illuminance_sensor",
        "type": "illuminance",
        "name": "卧室照度传感器",
        "room": "bedroom",
        "capabilities": (),
    },
    {
        "aliases": ("Living Room TV",),
        "domain": "switch",
        "entity_env": "SPACEBUTLER_ENTITY_TV",
        "node_names": ("living_room_aux",),
        "device_id": "living_room_tv",
        "type": "switch",
        "name": "客厅电视",
        "room": "living_room",
        "capabilities": ("power",),
        # 功耗是同节点的独立 sensor 实体，投影时读它填充 power_w。
        "power_entity_id": "sensor.living_room_tv_power",
        "power_entity_env": "SPACEBUTLER_ENTITY_TV_POWER",
    },
    {
        "aliases": ("Living Room Window",),
        "domain": "binary_sensor",
        "entity_env": "SPACEBUTLER_ENTITY_WINDOW",
        "node_names": ("living_room_aux",),
        "device_id": "living_room_window_sensor",
        "type": "contact",
        "name": "客厅窗户传感器",
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


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", unicodedata.normalize("NFKC", value).strip().casefold())


def _configured_entity(record: dict[str, object]) -> str:
    return os.environ.get(str(record["entity_env"]), "").strip()


def entity_matches(entity_id: str, friendly_name: str, record: dict[str, object]) -> bool:
    """Match known identities, never an arbitrary suffix such as 'light'.

    HA can prepend the ESPHome node name to the component name and retains an
    entity_id when users rename a display label. Explicit bindings take priority
    over discovery and never silently fall back to a different entity.
    """
    domain = f"{record['domain']}."
    if not entity_id.startswith(domain):
        return False
    configured = _configured_entity(record)
    if configured:
        return entity_id == configured
    aliases = {_normalize_name(str(item)) for item in record["aliases"]}
    base_labels = aliases | {_normalize_name(str(record["name"]))}
    labels = set(base_labels)
    # Short legacy display aliases remain supported, but do not authorize broad
    # entity IDs such as light.main or sensor.temperature by themselves.
    object_ids = {name for name in aliases if "_" in name} | {str(record["device_id"])}
    for node in record["node_names"]:
        prefixed = {f"{node}_{alias}" for alias in base_labels}
        object_ids.update(prefixed)
        labels.update(prefixed)
    object_id = re.sub(r"_\d+$", "", entity_id[len(domain):])
    return _normalize_name(friendly_name) in labels or object_id in object_ids


def discover_entities(states: list[dict[str, object]]) -> tuple[dict[str, str], dict[str, object]]:
    """Resolve unique role bindings and explain missing/ambiguous HA entities.

    Only scalar identity/state fields enter diagnostics; credentials and the
    arbitrary attributes of unrelated HA entities are never copied here.
    """
    domains = {str(record["domain"]) for record in ESPHOME_DEVICE_RECORDS}
    candidates: list[dict[str, str]] = []
    for state in states:
        entity_id = str(state.get("entity_id", ""))
        if entity_id.partition(".")[0] not in domains:
            continue
        attributes = state.get("attributes")
        friendly = str(attributes.get("friendly_name", "")) if isinstance(attributes, dict) else ""
        candidates.append({"entity_id": entity_id, "friendly_name": friendly, "state": str(state.get("state", ""))})
    candidates.sort(key=lambda item: item["entity_id"])
    resolved: dict[str, str] = {}
    missing: list[dict[str, object]] = []
    for record in ESPHOME_DEVICE_RECORDS:
        configured = _configured_entity(record)
        valid_binding = not configured or bool(re.fullmatch(rf"{record['domain']}\.[a-z0-9_]+", configured))
        matches = sorted({item["entity_id"] for item in candidates if entity_matches(
            item["entity_id"], item["friendly_name"], record,
        )}) if valid_binding else []
        if len(matches) == 1:
            resolved[str(record["device_id"])] = matches[0]
            continue
        reason = "invalid_binding" if not valid_binding else (
            "ambiguous" if len(matches) > 1 else "configured_entity_missing" if configured else "not_found"
        )
        missing.append({"device_id": record["device_id"], "name": record["name"],
                        "entity_env": record["entity_env"], "reason": reason, "candidates": matches})
    return resolved, {"ha_entity_count": len(states), "candidate_entity_count": len(candidates),
                      "matched_device_count": len(resolved), "bindings": resolved,
                      "missing_devices": missing, "ha_entities": candidates}


def resolve_entity_id(states: list[dict[str, object]], device_id: str) -> str:
    """Use the same unique mapping for device controls and the workbench."""
    record = device_record(device_id)
    if record is None:
        raise RuntimeError(f"unknown ESPHome device id: {device_id}")
    resolved, diagnostics = discover_entities(states)
    if device_id in resolved:
        return resolved[device_id]
    missing = next(item for item in diagnostics["missing_devices"] if item["device_id"] == device_id)
    raise RuntimeError(
        f"Home Assistant entity mapping failed for {device_id}: {missing['reason']}; "
        f"set {record['entity_env']} to the intended entity_id"
    )
