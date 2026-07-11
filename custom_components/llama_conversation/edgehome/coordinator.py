"""Coordinate the narrow fast path without exposing arbitrary Home Assistant services."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from ..entity import LocalLLMClient
from .entity_resolver import EntityNameResolver
from .execution.executor import FixedPlanExecutor
from .context.home_state import TaskHomeStateBuilder
from .models import EntitySnapshot, StepExecutionResult, TaskStatus
from .planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningStatus
from .planner.parser import StructuredPlanParser
from .router import ChineseFastRouter
from .understanding.models import FastIntent, FastRouteDecision, FastRouteStatus


@dataclass(frozen=True, slots=True)
class FastPathResponse:
    """Conversation-facing result that distinguishes safe handling from model fallback."""

    handled: bool
    speech: str | None = None
    decision: FastRouteDecision | None = None


class EdgeHomeCoordinator:
    """Execute a ready fixed plan, or deliberately leave non-fast requests to Home LLM."""

    def __init__(
        self,
        hass: HomeAssistant,
        resolver: EntityNameResolver | None = None,
        executor: FixedPlanExecutor | None = None,
    ) -> None:
        self._resolver = resolver or EntityNameResolver(hass)
        self._router = ChineseFastRouter(self._resolver)
        self._executor = executor or FixedPlanExecutor(hass, resolver=self._resolver)

    async def async_handle(self, text: str) -> FastPathResponse:
        """Handle ready plans; never retry a failed direct action through the legacy model path."""
        decision = self._router.route(text)
        if decision.status == FastRouteStatus.FALLBACK:
            return FastPathResponse(handled=False, decision=decision)
        if decision.status == FastRouteStatus.CLARIFICATION:
            return FastPathResponse(handled=True, speech=self._clarification(decision), decision=decision)

        assert decision.plan is not None
        report = await self._executor.async_execute(decision.plan)
        if report.status == TaskStatus.SUCCESS:
            return FastPathResponse(
                handled=True,
                speech=self._success_speech(decision, report.step_results[0]),
                decision=decision,
            )
        return FastPathResponse(
            handled=True,
            speech=self._failure_speech(decision, report.step_results),
            decision=decision,
        )

    async def async_handle_complex(
        self,
        text: str,
        agent_id: str,
        runtime_options: dict[str, object],
        planner: LLMTaskPlanner,
    ) -> FastPathResponse:
        """Plan and run a complex request only after the model result passes all fixed-plan gates."""
        if not planner.is_candidate(text):
            return FastPathResponse(handled=False)
        planning = await planner.async_plan(PlanningRequest(goal=text, agent_id=agent_id), runtime_options)
        if planning.status != PlanningStatus.ACCEPTED or planning.plan is None:
            return FastPathResponse(handled=False)

        report = await self._executor.async_execute(planning.plan)
        if report.status == TaskStatus.SUCCESS:
            return FastPathResponse(handled=True, speech=f"已完成任务：{planning.plan.goal}。")
        failure = report.step_results[-1] if report.step_results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return FastPathResponse(handled=True, speech=f"任务未完成：{reason}。")

    def build_planner(self, client: LocalLLMClient) -> LLMTaskPlanner:
        """Build the model adapter from the same resolver, registry, and validator as execution."""
        return LLMTaskPlanner(
            client,
            TaskHomeStateBuilder(self._resolver),
            StructuredPlanParser(self._executor.validator),
            self._executor.tools,
        )

    @staticmethod
    def _clarification(decision: FastRouteDecision) -> str:
        if decision.reason == "entity_name_ambiguous":
            return "找到多个匹配设备，请说出更完整的设备名称。"
        if decision.reason == "entity_name_not_found":
            return "没有找到已开放给对话控制的设备，请确认设备名称。"
        return f"暂时无法安全执行：{decision.reason}。"

    @staticmethod
    def _success_speech(decision: FastRouteDecision, result: StepExecutionResult) -> str:
        snapshot = result.state_after or result.state_before
        if decision.intent == FastIntent.QUERY_STATE and snapshot:
            return _state_speech(snapshot)
        entity_name = decision.entity.friendly_name if decision.entity and decision.entity.friendly_name else "设备"
        action = {
            FastIntent.TURN_ON: "已打开",
            FastIntent.TURN_OFF: "已关闭",
            FastIntent.SET_BRIGHTNESS: "已设置亮度",
            FastIntent.SET_TEMPERATURE: "已设置温度",
            FastIntent.CONTROL_CURTAIN: "已设置窗帘位置",
        }.get(decision.intent, "已完成")
        return f"{action}{entity_name}。"

    @staticmethod
    def _failure_speech(decision: FastRouteDecision, results: list[StepExecutionResult]) -> str:
        entity_name = decision.entity.friendly_name if decision.entity and decision.entity.friendly_name else "该设备"
        failure = results[-1] if results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return f"未能完成对{entity_name}的操作：{reason}。"


def _state_speech(snapshot: EntitySnapshot) -> str:
    """Return a concise state response without leaking unrelated entity attributes."""
    name = snapshot.friendly_name or snapshot.entity_id
    details: list[str] = []
    if "temperature" in snapshot.attributes:
        details.append(f"温度 {snapshot.attributes['temperature']}")
    if "current_position" in snapshot.attributes:
        details.append(f"位置 {snapshot.attributes['current_position']}%")
    if "brightness" in snapshot.attributes:
        brightness = snapshot.attributes["brightness"]
        if isinstance(brightness, int):
            details.append(f"亮度约 {round(brightness * 100 / 255)}%")
    suffix = f"，{'，'.join(details)}" if details else ""
    return f"{name}当前状态为 {snapshot.state}{suffix}。"
