"""Compile a small-model semantic task draft into a verified fixed plan."""

from __future__ import annotations

from enum import StrEnum
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..light import brightness_verification
from ..models import GoalSpec, OnFailurePolicy, PlanConstraint, PlanStep, TaskPlan, VerificationRule


class DraftAction(StrEnum):
    """High-level operations a model may propose; none exposes an HA service."""

    READ_HOME = "read_home"
    READ_ROOM = "read_room"
    READ_DEVICE = "read_device"
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_BRIGHTNESS = "set_brightness"
    SET_TEMPERATURE = "set_temperature"
    SET_HVAC_MODE = "set_hvac_mode"
    SET_CURTAIN_POSITION = "set_curtain_position"
    ENABLE_SECURITY_MODE = "enable_security_mode"


class ModelDraftStep(BaseModel):
    """A concise, model-authored step before typed tool arguments are compiled."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    action: DraftAction
    entity_id: str | None = Field(default=None, pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    room: Literal["living_room", "bedroom", "home"] | None = None
    value: int | float | str | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    on_failure: OnFailurePolicy = OnFailurePolicy.STOP
    optional: bool = False


class ModelPlanDraft(BaseModel):
    """A small output contract that preserves planning autonomy without tool authority."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=200)
    scene: str | None = Field(default=None, max_length=64)
    need_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    rationale: str | None = Field(default=None, max_length=240)
    goal_spec: GoalSpec | None = None
    constraints: list[PlanConstraint] = Field(default_factory=list, max_length=8)
    steps: list[ModelDraftStep] = Field(min_length=1, max_length=12)


class DraftCompileError(ValueError):
    """A deterministic reason why an untrusted semantic draft cannot become a plan."""


class ModelDraftCompiler:
    """Map a closed semantic action vocabulary onto typed, verified registered tools."""

    def compile(self, draft: ModelPlanDraft) -> TaskPlan:
        """Return a fixed plan; reject incomplete values rather than guessing defaults."""
        steps = self._with_preflight_reads([self._compile_step(step) for step in draft.steps])
        return TaskPlan(
            goal=draft.goal,
            scene=draft.scene,
            need_clarification=draft.need_clarification,
            clarification_question=draft.clarification_question,
            rationale=draft.rationale,
            goal_spec=draft.goal_spec,
            constraints=draft.constraints,
            steps=steps,
        )

    @staticmethod
    def _with_preflight_reads(steps: list[PlanStep]) -> list[PlanStep]:
        """Insert an exact-entity read immediately before every model control.

        The model chooses *what* it wants to do.  The trusted compiler adds
        the non-mutating preflight that makes the decision auditable and keeps
        every control operation behind a same-entity state observation.
        """
        compiled: list[PlanStep] = []
        used_ids = {step.id for step in steps}
        for step in steps:
            entity_id = step.arguments.get("entity_id")
            if step.verify is None or not isinstance(entity_id, str):
                compiled.append(step)
                continue
            preflight_id = ModelDraftCompiler._preflight_id(step.id, used_ids)
            used_ids.add(preflight_id)
            compiled.append(
                PlanStep(
                    id=preflight_id,
                    tool="get_device_state",
                    arguments={"entity_id": entity_id},
                    depends_on=step.depends_on,
                )
            )
            compiled.append(step.model_copy(update={"depends_on": [preflight_id]}))
        return compiled

    @staticmethod
    def _preflight_id(step_id: str, used_ids: set[str]) -> str:
        """Return a compact, unique ID within the fixed ``PlanStep`` contract."""
        base = f"preflight_{step_id}"[:64]
        candidate = base
        suffix = 1
        while candidate in used_ids:
            token = f"_{suffix}"
            candidate = f"{base[:64 - len(token)]}{token}"
            suffix += 1
        return candidate

    def _compile_step(self, step: ModelDraftStep) -> PlanStep:
        common = {
            "id": step.id,
            "depends_on": step.depends_on,
            "on_failure": step.on_failure,
            "optional": step.optional,
        }
        if step.action == DraftAction.READ_HOME:
            return PlanStep(tool="get_home_state", arguments={}, **common)
        if step.action == DraftAction.READ_ROOM:
            if step.room is None:
                raise DraftCompileError("draft_room_required")
            return PlanStep(tool="get_room_state", arguments={"room": step.room}, **common)

        entity_id = self._entity_id(step)
        if step.action == DraftAction.READ_DEVICE:
            return PlanStep(tool="get_device_state", arguments={"entity_id": entity_id}, **common)
        if step.action == DraftAction.TURN_ON:
            return PlanStep(
                tool="turn_on_device",
                arguments={"entity_id": entity_id},
                verify=VerificationRule(entity_id=entity_id, state="on"),
                **common,
            )
        if step.action == DraftAction.TURN_OFF:
            return PlanStep(
                tool="turn_off_device",
                arguments={"entity_id": entity_id},
                verify=VerificationRule(entity_id=entity_id, state="off"),
                **common,
            )
        if step.action == DraftAction.SET_BRIGHTNESS:
            brightness = self._integer(step.value, 0, 100, "draft_brightness_invalid")
            return PlanStep(
                tool="set_light_brightness",
                arguments={"entity_id": entity_id, "brightness": brightness},
                verify=brightness_verification(entity_id, brightness),
                **common,
            )
        if step.action == DraftAction.SET_TEMPERATURE:
            temperature = self._number(step.value, 16.0, 30.0, "draft_temperature_invalid")
            return PlanStep(
                tool="set_temperature",
                arguments={"entity_id": entity_id, "temperature": temperature},
                verify=VerificationRule(entity_id=entity_id, attributes={"temperature": temperature}, tolerance=0.5),
                **common,
            )
        if step.action == DraftAction.SET_HVAC_MODE:
            if step.value not in {"off", "cool", "heat"}:
                raise DraftCompileError("draft_hvac_mode_invalid")
            return PlanStep(
                tool="set_hvac_mode",
                arguments={"entity_id": entity_id, "hvac_mode": step.value},
                verify=VerificationRule(entity_id=entity_id, state=step.value),
                **common,
            )
        if step.action == DraftAction.SET_CURTAIN_POSITION:
            position = self._integer(step.value, 0, 100, "draft_curtain_position_invalid")
            return PlanStep(
                tool="control_curtain",
                arguments={"entity_id": entity_id, "position": position},
                verify=VerificationRule(entity_id=entity_id, attributes={"current_position": position}, tolerance=1.0),
                **common,
            )
        if step.action == DraftAction.ENABLE_SECURITY_MODE:
            return PlanStep(
                tool="activate_security_mode",
                arguments={"entity_id": entity_id},
                verify=VerificationRule(entity_id=entity_id, state="on"),
                **common,
            )
        raise DraftCompileError("draft_action_not_supported")

    @staticmethod
    def _entity_id(step: ModelDraftStep) -> str:
        if step.entity_id is None:
            raise DraftCompileError("draft_entity_id_required")
        return step.entity_id

    @staticmethod
    def _integer(value: Any, minimum: int, maximum: int, error: str) -> int:
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d{1,3})\s*(?:%|％)?\s*", value)
            if match is not None:
                value = int(match.group(1))
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise DraftCompileError(error)
        return value

    @staticmethod
    def _number(value: Any, minimum: float, maximum: float, error: str) -> float:
        if isinstance(value, str):
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:℃|°c|c|度)?\s*", value.lower())
            if match is not None:
                value = float(match.group(1))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
            raise DraftCompileError(error)
        return float(value)
