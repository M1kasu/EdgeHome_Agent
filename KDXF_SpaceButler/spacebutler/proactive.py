"""Persistent configuration for proactive service bindings."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


ENERGY_RULE_ID = "empty_room_open_window_energy_guard"
NIGHT_SAFETY_RULE_ID = "night_elder_safety"


class ProactiveRuleStore:
    """Keep rule-to-device bindings separate from learned household preferences."""

    def __init__(self, database_path: str | Path | None) -> None:
        self._database_path = Path(database_path) if database_path is not None else None
        self._memory: dict[str, dict[str, Any]] = {}
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()

    def load(self, rule_id: str) -> dict[str, Any] | None:
        if self._database_path is None:
            value = self._memory.get(rule_id)
            return dict(value) if value is not None else None
        with closing(sqlite3.connect(self._database_path)) as connection:
            row = connection.execute(
                "SELECT config_json FROM proactive_rule_config WHERE rule_id = ?",
                (rule_id,),
            ).fetchone()
        if row is None:
            return None
        decoded = json.loads(row[0])
        if not isinstance(decoded, dict):
            raise RuntimeError(f"invalid proactive rule configuration: {rule_id}")
        return decoded

    def save(self, rule_id: str, config: dict[str, Any]) -> dict[str, Any]:
        stored = dict(config)
        if self._database_path is None:
            self._memory[rule_id] = stored
            return dict(stored)
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO proactive_rule_config (rule_id, config_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(rule_id) DO UPDATE SET
                        config_json = excluded.config_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        rule_id,
                        json.dumps(stored, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        return dict(stored)

    def _initialize_database(self) -> None:
        if self._database_path is None:
            return
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS proactive_rule_config (
                        rule_id TEXT PRIMARY KEY,
                        config_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
