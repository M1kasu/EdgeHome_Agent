"""Retire the legacy template TV before MQTT Switch claims its stable ID."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENTITY_ID = "switch.edgehome_living_room_tv"


def migrate(config_dir: Path, *, dry_run: bool = False) -> dict[str, object]:
    """Remove only TV template ownership while preserving protected template switches."""
    template = config_dir / "edgehome_demo_templates.yaml"
    registry = config_dir / ".storage" / "core.entity_registry"
    if not template.exists() or not registry.exists():
        raise FileNotFoundError("Home Assistant template or entity registry is missing")
    document = yaml.safe_load(template.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError("template document must be a list")
    removed = 0
    for section in document:
        if not isinstance(section, dict) or not isinstance(section.get("switch"), list):
            continue
        switches = section["switch"]
        kept = [item for item in switches if not isinstance(item, dict) or item.get("unique_id") != "edgehome_living_room_tv"]
        removed += len(switches) - len(kept)
        section["switch"] = kept
    records_doc = json.loads(registry.read_text(encoding="utf-8"))
    records = records_doc.setdefault("data", {}).setdefault("entities", [])
    matching = [record for record in records if isinstance(record, dict) and record.get("entity_id") == ENTITY_ID]
    if any(record.get("platform") not in {"template", "mqtt"} for record in matching):
        raise ValueError("TV entity id is owned by an unrelated platform")
    mqtt_records = [record for record in matching if record.get("platform") == "mqtt"]
    template_records = [record for record in matching if record.get("platform") == "template"]
    result = {
        "entity_id": ENTITY_ID,
        "template_records_removed": removed,
        "registry_records_removed": len(template_records),
        "action": "already_migrated" if mqtt_records and not removed and not template_records else "migrate",
    }
    if dry_run:
        return result
    if result["action"] == "already_migrated":
        return result
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path in (template, registry):
        path.with_name(f"{path.name}.edgehome-tv-backup-{stamp}").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    template.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
    records_doc["data"]["entities"] = [record for record in records if record not in template_records]
    registry.write_text(json.dumps(records_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "deployment" / "homeassistant")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.config_dir, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
