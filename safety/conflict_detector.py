from __future__ import annotations

from agent.plan_schema import PlanStep


class ConflictDetector:
    """Detect contradictory device actions before execution."""

    def find_conflicts(self, steps: list[PlanStep]) -> list[str]:
        conflicts: list[str] = []
        actions: dict[str, set[str]] = {}
        for step in steps:
            entity_id = step.arguments.get("entity_id")
            if not entity_id:
                continue
            action = step.tool
            current = actions.setdefault(entity_id, set())
            if {"turn_on_device", "turn_off_device"}.issubset(current | {action}):
                conflicts.append(f"{entity_id} 在同一计划中同时要求打开和关闭")
            current.add(action)
        return conflicts
