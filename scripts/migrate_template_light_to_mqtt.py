"""Retire the legacy template-light registry record before MQTT Discovery claims its stable entity id.

Run only while Home Assistant is stopped. The script creates a timestamped backup and
performs an atomic JSON replacement, matching the deployment safety rules used by the
existing runtime configuration utility.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG_DIR = _ROOT / "deployment" / "homeassistant"
_ENTITY_ID = "light.edgehome_living_room_main"


def migrate(config_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    """Delete only the former template record, preserving every unrelated registry item."""
    path = config_dir / ".storage" / "core.entity_registry"
    if not path.exists():
        raise FileNotFoundError(f"未找到 Home Assistant 实体注册表：{path}")
    document = _read_json(path)
    records = document.setdefault("data", {}).setdefault("entities", [])
    if not isinstance(records, list):
        raise ValueError("core.entity_registry 的 entities 不是列表")

    matching = [
        record
        for record in records
        if isinstance(record, dict) and record.get("entity_id") == _ENTITY_ID
    ]
    if not matching:
        return {"entity_id": _ENTITY_ID, "action": "no_record"}
    if any(record.get("platform") != "template" for record in matching):
        return {"entity_id": _ENTITY_ID, "action": "already_migrated", "platforms": [record.get("platform") for record in matching]}

    document["data"]["entities"] = [record for record in records if record not in matching]
    summary = {"entity_id": _ENTITY_ID, "action": "removed_template_record", "count": len(matching)}
    if dry_run:
        return summary

    backup_suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.edgehome-mqtt-backup-{backup_suffix}.json")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    temporary = path.with_suffix(f"{path.suffix}.edgehome-mqtt.tmp")
    temporary.write_text(f"{json.dumps(document, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    temporary.replace(path)
    return summary


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 的根节点不是 JSON 对象")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=_DEFAULT_CONFIG_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.config_dir, dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
