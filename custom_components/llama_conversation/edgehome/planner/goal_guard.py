"""Compile explicit user prohibitions into checks on an untrusted task plan."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..models import EntitySnapshot, PlanStep, TaskPlan


_NEGATION = re.compile(r"(?:不要|别|禁止|不得|无需|不需要)")
_SENTENCE_END = re.compile(r"[。；，,！？!?]")
_ENABLE = re.compile(r"(?:打开|开启|启动|开)")
_DISABLE = re.compile(r"(?:关闭|关掉|关上|熄灭)")
_CHANGE = re.compile(r"(?:调节|调整|设置|设为|调到|拉到)")
_DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "climate": ("空调", "制冷", "制热", "climate", "hvac"),
    "light": ("主灯", "灯光", "照明", "灯", "light"),
    "cover": ("窗帘", "窗帘", "cover"),
    "switch": ("电视", "插座", "开关", "switch"),
    "media_player": ("电视", "媒体", "media"),
}


@dataclass(frozen=True, slots=True)
class PlanSanitization:
    """A traceable, least-privilege reduction of an already-valid plan."""

    plan: TaskPlan
    removed_step_ids: tuple[str, ...] = ()


class GoalConstraintGuard:
    """Reject a plan whose controls contradict an explicit user prohibition.

    This guard is intentionally narrow: it has no authority to add actions or
    select devices.  An explicit negative instruction such as ``不要打开空调``
    makes the named device read-only for that request.  This deliberately
    blocks *all* mutations, including a seemingly harmless ``turn_off``:
    planning must not invent an extra state change while trying to satisfy a
    safety boundary.
    Entity IDs and friendly names still come solely from the live snapshot.
    """

    def validate(self, plan: TaskPlan, goal: str, entities: list[EntitySnapshot]) -> str | None:
        """Return a deterministic issue code, or ``None`` when no conflict exists."""
        forbidden = self._forbidden_operations(goal, entities)
        if not forbidden:
            return None
        domains = {entity.entity_id: entity.domain for entity in entities}
        for step in plan.steps:
            entity_id = step.arguments.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            domain = domains.get(entity_id, entity_id.split(".", maxsplit=1)[0])
            operation = self._operation(step)
            if operation is not None and operation in forbidden.get(domain, frozenset()):
                return f"goal_constraint_no_control:{domain}"
        return None

    def read_only_domains(self, goal: str, entities: list[EntitySnapshot]) -> frozenset[str]:
        """Expose prompt-safe names for domains an explicit negative instruction protects.

        This is advisory information for the untrusted model.  ``validate``
        remains the policy enforcement point after the model responds.
        """
        return frozenset(self._forbidden_operations(goal, entities))

    def sanitize(self, plan: TaskPlan, goal: str, entities: list[EntitySnapshot]) -> PlanSanitization:
        """Remove controls barred by an explicit user constraint, never add one.

        A small local model can occasionally append a defensive action that
        the user did not authorize.  Dropping that action is safer than asking
        the executor to infer an opposite action, and preserves independent,
        already-valid parts of the model plan.  Any dependencies that pointed
        only to a removed action are dropped as well; no new dependency or
        control step is introduced.
        """
        forbidden = self._forbidden_operations(goal, entities)
        if not forbidden:
            return PlanSanitization(plan=plan)
        domains = {entity.entity_id: entity.domain for entity in entities}
        remaining: list[PlanStep] = []
        removed: list[str] = []
        for step in plan.steps:
            entity_id = step.arguments.get("entity_id")
            domain = domains.get(entity_id, entity_id.split(".", maxsplit=1)[0]) if isinstance(entity_id, str) else None
            if domain is not None and self._operation(step) in forbidden.get(domain, frozenset()):
                removed.append(step.id)
                continue
            remaining.append(step)
        if not removed:
            return PlanSanitization(plan=plan)
        remaining_ids = {step.id for step in remaining}
        rewritten = [
            step.model_copy(update={"depends_on": [dependency for dependency in step.depends_on if dependency in remaining_ids]})
            for step in remaining
        ]
        return PlanSanitization(plan=plan.model_copy(update={"steps": rewritten}), removed_step_ids=tuple(removed))

    @staticmethod
    def _operation(step: PlanStep) -> str | None:
        if step.tool == "turn_on_device":
            return "enable"
        if step.tool == "turn_off_device":
            return "disable"
        if step.tool == "set_hvac_mode":
            return "disable" if step.arguments.get("hvac_mode") == "off" else "enable"
        if step.tool in {"set_light_brightness", "set_temperature", "control_curtain"}:
            return "change"
        return None

    def _forbidden_operations(self, goal: str, entities: list[EntitySnapshot]) -> dict[str, frozenset[str]]:
        normalized = "".join(goal.lower().split())
        forbidden: dict[str, set[str]] = {}
        for match in _NEGATION.finditer(normalized):
            fragment = normalized[match.start() : match.start() + 28]
            sentence_end = _SENTENCE_END.search(fragment)
            if sentence_end is not None:
                fragment = fragment[: sentence_end.start()]
            if self._fragment_operation(fragment) is None:
                continue
            target_domains = self._matching_domains(fragment, entities)
            for domain in target_domains:
                # A negative user instruction is a control boundary rather
                # than an invitation to choose the opposite operation.  The
                # planner may read this device, but it may not alter it.
                forbidden.setdefault(domain, set()).update({"enable", "disable", "change"})
        return {domain: frozenset(operations) for domain, operations in forbidden.items()}

    @staticmethod
    def _fragment_operation(fragment: str) -> str | None:
        if _ENABLE.search(fragment):
            return "enable"
        if _DISABLE.search(fragment):
            return "disable"
        if _CHANGE.search(fragment):
            return "change"
        return None

    @staticmethod
    def _matching_domains(fragment: str, entities: list[EntitySnapshot]) -> set[str]:
        # Domain aliases are policy vocabulary, not a device allow-list.  They
        # still work when a textual target was intentionally omitted from the
        # compact model snapshot, while actual execution remains limited by
        # the resolver and registry.
        domains = {
            domain
            for domain, aliases in _DOMAIN_ALIASES.items()
            if any(alias in fragment for alias in aliases)
        }
        for entity in entities:
            friendly_name = (entity.friendly_name or "").lower()
            if friendly_name and friendly_name in fragment:
                domains.add(entity.domain)
        if domains:
            return domains
        if any(token in fragment for token in ("设备", "电器", "家电")):
            return {entity.domain for entity in entities}
        return set()
