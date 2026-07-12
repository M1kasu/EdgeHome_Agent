"""Resolve a narrow set of Chinese references into a copy of the last validated plan."""

from __future__ import annotations

import re

from ..context.dialogue_state import DialogueState
from ..entity_resolver import EntityNameResolver
from ..light import brightness_verification
from ..models import PlanStep, TaskPlan, VerificationRule


class ReferencePlanResolver:
    """Support explicit same-as-last and small relative adjustments without model guesswork."""

    def __init__(self, entity_resolver: EntityNameResolver) -> None:
        self._entity_resolver = entity_resolver

    def resolve(self, text: str, state: DialogueState) -> TaskPlan | None:
        """Build a new one-step plan only for an unambiguous stored reference."""
        if state.last_plan is None or not state.last_plan.steps or state.last_entity_id is None:
            return None
        last_step = state.last_plan.steps[0]
        if self._contains_any(text, ("\u5173\u6389\u5427", "\u628a\u5b83\u5173\u6389", "\u628a\u8fd9\u4e2a\u5173\u6389")):
            return self._turn_off_last(text, state.last_entity_id)
        if self._contains_any(text, ("\u518d\u4eae\u4e00\u70b9", "\u518d\u8c03\u4eae\u4e00\u70b9", "\u8c03\u4eae\u4e00\u70b9", "\u8c03\u4eae\u4e00\u4e9b")):
            return self._relative_brightness(text, state, last_step, increase=True)
        if self._contains_any(text, ("\u518d\u6697\u4e00\u70b9", "\u518d\u8c03\u6697\u4e00\u70b9", "\u8c03\u6697\u4e00\u70b9", "\u964d\u4f4e\u4e00\u4e9b")):
            return self._relative_brightness(text, state, last_step, increase=False)
        if "再调亮一点" in text or "再亮一点" in text:
            return self._relative_brightness(text, state, last_step, increase=True)
        if "再调暗一点" in text or "再暗一点" in text:
            return self._relative_brightness(text, state, last_step, increase=False)
        if "温度高一点" in text:
            return self._relative_temperature(text, state, last_step, increase=True)
        if "温度低一点" in text:
            return self._relative_temperature(text, state, last_step, increase=False)
        if "也一样" in text or "和昨天一样" in text or "和刚才一样" in text:
            target = self._target_entity(text, state.last_entity_id)
            if target is None:
                return None
            return self._copy_with_entity(text, state.last_plan, state.last_entity_id, target)
        if any(pronoun in text for pronoun in ("它", "这个", "刚才那个")):
            return self._copy_with_entity(text, state.last_plan, state.last_entity_id, state.last_entity_id)
        return None

    def _target_entity(self, text: str, source_entity_id: str) -> str | None:
        source_domain = source_entity_id.split(".", maxsplit=1)[0]
        try:
            return self._entity_resolver.resolve_name(text, {source_domain}).snapshot.entity_id
        except Exception:
            pass

        area_match = re.match(r"\s*([^的\s]+)的也一样", text)
        if not area_match:
            return None
        area_hint = area_match.group(1)
        candidates = []
        for candidate in self._entity_resolver.hass.states.async_all():
            if candidate.domain != source_domain:
                continue
            try:
                snapshot = self._entity_resolver.snapshot(candidate.entity_id, {source_domain})
            except Exception:
                continue
            if (snapshot.area_name and area_hint in snapshot.area_name) or (
                snapshot.friendly_name and snapshot.friendly_name.startswith(area_hint)
            ):
                candidates.append(snapshot.entity_id)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _copy_with_entity(text: str, plan: TaskPlan, source_entity_id: str, target_entity_id: str) -> TaskPlan:
        steps = [_replace_entity(step, source_entity_id, target_entity_id) for step in plan.steps]
        return plan.model_copy(update={"goal": text, "scene": "dialogue_reference", "steps": steps})

    def _relative_brightness(self, text: str, state: DialogueState, step: PlanStep, increase: bool) -> TaskPlan | None:
        if step.tool != "set_light_brightness":
            return None
        brightness = self._current_brightness(state.last_entity_id)
        if brightness is None:
            requested_brightness = step.arguments.get("brightness")
            brightness = requested_brightness if isinstance(requested_brightness, int) else None
        if brightness is None:
            return None
        next_brightness = max(0, min(100, brightness + (10 if increase else -10)))
        entity_id = state.last_entity_id
        if entity_id is None:
            return None
        verify = brightness_verification(entity_id, next_brightness)
        return TaskPlan(
            goal=text,
            scene="dialogue_reference",
            steps=[PlanStep(id="reference_1", tool="set_light_brightness", arguments={"entity_id": entity_id, "brightness": next_brightness}, verify=verify)],
        )

    def _current_brightness(self, entity_id: str | None) -> int | None:
        if entity_id is None:
            return None
        state = self._entity_resolver.hass.states.get(entity_id)
        if state is None or state.domain != "light":
            return None
        brightness = state.attributes.get("brightness")
        if not isinstance(brightness, (int, float)):
            return None
        return round(float(brightness) * 100 / 255)

    @staticmethod
    def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def _turn_off_last(text: str, entity_id: str) -> TaskPlan:
        return TaskPlan(
            goal=text,
            scene="dialogue_reference",
            steps=[
                PlanStep(
                    id="reference_1",
                    tool="turn_off_device",
                    arguments={"entity_id": entity_id},
                    verify=VerificationRule(entity_id=entity_id, state="off"),
                )
            ],
        )

    def _relative_temperature(self, text: str, state: DialogueState, step: PlanStep, increase: bool) -> TaskPlan | None:
        if step.tool != "set_temperature":
            return None
        temperature = step.arguments.get("temperature")
        if not isinstance(temperature, (int, float)):
            return None
        next_temperature = max(16.0, min(30.0, float(temperature) + (1.0 if increase else -1.0)))
        entity_id = state.last_entity_id
        if entity_id is None:
            return None
        return TaskPlan(
            goal=text,
            scene="dialogue_reference",
            steps=[
                PlanStep(
                    id="reference_1",
                    tool="set_temperature",
                    arguments={"entity_id": entity_id, "temperature": next_temperature},
                    verify=VerificationRule(entity_id=entity_id, attributes={"temperature": next_temperature}, tolerance=0.1),
                )
            ],
        )


def _replace_entity(step: PlanStep, source_entity_id: str, target_entity_id: str) -> PlanStep:
    arguments = dict(step.arguments)
    if arguments.get("entity_id") == source_entity_id:
        arguments["entity_id"] = target_entity_id
    verify = step.verify
    if verify and verify.entity_id == source_entity_id:
        verify = verify.model_copy(update={"entity_id": target_entity_id})
    preconditions = [
        condition.model_copy(update={"entity_id": target_entity_id}) if condition.entity_id == source_entity_id else condition
        for condition in step.preconditions
    ]
    return step.model_copy(update={"arguments": arguments, "verify": verify, "preconditions": preconditions})
