"""Reject unsafe fixed plans before Home Assistant services are called."""

from __future__ import annotations

from collections.abc import Iterable

from ..entity_resolver import ExposedEntityResolver
from ..errors import EdgeHomeError, PlanValidationIssue
from ..models import PlanStep, TaskPlan


_PROTECTED_TOKENS = ("冰箱", "refrigerator", "fridge", "路由器", "router", "nas", "服务器", "server")


class PlanSafetyGuard:
    """Apply protected-device and contradictory-action rules to one fixed plan."""

    def __init__(
        self,
        resolver: ExposedEntityResolver,
        protected_entity_ids: Iterable[str] = (),
    ) -> None:
        self._resolver = resolver
        self._protected_entity_ids = frozenset(protected_entity_ids)

    def validate(
        self,
        plan: TaskPlan,
        authorized_protected_entities: frozenset[str] = frozenset(),
    ) -> list[PlanValidationIssue]:
        """Return every safety violation without executing or mutating device state."""
        issues = self._conflicts(plan.steps)
        for step in plan.steps:
            if step.tool != "turn_off_device":
                continue
            entity_id = step.arguments.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            try:
                snapshot = self._resolver.snapshot(entity_id)
            except EdgeHomeError:
                continue
            if self._is_protected(snapshot.entity_id, snapshot.friendly_name) and snapshot.entity_id not in authorized_protected_entities:
                name = snapshot.friendly_name or snapshot.entity_id
                issues.append(
                    PlanValidationIssue(
                        step.id,
                        "protected_entity_confirmation_required",
                        f"{name} 是受保护设备，关闭前必须经过二次确认",
                    )
                )
        return issues

    def protected_shutdowns(self, plan: TaskPlan) -> list[str]:
        """List protected shutdown targets for a trusted conversation confirmation flow."""
        entity_ids: list[str] = []
        for step in plan.steps:
            if step.tool != "turn_off_device":
                continue
            entity_id = step.arguments.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            try:
                snapshot = self._resolver.snapshot(entity_id)
            except EdgeHomeError:
                continue
            if self._is_protected(snapshot.entity_id, snapshot.friendly_name):
                entity_ids.append(snapshot.entity_id)
        return entity_ids

    def _is_protected(self, entity_id: str, friendly_name: str | None) -> bool:
        if self._resolver.is_protected(entity_id):
            return True
        if entity_id in self._protected_entity_ids:
            return True
        searchable = f"{entity_id} {friendly_name or ''}".lower()
        return any(token in searchable for token in _PROTECTED_TOKENS)

    @staticmethod
    def _conflicts(steps: list[PlanStep]) -> list[PlanValidationIssue]:
        by_entity: dict[str, list[PlanStep]] = {}
        for step in steps:
            entity_id = step.arguments.get("entity_id")
            if isinstance(entity_id, str):
                by_entity.setdefault(entity_id, []).append(step)

        issues: list[PlanValidationIssue] = []
        for entity_id, entity_steps in by_entity.items():
            tools = {step.tool for step in entity_steps}
            conflicting = (
                {"turn_on_device", "turn_off_device"}.issubset(tools)
                or ("turn_off_device" in tools and "set_light_brightness" in tools)
            )
            if conflicting:
                for step in entity_steps:
                    issues.append(
                        PlanValidationIssue(
                            step.id,
                            "conflicting_actions",
                            f"{entity_id} 在同一计划中包含相互冲突的控制动作",
                        )
                    )
        return issues
