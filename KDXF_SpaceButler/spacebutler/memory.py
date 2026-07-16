"""Small auditable household memory for preference learning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


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

    def __init__(self) -> None:
        self._preferences: dict[tuple[str, str, str], Preference] = {}

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

    def recall(self, member_id: str, scene: str, key: str, default: object | None = None) -> object | None:
        preference = self._preferences.get((member_id, scene, key))
        if preference is None or preference.confidence < 0.5:
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
