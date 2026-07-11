"""Select and cache only environment state relevant to one EdgeHome task."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field

from ..entity_resolver import EntityNameResolver
from ..models import EntitySnapshot
from .home_state import TaskHomeStateBuilder


class EnvironmentContextSnapshot(BaseModel):
    """A bounded, explainable snapshot used for planning and safety analysis."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=300)
    scene: str | None = None
    entities: list[EntitySnapshot] = Field(default_factory=list)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False


class EnvironmentContextBuilder:
    """Combine textual device matches with narrowly selected environmental entities."""

    _AWAY_WORDS = ("离家", "出门", "我走了", "安全检查")
    _ENVIRONMENT_WORDS = ("温度", "湿度", "光照", "亮度", "天气")

    def __init__(self, resolver: EntityNameResolver, cache_seconds: int = 30, max_entities: int = 16) -> None:
        self._resolver = resolver
        self._task_builder = TaskHomeStateBuilder(resolver, max_entities=max_entities)
        self._cache_seconds = cache_seconds
        self._max_entities = max_entities
        self._cache: dict[str, tuple[datetime, EnvironmentContextSnapshot]] = {}

    def build(self, goal: str, scene: str | None = None) -> EnvironmentContextSnapshot:
        """Return cached context when still fresh; otherwise select only relevant exposed state."""
        key = f"{self._normalize(goal)}:{scene or ''}"
        cached = self._cache.get(key)
        now = datetime.now(timezone.utc)
        if cached and cached[0] > now:
            return cached[1].model_copy(update={"cache_hit": True})

        selected = {snapshot.entity_id: snapshot for snapshot in self._task_builder.build(goal).entities}
        normalized = self._normalize(goal)
        include_away = any(word in normalized for word in self._AWAY_WORDS)
        include_environment = any(word in normalized for word in self._ENVIRONMENT_WORDS)

        for state in self._resolver.hass.states.async_all():
            if len(selected) >= self._max_entities:
                break
            try:
                snapshot = self._resolver.snapshot(state.entity_id)
            except Exception:
                continue
            if include_away and self._is_away_relevant(snapshot):
                selected.setdefault(snapshot.entity_id, snapshot)
            elif include_environment and self._is_environment_relevant(snapshot):
                selected.setdefault(snapshot.entity_id, snapshot)

        context = EnvironmentContextSnapshot(goal=goal.strip(), scene=scene, entities=list(selected.values()))
        self._cache[key] = (now + timedelta(seconds=self._cache_seconds), context)
        return context

    def invalidate(self, entity_id: str | None = None) -> None:
        """Drop all cache entries or only entries containing a changed entity."""
        if entity_id is None:
            self._cache.clear()
            return
        for key, (_, snapshot) in list(self._cache.items()):
            if any(entity.entity_id == entity_id for entity in snapshot.entities):
                self._cache.pop(key, None)

    @staticmethod
    def _is_away_relevant(snapshot: EntitySnapshot) -> bool:
        if snapshot.domain in {"person", "device_tracker"}:
            return True
        if snapshot.domain == "binary_sensor":
            return snapshot.attributes.get("device_class") in {"door", "window", "garage_door"}
        return False

    @staticmethod
    def _is_environment_relevant(snapshot: EntitySnapshot) -> bool:
        if snapshot.domain == "weather":
            return True
        if snapshot.domain == "sensor":
            return snapshot.attributes.get("device_class") in {"temperature", "humidity", "illuminance"}
        return False

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(character.lower() for character in value if not character.isspace())
