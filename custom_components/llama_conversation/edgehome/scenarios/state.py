"""Persist the small set of device states affected by a dynamic EdgeHome scene."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pydantic import BaseModel, ConfigDict, Field

from ..entity_resolver import ExposedEntityResolver
from ..models import EntitySnapshot, PlanStep, TaskPlan, VerificationRule


class ActiveScenario(BaseModel):
    """The pre-scene state required to restore one user-visible dynamic scene."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=64)
    snapshots: list[EntitySnapshot] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScenarioStateManager:
    """Capture only fixed-plan targets, then construct a new validated restoration plan."""

    _KEY = "edgehome_active_scenarios"
    _VERSION = 1

    def __init__(self, hass: HomeAssistant, resolver: ExposedEntityResolver) -> None:
        self._resolver = resolver
        self._store: Store[dict[str, Any]] = Store(hass, self._VERSION, self._KEY)
        self._active: dict[str, ActiveScenario] = {}
        self._loaded = False

    async def async_capture(self, conversation_id: str, name: str, plan: TaskPlan) -> ActiveScenario:
        """Persist states of plan targets before the caller executes the scene plan."""
        await self.async_load()
        entity_ids = {
            entity_id
            for step in plan.steps
            for entity_id in [step.arguments.get("entity_id")]
            if isinstance(entity_id, str)
        }
        scenario = ActiveScenario(
            conversation_id=conversation_id,
            name=name,
            snapshots=[self._resolver.snapshot(entity_id) for entity_id in sorted(entity_ids)],
        )
        self._active[self._key(conversation_id, name)] = scenario
        await self._async_save()
        return scenario

    async def async_restore_plan(self, conversation_id: str, name: str) -> TaskPlan | None:
        """Build a restoration TaskPlan; unsupported domains are deliberately skipped."""
        await self.async_load()
        scenario = self._active.get(self._key(conversation_id, name))
        if scenario is None:
            return None
        # A climate snapshot can require two independently verified operations:
        # restore its HVAC mode first, then its target temperature.  Keep those
        # steps separate so a successful service return is never mistaken for a
        # fully restored device state.
        steps = [
            step
            for index, snapshot in enumerate(scenario.snapshots, start=1)
            for step in _restore_steps(index, snapshot)
        ]
        if not steps:
            return None
        return TaskPlan(goal=f"恢复{name}场景前状态", scene=f"restore_{name}", steps=steps)

    async def async_clear(self, conversation_id: str, name: str) -> None:
        """Clear a successfully restored scenario; failures retain the recovery point."""
        await self.async_load()
        if self._active.pop(self._key(conversation_id, name), None):
            await self._async_save()

    async def async_load(self) -> None:
        """Load valid persisted scenes; malformed data cannot break Conversation startup."""
        if self._loaded:
            return
        payload = await self._store.async_load() or {}
        records = payload.get("scenarios", {}) if isinstance(payload, dict) else {}
        if isinstance(records, dict):
            for key, value in records.items():
                try:
                    scenario = ActiveScenario.model_validate(value)
                except ValueError:
                    continue
                if key == self._key(scenario.conversation_id, scenario.name):
                    self._active[key] = scenario
        self._loaded = True

    @staticmethod
    def _key(conversation_id: str, name: str) -> str:
        return f"{conversation_id}:{name}"

    async def _async_save(self) -> None:
        await self._store.async_save({"scenarios": {key: state.model_dump(mode="json") for key, state in self._active.items()}})


def _restore_steps(index: int, snapshot: EntitySnapshot) -> tuple[PlanStep, ...]:
    """Build all verified steps required to restore one captured device state."""
    step_id = f"restore_{index}"
    if snapshot.domain == "light":
        if snapshot.state == "off":
            return (
                PlanStep(
                    id=step_id,
                    tool="turn_off_device",
                    arguments={"entity_id": snapshot.entity_id},
                    verify=VerificationRule(entity_id=snapshot.entity_id, state="off"),
                ),
            )
        brightness = snapshot.attributes.get("brightness")
        if isinstance(brightness, int):
            percentage = round(brightness * 100 / 255)
            return (
                PlanStep(
                    id=step_id,
                    tool="set_light_brightness",
                    arguments={"entity_id": snapshot.entity_id, "brightness": percentage},
                    verify=VerificationRule(entity_id=snapshot.entity_id, state="on", attributes={"brightness": brightness}, tolerance=1.0),
                ),
            )
        return (
            PlanStep(
                id=step_id,
                tool="turn_on_device",
                arguments={"entity_id": snapshot.entity_id},
                verify=VerificationRule(entity_id=snapshot.entity_id, state="on"),
            ),
        )
    if snapshot.domain in {"switch", "fan"}:
        tool = "turn_on_device" if snapshot.state == "on" else "turn_off_device"
        return (
            PlanStep(
                id=step_id,
                tool=tool,
                arguments={"entity_id": snapshot.entity_id},
                verify=VerificationRule(entity_id=snapshot.entity_id, state=snapshot.state),
            ),
        )
    if snapshot.domain == "cover" and isinstance(snapshot.attributes.get("current_position"), int):
        position = snapshot.attributes["current_position"]
        return (
            PlanStep(
                id=step_id,
                tool="control_curtain",
                arguments={"entity_id": snapshot.entity_id, "position": position},
                verify=VerificationRule(entity_id=snapshot.entity_id, attributes={"current_position": position}, tolerance=1.0),
            ),
        )
    if snapshot.domain == "climate":
        if snapshot.state == "off":
            return (
                PlanStep(
                    id=step_id,
                    tool="turn_off_device",
                    arguments={"entity_id": snapshot.entity_id},
                    verify=VerificationRule(entity_id=snapshot.entity_id, state="off"),
                ),
            )
        if snapshot.state not in {"cool", "heat"}:
            return ()
        steps = [
            PlanStep(
                id=f"{step_id}_mode",
                tool="set_hvac_mode",
                arguments={"entity_id": snapshot.entity_id, "hvac_mode": snapshot.state},
                verify=VerificationRule(entity_id=snapshot.entity_id, state=snapshot.state),
            )
        ]
        temperature = snapshot.attributes.get("temperature")
        if isinstance(temperature, (int, float)) and 16.0 <= temperature <= 30.0:
            steps.append(
                PlanStep(
                    id=f"{step_id}_temperature",
                    tool="set_temperature",
                    arguments={"entity_id": snapshot.entity_id, "temperature": float(temperature)},
                    verify=VerificationRule(
                        entity_id=snapshot.entity_id,
                        attributes={"temperature": float(temperature)},
                        tolerance=0.5,
                    ),
                )
            )
        return tuple(steps)
    return ()
