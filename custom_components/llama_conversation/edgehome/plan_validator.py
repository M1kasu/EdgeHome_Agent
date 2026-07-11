"""Deterministic validation for fixed EdgeHome plans."""

from __future__ import annotations

from .entity_resolver import ExposedEntityResolver
from .errors import EdgeHomeError, PlanValidationError, PlanValidationIssue
from .models import PlanStep, TaskPlan
from .tools.registry import ToolRegistry


class PlanValidator:
    """Validate every tool, entity, argument, precondition, and verifier before execution."""

    def __init__(self, tools: ToolRegistry, resolver: ExposedEntityResolver, max_steps: int = 12) -> None:
        self._tools = tools
        self._resolver = resolver
        self._max_steps = max_steps

    def validate(self, plan: TaskPlan) -> TaskPlan:
        """Return a normalized plan or raise all discovered validation issues."""
        issues: list[PlanValidationIssue] = []
        normalized_steps: list[PlanStep] = []

        if len(plan.steps) > self._max_steps:
            issues.append(
                PlanValidationIssue(
                    step_id="plan",
                    code="max_steps_exceeded",
                    message=f"计划步骤数超过上限 {self._max_steps}",
                )
            )

        seen_step_ids: set[str] = set()
        for step in plan.steps:
            if step.id in seen_step_ids:
                issues.append(PlanValidationIssue(step.id, "duplicate_step_id", "步骤 ID 不能重复"))
                continue
            seen_step_ids.add(step.id)
            normalized = self._validate_step(step, issues)
            if normalized:
                normalized_steps.append(normalized)

        if issues:
            raise PlanValidationError(issues)
        return plan.model_copy(update={"steps": normalized_steps})

    def _validate_step(self, step: PlanStep, issues: list[PlanValidationIssue]) -> PlanStep | None:
        definition = self._tools.get(step.tool)
        if definition is None:
            issues.append(PlanValidationIssue(step.id, "tool_not_registered", f"工具未注册: {step.tool}"))
            return None

        try:
            arguments_model = definition.validate_arguments(step.arguments)
        except EdgeHomeError as error:
            issues.append(PlanValidationIssue(step.id, error.code, error.message))
            return None

        normalized_arguments = arguments_model.model_dump()
        entity_id = normalized_arguments.get("entity_id")
        if not isinstance(entity_id, str):
            issues.append(PlanValidationIssue(step.id, "entity_id_missing", "工具参数缺少 entity_id"))
            return None

        try:
            self._resolver.resolve(entity_id, definition.allowed_domains)
        except EdgeHomeError as error:
            issues.append(PlanValidationIssue(step.id, error.code, error.message))

        if definition.requires_verification and step.verify is None:
            issues.append(PlanValidationIssue(step.id, "verification_required", "关键设备动作必须提供 verify"))
        if step.verify and step.verify.entity_id != entity_id:
            issues.append(PlanValidationIssue(step.id, "verification_entity_mismatch", "验证实体必须与操作实体一致"))

        for condition in step.preconditions:
            try:
                self._resolver.resolve(condition.entity_id)
            except EdgeHomeError as error:
                issues.append(PlanValidationIssue(step.id, error.code, error.message))

        return step.model_copy(update={"arguments": normalized_arguments})
