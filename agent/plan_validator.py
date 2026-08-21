from __future__ import annotations

from dataclasses import dataclass

from agent.plan_schema import TaskPlan
from safety.action_guard import ActionGuard
from safety.conflict_detector import ConflictDetector
from tools.registry import ToolRegistry


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: list[str]
    requires_confirmation: bool = False


class PlanValidator:
    def __init__(self, tools: ToolRegistry, guard: ActionGuard, max_steps: int) -> None:
        self.tools = tools
        self.guard = guard
        self.max_steps = max_steps
        self.conflicts = ConflictDetector()

    def validate(self, plan: TaskPlan, *, confirm: bool = False) -> ValidationResult:
        errors: list[str] = []
        needs_confirmation = False
        if not plan.steps:
            errors.append("任务计划不能为空")
        if len(plan.steps) > self.max_steps:
            errors.append(f"任务计划超过最大步骤数 {self.max_steps}")
        for step in plan.steps:
            if self.tools.get(step.tool) is None:
                errors.append(f"未注册工具: {step.tool}")
                continue
            result = self.guard.check(step.tool, step.arguments, confirm=confirm)
            errors.extend(f"{step.id}: {error}" for error in result.errors)
            needs_confirmation = needs_confirmation or result.requires_confirmation
        errors.extend(self.conflicts.find_conflicts(plan.steps))
        return ValidationResult(not errors, errors, needs_confirmation)
