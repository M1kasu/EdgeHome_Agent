"""Build a small, exposure-aware state snapshot instead of exposing the whole home."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from ..entity_resolver import EntityNameResolver
from ..errors import EdgeHomeError
from ..models import EntitySnapshot


class DeviceCapabilitySummary(BaseModel):
    """Planner-safe device capability facts derived from one HA state."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    domain: str
    room: str | None = None
    state: str
    available: bool = True
    controllable: bool = False
    attributes: dict[str, object] = Field(default_factory=dict)


class HomeStateSnapshot(BaseModel):
    """Unified task-scoped home state used by goal planning and evidence reports."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    goal: str = Field(min_length=1, max_length=300)
    scene: str | None = None
    captured_at: datetime
    stale_after: datetime
    source: str = "home_assistant"
    rooms: dict[str, list[str]] = Field(default_factory=dict)
    environment: dict[str, object] = Field(default_factory=dict)
    safety: dict[str, object] = Field(default_factory=dict)
    energy: dict[str, object] = Field(default_factory=dict)
    availability: dict[str, bool] = Field(default_factory=dict)
    device_capability_summary: list[DeviceCapabilitySummary] = Field(default_factory=list)
    entities: list[EntitySnapshot] = Field(default_factory=list)
    truncated: bool = False


class TaskHomeStateSnapshot(BaseModel):
    """Only entities textually relevant to a planning request are included."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=300)
    scene: str | None = None
    entities: list[EntitySnapshot] = Field(default_factory=list)
    truncated: bool = False
    home_state: HomeStateSnapshot | None = None


def build_home_state_snapshot(
    goal: str,
    scene: str | None,
    entities: list[EntitySnapshot],
    *,
    source: str = "home_assistant",
    ttl_seconds: int = 30,
    truncated: bool = False,
) -> HomeStateSnapshot:
    """Build one auditable state object without exposing unrelated HA internals."""
    captured_at = datetime.now(timezone.utc)
    rooms: dict[str, list[str]] = {}
    environment: dict[str, object] = {}
    safety: dict[str, object] = {}
    energy: dict[str, object] = {}
    availability: dict[str, bool] = {}
    capabilities: list[DeviceCapabilitySummary] = []

    for entity in entities:
        room = entity.area_id or entity.area_name or _room_from_entity_id(entity.entity_id)
        rooms.setdefault(room or "unknown", []).append(entity.entity_id)
        available = entity.state not in {"unknown", "unavailable"}
        availability[entity.entity_id] = available
        compact_attributes = _safe_attributes(entity.attributes)
        capabilities.append(
            DeviceCapabilitySummary(
                entity_id=entity.entity_id,
                domain=entity.domain,
                room=room,
                state=entity.state,
                available=available,
                controllable=entity.domain in {"light", "cover", "switch", "climate", "input_boolean"},
                attributes=compact_attributes,
            )
        )
        if entity.domain == "sensor" and entity.attributes.get("device_class") in {"temperature", "humidity", "illuminance"}:
            environment[entity.entity_id] = {"state": entity.state, **compact_attributes}
        if entity.domain in {"person", "device_tracker"} or (
            entity.domain == "binary_sensor"
            and entity.attributes.get("device_class") in {"door", "window", "garage_door", "presence"}
        ):
            safety[entity.entity_id] = {"state": entity.state, **compact_attributes}
        if entity.domain == "sensor" and entity.attributes.get("device_class") in {"power", "energy"}:
            energy[entity.entity_id] = {"state": entity.state, **compact_attributes}

    payload = {
        "goal": goal,
        "scene": scene,
        "captured_at": captured_at.isoformat(),
        "entities": [(entity.entity_id, entity.state, _safe_attributes(entity.attributes)) for entity in entities],
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return HomeStateSnapshot(
        snapshot_id=f"hs_{digest}",
        goal=goal.strip(),
        scene=scene,
        captured_at=captured_at,
        stale_after=captured_at + timedelta(seconds=ttl_seconds),
        source=source,
        rooms={room: sorted(ids) for room, ids in rooms.items()},
        environment=environment,
        safety=safety,
        energy=energy,
        availability=availability,
        device_capability_summary=capabilities,
        entities=entities,
        truncated=truncated,
    )


class TaskHomeStateBuilder:
    """Select exposed entities by friendly name, alias, area name, or entity object id."""

    def __init__(self, resolver: EntityNameResolver, max_entities: int = 12) -> None:
        self._resolver = resolver
        self._max_entities = max_entities

    def build(self, goal: str, scene: str | None = None) -> TaskHomeStateSnapshot:
        """Create a bounded snapshot; unrelated entities never enter a model prompt."""
        query = self._normalize(goal)
        matches: list[EntitySnapshot] = []
        registered = self._resolver.registered_entities()
        candidates = registered or tuple(state.entity_id for state in self._resolver.hass.states.async_all())
        # M5 scenes are allowed to see only their explicitly registered MQTT
        # devices.  This supplies a current baseline for dynamic no-op removal.
        scene_entities = set(self._resolver.scene_entities(scene, protocol="mqtt")) if scene else set()
        for entity_id in candidates:
            if not registered and not self._resolver._exposure_checker(self._resolver.hass, "conversation", entity_id):
                continue
            try:
                snapshot = self._resolver.snapshot(entity_id)
            except EdgeHomeError:
                continue
            if entity_id in scene_entities or any(name in query for name in self._names(snapshot) if len(name) >= 2):
                matches.append(snapshot)

        entities = matches[: self._max_entities]
        truncated = len(matches) > self._max_entities
        return TaskHomeStateSnapshot(
            goal=goal.strip(),
            scene=scene,
            entities=entities,
            truncated=truncated,
            home_state=build_home_state_snapshot(goal, scene, entities, truncated=truncated),
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


def _room_from_entity_id(entity_id: str) -> str | None:
    object_id = entity_id.partition(".")[2]
    for room in ("living_room", "bedroom", "kitchen", "bathroom"):
        if room in object_id:
            return room
    return None


def _safe_attributes(attributes: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in attributes.items()
        if key
        in {
            "brightness",
            "temperature",
            "current_temperature",
            "current_position",
            "device_class",
            "unit_of_measurement",
            "power_w",
        }
    }
