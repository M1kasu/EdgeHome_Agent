from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MEMORY: dict[str, Any] = {
    "active_scene": None,
    "last_entity_id": None,
    "last_intent": None,
    "last_slots": {},
    "pending_confirmation": None,
    "saved_state": {},
    "delayed_task_ids": [],
}


class SessionManager:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        connection = self._connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, memory TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def get(self, session_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT memory FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        finally:
            connection.close()
        memory = DEFAULT_MEMORY.copy()
        if row:
            memory.update(json.loads(row[0]))
        return memory

    def save(self, session_id: str, memory: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(memory, ensure_ascii=False)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO sessions(session_id, memory, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET memory = excluded.memory, updated_at = excluded.updated_at",
                (session_id, serialized, now),
            )
            connection.commit()
        finally:
            connection.close()

    def save_task(self, task: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        task["updated_at"] = now
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO tasks(task_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (task["task_id"], json.dumps(task, ensure_ascii=False), now),
            )
            connection.commit()
        finally:
            connection.close()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        finally:
            connection.close()
        return json.loads(row[0]) if row else None

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        if task.get("status") == "scheduled":
            task["status"] = "cancelled"
            self.save_task(task)
        return task
