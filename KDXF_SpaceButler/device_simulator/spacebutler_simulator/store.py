"""SQLite persistence for independently simulated devices and their audit events."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


FAULT_MODES = frozenset(
    {
        "none",
        "offline",
        "delay",
        "reject",
        "ack_without_state_change",
        "invalid_state",
        "random_failure",
        "stuck",
    }
)


class DeviceStateStore:
    """Persist device-owned state; Home Assistant never writes this database."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def ensure_device(self, device_id: str, initial_state: dict[str, Any]) -> dict[str, Any]:
        """Create a device only once so a container restart preserves its state."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM device_state WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                now = _now()
                connection.execute(
                    """
                    INSERT INTO device_state (
                        device_id, state_json, updated_at, last_command_id, online, fault_mode, fault_delay_ms
                    ) VALUES (?, ?, ?, NULL, 1, 'none', 0)
                    """,
                    (device_id, _encode(initial_state), now),
                )
                self._append_event(connection, device_id, "device_initialized", {"state": initial_state})
                row = connection.execute(
                    "SELECT * FROM device_state WHERE device_id = ?", (device_id,)
                ).fetchone()
            return _record(row)

    def save_definition(
        self,
        device_id: str,
        definition: dict[str, Any],
        *,
        source: str,
        replace: bool = False,
    ) -> dict[str, Any]:
        if source not in {"configured", "runtime"}:
            raise ValueError("device definition source must be configured or runtime")
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM device_definition WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if existing is not None and not replace:
                return _definition_record(existing)
            now = _now()
            connection.execute(
                """
                INSERT INTO device_definition (
                    device_id, definition_json, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    definition_json = excluded.definition_json,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (device_id, _encode(definition), source, now, now),
            )
            row = connection.execute(
                "SELECT * FROM device_definition WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"device definition was not saved: {device_id}")
            return _definition_record(row)

    def definitions(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM device_definition ORDER BY created_at, device_id"
            ).fetchall()
        return [_definition_record(row) for row in rows]

    def delete_device(self, device_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            state_row = self._require_row(connection, device_id)
            definition_row = connection.execute(
                "SELECT * FROM device_definition WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if definition_row is None:
                raise KeyError(f"unknown device definition: {device_id}")
            definition = _definition_record(definition_row)
            if definition["source"] != "runtime":
                raise ValueError("configured seed devices cannot be deleted")
            record = _record(state_row)
            self._append_event(
                connection,
                device_id,
                "device_removed",
                {"definition": definition["definition"], "state": record["state"]},
            )
            connection.execute("DELETE FROM device_state WHERE device_id = ?", (device_id,))
            connection.execute("DELETE FROM device_definition WHERE device_id = ?", (device_id,))
            return {**record, **definition}

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM device_state WHERE device_id = ?", (device_id,)
            ).fetchone()
            return _record(row) if row is not None else None

    def update_state(self, device_id: str, state: dict[str, Any], command_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE device_state
                SET state_json = ?, updated_at = ?, last_command_id = ?
                WHERE device_id = ?
                """,
                (_encode(state), _now(), command_id, device_id),
            )
            self._append_event(connection, device_id, "state_changed", {"command_id": command_id, "state": state})
            return _record(self._require_row(connection, device_id))

    def set_fault(self, device_id: str, mode: str, delay_ms: int = 0) -> dict[str, Any]:
        if mode not in FAULT_MODES:
            raise ValueError(f"unsupported fault mode: {mode}")
        if delay_ms < 0 or delay_ms > 60_000:
            raise ValueError("delay_ms must be between 0 and 60000")
        online = 0 if mode == "offline" else 1
        with self._connection() as connection:
            self._require_row(connection, device_id)
            connection.execute(
                """
                UPDATE device_state
                SET fault_mode = ?, fault_delay_ms = ?, online = ?, updated_at = ?
                WHERE device_id = ?
                """,
                (mode, delay_ms, online, _now(), device_id),
            )
            self._append_event(connection, device_id, "fault_changed", {"mode": mode, "delay_ms": delay_ms})
            return _record(self._require_row(connection, device_id))

    def reset_device(self, device_id: str, initial_state: dict[str, Any]) -> dict[str, Any]:
        with self._connection() as connection:
            self._require_row(connection, device_id)
            connection.execute(
                """
                UPDATE device_state
                SET state_json = ?, updated_at = ?, last_command_id = NULL,
                    online = 1, fault_mode = 'none', fault_delay_ms = 0
                WHERE device_id = ?
                """,
                (_encode(initial_state), _now(), device_id),
            )
            self._append_event(connection, device_id, "device_reset", {"state": initial_state})
            return _record(self._require_row(connection, device_id))

    def append_event(self, device_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connection() as connection:
            self._append_event(connection, device_id, event_type, payload)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        capped_limit = min(max(limit, 1), 500)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM device_event ORDER BY id DESC LIMIT ?", (capped_limit,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "device_id": row["device_id"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_state (
                    device_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_command_id TEXT,
                    online INTEGER NOT NULL,
                    fault_mode TEXT NOT NULL,
                    fault_delay_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_definition (
                    device_id TEXT PRIMARY KEY,
                    definition_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _require_row(self, connection: sqlite3.Connection, device_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM device_state WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown device: {device_id}")
        return row

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        device_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO device_event (device_id, event_type, timestamp, payload_json) VALUES (?, ?, ?, ?)",
            (device_id, event_type, _now(), _encode(payload)),
        )


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "device_id": row["device_id"],
        "state": json.loads(row["state_json"]),
        "updated_at": row["updated_at"],
        "last_command_id": row["last_command_id"],
        "online": bool(row["online"]),
        "fault_mode": row["fault_mode"],
        "fault_delay_ms": row["fault_delay_ms"],
    }


def _definition_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "device_id": row["device_id"],
        "definition": json.loads(row["definition_json"]),
        "source": row["source"],
        "created_at": row["created_at"],
        "definition_updated_at": row["updated_at"],
    }


def _encode(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
