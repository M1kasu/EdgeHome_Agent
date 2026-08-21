"""Persistent control provenance for automation/manual takeover decisions."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class ManualControlContext:
    device_id: str
    source: str
    started_at: datetime
    expires_at: datetime

    @property
    def remaining_seconds(self) -> int:
        return max(0, int((self.expires_at - datetime.now(timezone.utc)).total_seconds()))


class ControlContextRegistry:
    """Keep manual takeover explicit, persistent, and automatically expiring."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = Path(database_path) if database_path is not None else None
        self._memory: dict[str, ManualControlContext] = {}
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()

    def set_manual(
        self,
        device_id: str,
        *,
        source: str = "household_control",
        ttl_seconds: int = 1800,
    ) -> ManualControlContext:
        if not device_id.strip():
            raise ValueError("device_id is required")
        if not source.strip():
            raise ValueError("source is required")
        if not 0 < ttl_seconds <= 86_400:
            raise ValueError("ttl_seconds must be between 1 and 86400")
        now = datetime.now(timezone.utc)
        context = ManualControlContext(device_id, source, now, now + timedelta(seconds=ttl_seconds))
        self._memory[device_id] = context
        self._persist(context)
        return context

    def clear_manual(self, device_id: str) -> None:
        self._memory.pop(device_id, None)
        if self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute("DELETE FROM control_context WHERE device_id = ?", (device_id,))

    def get(self, device_id: str) -> ManualControlContext | None:
        context = self._memory.get(device_id)
        if context is None and self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                row = connection.execute(
                    "SELECT context_json FROM control_context WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
            if row is not None:
                context = _decode_context(row[0])
                self._memory[device_id] = context
        if context is None:
            return None
        if context.expires_at <= datetime.now(timezone.utc):
            self.clear_manual(device_id)
            return None
        return context

    def active(self, device_ids: list[str] | tuple[str, ...] | set[str]) -> dict[str, ManualControlContext]:
        return {
            device_id: context
            for device_id in device_ids
            if (context := self.get(device_id)) is not None
        }

    def clear_all(self) -> None:
        self._memory.clear()
        if self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute("DELETE FROM control_context")

    def _initialize_database(self) -> None:
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS control_context (
                        device_id TEXT PRIMARY KEY,
                        context_json TEXT NOT NULL
                    )
                    """
                )

    def _persist(self, context: ManualControlContext) -> None:
        if self._database_path is None:
            return
        value = json.dumps(
            {
                "device_id": context.device_id,
                "source": context.source,
                "started_at": context.started_at.isoformat(),
                "expires_at": context.expires_at.isoformat(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO control_context (device_id, context_json)
                    VALUES (?, ?)
                    ON CONFLICT(device_id) DO UPDATE SET context_json = excluded.context_json
                    """,
                    (context.device_id, value),
                )


def _decode_context(value: str) -> ManualControlContext:
    raw = json.loads(value)
    if not isinstance(raw, dict):
        raise RuntimeError("invalid control context")
    return ManualControlContext(
        device_id=str(raw["device_id"]),
        source=str(raw["source"]),
        started_at=datetime.fromisoformat(str(raw["started_at"])),
        expires_at=datetime.fromisoformat(str(raw["expires_at"])),
    )
