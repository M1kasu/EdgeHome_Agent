"""Coordinate the narrow fast path without exposing arbitrary Home Assistant services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

from ..entity import LocalLLMClient
from .entity_registry import build_demo_entity_registry
from .entity_resolver import EntityNameResolver
from .execution.executor import FixedPlanExecutor
from .execution.environment_guard import EnvironmentPlanGuard
from .execution.replanner import FailureReplanner
from .context.environment import EnvironmentContextBuilder
from .context.store import DialogueStateStore
from .context.resolver import ContextResolver
from .context.service import ConversationContextService
from .energy.statistics import HomeAssistantEnergyStatistics
from .energy.conversation import EnergyConversationHandler
from .energy.service import EnergyService
from .understanding.input_guard import InputLengthGuard
from .models import EntitySnapshot, StepExecutionResult, StepStatus, TaskExecutionReport, TaskPlan, TaskStatus
from .information import ReadOnlyInformationHandler
from .knowledge.associations import DeviceManualRegistry
from .knowledge.indexer import LocalKnowledgeIndex
from .knowledge.service import KnowledgeService
from .planner.demo_scenes import DemoScenePlanFactory
from .planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningResult, PlanningStatus
from .planner.parser import StructuredPlanParser
from .planner.templates import SceneName, SceneTemplateLibrary
from .resource.monitor import ResourceMonitor
from .resource.scheduler import ResourceAwareScheduler
from .resource.system_monitor import SystemResourceMonitor
from .router import ChineseFastRouter
from .safety.away_report import AwaySecurityReporter
from .scenarios.state import ScenarioStateManager
from .tasks.delayed import DelayedTaskManager
from .tasks.parser import ChineseDelayedCommandParser
from .understanding.models import FastIntent, FastRouteDecision, FastRouteStatus
from .understanding.reference_resolver import ReferencePlanResolver

_LOGGER = logging.getLogger(__name__)


# Bulk deletion and blanket-power-off wording must never reach a generative
# fallback.  This is a semantic safety boundary, not a list of acceptance
# sentences: it covers destructive API-like tokens and broad shutdown grammar.
_BULK_DESTRUCTIVE_REQUEST = re.compile(
    r"(?:"
    r"delete[_\s-]*(?:all|every)[_\s-]*devices|"
    r"(?:全部|所有|全屋).{0,12}(?:断电|关(?:闭|掉|上)?)|"
    r"(?:断电|关(?:闭|掉|上)?).{0,12}(?:全部|所有|全屋)(?:设备|电器|家电)|"
    r"(?:忽略|跳过).{0,16}(?:规则|检查).{0,16}(?:断电|关(?:闭|掉|上)?)"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FastPathResponse:
    """Conversation-facing result that distinguishes safe handling from model fallback."""

    handled: bool
    speech: str | None = None
    decision: FastRouteDecision | None = None
    completed_plan: TaskPlan | None = None
    planned_plan: TaskPlan | None = None
    execution_report: TaskExecutionReport | None = None
    trace_route: str = "fast_path"
    trace_status: TaskStatus | None = None
    planning_text: str | None = None
    planning_source: str | None = None
    planning_attempts: int = 0
    planning_repair_reason: str | None = None


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
        self._hass = hass
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
        self._context_service = ConversationContextService(self._dialogue_store)
        self._context_resolver = ContextResolver()
        self._reference_resolver = ReferencePlanResolver(self._resolver)
        self._delayed_tasks = DelayedTaskManager(hass, self._executor)
        self._delayed_parser = ChineseDelayedCommandParser()
        self._scene_templates = SceneTemplateLibrary()
        self._demo_scene_factory = DemoScenePlanFactory()
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
                planned_plan=decision.plan,
                trace_route="protected_confirmation",
            )
        report = await self._executor.async_execute(decision.plan)
        if report.status == TaskStatus.SUCCESS:
            return FastPathResponse(
                handled=True,
                speech=self._success_speech(decision, report.step_results[0]),
                decision=decision,
                completed_plan=decision.plan,
                execution_report=report,
            )
        return FastPathResponse(
            handled=True,
            speech=self._failure_speech(decision, report.step_results),
            decision=decision,
            execution_report=report,
        )

    async def async_handle_with_dialogue(
        self,
        text: str,
        conversation_id: str,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> FastPathResponse:
        """Apply clarification, references, and delayed commands before normal fast-path routing."""
        # Omit absent identity fields so optional integration hooks keep their
        # two-argument compatibility, while authenticated calls remain scoped.
        context_identity = {
            key: value
            for key, value in {"user_id": user_id, "assistant_id": assistant_id}.items()
            if value is not None
        }
        if not self._input_guard.accepts(text):
            return FastPathResponse(handled=True, speech="输入为空或超过 EdgeHome 允许长度，已拒绝处理。")
        if _BULK_DESTRUCTIVE_REQUEST.search(text):
            return FastPathResponse(
                handled=True,
                speech=(
                    "已拒绝该批量断电请求：冰箱、路由器等受保护设备不能通过批量操作关闭；"
                    "未发送任何设备控制命令。"
                ),
                trace_route="bulk_destructive_rejected",
            )
        if text.strip() == "确认关闭受保护设备":
            pending = await self._dialogue_store.async_consume_protected_confirmation(conversation_id, **context_identity)
            if pending is None:
                return FastPathResponse(handled=True, speech="当前没有待确认的受保护设备关闭操作。")
            report = await self._executor.async_execute(pending.plan, frozenset(pending.entity_ids))
            if report.status == TaskStatus.SUCCESS:
                await self._context_service.async_record_success(
                    conversation_id,
                    pending.plan,
                    report,
                    **context_identity,
                )
                return FastPathResponse(
                    handled=True,
                    speech="已按二次确认完成受保护设备操作。",
                    completed_plan=pending.plan,
                    planned_plan=pending.plan,
                    execution_report=report,
                    trace_route="protected_confirmation",
                )
            await self._context_service.async_record_failure(
                conversation_id,
                pending.plan,
                report,
                **context_identity,
            )
            return FastPathResponse(
                handled=True,
                speech="受保护设备操作未完成，已停止后续动作。",
                planned_plan=pending.plan,
                execution_report=report,
                trace_route="protected_confirmation",
            )

        resumed = await self._async_resume_clarification(text, conversation_id, **context_identity)
        if isinstance(resumed, FastPathResponse):
            return resumed
        resumed_text = resumed
        scene_candidate = self._scene_templates.match(resumed_text)

        energy_conversation = await self._energy_conversation.async_handle(
            resumed_text,
            conversation_id,
            **context_identity,
        )
        if energy_conversation is not None:
            return FastPathResponse(handled=True, speech=energy_conversation, trace_route="energy_analysis")
        information = await self._information.async_handle(resumed_text)
        if information is not None:
            return FastPathResponse(handled=True, speech=information, trace_route="local_information")

        state = await self._dialogue_store.async_get(conversation_id, **context_identity)
        if state is None and scene_candidate is None and self._reference_resolver.requires_prior_context(resumed_text):
            question = "\u8bf7\u8bf4\u660e\u8981\u64cd\u4f5c\u54ea\u4e2a\u8bbe\u5907\u3002"
            await self._dialogue_store.async_set_clarification(
                conversation_id,
                resumed_text,
                question,
                intent="context_reference",
                missing_slots=["entity_id"],
                **context_identity,
            )
            return FastPathResponse(handled=True, speech=question, trace_route="clarification")
        if state:
            query_speech = self._reference_resolver.query_speech(resumed_text, state)
            if query_speech is not None:
                return FastPathResponse(handled=True, speech=query_speech, trace_route="dialogue_query")
            reference_plan = self._reference_resolver.resolve(resumed_text, state)
            if reference_plan:
                report = await self._executor.async_execute(reference_plan)
                if report.status == TaskStatus.SUCCESS:
                    await self._context_service.async_record_success(
                        conversation_id,
                        reference_plan,
                        report,
                        **context_identity,
                    )
                    return FastPathResponse(
                        handled=True,
                        speech="已按上一条设置完成操作。",
                        completed_plan=reference_plan,
                        planned_plan=reference_plan,
                        execution_report=report,
                        trace_route="dialogue_reference",
                    )
                failure = report.step_results[-1] if report.step_results else None
                await self._context_service.async_record_failure(
                    conversation_id,
                    reference_plan,
                    report,
                    **context_identity,
                )
                reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
                return FastPathResponse(
                    handled=True,
                    speech=f"引用操作未完成：{reason}。",
                    planned_plan=reference_plan,
                    execution_report=report,
                    trace_route="dialogue_reference",
                )

        environment = await self._async_handle_environment(resumed_text, conversation_id)
        if environment is not None:
            return environment

        delayed = await self._async_handle_delayed(resumed_text, conversation_id, **context_identity)
        if delayed is not None:
            return delayed

        # A named multi-device scene is never sent through the single-device
        # fast router.  Otherwise a word such as "窗帘" can make the router
        # request an arbitrary disambiguation before the model is allowed to
        # compile the complete goal, constraints, and execution graph.
        # Environment checks and deliberate scheduling still take precedence.
        if scene_candidate is not None:
            return FastPathResponse(
                handled=False,
                planning_text=resumed_text,
                trace_route="complex_scene_candidate",
            )

        decision = self._router.route(resumed_text)
        if decision.status == FastRouteStatus.READY and decision.plan is not None:
            protected = self._executor.validator.safety_guard.protected_shutdowns(decision.plan)
            if protected:
                await self._dialogue_store.async_set_protected_confirmation(
                    conversation_id,
                    decision.plan,
                    protected,
                    **context_identity,
                )
                return FastPathResponse(
                    handled=True,
                    speech="检测到受保护设备。请回复“确认关闭受保护设备”进行二次确认。",
                    decision=decision,
                    planned_plan=decision.plan,
                    trace_route="protected_confirmation",
                )
        result = await self.async_handle(resumed_text)
        if result.completed_plan and result.execution_report:
            await self._context_service.async_record_success(
                conversation_id,
                result.completed_plan,
                result.execution_report,
                **context_identity,
            )
        elif result.planned_plan and result.execution_report:
            await self._context_service.async_record_failure(
                conversation_id,
                result.planned_plan,
                result.execution_report,
                **context_identity,
            )
        if result.handled and result.decision and result.decision.status == FastRouteStatus.CLARIFICATION:
            await self._dialogue_store.async_set_clarification(
                conversation_id,
                resumed_text,
                result.speech or "Please provide the missing information.",
                intent=result.decision.intent.value,
                missing_slots=["value"],
                **context_identity,
            )
        # A clarification may turn a short follow-up into a complete scene goal.
        # Preserve that merged text for the caller's complex-planning handoff.
        return FastPathResponse(
            handled=result.handled,
            speech=result.speech,
            decision=result.decision,
            completed_plan=result.completed_plan,
            planned_plan=result.planned_plan,
            execution_report=result.execution_report,
            trace_route=result.trace_route,
            planning_text=resumed_text,
        )

    async def async_remember_completed(
        self,
        conversation_id: str,
        plan: TaskPlan | None,
        report: TaskExecutionReport | None,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> None:
        """Persist a complex outcome only when a real executor report is available."""
        if plan is None or report is None:
            return
        identity = {"user_id": user_id, "assistant_id": assistant_id}
        if report.status == TaskStatus.SUCCESS:
            await self._context_service.async_record_success(conversation_id, plan, report, **identity)
        else:
            await self._context_service.async_record_failure(conversation_id, plan, report, **identity)

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
        *,
        user_id: str | None = None,
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
            return await self._async_execute_complex_plan(
                text,
                conversation_id,
                agent_id,
                runtime_options,
                planner,
                planning,
                user_id=user_id,
            )
        queue_wait_ms = await self._resource_scheduler.async_acquire_llm()
        lease = await self._resource_monitor.async_try_acquire("complex_planning")
        if lease is None:
            self._resource_scheduler.release_llm()
            return FastPathResponse(handled=True, speech="当前端侧资源繁忙，已保留快速控制，请稍后重试复杂任务。")
        try:
            planning = await planner.async_plan(request, {**runtime_options, "edgehome_queue_wait_ms": queue_wait_ms})
        except Exception:
            _LOGGER.exception("EdgeHome complex planning service failed before a plan was accepted")
            return FastPathResponse(
                handled=True,
                speech="本轮未能完成：本地规划服务不可用或响应超时；未生成执行计划，也未发送任何设备控制命令。",
                trace_route="complex_planning_unavailable",
                trace_status=TaskStatus.FAILED,
            )
        finally:
            await self._resource_monitor.async_release(lease)
            self._resource_scheduler.release_llm()
        return await self._async_execute_complex_plan(
            text,
            conversation_id,
            agent_id,
            runtime_options,
            planner,
            planning,
            user_id=user_id,
        )

    async def _async_execute_complex_plan(
        self,
        text: str,
        conversation_id: str,
        agent_id: str,
        runtime_options: dict[str, object],
        planner: LLMTaskPlanner,
        planning: PlanningResult,
        *,
        user_id: str | None = None,
    ) -> FastPathResponse:
        """执行已通过模型或模板入口产生的同一份受限计划。"""
        planning_trace = {
            "planning_source": planning.source.value if planning.source is not None else None,
            "planning_attempts": planning.model_attempts,
            "planning_repair_reason": planning.repair_reason,
        }
        if planning.status != PlanningStatus.ACCEPTED or planning.plan is None:
            return FastPathResponse(
                handled=True,
                speech="任务规划未通过安全校验，未执行任何设备。",
                trace_route="complex_planning",
                **planning_trace,
            )
        if planning.plan.need_clarification:
            await self._dialogue_store.async_set_clarification(
                conversation_id,
                text,
                planning.plan.clarification_question or "请补充完成该场景所需的信息。",
                user_id=user_id,
                assistant_id=agent_id,
            )
            return FastPathResponse(handled=True, speech=planning.plan.clarification_question, **planning_trace)

        revalidation = self._environment_guard.revalidate(
            planning.plan,
            self._environment_guard.from_context(planning.plan, planning.context_entities),
        )
        if not revalidation.accepted:
            return FastPathResponse(handled=True, speech="环境状态已变化，已取消过时计划，请重新确认需求。", **planning_trace)

        scenario_name = planning.scene if planning.scene == "movie" else None
        protected = self._executor.validator.safety_guard.protected_shutdowns(planning.plan)
        if protected:
            await self._dialogue_store.async_set_protected_confirmation(
                conversation_id,
                planning.plan,
                protected,
                user_id=user_id,
                assistant_id=agent_id,
            )
            return FastPathResponse(
                handled=True,
                speech="检测到受保护设备。请回复“确认关闭受保护设备”进行二次确认。",
                planned_plan=planning.plan,
                trace_route="protected_confirmation",
                **planning_trace,
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
                        planned_plan=planning.plan,
                        execution_report=report,
                        trace_route="complex_planning",
                        **planning_trace,
                    )
                return FastPathResponse(
                    handled=True,
                    speech=f"已完成任务：{planning.plan.goal}。已安排延时任务 {task.id[:8]}。",
                    completed_plan=planning.plan,
                    planned_plan=planning.plan,
                    execution_report=report,
                    trace_route="complex_planning",
                    **planning_trace,
                )
            return FastPathResponse(
                handled=True,
                speech=f"已完成任务：{planning.plan.goal}。",
                completed_plan=planning.plan,
                planned_plan=planning.plan,
                execution_report=report,
                trace_route="complex_planning",
                **planning_trace,
            )
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
                            planned_plan=replacement.plan,
                            execution_report=replacement_report,
                            trace_route="complex_replanning",
                            planning_source=replacement.source.value if replacement.source is not None else None,
                            planning_attempts=replacement.model_attempts,
                            planning_repair_reason=replacement.repair_reason,
                        )
                    report = replacement_report
        if scenario_name:
            await self._scenarios.async_clear(conversation_id, scenario_name)
        failure = report.step_results[-1] if report.step_results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return FastPathResponse(
            handled=True,
            speech=f"任务未完成：{reason}。",
            planned_plan=planning.plan,
            execution_report=report,
            trace_route="complex_planning",
            **planning_trace,
        )

    def record_trace(self, result: FastPathResponse) -> str:
        """Store a compact, non-sensitive execution trace and return its Assist card text."""
        plan = result.completed_plan or result.planned_plan
        if plan is None and result.decision is not None:
            plan = result.decision.plan

        report = result.execution_report
        decision = None
        if result.decision is not None:
            decision = f"{result.decision.status.value}：{result.decision.reason}"

        plan_steps = []
        if plan is not None:
            plan_steps = [
                {
                    "step_id": step.id,
                    "tool": step.tool,
                    "entity_id": str(step.arguments.get("entity_id", "")),
                    "arguments": step.arguments,
                    "depends_on": list(step.depends_on),
                    "reason": step.reason,
                    "expected_outcome": step.expected_outcome,
                    "risk_level": step.risk_level.value,
                    "evidence_required": list(step.evidence_required),
                }
                for step in plan.steps
            ]
        execution_steps = []
        if report is not None:
            execution_steps = [self._trace_step_result(step) for step in report.step_results]

        state = report.status.value if report is not None else (result.trace_status.value if result.trace_status else "handled")
        attributes: dict[str, Any] = {
            "route": result.trace_route,
            "route_label": self._trace_route_label(result.trace_route),
            "decision": decision,
            "planning_source": result.planning_source,
            "planning_source_label": self._planning_source_label(result.planning_source),
            "planning_attempts": result.planning_attempts,
            "planning_repair_reason": result.planning_repair_reason,
            "goal": plan.goal if plan is not None else None,
            "planning_rationale": plan.rationale if plan is not None else None,
            "planning_constraints": [constraint.model_dump(mode="json") for constraint in plan.constraints] if plan is not None else [],
            "goal_spec": plan.goal_spec.model_dump(mode="json") if plan is not None and plan.goal_spec is not None else None,
            "risk_assessment": plan.risk_assessment.model_dump(mode="json") if plan is not None and plan.risk_assessment is not None else None,
            "plan_score": plan.plan_score.model_dump(mode="json") if plan is not None and plan.plan_score is not None else None,
            "plan_steps": plan_steps,
            "step_results": execution_steps,
            "speech": result.speech,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._hass.states.async_set("sensor.edgehome_last_execution", state, attributes)

        lines = [f"处理路径：{attributes['route_label']}"]
        if result.planning_source:
            lines.append(f"规划来源：{attributes['planning_source_label']}（模型尝试 {result.planning_attempts} 次）")
        if result.planning_repair_reason:
            lines.append(f"规划修复原因：{result.planning_repair_reason}")
        if decision:
            lines.append(f"决策：{decision}")
        if plan is not None:
            lines.append(f"固定计划：{plan.goal}")
            if plan.rationale:
                lines.append(f"规划说明：{plan.rationale}")
            if plan.constraints:
                lines.append("显式约束：" + "；".join(constraint.statement for constraint in plan.constraints))
            if plan.risk_assessment:
                lines.append("风险评估：" + plan.risk_assessment.level.value + "；" + "；".join(plan.risk_assessment.reasons))
            if plan.plan_score:
                lines.append(f"计划评分：{plan.plan_score.total}/100")
            lines.append("计划步骤：")
            lines.extend(self._trace_plan_step(step) for step in plan_steps)
        if execution_steps:
            lines.append("执行结果：")
            lines.extend(self._trace_execution_step(step) for step in execution_steps)
        else:
            lines.append("执行结果：本次未触发设备控制。")
        return "\n".join(lines)

    @staticmethod
    def _trace_step_result(step: StepExecutionResult) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "tool": step.tool,
            "status": step.status.value,
            "attempts": step.attempts,
            "entity_id": step.state_after.entity_id if step.state_after else (
                step.state_before.entity_id if step.state_before else None
            ),
            "state_before": step.state_before.state if step.state_before else None,
            "state_after": step.state_after.state if step.state_after else None,
            "verified": step.verification.passed if step.verification else None,
            "error": step.error_message,
            "duration_ms": max(0, int((step.completed_at - step.started_at).total_seconds() * 1000)),
        }

    @staticmethod
    def _trace_route_label(route: str) -> str:
        return {
            "fast_path": "快速规则",
            "complex_planning": "复杂任务规划",
            "complex_planning_unavailable": "本地规划服务异常",
            "complex_replanning": "受限重规划",
            "protected_confirmation": "受保护设备二次确认",
            "dialogue_reference": "多轮引用操作",
            "dialogue_query": "多轮状态查询",
            "mqtt_movie_scene": "动态 MQTT 观影场景",
            "deterministic_leaving_scene": "确定性离家保护",
            "energy_analysis": "本地能耗分析",
            "local_information": "本地资料检索",
        }.get(route, "EdgeHome 本地处理")

    @staticmethod
    def _planning_source_label(source: str | None) -> str:
        return {
            "model": "本地模型自主规划",
            "model_repaired": "本地模型修复后规划",
            "model_sanitized": "本地模型规划（安全收缩）",
            "goal_policy": "目标驱动安全策略",
            "demo_scene_fallback": "安全模板降级",
        }.get(source, "未使用模型规划")

    @staticmethod
    def _trace_plan_step(step: dict[str, Any]) -> str:
        entity_id = step["entity_id"] or "无实体参数"
        dependencies = step.get("depends_on") or []
        dependency_text = f"；依赖 {','.join(dependencies)}" if dependencies else ""
        return f"- {step['step_id']}：{step['tool']}（{entity_id}{dependency_text}）"

    @staticmethod
    def _trace_execution_step(step: dict[str, Any]) -> str:
        before = step["state_before"] or "未知"
        after = step["state_after"] or "未知"
        verification = "；校验通过" if step["verified"] is True else "；校验失败" if step["verified"] is False else ""
        error = f"；错误：{step['error']}" if step["error"] else ""
        return f"- {step['step_id']}：{step['status']}，状态 {before} → {after}{verification}{error}"

    def build_planner(self, client: LocalLLMClient) -> LLMTaskPlanner:
        """Build the model adapter from the same resolver, registry, and validator as execution."""
        return LLMTaskPlanner(
            client,
            self._environment,
            StructuredPlanParser(self._executor.validator),
            self._executor.tools,
        )

    async def _async_resume_clarification(
        self,
        text: str,
        conversation_id: str,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> str | FastPathResponse:
        pending = await self._dialogue_store.async_consume_clarification(
            conversation_id,
            user_id=user_id,
            assistant_id=assistant_id,
        )
        if pending is None:
            return text
        resolution = self._context_resolver.resolve_clarification(pending, text)
        if resolution.repeat_question is not None:
            await self._dialogue_store.async_set_clarification(
                conversation_id,
                pending.original_text,
                resolution.repeat_question,
                intent=pending.intent,
                candidate_entity_id=pending.candidate_entity_id,
                missing_slots=pending.missing_slots,
                user_id=user_id,
                assistant_id=assistant_id,
            )
            return FastPathResponse(handled=True, speech=resolution.repeat_question, trace_route="clarification")
        return resolution.text or text

    async def _async_handle_environment(self, text: str, conversation_id: str) -> FastPathResponse | None:
        if text in {"离家安全检查", "离家检查"}:
            security_report = self._away_reporter.build(self._environment.build(text))
            people = "、".join(entity.friendly_name or entity.entity_id for entity in security_report.people_home) or "无"
            opened = "、".join(entity.friendly_name or entity.entity_id for entity in security_report.open_entries) or "无"
            unavailable = "、".join(entity.friendly_name or entity.entity_id for entity in security_report.unavailable_entries) or "无"
            if security_report.safe_to_leave:
                return FastPathResponse(handled=True, speech="离家检查完成：未发现人员、门窗或传感器异常。")
            return FastPathResponse(handled=True, speech=f"离家检查：在家人员 {people}；打开门窗 {opened}；不可用门窗 {unavailable}。")

        if text.strip() not in {"恢复观影前状态", "恢复观影场景", "恢复观影"}:
            return None
        plan = await self._scenarios.async_restore_plan(conversation_id, "movie")
        if plan is None:
            return FastPathResponse(handled=True, speech="没有可恢复的观影场景状态。", trace_route="scenario_restore")
        report = await self._executor.async_execute(plan)
        if report.status == TaskStatus.SUCCESS:
            await self._scenarios.async_clear(conversation_id, "movie")
            return FastPathResponse(
                handled=True,
                speech="已恢复观影前状态。",
                completed_plan=plan,
                planned_plan=plan,
                execution_report=report,
                trace_route="scenario_restore",
            )
        failure = report.step_results[-1] if report.step_results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return FastPathResponse(
            handled=True,
            speech=f"恢复观影前状态失败：{reason}。恢复点已保留，可在设备恢复后重试。",
            planned_plan=plan,
            execution_report=report,
            trace_route="scenario_restore",
        )

    async def _async_handle_mqtt_movie_scene(
        self,
        text: str,
        conversation_id: str,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> FastPathResponse | None:
        """Run M5 through the four independently verified MQTT device boundaries only."""
        scene = self._scene_templates.match(text)
        if scene is None or scene.name != SceneName.MOVIE:
            return None
        snapshots = [
            self._resolver.snapshot(entity_id)
            for entity_id in self._resolver.scene_entities(SceneName.MOVIE.value, protocol="mqtt")
        ]
        draft = self._demo_scene_factory.build(text, SceneName.MOVIE, snapshots)
        if draft is None:
            return FastPathResponse(handled=True, speech="观影场景未找到可验证的 MQTT 设备。", trace_route="mqtt_movie_scene")
        plan = draft.plan
        if plan.need_clarification:
            return FastPathResponse(
                handled=True,
                speech=plan.clarification_question,
                planned_plan=plan,
                trace_route="mqtt_movie_scene",
            )
        revalidation = self._environment_guard.revalidate(plan, self._environment_guard.capture(plan))
        if not revalidation.accepted:
            return FastPathResponse(
                handled=True,
                speech="观影场景的设备状态已变化或不可用，未执行任何操作。",
                planned_plan=plan,
                trace_route="mqtt_movie_scene",
            )
        await self._scenarios.async_capture(conversation_id, "movie", plan)
        report = await self._executor.async_execute(plan)
        if report.status == TaskStatus.SUCCESS:
            await self._context_service.async_record_success(
                conversation_id,
                plan,
                report,
                user_id=user_id,
                assistant_id=assistant_id,
            )
            return FastPathResponse(
                handled=True,
                speech="观影场景已完成，所有需要调整的 MQTT 设备均已回读确认。",
                completed_plan=plan,
                planned_plan=plan,
                execution_report=report,
                trace_route="mqtt_movie_scene",
            )
        await self._context_service.async_record_failure(
            conversation_id,
            plan,
            report,
            user_id=user_id,
            assistant_id=assistant_id,
        )
        await self._scenarios.async_clear(conversation_id, "movie")
        failure = next((step for step in report.step_results if step.status == StepStatus.FAILED), None)
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        prefix = "观影场景部分完成" if report.status == TaskStatus.PARTIAL_SUCCESS else "观影场景未完成"
        return FastPathResponse(
            handled=True,
            speech=f"{prefix}：{reason}。",
            planned_plan=plan,
            execution_report=report,
            trace_route="mqtt_movie_scene",
        )

    async def _async_handle_deterministic_leaving_scene(
        self,
        text: str,
        conversation_id: str,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> FastPathResponse | None:
        """Prefer the conservative local leaving template over a single-device keyword match."""
        scene = self._scene_templates.match(text)
        if scene is None or scene.name != SceneName.LEAVING:
            return None

        self._environment.invalidate()
        safety_report = self._away_reporter.build(self._environment.build(text, scene=SceneName.LEAVING.value))
        if not safety_report.safe_to_leave:
            people = "、".join(entity.friendly_name or entity.entity_id for entity in safety_report.people_home) or "无"
            opened = "、".join(entity.friendly_name or entity.entity_id for entity in safety_report.open_entries) or "无"
            unavailable = "、".join(entity.friendly_name or entity.entity_id for entity in safety_report.unavailable_entries) or "无"
            return FastPathResponse(
                handled=True,
                speech=f"离家保护已阻断：在家人员 {people}；打开门窗 {opened}；不可用门窗 {unavailable}。",
                trace_route="deterministic_leaving_scene",
            )

        snapshots = [self._resolver.snapshot(entity_id) for entity_id in self._resolver.scene_entities(SceneName.LEAVING.value)]
        draft = self._demo_scene_factory.build(text, SceneName.LEAVING, snapshots)
        if draft is None:
            return None
        plan = draft.plan
        if plan.need_clarification:
            return FastPathResponse(
                handled=True,
                speech=plan.clarification_question,
                planned_plan=plan,
                trace_route="deterministic_leaving_scene",
            )
        revalidation = self._environment_guard.revalidate(plan, self._environment_guard.capture(plan))
        if not revalidation.accepted:
            return FastPathResponse(
                handled=True,
                speech="离家保护计划未通过当前环境校验，未执行任何设备操作。",
                planned_plan=plan,
                trace_route="deterministic_leaving_scene",
            )

        protected = self._executor.validator.safety_guard.protected_shutdowns(plan)
        if protected:
            return FastPathResponse(
                handled=True,
                speech="离家保护计划包含受保护设备，已拒绝执行。",
                planned_plan=plan,
                trace_route="deterministic_leaving_scene",
            )

        report = await self._executor.async_execute(plan)
        if report.status == TaskStatus.SUCCESS:
            await self._context_service.async_record_success(
                conversation_id,
                plan,
                report,
                user_id=user_id,
                assistant_id=assistant_id,
            )
            return FastPathResponse(
                handled=True,
                speech="已完成离家保护：已关闭需要关闭的 MQTT 设备，冰箱和路由器未被纳入控制，并已启用安防模式。",
                completed_plan=plan,
                planned_plan=plan,
                execution_report=report,
                trace_route="deterministic_leaving_scene",
            )

        failure = report.step_results[-1] if report.step_results else None
        reason = failure.error_message if failure and failure.error_message else "设备状态没有达到预期"
        return FastPathResponse(
            handled=True,
            speech=f"离家保护未完成：{reason}。",
            planned_plan=plan,
            execution_report=report,
            trace_route="deterministic_leaving_scene",
        )

    async def _async_handle_delayed(
        self,
        text: str,
        conversation_id: str,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> FastPathResponse | None:
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
                await self._dialogue_store.async_set_clarification(
                    conversation_id,
                    request.command,
                    "请补充延时任务中的设备或参数",
                    user_id=user_id,
                    assistant_id=assistant_id,
                )
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
