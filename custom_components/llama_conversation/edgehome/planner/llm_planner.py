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
from .parser import PlanParseStatus, StructuredPlanParser
from .templates import SceneTemplateLibrary


class PlanningStatus(StrEnum):
    """Outcome of a planning-only model call."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


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


class LLMTaskPlanner:
    """Constrain an existing model client to a JSON-only, no-tool planning request."""

    def __init__(
        self,
        client: LocalLLMClient,
        state_builder: TaskHomeStateBuilder | EnvironmentContextBuilder,
        parser: StructuredPlanParser,
        tools: ToolRegistry,
        templates: SceneTemplateLibrary | None = None,
    ) -> None:
        self._client = client
        self._state_builder = state_builder
        self._parser = parser
        self._tools = tools
        self._templates = templates or SceneTemplateLibrary()

    async def async_plan(self, request: PlanningRequest, runtime_options: dict[str, Any]) -> PlanningResult:
        """Request JSON from the existing backend without exposing its Home Assistant tool API."""
        template = self._templates.match(request.goal)
        scene = template.name.value if template else None
        snapshot = self._state_builder.build(request.goal, scene=scene)
        prompt = self._build_prompt(request.goal, snapshot.model_dump(mode="json"), template.planning_guidance if template else None)
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
            return PlanningResult(
                status=PlanningStatus.UNAVAILABLE,
                reason=type(error).__name__,
                scene=scene,
                context_entities=snapshot.entities,
            )
        if not response.response:
            return PlanningResult(
                status=PlanningStatus.REJECTED,
                reason="模型没有返回计划 JSON",
                scene=scene,
                context_entities=snapshot.entities,
            )

        parsed = self._parser.parse(response.response)
        if parsed.status != PlanParseStatus.ACCEPTED or parsed.plan is None:
            return PlanningResult(
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
