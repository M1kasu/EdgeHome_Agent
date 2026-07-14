"""Assign areas and safely expose only EdgeHome's virtual test entities.

This utility intentionally edits Home Assistant storage only while Home Assistant is
stopped. It creates complete backups, parses JSON before changing it, validates the
new JSON payloads, and uses atomic replacement for each file.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG_DIR = _ROOT / "deployment" / "homeassistant"
_AREAS = {
    "客厅": {"id": "ke_ting", "icon": "mdi:sofa"},
    "卧室": {"id": "wo_shi", "icon": "mdi:bed"},
    "全屋": {"id": "quan_wu", "icon": "mdi:home"},
}
_TARGETS: dict[str, tuple[str, str]] = {
    "light.edgehome_living_room_main": ("客厅", "客厅主灯"),
    "light.edgehome_living_room_ambient": ("客厅", "客厅氛围灯"),
    "switch.edgehome_living_room_tv": ("客厅", "客厅电视"),
    "climate.edgehome_living_room_ac": ("客厅", "客厅空调"),
    "cover.edgehome_living_room_curtain": ("客厅", "客厅窗帘"),
    "sensor.edgehome_living_room_temperature": ("客厅", "客厅温度"),
    "sensor.edgehome_living_room_illuminance": ("客厅", "客厅光照"),
    "binary_sensor.edgehome_living_room_presence": ("客厅", "客厅有人"),
    "light.edgehome_bedroom_main": ("卧室", "卧室主灯"),
    "climate.edgehome_bedroom_ac": ("卧室", "卧室空调"),
    "sensor.edgehome_bedroom_temperature": ("卧室", "卧室温度"),
    "binary_sensor.edgehome_bedroom_presence": ("卧室", "卧室有人"),
    "switch.edgehome_router": ("全屋", "路由器"),
    "switch.edgehome_refrigerator": ("全屋", "冰箱"),
    "switch.edgehome_test_socket": ("全屋", "测试插座"),
    "binary_sensor.edgehome_window": ("全屋", "窗户"),
    "input_boolean.edgehome_security_mode": ("全屋", "安防模式"),
}
_PROTECTED_ENTITY_IDS = frozenset({"switch.edgehome_router", "switch.edgehome_refrigerator"})


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path} 的根节点不是 JSON 对象")
    return value


def _write_json(path: Path, payload: dict[str, Any], backup_suffix: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    json.loads(encoded)  # Guard against changing a valid file into invalid JSON.
    backup = path.with_name(f"{path.name}.edgehome-demo-backup-{backup_suffix}.json")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temporary = path.with_suffix(f"{path.suffix}.edgehome-demo.tmp")
    temporary.write_text(f"{encoded}\n", encoding="utf-8")
    temporary.replace(path)


def configure(config_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    """Assign target entities to areas and expose no non-target entity."""
    storage = config_dir / ".storage"
    area_path = storage / "core.area_registry"
    entity_path = storage / "core.entity_registry"
    exposed_path = storage / "homeassistant.exposed_entities"
    for path in (area_path, entity_path, exposed_path):
        if not path.exists():
            raise FileNotFoundError(f"未找到 Home Assistant Storage 文件：{path}")

    areas_document = _read_json(area_path)
    entities_document = _read_json(entity_path)
    exposed_document = _read_json(exposed_path)
    now = datetime.now(timezone.utc).isoformat()
    area_records = areas_document.setdefault("data", {}).setdefault("areas", [])
    if not isinstance(area_records, list):
        raise ValueError("core.area_registry 的 areas 不是列表")
    known_area_ids = {item.get("name"): item.get("id") for item in area_records if isinstance(item, dict)}
    for name, definition in _AREAS.items():
        if name in known_area_ids:
            continue
        area_records.append(
            {
                "aliases": [],
                "floor_id": None,
                "humidity_entity_id": None,
                "icon": definition["icon"],
                "id": definition["id"],
                "labels": [],
                "name": name,
                "picture": None,
                "temperature_entity_id": None,
                "created_at": now,
                "modified_at": now,
            }
        )
        known_area_ids[name] = definition["id"]

    entity_records = entities_document.setdefault("data", {}).setdefault("entities", [])
    if not isinstance(entity_records, list):
        raise ValueError("core.entity_registry 的 entities 不是列表")
    found: set[str] = set()
    for record in entity_records:
        if not isinstance(record, dict):
            continue
        entity_id = record.get("entity_id")
        if entity_id not in _TARGETS:
            continue
        area_name, friendly_name = _TARGETS[entity_id]
        record["area_id"] = known_area_ids[area_name]
        record["name"] = friendly_name
        record["modified_at"] = now
        found.add(entity_id)
    missing = sorted(set(_TARGETS) - found)
    if missing:
        raise ValueError(f"虚拟实体尚未加载，不能公开：{', '.join(missing)}")

    exposed_records = exposed_document.setdefault("data", {}).setdefault("exposed_entities", {})
    if not isinstance(exposed_records, dict):
        raise ValueError("homeassistant.exposed_entities 的 exposed_entities 不是对象")
    for entity_id in _TARGETS:
        # 路由器和冰箱由 EdgeHome 的专属注册表管理；不允许通用 LLM 控制链路绕过二次确认。
        exposed_records[entity_id] = {
            "assistants": {"conversation": {"should_expose": entity_id not in _PROTECTED_ENTITY_IDS}}
        }

    summary = {
        "areas": {name: known_area_ids[name] for name in _AREAS},
        "exposed_entities": sorted(set(_TARGETS) - _PROTECTED_ENTITY_IDS),
        "protected_entities": sorted(_PROTECTED_ENTITY_IDS),
        "missing_entities": missing,
    }
    if dry_run:
        return summary

    backup_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _write_json(area_path, areas_document, backup_suffix)
    _write_json(entity_path, entities_document, backup_suffix)
    _write_json(exposed_path, exposed_document, backup_suffix)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=_DEFAULT_CONFIG_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = configure(args.config_dir, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
