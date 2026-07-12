"""Create a structured away-home door, window, and presence report without acting."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..context.environment import EnvironmentContextSnapshot
from ..models import EntitySnapshot


class AwaySecurityReport(BaseModel):
    """Read-only outcome of an away-home safety inspection."""

    model_config = ConfigDict(extra="forbid")

    people_home: list[EntitySnapshot] = Field(default_factory=list)
    open_entries: list[EntitySnapshot] = Field(default_factory=list)
    unavailable_entries: list[EntitySnapshot] = Field(default_factory=list)

    @property
    def safe_to_leave(self) -> bool:
        return not self.people_home and not self.open_entries and not self.unavailable_entries


class AwaySecurityReporter:
    """Report only exposed context entities; it never closes a door or changes a lock."""

    def build(self, context: EnvironmentContextSnapshot) -> AwaySecurityReport:
        """Classify people and entry sensors from a task-scoped environment snapshot."""
        people_home = [
            entity
            for entity in context.entities
            if (entity.domain in {"person", "device_tracker"} and entity.state == "home")
            or (entity.domain == "binary_sensor" and entity.attributes.get("device_class") == "presence" and entity.state == "on")
        ]
        entry_sensors = [
            entity
            for entity in context.entities
            if entity.domain == "binary_sensor" and entity.attributes.get("device_class") in {"door", "window", "garage_door"}
        ]
        return AwaySecurityReport(
            people_home=people_home,
            open_entries=[entity for entity in entry_sensors if entity.state == "on"],
            unavailable_entries=[entity for entity in entry_sensors if entity.state in {"unavailable", "unknown"}],
        )
