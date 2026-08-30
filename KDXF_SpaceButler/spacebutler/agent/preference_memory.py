"""Implicit preference memory learned from human overrides.

用户从不说"请记住我看电影喜欢 45%"——偏好从 "AI 动作 → 人工纠正" 的
现实交互中自动提取。单次偶然修改不产生高置信偏好（min_samples=2、
置信度随样本数渐进），近期观测权重更高，避免一次误操作永久改写。
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from statistics import pstdev
import threading

from spacebutler.agent.models import PreferenceEstimate, now

MIN_OBSERVATIONS = 2
MIN_CONFIDENCE = 0.6
_DECAY_WINDOW = 20


class PreferenceMemory:
    def __init__(self, database_path: str | Path | None) -> None:
        self._lock = threading.RLock()
        self._path = Path(database_path) if database_path else None
        self._memory_rows: list[tuple[str, str, str, float, float]] = []
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def _initialize(self) -> None:
        assert self._path is not None
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS preference_observation (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        activity TEXT NOT NULL,
                        room TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        value REAL NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """
                )

    def observe(self, activity: str, room: str, metric: str, value: float, at: float | None = None) -> None:
        timestamp = at or now()
        if self._path is None:
            with self._lock:
                self._memory_rows.append((activity, room, metric, float(value), timestamp))
            return
        with self._lock, closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO preference_observation (activity, room, metric, value, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (activity, room, metric, float(value), timestamp),
                )

    def _recent(self, activity: str, room: str, metric: str) -> list[tuple[float, float]]:
        if self._path is None:
            with self._lock:
                rows = [
                    (value, timestamp)
                    for row_activity, row_room, row_metric, value, timestamp in reversed(self._memory_rows)
                    if row_activity == activity and row_room == room and row_metric == metric
                ]
            return rows[:_DECAY_WINDOW]
        with self._lock, closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT value, created_at FROM preference_observation"
                " WHERE activity = ? AND room = ? AND metric = ?"
                " ORDER BY id DESC LIMIT ?",
                (activity, room, metric, _DECAY_WINDOW),
            ).fetchall()
        return [(float(row[0]), float(row[1])) for row in rows]

    def estimate(self, activity: str, room: str, metric: str) -> PreferenceEstimate | None:
        rows = self._recent(activity, room, metric)
        if not rows:
            return None
        values = [row[0] for row in rows]
        count = len(values)
        # 近期观测权重更高（最新权重 1.0，线性衰减到 0.4）。
        weights = [1.0 - 0.6 * index / max(_DECAY_WINDOW - 1, 1) for index in range(count)]
        mean = sum(value * weight for value, weight in zip(values, weights)) / sum(weights)
        spread = max(4.0, (max(values) - min(values)) / 2.0)
        if count < MIN_OBSERVATIONS:
            confidence = 0.15
        else:
            sample_confidence = min(1.0, 0.4 + 0.3 * (count - MIN_OBSERVATIONS))
            consistency = max(0.0, 1.0 - pstdev(values) / 20.0)
            confidence = round(sample_confidence * consistency, 2)
        return PreferenceEstimate(
            metric=metric,
            mean=round(mean, 1),
            lower=round(max(1.0, mean - spread), 1),
            upper=round(min(100.0, mean + spread), 1),
            count=count,
            confidence=confidence,
        )

    def summary(self) -> list[dict[str, object]]:
        if self._path is None:
            seen: set[tuple[str, str, str]] = set()
            with self._lock:
                for activity, room, metric, _, _ in self._memory_rows:
                    seen.add((activity, room, metric))
            keys = sorted(seen)
        else:
            with self._lock, closing(sqlite3.connect(self._path)) as connection:
                keys = [
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in connection.execute(
                        "SELECT DISTINCT activity, room, metric FROM preference_observation"
                    ).fetchall()
                ]
        estimates = []
        for activity, room, metric in keys:
            estimate = self.estimate(activity, room, metric)
            if estimate is not None:
                estimates.append({"activity": activity, "room": room, **estimate.to_dict()})
        return estimates


def preference_is_eligible(estimate: PreferenceEstimate | None) -> bool:
    return bool(
        estimate is not None
        and estimate.count >= MIN_OBSERVATIONS
        and estimate.confidence >= MIN_CONFIDENCE
    )
