"""Use the existing Home LLM backend to propose, never execute, a fixed plan."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from homeassistant.components import conversation
from pydantic import BaseModel, ConfigDict, Field

from ...const import CONF_ENABLE_STREAMING, CONF_RESPONSE_JSON_SCHEMA
from ...entity import LocalLLMClient
from ..context.environment import EnvironmentContextBuilder
from ..context.home_state import TaskHomeStateBuilder
from ..models import EntitySnapshot, TaskPlan
from ..tools.registry import ToolRegistry
from .context_budget import PlanningContextBudgeter
from .demo_scenes import DemoScenePlanFactory
from .parser import PlanParseStatus, StructuredPlanParser
from .templates import SceneName, SceneTemplateLibrary


class PlanningStatus(StrEnum):
    """Outcome of a planning-only model call."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class PlanningSource(StrEnum):
    """Record whether a plan was supplied by the local model or the safe scene fallback."""

    MODEL = "model"
    DEMO_SCENE_FALLBACK = "demo_scene_fallback"


class PlanningRequest(BaseModel):
    """Validated external request data passed into the structured planning boundary."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=300)
    agent_id: str = Field(min_length=1, max_length=128)


class PlanningResult(BaseModel):
    """A plan is returned only after model output has passed deterministic validation."""

    model_config = ConfigDict(extra="forbid")

    status: PlanningStatus
    plan: TaskPlan | None = None
    reason: str | None = None
    scene: str | None = None
    context_entities: list[EntitySnapshot] = Field(default_factory=list)
    source: PlanningSource | None = None
    delayed_plan: TaskPlan | None = None
    delay_seconds: int | None = Field(default=None, ge=1, le=7 * 24 * 60 * 60)


class LLMTaskPlanner:
    """Constrain an existing model client to a JSON-only, no-tool planning request."""

    def __init__(
        self,
        client: LocalLLMClient,
        state_builder: TaskHomeStateBuilder | EnvironmentContextBuilder,
        parser: StructuredPlanParser,
        tools: ToolRegistry,
        templates: SceneTemplateLibrary | None = None,
        context_budgeter: PlanningContextBudgeter | None = None,
        demo_scene_factory: DemoScenePlanFactory | None = None,
    ) -> None:
        self._client = client
        self._state_builder = state_builder
        self._parser = parser
        self._tools = tools
        self._templates = templates or SceneTemplateLibrary()
        self._context_budgeter = context_budgeter or PlanningContextBudgeter()
        self._demo_scene_factory = demo_scene_factory or DemoScenePlanFactory()

    async def async_plan(self, request: PlanningRequest, runtime_options: dict[str, Any]) -> PlanningResult:
        """Request JSON from the existing backend without exposing its Home Assistant tool API."""
        template = self._templates.match(request.goal)
        scene = template.name.value if template else None
        snapshot = self._state_builder.build(request.goal, scene=scene)
        budget = self._context_budgeter.trim(snapshot)
        prompt = self._build_prompt(request.goal, budget.payload, template.planning_guidance if template else None)
        model_options = {
            **runtime_options,
            CONF_ENABLE_STREAMING: False,
            CONF_RESPONSE_JSON_SCHEMA: TaskPlan.model_json_schema(),
        }
        messages: list[conversation.Content] = [
            conversation.SystemContent(content=prompt),
            conversation.UserContent(content=request.goal),
        ]
        try:
            response = await self._client._generate(messages, None, request.agent_id, model_options)
        except Exception as error:
            fallback = self._scene_fallback(request.goal, template.name if template else None, snapshot.entities, type(error).__name__)
            return fallback or PlanningResult(
                status=PlanningStatus.UNAVAILABLE,
                reason=type(error).__name__,
                scene=scene,
                context_entities=snapshot.entities,
            )
        if not response.response:
            fallback = self._scene_fallback(request.goal, template.name if template else None, snapshot.entities, "模型没有返回计划 JSON")
            return fallback or PlanningResult(
                status=PlanningStatus.REJECTED,
                reason="模型没有返回计划 JSON",
                scene=scene,
                context_entities=snapshot.entities,
            )

        parsed = self._parser.parse(response.response)
        if parsed.status != PlanParseStatus.ACCEPTED or parsed.plan is None:
            fallback = self._scene_fallback(
                request.goal,
                template.name if template else None,
                snapshot.entities,
                parsed.reason or parsed.status.value,
            )
            return fallback or PlanningResult(
                status=PlanningStatus.REJECTED,
                reason=parsed.reason or parsed.status.value,
                scene=scene,
                context_entities=snapshot.entities,
            )
        return PlanningResult(
            status=PlanningStatus.ACCEPTED,
            plan=parsed.plan,
            scene=scene,
            context_entities=snapshot.entities,
            source=PlanningSource.MODEL,
        )

    async def async_template_fallback(self, request: PlanningRequest, reason: str) -> PlanningResult:
        """资源或模型不可用时仅使用已登记的虚拟场景模板，不再请求模型。"""
        template = self._templates.match(request.goal)
        scene = template.name.value if template else None
        snapshot = self._state_builder.build(request.goal, scene=scene)
        fallback = self._scene_fallback(request.goal, template.name if template else None, snapshot.entities, reason)
        return fallback or PlanningResult(
            status=PlanningStatus.UNAVAILABLE,
            reason=reason,
            scene=scene,
            context_entities=snapshot.entities,
        )

    def _scene_fallback(
        self,
        goal: str,
        scene: SceneName | None,
        context_entities: list[EntitySnapshot],
        model_reason: str,
    ) -> PlanningResult | None:
        """Use a versioned virtual-demo template only after the model output was rejected."""
        if scene is None:
            return None
        draft = self._demo_scene_factory.build(goal, scene, context_entities)
        if draft is None:
            return None
        parsed = self._parser.parse(draft.plan.model_dump(mode="json"))
        if parsed.status != PlanParseStatus.ACCEPTED or parsed.plan is None:
            return None
        delayed_plan = None
        if draft.delayed_plan is not None:
            delayed = self._parser.parse(draft.delayed_plan.model_dump(mode="json"))
            if delayed.status != PlanParseStatus.ACCEPTED or delayed.plan is None:
                return None
            delayed_plan = delayed.plan
        return PlanningResult(
            status=PlanningStatus.ACCEPTED,
            plan=parsed.plan,
            reason=f"本地模型计划未通过校验，已使用受限虚拟场景计划：{model_reason}",
            scene=scene.value,
            context_entities=context_entities,
            source=PlanningSource.DEMO_SCENE_FALLBACK,
            delayed_plan=delayed_plan,
            delay_seconds=draft.delay_seconds,
        )

    def is_candidate(self, text: str) -> bool:
        """Avoid sending ordinary chat or already-handled fast commands to the planner."""
        if self._templates.match(text):
            return True
        return bool(
            re.search(r"打开|关闭|调到|设置|查询|查看", text)
            and re.search(r"然后|再把|并且|同时|以及|全部|所有|都|、|；|;|如果|定时|后", text)
        )

    def _build_prompt(self, goal: str, snapshot: dict[str, Any], scene_guidance: str | None) -> str:
        tool_metadata = [
            {
                "name": definition.name,
                "arguments": definition.argument_model.model_json_schema(),
                "allowed_domains": sorted(definition.allowed_domains),
                "requires_verification": definition.requires_verification,
            }
            for definition in self._tools.definitions()
            if definition.plan_enabled
        ]
        guidance = scene_guidance or "没有场景模板；不得假定用户没有提及的设备。"
        return "\n".join(
            [
                "你是 EdgeHome 的受限任务规划器。只返回一个 JSON 对象，不要 Markdown、解释、工具调用或代码。",
                "你只能使用下方工具和实体 ID；不得编造实体、服务、参数或步骤。",
                "关键控制步骤必须包含 verify；无法安全完成时应返回无法通过校验的最小计划，系统会拒绝而不会执行。",
                f"用户目标：{goal}",
                f"场景约束：{guidance}",
                f"任务相关状态快照：{json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))}",
                f"允许工具：{json.dumps(tool_metadata, ensure_ascii=False, separators=(',', ':'))}",
                f"TaskPlan JSON Schema：{json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False, separators=(',', ':'))}",
            ]
        )
