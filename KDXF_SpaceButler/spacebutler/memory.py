"""Small auditable household memory for preference learning."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Preference:
    member_id: str
    preference_type: str
    condition: str
    value: object
    source: str
    sample_count: int
    confidence: float
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class HouseholdMemory:
    """Store explicit household preferences without hidden profiling."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._preferences: dict[tuple[str, str, str], Preference] = {}
        self._database_path = Path(database_path) if database_path is not None else None
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
            self._load_preferences()

    def learn_preference(
        self,
        member_id: str,
        scene: str,
        key: str,
        value: object,
        *,
        confidence: float = 1.0,
        source: str = "explicit_feedback",
        sample_count: int = 1,
    ) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        now = datetime.now(timezone.utc)
        existing = self._preferences.get((member_id, scene, key))
        self._preferences[(member_id, scene, key)] = Preference(
            member_id=member_id,
            preference_type=key,
            condition=scene,
            value=value,
            source=source,
            sample_count=sample_count if existing is None else max(sample_count, existing.sample_count + 1),
            confidence=confidence,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._persist(self._preferences[(member_id, scene, key)])

    def recall(self, member_id: str, scene: str, key: str, default: object | None = None) -> object | None:
        preference = self._preferences.get((member_id, scene, key))
        if preference is None or preference.confidence < 0.5 or _expired(preference):
            return default
        return preference.value

    def recall_number(self, member_id: str, scene: str, key: str, default: float) -> float:
        value = self.recall(member_id, scene, key, default)
        if isinstance(value, (int, float)):
            return float(value)
        return default

    def apply_energy_feedback(self, member_id: str, feedback: str) -> str:
        normalized = "".join(feedback.split())
        future_scope = any(token in normalized for token in ("以后", "下次", "往后", "今后"))
        direct_action = any(token in normalized for token in ("直接执行", "自动关闭", "直接关", "直接关闭", "自动关"))
        if (future_scope and direct_action) or any(token in normalized for token in ("以后直接执行", "以后自动关闭")):
            self.learn_preference(member_id, "empty_room_open_window_energy_guard", "auto_execute", True)
            return "learned_auto_execute"
        if any(token in normalized for token in ("下次不要提醒", "不要再提醒", "别提醒")):
            self.learn_preference(member_id, "empty_room_open_window_energy_guard", "suppress_reminder", True)
            return "learned_suppress_reminder"
        if "30分钟" in normalized or "半小时" in normalized:
            self.learn_preference(member_id, "empty_room_open_window_energy_guard", "unoccupied_minutes", 30)
            return "learned_unoccupied_30"
        if "20分钟" in normalized:
            self.learn_preference(member_id, "empty_room_open_window_energy_guard", "unoccupied_minutes", 20)
            return "learned_unoccupied_20"
        if "高峰" in normalized or "峰电" in normalized:
            self.learn_preference(member_id, "empty_room_open_window_energy_guard", "only_peak_price", True)
            return "learned_only_peak_price"
        return "no_structured_energy_feedback"

    def export_preferences(self) -> list[Preference]:
        return list(self._preferences.values())

    def clear(self) -> None:
        self._preferences.clear()
        if self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute("DELETE FROM household_preference")

    @property
    def database_path(self) -> Path | None:
        return self._database_path

    def _initialize_database(self) -> None:
        if self._database_path is None:
            return
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS household_preference (
                        member_id TEXT NOT NULL,
                        condition TEXT NOT NULL,
                        preference_type TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        source TEXT NOT NULL,
                        sample_count INTEGER NOT NULL,
                        confidence REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT,
                        PRIMARY KEY (member_id, condition, preference_type)
                    )
                    """
                )

    def _load_preferences(self) -> None:
        if self._database_path is None:
            return
        with closing(sqlite3.connect(self._database_path)) as connection:
            rows = connection.execute(
                """
                SELECT member_id, condition, preference_type, value_json, source,
                       sample_count, confidence, created_at, updated_at, expires_at
                FROM household_preference
                """
            ).fetchall()
        for row in rows:
            preference = Preference(
                member_id=row[0],
                condition=row[1],
                preference_type=row[2],
                value=json.loads(row[3]),
                source=row[4],
                sample_count=int(row[5]),
                confidence=float(row[6]),
                created_at=datetime.fromisoformat(row[7]),
                updated_at=datetime.fromisoformat(row[8]),
                expires_at=datetime.fromisoformat(row[9]) if row[9] else None,
            )
            if not _expired(preference):
                self._preferences[(preference.member_id, preference.condition, preference.preference_type)] = preference

    def _persist(self, preference: Preference) -> None:
        if self._database_path is None:
            return
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO household_preference (
                        member_id, condition, preference_type, value_json, source,
                        sample_count, confidence, created_at, updated_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(member_id, condition, preference_type) DO UPDATE SET
                        value_json = excluded.value_json,
                        source = excluded.source,
                        sample_count = excluded.sample_count,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at
                    """,
                    (
                        preference.member_id,
                        preference.condition,
                        preference.preference_type,
                        json.dumps(preference.value, ensure_ascii=False, separators=(",", ":")),
                        preference.source,
                        preference.sample_count,
                        preference.confidence,
                        preference.created_at.isoformat(),
                        preference.updated_at.isoformat(),
                        preference.expires_at.isoformat() if preference.expires_at else None,
                    ),
                )


def _expired(preference: Preference) -> bool:
    return preference.expires_at is not None and preference.expires_at <= datetime.now(timezone.utc)
