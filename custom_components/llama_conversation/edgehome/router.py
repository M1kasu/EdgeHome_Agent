"""Build one fixed plan for a narrow Chinese fast-path request, or defer safely."""

from __future__ import annotations

from collections.abc import Container

from .entity_resolver import EntityNameResolver
from .errors import EntityResolutionError
from .models import PlanStep, TaskPlan, VerificationRule
from .understanding.intent_classifier import ChineseIntentClassifier
from .understanding.models import FastIntent, FastRouteDecision, FastRouteStatus
from .understanding.slot_extractor import ChineseSlotExtractor


class ChineseFastRouter:
    """Convert only high-confidence single-device Chinese requests into fixed plans."""

    _CONTROLLABLE_DOMAINS = frozenset({"light", "switch", "fan", "media_player", "vacuum"})

    def __init__(
        self,
        resolver: EntityNameResolver,
        classifier: ChineseIntentClassifier | None = None,
        extractor: ChineseSlotExtractor | None = None,
    ) -> None:
        self._resolver = resolver
        self._classifier = classifier or ChineseIntentClassifier()
        self._extractor = extractor or ChineseSlotExtractor()

    def route(self, text: str) -> FastRouteDecision:
        """Return a fixed plan only after intent, value, and entity checks all pass."""
        intent_match = self._classifier.classify(text)
        if intent_match.intent == FastIntent.UNKNOWN:
            return FastRouteDecision(
                status=FastRouteStatus.FALLBACK,
                intent=intent_match.intent,
                reason=intent_match.reason,
            )

        slots = self._extractor.extract(text, intent_match.intent)
        if slots.errors:
            return FastRouteDecision(
                status=FastRouteStatus.CLARIFICATION,
                intent=intent_match.intent,
                reason="；".join(slots.errors),
            )

        try:
            resolved = self._resolver.resolve_name(slots.entity_query, self._domains_for(intent_match.intent))
        except EntityResolutionError as error:
            return FastRouteDecision(
                status=FastRouteStatus.CLARIFICATION,
                intent=intent_match.intent,
                reason=error.code,
            )

        return FastRouteDecision(
            status=FastRouteStatus.READY,
            intent=intent_match.intent,
            reason=intent_match.reason,
            entity=resolved.snapshot,
            plan=self._build_plan(text, intent_match.intent, resolved.snapshot.entity_id, slots.value),
        )

    def _domains_for(self, intent: FastIntent) -> Container[str] | None:
        if intent in {FastIntent.TURN_ON, FastIntent.TURN_OFF}:
            return self._CONTROLLABLE_DOMAINS
        if intent == FastIntent.SET_BRIGHTNESS:
            return frozenset({"light"})
        if intent == FastIntent.SET_TEMPERATURE:
            return frozenset({"climate"})
        if intent == FastIntent.CONTROL_CURTAIN:
            return frozenset({"cover"})
        return None

    @staticmethod
    def _build_plan(text: str, intent: FastIntent, entity_id: str, value: float | None) -> TaskPlan:
        step = _step_for(intent, entity_id, value)
        return TaskPlan(goal=text.strip(), scene="fast_path", steps=[step])


def _step_for(intent: FastIntent, entity_id: str, value: float | None) -> PlanStep:
    if intent == FastIntent.QUERY_STATE:
        return PlanStep(id="fast_1", tool="get_device_state", arguments={"entity_id": entity_id})
    if intent == FastIntent.TURN_ON:
        return PlanStep(
            id="fast_1",
            tool="turn_on_device",
            arguments={"entity_id": entity_id},
            verify=VerificationRule(entity_id=entity_id, state="on"),
        )
    if intent == FastIntent.TURN_OFF:
        return PlanStep(
            id="fast_1",
            tool="turn_off_device",
            arguments={"entity_id": entity_id},
            verify=VerificationRule(entity_id=entity_id, state="off"),
        )
    if intent == FastIntent.SET_BRIGHTNESS and value is not None:
        brightness = int(value)
        return PlanStep(
            id="fast_1",
            tool="set_light_brightness",
            arguments={"entity_id": entity_id, "brightness": brightness},
            verify=VerificationRule(
                entity_id=entity_id,
                state="on",
                attributes={"brightness": round(brightness * 255 / 100)},
                tolerance=1.0,
            ),
        )
    if intent == FastIntent.SET_TEMPERATURE and value is not None:
        return PlanStep(
            id="fast_1",
            tool="set_temperature",
            arguments={"entity_id": entity_id, "temperature": value},
            verify=VerificationRule(entity_id=entity_id, attributes={"temperature": value}, tolerance=0.1),
        )
    if intent == FastIntent.CONTROL_CURTAIN and value is not None:
        return PlanStep(
            id="fast_1",
            tool="control_curtain",
            arguments={"entity_id": entity_id, "position": int(value)},
            verify=VerificationRule(
                entity_id=entity_id,
                attributes={"current_position": int(value)},
                tolerance=1.0,
            ),
        )
    raise ValueError(f"无法为快速意图构造固定计划: {intent}")
