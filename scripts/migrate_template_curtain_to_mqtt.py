"""Retire the legacy template curtain before MQTT Discovery claims its stable ID.

Run this while Home Assistant is stopped. The migration removes only the
``cover`` block from EdgeHome's included template YAML and the matching
template registry record; both files receive timestamped backups first.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = ROOT / "deployment" / "homeassistant"
ENTITY_ID = "cover.edgehome_living_room_curtain"
TEMPLATE_FILE = "edgehome_demo_templates.yaml"


def migrate(config_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Remove only the legacy EdgeHome curtain ownership records."""
    template_path = config_dir / TEMPLATE_FILE
    registry_path = config_dir / ".storage" / "core.entity_registry"
    if not template_path.exists() or not registry_path.exists():
        raise FileNotFoundError("Home Assistant template or entity registry file is missing")

    template_document = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    if not isinstance(template_document, list):
        raise ValueError("EdgeHome template document must be a YAML list")
    retained = [item for item in template_document if not isinstance(item, dict) or "cover" not in item]

    registry_document = json.loads(registry_path.read_text(encoding="utf-8"))
    records = registry_document.setdefault("data", {}).setdefault("entities", [])
    if not isinstance(records, list):
        raise ValueError("Home Assistant entity registry records must be a list")
    matching = [record for record in records if isinstance(record, dict) and record.get("entity_id") == ENTITY_ID]
    if any(record.get("platform") not in {"template", "mqtt"} for record in matching):
        raise ValueError("curtain entity id is owned by an unrelated platform")
    template_records = [record for record in matching if record.get("platform") == "template"]
    mqtt_records = [record for record in matching if record.get("platform") == "mqtt"]

    summary = {
        "entity_id": ENTITY_ID,
        "template_blocks_removed": len(template_document) - len(retained),
        "registry_records_removed": len(template_records),
        "action": "already_migrated" if mqtt_records and retained == template_document and not template_records else "migrate",
    }
    if dry_run:
        return summary
    if summary["action"] == "already_migrated":
        return summary

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path in (template_path, registry_path):
        path.with_name(f"{path.name}.edgehome-curtain-backup-{stamp}").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    template_path.write_text(yaml.safe_dump(retained, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
    registry_document["data"]["entities"] = [record for record in records if record not in template_records]
    registry_path.write_text(json.dumps(registry_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.config_dir, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
