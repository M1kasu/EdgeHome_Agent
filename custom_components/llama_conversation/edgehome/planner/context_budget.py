"""Bound the model-only planning context without weakening execution validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..context.environment import EnvironmentContextSnapshot
from ..context.home_state import TaskHomeStateSnapshot


class ContextBudgetResult(BaseModel):
    """A compact prompt payload plus an auditable truncation summary."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    entity_count: int = Field(ge=0)
    dropped_entities: int = Field(ge=0)
    truncated: bool = False


class PlanningContextBudgeter:
    """Limit prompt state by entity count and serialized size, never by hidden model logic."""

    def __init__(self, max_entities: int = 12, max_characters: int = 6000) -> None:
        self._max_entities = max_entities
        self._max_characters = max_characters

    def trim(self, snapshot: EnvironmentContextSnapshot | TaskHomeStateSnapshot) -> ContextBudgetResult:
        """Return a deterministic prefix of relevant snapshots small enough for edge models."""
        entities = [self._entity_payload(entity.model_dump(mode="json")) for entity in snapshot.entities[: self._max_entities]]
        payload: dict[str, Any] = {
            "goal": snapshot.goal,
            "scene": snapshot.scene,
            "entities": entities,
        }
        if isinstance(snapshot, EnvironmentContextSnapshot):
            payload["captured_at"] = snapshot.captured_at.isoformat()
            payload["snapshot_id"] = snapshot.snapshot_id
            payload["stale_after"] = snapshot.stale_after.isoformat() if snapshot.stale_after else None
            if snapshot.home_state is not None:
                payload["home_state"] = {
                    "snapshot_id": snapshot.home_state.snapshot_id,
                    "source": snapshot.home_state.source,
                    "rooms": snapshot.home_state.rooms,
                    "environment": snapshot.home_state.environment,
                    "safety": snapshot.home_state.safety,
                    "energy": snapshot.home_state.energy,
                    "availability": snapshot.home_state.availability,
                    "device_capability_summary": [
                        capability.model_dump(mode="json")
                        for capability in snapshot.home_state.device_capability_summary[: self._max_entities]
                    ],
                }
        elif snapshot.home_state is not None:
            payload["snapshot_id"] = snapshot.home_state.snapshot_id
            payload["home_state"] = {
                "snapshot_id": snapshot.home_state.snapshot_id,
                "rooms": snapshot.home_state.rooms,
                "availability": snapshot.home_state.availability,
            }
        while entities and self._serialized_length(payload) > self._max_characters:
            entities.pop()
        dropped = len(snapshot.entities) - len(entities)
        if self._serialized_length(payload) > self._max_characters:
            payload["goal"] = payload["goal"][: max(1, self._max_characters // 4)]
        return ContextBudgetResult(
            payload=payload,
            entity_count=len(entities),
            dropped_entities=dropped,
            truncated=dropped > 0,
        )

    @staticmethod
    def _entity_payload(entity: dict[str, Any]) -> dict[str, Any]:
        attributes = entity.get("attributes")
        if isinstance(attributes, dict):
            entity["attributes"] = {
                key: value
                for key, value in attributes.items()
                if key in {"brightness", "temperature", "current_position", "device_class", "unit_of_measurement"}
            }
        return entity

    @staticmethod
    def _serialized_length(payload: dict[str, Any]) -> int:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
