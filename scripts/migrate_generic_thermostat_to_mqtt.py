"""Retire the legacy Generic Thermostat before MQTT Climate claims its stable ID."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTITY_ID = "climate.edgehome_living_room_ac"


def migrate(config_dir: Path, *, dry_run: bool = False) -> dict[str, object]:
    """Remove only the demo climate include and its template/thermostat registry record."""
    configuration = config_dir / "configuration.yaml"
    registry = config_dir / ".storage" / "core.entity_registry"
    if not configuration.exists() or not registry.exists():
        raise FileNotFoundError("Home Assistant configuration or entity registry is missing")
    lines = configuration.read_text(encoding="utf-8").splitlines()
    retained = [line for line in lines if not line.startswith("climate:") and "edgehome_demo_climate.yaml" not in line]
    removed_lines = len(lines) - len(retained)
    if not any(line.strip() == "unit_system: metric" for line in retained):
        retained.extend(("", "# EdgeHome MQTT climate accepts Chinese Celsius commands.", "homeassistant:", "  unit_system: metric"))
    document = json.loads(registry.read_text(encoding="utf-8"))
    records = document.setdefault("data", {}).setdefault("entities", [])
    if not isinstance(records, list):
        raise ValueError("entity registry records must be a list")
    matching = [record for record in records if isinstance(record, dict) and record.get("entity_id") == ENTITY_ID]
    if any(record.get("platform") not in {"generic_thermostat", "mqtt"} for record in matching):
        raise ValueError("climate entity id is owned by an unrelated platform")
    result = {"entity_id": ENTITY_ID, "configuration_lines_removed": removed_lines, "registry_records_removed": len(matching)}
    if dry_run:
        return result
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path in (configuration, registry):
        path.with_name(f"{path.name}.edgehome-ac-backup-{stamp}").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    configuration.write_text("\n".join(retained) + "\n", encoding="utf-8", newline="\n")
    document["data"]["entities"] = [record for record in records if record not in matching]
    registry.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "deployment" / "homeassistant")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.config_dir, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
