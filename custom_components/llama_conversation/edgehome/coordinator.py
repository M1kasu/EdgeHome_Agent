"""Coordinate the narrow fast path without exposing arbitrary Home Assistant services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from homeassistant.core import HomeAssistant

from ..entity import LocalLLMClient
from .entity_registry import build_demo_entity_registry
from .entity_resolver import EntityNameResolver
from .execution.executor import FixedPlanExecutor
from .execution.environment_guard import EnvironmentPlanGuard
from .execution.replanner import FailureReplanner
from .context.environment import EnvironmentContextBuilder
from .context.store import DialogueStateStore
from .energy.statistics import HomeAssistantEnergyStatistics
from .energy.conversation import EnergyConversationHandler
from .energy.service import EnergyService
from .benchmark.input_guard import InputLengthGuard
from .models import EntitySnapshot, StepExecutionResult, TaskPlan, TaskStatus
from .information import ReadOnlyInformationHandler
from .knowledge.associations import DeviceManualRegistry
from .knowledge.indexer import LocalKnowledgeIndex
from .knowledge.service import KnowledgeService
from .planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningResult, PlanningStatus
from .planner.parser import StructuredPlanParser
from .resource.monitor import ResourceMonitor
from .resource.scheduler import ResourceAwareScheduler
from .benchmark.resource_monitor import SystemResourceMonitor
from .router import ChineseFastRouter
from .safety.away_report import AwaySecurityReporter
from .scenarios.state import ScenarioStateManager
from .tasks.delayed import DelayedTaskManager
from .tasks.parser import ChineseDelayedCommandParser
from .understanding.models import FastIntent, FastRouteDecision, FastRouteStatus
from .understanding.reference_resolver import ReferencePlanResolver


@dataclass(frozen=True, slots=True)
class FastPathResponse:
    """Conversation-facing result that distinguishes safe handling from model fallback."""

    handled: bool
    speech: str | None = None
    decision: FastRouteDecision | None = None
    completed_plan: TaskPlan | None = None
    planning_text: str | None = None


class EdgeHomeCoordinator:
    """Execute a ready fixed plan, or deliberately leave non-fast requests to Home LLM."""

    def __init__(
        self,
        hass: HomeAssistant,
        resolver: EntityNameResolver | None = None,
        executor: FixedPlanExecutor | None = None,
        knowledge_index: LocalKnowledgeIndex | None = None,
        manual_registry: DeviceManualRegistry | None = None,
        energy_statistics: HomeAssistantEnergyStatistics | None = None,
        clock: Callable[[], datetime] | None = None,
        resource_monitor: ResourceMonitor | None = None,
        resource_scheduler: ResourceAwareScheduler | None = None,
        input_guard: InputLengthGuard | None = None,
    ) -> None:
        self._resolver = resolver or EntityNameResolver(hass, registry=build_demo_entity_registry())
        self._router = ChineseFastRouter(self._resolver)
        self._executor = executor or FixedPlanExecutor(hass, resolver=self._resolver)
        self._environment = EnvironmentContextBuilder(self._resolver)
        self._environment_guard = EnvironmentPlanGuard(self._executor.validator, self._resolver)
        self._failure_replanner = FailureReplanner()
        self._resource_monitor = resource_monitor or ResourceMonitor()
        self._resource_scheduler = resource_scheduler or ResourceAwareScheduler()
        self._system_resource_monitor = SystemResourceMonitor()
        self._input_guard = input_guard or InputLengthGuard()
        self._away_reporter = AwaySecurityReporter()
        self._scenarios = ScenarioStateManager(hass, self._resolver)
        self._dialogue_store = DialogueStateStore(hass)
        self._reference_resolver = ReferencePlanResolver(self._resolver)
        self._delayed_tasks = DelayedTaskManager(hass, self._executor)
        self._delayed_parser = ChineseDelayedCommandParser()
        active_knowledge_index = knowledge_index or LocalKnowledgeIndex(hass)
        self._information = ReadOnlyInformationHandler(
            active_knowledge_index,
            manual_registry or DeviceManualRegistry(hass),
            energy_statistics or HomeAssistantEnergyStatistics(hass),
            clock,
            # 注入测试索引时保留第 8 阶段行为；生产默认载入受控本地资料。
            knowledge_service=KnowledgeService(hass, active_knowledge_index, resolver=self._resolver) if knowledge_index is None else None,
        )
        self._energy_conversation = EnergyConversationHandler(self._dialogue_store, EnergyService(hass))

    async def async_handle(self, text: str) -> FastPathResponse:
        """Handle ready plans; never retry a failed direct action through the legacy model path."""
        if not self._input_guard.accepts(text):
            return FastPathResponse(handled=True, speech="输入为空或超过 EdgeHome 允许长度，已拒绝处理。")
        decision = self._router.route(text)
        if decision.status == FastRouteStatus.FALLBACK:
            return FastPathResponse(handled=False, decision=decision)
        if decision.status == FastRouteStatus.CLARIFICATION:
            return FastPathResponse(handled=True, speech=self._clarification(decision), decision=decision)

        assert decision.plan is not None
        protected = self._executor.validator.safety_guard.protected_shutdowns(decision.plan)
        if protected:
            return FastPathResponse(
                handled=True,
                speech="受保护设备不能在无会话确认时关闭，请通过对话入口进行二次确认。",
                decision=decision,
            )
        report = await self._executor.async_execute(decision.plan)
        if report.status == TaskStatus.SUCCESS:
            return FastPathResponse(
                handled=True,
                speech=self._success_speech(decision, report.step_results[0]),
                decision=decision,
                completed_plan=decision.plan,
            )
        return FastPathResponse(
            handled=True,
            speech=self._failure_speech(decision, report.step_results),
            decision=decision,
        )

    async def async_handle_with_dialogue(self, text: str, conversation_id: str) -> FastPathResponse:
        """Apply clarification, references, and delayed commands before normal fast-path routing."""
        if not self._input_guard.accepts(text):
            return FastPathResponse(handled=True, speech="输入为空或超过 EdgeHome 允许长度，已拒绝处理。")
        if text.strip() == "确认关闭受保护设备":
            pending = await self._dialogue_store.async_consume_protected_confirmation(conversation_id)
            if pending is None:
                return FastPathResponse(handled=True, speech="当前没有待确认的受保护设备关闭操作。")
            report = await self._executor.async_execute(pending.plan, frozenset(pending.entity_ids))
            if report.status == TaskStatus.SUCCESS:
                await self._dialogue_store.async_remember_plan(conversation_id, pending.plan)
                return FastPathResponse(handled=True, speech="已按二次确认完成受保护设备操作。", completed_plan=pending.plan)
            return FastPathResponse(handled=True, speech="受保护设备操作未完成，已停止后续动作。")

        resumed_text = await self._async_resume_clarification(text, conversation_id)
        energy_conversation = await self._energy_conversation.async_handle(resumed_text, conversation_id)
        if energy_conversation is not None:
            return FastPathResponse(handled=True, speech=energy_conversation)
        information = await self._information.async_handle(resumed_text)
        if information is not None:
            return FastPathResponse(handled=True, speech=information)

        state = await self._dialogue_store.async_get(conversation_id)
        if state:
            reference_plan = self._reference_resolver.resolve(resumed_text, state)
            if reference_plan:
                report = await self._executor.async_execute(reference_plan)
                if report.status == TaskStatus.SUCCESS:
                    await self._dialogue_store.async_remember_plan(conversation_id, reference_plan)
                    return FastPathResponse(handled=True, speech="已按上一条设置完成操作。", completed_plan=reference_plan)
                failure = report.step_results[-1] if report.step_results else None
                reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
                return FastPathResponse(handled=True, speech=f"引用操作未完成：{reason}。")

        environment = await self._async_handle_environment(resumed_text, conversation_id)
        if environment is not None:
            return environment

        delayed = await self._async_handle_delayed(resumed_text, conversation_id)
        if delayed is not None:
            return delayed

        decision = self._router.route(resumed_text)
        if decision.status == FastRouteStatus.READY and decision.plan is not None:
            protected = self._executor.validator.safety_guard.protected_shutdowns(decision.plan)
            if protected:
                await self._dialogue_store.async_set_protected_confirmation(conversation_id, decision.plan, protected)
                return FastPathResponse(
                    handled=True,
                    speech="检测到受保护设备。请回复“确认关闭受保护设备”进行二次确认。",
                    decision=decision,
                )
        result = await self.async_handle(resumed_text)
        if result.completed_plan:
            await self._dialogue_store.async_remember_plan(conversation_id, result.completed_plan)
        elif result.handled and result.decision and result.decision.status == FastRouteStatus.CLARIFICATION:
            await self._dialogue_store.async_set_clarification(conversation_id, resumed_text, result.speech or "请补充必要信息")
        # A clarification may turn a short follow-up into a complete scene goal.
        # Preserve that merged text for the caller's complex-planning handoff.
        return FastPathResponse(
            handled=result.handled,
            speech=result.speech,
            decision=result.decision,
            completed_plan=result.completed_plan,
            planning_text=resumed_text,
        )

    async def async_remember_completed(self, conversation_id: str, plan: TaskPlan | None) -> None:
        """Persist a successful complex plan after the caller has chosen to expose it to a conversation."""
        if plan:
            await self._dialogue_store.async_remember_plan(conversation_id, plan)

    async def async_shutdown(self) -> None:
        """Release delayed task listeners when this coordinator is removed."""
        await self._delayed_tasks.async_shutdown()

    async def async_handle_complex(
        self,
        text: str,
        conversation_id: str,
        agent_id: str,
        runtime_options: dict[str, object],
        planner: LLMTaskPlanner,
    ) -> FastPathResponse:
        """Plan and run a complex request only after the model result passes all fixed-plan gates."""
        if not planner.is_candidate(text):
            return FastPathResponse(handled=False)
        scheduling = self._resource_scheduler.decide(
            task_is_complex=True,
            model_available=True,
            safety_critical=False,
            resources=self._system_resource_monitor.snapshot(),
        )
        request = PlanningRequest(goal=text, agent_id=agent_id)
        if scheduling.decision == "template_fallback":
            planning = await planner.async_template_fallback(request, scheduling.reason)
            if planning.status != PlanningStatus.ACCEPTED or planning.plan is None:
                return FastPathResponse(
                    handled=True,
                    speech="端侧资源或模型不可用，且当前请求没有可用的安全场景模板。",
                )
            return await self._async_execute_complex_plan(text, conversation_id, agent_id, runtime_options, planner, planning)
        queue_wait_ms = await self._resource_scheduler.async_acquire_llm()
        lease = await self._resource_monitor.async_try_acquire("complex_planning")
        if lease is None:
            self._resource_scheduler.release_llm()
            return FastPathResponse(handled=True, speech="当前端侧资源繁忙，已保留快速控制，请稍后重试复杂任务。")
        try:
            planning = await planner.async_plan(request, {**runtime_options, "edgehome_queue_wait_ms": queue_wait_ms})
        finally:
            await self._resource_monitor.async_release(lease)
            self._resource_scheduler.release_llm()
        return await self._async_execute_complex_plan(text, conversation_id, agent_id, runtime_options, planner, planning)

    async def _async_execute_complex_plan(
        self,
        text: str,
        conversation_id: str,
        agent_id: str,
        runtime_options: dict[str, object],
        planner: LLMTaskPlanner,
        planning: PlanningResult,
    ) -> FastPathResponse:
        """执行已通过模型或模板入口产生的同一份受限计划。"""
        if planning.status != PlanningStatus.ACCEPTED or planning.plan is None:
            return FastPathResponse(handled=False)
        if planning.plan.need_clarification:
            await self._dialogue_store.async_set_clarification(
                conversation_id,
                text,
                planning.plan.clarification_question or "请补充完成该场景所需的信息。",
            )
            return FastPathResponse(handled=True, speech=planning.plan.clarification_question)

        revalidation = self._environment_guard.revalidate(
            planning.plan,
            self._environment_guard.from_context(planning.plan, planning.context_entities),
        )
        if not revalidation.accepted:
            return FastPathResponse(handled=True, speech="环境状态已变化，已取消过时计划，请重新确认需求。")

        scenario_name = planning.scene if planning.scene == "movie" else None
        protected = self._executor.validator.safety_guard.protected_shutdowns(planning.plan)
        if protected:
            await self._dialogue_store.async_set_protected_confirmation(conversation_id, planning.plan, protected)
            return FastPathResponse(
                handled=True,
                speech="检测到受保护设备。请回复“确认关闭受保护设备”进行二次确认。",
            )
        if scenario_name:
            await self._scenarios.async_capture(conversation_id, scenario_name, planning.plan)

        report = await self._executor.async_execute(planning.plan)
        if report.status == TaskStatus.SUCCESS:
            if planning.delayed_plan is not None and planning.delay_seconds is not None:
                try:
                    task = await self._delayed_tasks.async_create(
                        conversation_id,
                        planning.delayed_plan,
                        datetime.now(timezone.utc) + timedelta(seconds=planning.delay_seconds),
                    )
                except ValueError:
                    return FastPathResponse(
                        handled=True,
                        speech="即时操作已完成，但延时任务未能创建，请重新确认延时时间。",
                        completed_plan=planning.plan,
                    )
                return FastPathResponse(
                    handled=True,
                    speech=f"已完成任务：{planning.plan.goal}。已安排延时任务 {task.id[:8]}。",
                    completed_plan=planning.plan,
                )
            return FastPathResponse(handled=True, speech=f"已完成任务：{planning.plan.goal}。", completed_plan=planning.plan)
        replan = self._failure_replanner.decide(text, planning.plan, report)
        if replan.allowed and replan.prompt_goal is not None:
            replacement = await planner.async_plan(
                PlanningRequest(goal=replan.prompt_goal, agent_id=agent_id),
                runtime_options,
            )
            if replacement.status == PlanningStatus.ACCEPTED and replacement.plan is not None:
                replacement_guard = self._environment_guard.revalidate(
                    replacement.plan,
                    self._environment_guard.from_context(replacement.plan, replacement.context_entities),
                )
                replacement_protected = self._executor.validator.safety_guard.protected_shutdowns(replacement.plan)
                if replacement_guard.accepted and not replacement_protected:
                    replacement_report = await self._executor.async_execute(replacement.plan)
                    if replacement_report.status == TaskStatus.SUCCESS:
                        return FastPathResponse(
                            handled=True,
                            speech=f"已通过一次受限重规划完成任务：{replacement.plan.goal}。",
                            completed_plan=replacement.plan,
                        )
                    report = replacement_report
        if scenario_name:
            await self._scenarios.async_clear(conversation_id, scenario_name)
        failure = report.step_results[-1] if report.step_results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return FastPathResponse(handled=True, speech=f"任务未完成：{reason}。")

    def build_planner(self, client: LocalLLMClient) -> LLMTaskPlanner:
        """Build the model adapter from the same resolver, registry, and validator as execution."""
        return LLMTaskPlanner(
            client,
            self._environment,
            StructuredPlanParser(self._executor.validator),
            self._executor.tools,
        )

    async def _async_resume_clarification(self, text: str, conversation_id: str) -> str:
        pending = await self._dialogue_store.async_consume_clarification(conversation_id)
        if pending is None:
            return text
        return f"{pending.original_goal} {text}"

    async def _async_handle_environment(self, text: str, conversation_id: str) -> FastPathResponse | None:
        if text in {"离家安全检查", "离家检查"}:
            security_report = self._away_reporter.build(self._environment.build(text))
            people = "、".join(entity.friendly_name or entity.entity_id for entity in security_report.people_home) or "无"
            opened = "、".join(entity.friendly_name or entity.entity_id for entity in security_report.open_entries) or "无"
            unavailable = "、".join(entity.friendly_name or entity.entity_id for entity in security_report.unavailable_entries) or "无"
            if security_report.safe_to_leave:
                return FastPathResponse(handled=True, speech="离家检查完成：未发现人员、门窗或传感器异常。")
            return FastPathResponse(handled=True, speech=f"离家检查：在家人员 {people}；打开门窗 {opened}；不可用门窗 {unavailable}。")

        if text not in {"恢复观影前状态", "恢复观影场景"}:
            return None
        plan = await self._scenarios.async_restore_plan(conversation_id, "movie")
        if plan is None:
            return FastPathResponse(handled=True, speech="没有可恢复的观影场景状态。")
        report = await self._executor.async_execute(plan)
        if report.status == TaskStatus.SUCCESS:
            await self._scenarios.async_clear(conversation_id, "movie")
            return FastPathResponse(handled=True, speech="已恢复观影前状态。", completed_plan=plan)
        failure = report.step_results[-1] if report.step_results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return FastPathResponse(handled=True, speech=f"恢复观影前状态失败：{reason}。")

    async def _async_handle_delayed(self, text: str, conversation_id: str) -> FastPathResponse | None:
        if text in {"查询延时任务", "查看延时任务", "查询定时任务", "查看定时任务"}:
            tasks = await self._delayed_tasks.async_list(conversation_id)
            pending = [task for task in tasks if task.status.value == "pending"]
            if not pending:
                return FastPathResponse(handled=True, speech="当前没有待执行的延时任务。")
            descriptions = [f"{task.id[:8]}（{task.execute_at.astimezone().strftime('%H:%M')}）" for task in pending[:5]]
            return FastPathResponse(handled=True, speech=f"待执行任务：{'、'.join(descriptions)}。")

        cancellation = re.fullmatch(r"取消(?:延时|定时)任务\s+([a-zA-Z0-9]+)", text.strip())
        if cancellation:
            matches = await self._delayed_tasks.async_list(conversation_id)
            task = next((item for item in matches if item.id.startswith(cancellation.group(1))), None)
            cancelled = await self._delayed_tasks.async_cancel(conversation_id, task.id) if task else None
            return FastPathResponse(
                handled=True,
                speech="已取消延时任务。" if cancelled else "没有找到可取消的延时任务。",
            )

        request = self._delayed_parser.parse(text)
        if request is None:
            return None
        decision = self._router.route(request.command)
        if decision.status != FastRouteStatus.READY or decision.plan is None:
            if decision.status == FastRouteStatus.CLARIFICATION:
                await self._dialogue_store.async_set_clarification(conversation_id, request.command, "请补充延时任务中的设备或参数")
                return FastPathResponse(handled=True, speech="请补充延时任务中的设备或参数。")
            return None
        task = await self._delayed_tasks.async_create(
            conversation_id,
            decision.plan,
            datetime.now(timezone.utc) + timedelta(seconds=request.delay_seconds),
        )
        return FastPathResponse(handled=True, speech=f"已安排延时任务 {task.id[:8]}，将在指定时间执行。")

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
            FastIntent.ACTIVATE_SECURITY: "已开启",
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
