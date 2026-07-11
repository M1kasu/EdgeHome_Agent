"""Build a bounded, auditable follow-up planning request after a recoverable failure."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..models import TaskExecutionReport, TaskPlan, TaskStatus


_NON_REPLANNABLE_CODES = frozenset(
    {
        "protected_entity_confirmation_required",
        "conflicting_actions",
        "entity_not_exposed",
        "entity_not_found",
        "precondition_failed",
    }
)


class ReplanDecision(BaseModel):
    """A single model-planning retry request, or an explicit reason to stop."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    prompt_goal: str | None = None
    reason: str


class FailureReplanner:
    """Permit one fresh plan only for a report explicitly marked as needing replan."""

    def decide(self, original_goal: str, plan: TaskPlan, report: TaskExecutionReport) -> ReplanDecision:
        """Convert a trusted execution report into concise, non-secret model context."""
        if report.status != TaskStatus.NEEDS_REPLAN or not report.needs_replan:
            return ReplanDecision(allowed=False, reason="report_not_marked_for_replan")
        failure = next((result for result in reversed(report.step_results) if result.status.value == "failed"), None)
        if failure is None:
            return ReplanDecision(allowed=False, reason="failure_not_found")
        if failure.error_code in _NON_REPLANNABLE_CODES:
            return ReplanDecision(allowed=False, reason=failure.error_code)

        failed_entity = failure.state_after.entity_id if failure.state_after else None
        entity_hint = failed_entity or "未提供实体快照"
        reason = (failure.error_code or "execution_failed")[:80]
        prompt_goal = (
            f"{original_goal}\n"
            f"首次固定计划“{plan.goal}”在步骤 {failure.step_id} 失败（{reason}，实体 {entity_hint}）。"
            "请仅生成一次替代固定计划；不得重复失败步骤、不得扩大控制范围，且所有控制步骤必须带验证规则。"
        )
        return ReplanDecision(allowed=True, prompt_goal=prompt_goal[:300], reason=reason)
