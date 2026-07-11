"""Build a small, exposure-aware state snapshot instead of exposing the whole home."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..entity_resolver import EntityNameResolver
from ..models import EntitySnapshot


class TaskHomeStateSnapshot(BaseModel):
    """Only entities textually relevant to a planning request are included."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=300)
    scene: str | None = None
    entities: list[EntitySnapshot] = Field(default_factory=list)
    truncated: bool = False


class TaskHomeStateBuilder:
    """Select exposed entities by friendly name, alias, area name, or entity object id."""

    def __init__(self, resolver: EntityNameResolver, max_entities: int = 12) -> None:
        self._resolver = resolver
        self._max_entities = max_entities

    def build(self, goal: str, scene: str | None = None) -> TaskHomeStateSnapshot:
        """Create a bounded snapshot; unrelated entities never enter a model prompt."""
        query = self._normalize(goal)
        matches: list[EntitySnapshot] = []
        for state in self._resolver.hass.states.async_all():
            if not self._resolver._exposure_checker(self._resolver.hass, "conversation", state.entity_id):
                continue
            snapshot = self._resolver.snapshot(state.entity_id)
            if any(name in query for name in self._names(snapshot) if len(name) >= 2):
                matches.append(snapshot)

        return TaskHomeStateSnapshot(
            goal=goal.strip(),
            scene=scene,
            entities=matches[: self._max_entities],
            truncated=len(matches) > self._max_entities,
        )

    @classmethod
    def _names(cls, snapshot: EntitySnapshot) -> set[str]:
        names = {cls._normalize(snapshot.entity_id.split(".", maxsplit=1)[1].replace("_", " "))}
        if snapshot.friendly_name:
            names.add(cls._normalize(snapshot.friendly_name))
        if snapshot.area_name and snapshot.friendly_name:
            names.add(cls._normalize(f"{snapshot.area_name}{snapshot.friendly_name}"))
        return names

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())
