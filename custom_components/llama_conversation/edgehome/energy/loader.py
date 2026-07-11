"""严格读取本地 CSV；不把原始 CSV 传给模型。"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .schemas import EnergyLoadResult, EnergyRecord


_REQUIRED_FIELDS = {
    "timestamp",
    "entity_id",
    "device_name",
    "room",
    "power_w",
    "energy_kwh",
    "state",
    "occupancy",
    "temperature",
    "scene",
}


class EnergyDatasetLoader:
    """将 CSV 规范化为排序、去重的 EnergyRecord 列表。"""

    def load(self, path: Path) -> EnergyLoadResult:
        if not path.exists():
            return EnergyLoadResult(success=False, error_code="DATASET_NOT_FOUND")
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or not _REQUIRED_FIELDS.issubset(reader.fieldnames):
                    return EnergyLoadResult(success=False, error_code="MISSING_COLUMNS")
                records: list[EnergyRecord] = []
                rejected = 0
                duplicates = 0
                keys: set[tuple[datetime, str]] = set()
                for row in reader:
                    try:
                        record = EnergyRecord(
                            timestamp=_timestamp(row["timestamp"]),
                            entity_id=row["entity_id"],
                            device_name=row["device_name"],
                            room=_room(row["room"]),
                            power_w=float(row["power_w"]),
                            energy_kwh=float(row["energy_kwh"]),
                            state=row["state"],
                            occupancy=_boolean(row["occupancy"]),
                            temperature=float(row["temperature"]),
                            scene=row["scene"],
                        )
                    except (KeyError, TypeError, ValueError, ValidationError):
                        rejected += 1
                        continue
                    key = (record.timestamp, record.entity_id)
                    if key in keys:
                        duplicates += 1
                        continue
                    keys.add(key)
                    records.append(record)
        except OSError:
            return EnergyLoadResult(success=False, error_code="DATASET_UNREADABLE")
        return EnergyLoadResult(
            success=True,
            records=sorted(records, key=lambda item: (item.timestamp, item.entity_id)),
            rejected_rows=rejected,
            duplicate_rows=duplicates,
        )


def _timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamp 必须含时区")
    return timestamp.astimezone(timezone.utc)


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("occupancy 必须是 true 或 false")


def _room(value: str) -> Literal["living_room", "bedroom", "home"]:
    if value == "living_room":
        return "living_room"
    if value == "bedroom":
        return "bedroom"
    if value == "home":
        return "home"
    raise ValueError("room 不受支持")
