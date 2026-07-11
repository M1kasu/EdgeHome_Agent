"""Execute only validated fixed plans through Home Assistant's service registry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pydantic import BaseModel

from ..entity_resolver import ExposedEntityResolver
from ..errors import EdgeHomeError, PlanValidationError
from ..models import (
    PlanStep,
    EntitySnapshot,
    StepExecutionResult,
    StepStatus,
    TaskExecutionReport,
    TaskPlan,
    TaskStatus,
    VerificationResult,
    VerificationRule,
)
from ..plan_validator import PlanValidator
from ..tools.registry import ToolDefinition, ToolRegistry, build_default_tool_registry
from .verifier import StateVerifier


class FixedPlanExecutor:
    """Run one validated plan in order, with bounded retries and fresh state reads."""

    def __init__(
        self,
        hass: HomeAssistant,
        tools: ToolRegistry | None = None,
        resolver: ExposedEntityResolver | None = None,
        max_steps: int = 12,
    ) -> None:
        self.hass = hass
        self.tools = tools or build_default_tool_registry()
        self.resolver = resolver or ExposedEntityResolver(hass)
        self.validator = PlanValidator(self.tools, self.resolver, max_steps=max_steps)
        self.verifier = StateVerifier(self.resolver)

    async def async_execute(
        self,
        plan: TaskPlan,
        authorized_protected_entities: frozenset[str] = frozenset(),
    ) -> TaskExecutionReport:
        """Validate the whole plan before the first service call, then run sequentially."""
        started_at = datetime.now(timezone.utc)
        try:
            validated_plan = self.validator.validate(plan, authorized_protected_entities)
        except PlanValidationError as error:
            failed_steps = [
                StepExecutionResult(
                    step_id=issue.step_id,
                    tool="validation",
                    status=StepStatus.FAILED,
                    attempts=0,
                    error_code=issue.code,
                    error_message=issue.message,
                )
                for issue in error.issues
            ]
            return TaskExecutionReport(
                goal=plan.goal,
                status=TaskStatus.FAILED,
                step_results=failed_steps,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        results: list[StepExecutionResult] = []
        needs_replan = False
        for step in validated_plan.steps:
            result = await self.async_execute_step(step)
            results.append(result)
            if result.status == StepStatus.SUCCESS:
                continue
            if step.on_failure.value == "skip" or step.optional:
                continue
            if step.on_failure.value == "replan":
                needs_replan = True
            break

        status = self._task_status(results, needs_replan)
        return TaskExecutionReport(
            goal=validated_plan.goal,
            status=status,
            step_results=results,
            needs_replan=needs_replan,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )

    async def async_execute_step(self, step: PlanStep) -> StepExecutionResult:
        """Execute one already validated step; retry only service/verification failures."""
        definition = self.tools.get(step.tool)
        if definition is None:
            return self._failure(step, 0, "tool_not_registered", f"工具未注册: {step.tool}")

        arguments = definition.validate_arguments(step.arguments)
        entity_id = arguments.model_dump()["entity_id"]
        try:
            for condition in step.preconditions:
                condition_result = await self.verifier.async_verify(condition)
                if not condition_result.passed:
                    return self._failure(
                        step,
                        0,
                        "precondition_failed",
                        condition_result.reason or "前置条件不满足",
                        verification=condition_result,
                    )

            if not definition.invokes_service:
                snapshot = self.resolver.snapshot(entity_id, definition.allowed_domains)
                return StepExecutionResult(
                    step_id=step.id,
                    tool=step.tool,
                    status=StepStatus.SUCCESS,
                    attempts=1,
                    state_before=snapshot,
                    state_after=snapshot,
                )

            return await self._async_execute_service(step, definition, arguments)
        except EdgeHomeError as error:
            return self._failure(step, 0, error.code, error.message)

    async def _async_execute_service(
        self,
        step: PlanStep,
        definition: ToolDefinition,
        arguments: BaseModel,
    ) -> StepExecutionResult:
        attempts = 0
        last_result: StepExecutionResult | None = None
        entity_id = arguments.model_dump()["entity_id"]
        for attempts in range(1, definition.max_retries + 2):
            started_at = datetime.now(timezone.utc)
            try:
                before = self.resolver.snapshot(entity_id, definition.allowed_domains)
                domain = definition.service_domain or before.domain
                service_data = definition.build_service_data(arguments)
                async with asyncio.timeout(definition.timeout_seconds):
                    await self.hass.services.async_call(
                        domain,
                        definition.service or "",
                        service_data=service_data,
                        blocking=True,
                    )
                verification = await self._verify_step(step)
                after = verification.snapshot or self.resolver.snapshot(entity_id, definition.allowed_domains)
                if verification.passed:
                    return StepExecutionResult(
                        step_id=step.id,
                        tool=step.tool,
                        status=StepStatus.SUCCESS,
                        attempts=attempts,
                        state_before=before,
                        state_after=after,
                        verification=verification,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )
                last_result = self._failure(
                    step,
                    attempts,
                    "verification_failed",
                    verification.reason or "设备状态未达到预期",
                    state_before=before,
                    state_after=after,
                    verification=verification,
                    started_at=started_at,
                )
            except TimeoutError:
                last_result = self._failure(
                    step,
                    attempts,
                    "service_timeout",
                    f"工具调用超时: {definition.timeout_seconds} 秒",
                    started_at=started_at,
                )
            except HomeAssistantError as error:
                last_result = self._failure(
                    step,
                    attempts,
                    "service_call_failed",
                    str(error),
                    started_at=started_at,
                )
            except EdgeHomeError as error:
                last_result = self._failure(step, attempts, error.code, error.message, started_at=started_at)

        assert last_result is not None
        return last_result

    async def _verify_step(self, step: PlanStep) -> VerificationResult:
        if step.verify is None:
            return VerificationResult(passed=False, reason="关键步骤缺少验证规则")
        return await self.verifier.async_verify(step.verify)

    @staticmethod
    def _task_status(results: list[StepExecutionResult], needs_replan: bool) -> TaskStatus:
        if needs_replan:
            return TaskStatus.NEEDS_REPLAN
        if results and all(result.status == StepStatus.SUCCESS for result in results):
            return TaskStatus.SUCCESS
        if any(result.status == StepStatus.SUCCESS for result in results):
            return TaskStatus.PARTIAL_SUCCESS
        return TaskStatus.FAILED

    @staticmethod
    def _failure(
        step: PlanStep,
        attempts: int,
        code: str,
        message: str,
        *,
        state_before: EntitySnapshot | None = None,
        state_after: EntitySnapshot | None = None,
        verification: VerificationResult | None = None,
        started_at: datetime | None = None,
    ) -> StepExecutionResult:
        return StepExecutionResult(
            step_id=step.id,
            tool=step.tool,
            status=StepStatus.FAILED,
            attempts=attempts,
            error_code=code,
            error_message=message,
            state_before=state_before,
            state_after=state_after,
            verification=verification,
            started_at=started_at or datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
