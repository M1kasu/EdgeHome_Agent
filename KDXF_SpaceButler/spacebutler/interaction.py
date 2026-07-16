"""Interaction layer for proactive suggestions, confirmation and feedback."""

from __future__ import annotations

from dataclasses import dataclass

from .agent import SpaceButlerAgent
from .models import ExecutionReport, ServicePlan, SpatialSnapshot
from .runtime import HomeRuntime, execute_and_verify


@dataclass(frozen=True)
class InteractionResponse:
    status: str
    message: str
    plan: ServicePlan | None = None
    report: ExecutionReport | None = None
    learned: str | None = None


class SpaceButlerSession:
    """Small stateful shell around the Agent for black-box scenario acceptance."""

    def __init__(self, agent: SpaceButlerAgent, runtime: HomeRuntime) -> None:
        self._agent = agent
        self._runtime = runtime
        self._pending_plan: ServicePlan | None = None
        self._last_report: ExecutionReport | None = None

    def observe(self, snapshot: SpatialSnapshot) -> InteractionResponse:
        plans = self._agent.observe_and_plan(snapshot)
        plan = next((item for item in plans if item.plan_id == "empty_room_open_window_energy_guard"), None)
        if plan is None:
            return InteractionResponse(status="ignored", message="没有发现需要主动节能处理的可靠机会。")
        if plan.requires_confirmation:
            self._pending_plan = plan
            return InteractionResponse(
                status="needs_confirmation",
                message=f"{plan.title}：{plan.explanation}",
                plan=plan,
            )
        report = execute_and_verify(self._runtime, plan)
        self._last_report = report
        return InteractionResponse(
            status="executed" if report.verified else "execution_failed",
            message=report.verification_summary,
            plan=plan,
            report=report,
        )

    def user_message(self, member_id: str, text: str) -> InteractionResponse:
        normalized = "".join(text.split())
        if normalized in {"确认", "同意", "执行", "可以", "关掉吧"}:
            if self._pending_plan is None:
                return InteractionResponse(status="no_pending_plan", message="当前没有待确认计划。")
            plan = self._pending_plan
            self._pending_plan = None
            report = execute_and_verify(self._runtime, plan)
            self._last_report = report
            return InteractionResponse(
                status="executed" if report.verified else "execution_failed",
                message=report.verification_summary,
                plan=plan,
                report=report,
            )
        if normalized in {"拒绝", "不用", "取消", "先别关"}:
            self._pending_plan = None
            return InteractionResponse(status="cancelled", message="已取消本次主动节能建议。")
        learned = self._agent.memory.apply_energy_feedback(member_id, text)
        if learned != "no_structured_energy_feedback":
            return InteractionResponse(status="learned", message="已记录这条节能偏好。", learned=learned)
        return InteractionResponse(status="unhandled", message="暂未识别为确认、取消或节能偏好反馈。")

    @property
    def last_report(self) -> ExecutionReport | None:
        return self._last_report
