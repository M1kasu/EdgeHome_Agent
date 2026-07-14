"""Use the existing Home LLM backend to propose, never execute, a fixed plan."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from homeassistant.components import conversation
from pydantic import BaseModel, ConfigDict, Field

from ...const import CONF_ENABLE_STREAMING, CONF_MAX_TOKENS, CONF_RESPONSE_JSON_SCHEMA, CONF_TOOL_CALL_PREFIX, CONF_TOOL_CALL_SUFFIX
from ...entity import LocalLLMClient
from ..context.environment import EnvironmentContextBuilder
from ..context.home_state import TaskHomeStateBuilder
from ..models import EntitySnapshot, TaskPlan
from ..tools.registry import ToolRegistry
from .context_budget import PlanningContextBudgeter
from .demo_scenes import DemoScenePlanFactory
from .draft import DraftCompileError, ModelDraftCompiler, ModelPlanDraft
from .goal_guard import GoalConstraintGuard
from .parser import ParsedPlan, PlanParseStatus, StructuredPlanParser
from .templates import SceneName, SceneTemplateLibrary


class PlanningStatus(StrEnum):
    """Outcome of a planning-only model call."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class PlanningSource(StrEnum):
    """Record whether a plan was supplied by the local model or the safe scene fallback."""

    MODEL = "model"
    MODEL_REPAIRED = "model_repaired"
    MODEL_SANITIZED = "model_sanitized"
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
    model_attempts: int = Field(default=0, ge=0, le=2)
    repair_reason: str | None = None
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
        self._goal_guard = GoalConstraintGuard()
        self._draft_compiler = ModelDraftCompiler()

    async def async_plan(self, request: PlanningRequest, runtime_options: dict[str, Any]) -> PlanningResult:
        """Request JSON from the existing backend without exposing its Home Assistant tool API."""
        template = self._templates.match(request.goal)
        scene = template.name.value if template else None
        snapshot = self._state_builder.build(request.goal, scene=scene)
        budget = self._context_budgeter.trim(snapshot)
        read_only_domains = self._goal_guard.read_only_domains(request.goal, snapshot.entities)
        prompt = self._build_prompt(
            request.goal,
            budget.payload,
            template.planning_guidance if template else None,
            read_only_domains=read_only_domains,
        )
        model_options = {
            **runtime_options,
            CONF_ENABLE_STREAMING: False,
            # Complex plans can contain several state reads and dependencies.
            # Keep the planning response bounded, but reserve enough tokens to
            # close one complete JSON object instead of truncating it at the
            # conversational reply budget.
            CONF_MAX_TOKENS: 512,
            CONF_RESPONSE_JSON_SCHEMA: ModelPlanDraft.model_json_schema(),
            # A planning request has no LLM API.  Treat legacy Home-LLM tool
            # delimiters as plain invalid text instead of silently consuming a
            # model-proposed service call before validation can reject it.
            CONF_TOOL_CALL_PREFIX: "<edgehome_planning_tool_disabled>",
            CONF_TOOL_CALL_SUFFIX: "</edgehome_planning_tool_disabled>",
        }
        messages: list[conversation.Content] = [
            conversation.SystemContent(content=prompt),
            conversation.UserContent(content=request.goal),
        ]
        try:
            response = await self._client._generate(messages, None, request.agent_id, model_options)
        except Exception as error:
            fallback = self._scene_fallback(
                request.goal,
                template.name if template else None,
                snapshot.entities,
                type(error).__name__,
                model_attempts=1,
            )
            return fallback or PlanningResult(
                status=PlanningStatus.UNAVAILABLE,
                reason=type(error).__name__,
                scene=scene,
                context_entities=snapshot.entities,
                model_attempts=1,
            )

        parsed = self._parse_model_response(response.response or "", snapshot.entities)
        if parsed.status == PlanParseStatus.ACCEPTED and parsed.plan is not None:
            sanitization = self._goal_guard.sanitize(parsed.plan, request.goal, snapshot.entities)
            if sanitization.removed_step_ids and sanitization.plan.steps:
                sanitized = self._parser.parse(sanitization.plan.model_dump(mode="json"))
                if sanitized.status == PlanParseStatus.ACCEPTED and sanitized.plan is not None:
                    return PlanningResult(
                        status=PlanningStatus.ACCEPTED,
                        plan=sanitized.plan,
                        scene=scene,
                        context_entities=snapshot.entities,
                        source=PlanningSource.MODEL_SANITIZED,
                        model_attempts=1,
                        repair_reason=(
                            "安全收缩：移除未获授权的控制步骤 "
                            + ", ".join(sanitization.removed_step_ids)
                        ),
                    )
        first_constraint_error = self._constraint_error(parsed.plan, request.goal, snapshot.entities)
        if parsed.status == PlanParseStatus.ACCEPTED and parsed.plan is not None and first_constraint_error is None:
            return PlanningResult(
                status=PlanningStatus.ACCEPTED,
                plan=parsed.plan,
                scene=scene,
                context_entities=snapshot.entities,
                source=PlanningSource.MODEL,
                model_attempts=1,
            )

        # The model has no service access. A second turn can only repair the
        # rejected JSON against the same schema, tool registry, and entity snapshot.
        repair_reason = first_constraint_error or parsed.reason or parsed.status.value
        repair_messages: list[conversation.Content] = [
            conversation.SystemContent(content=self._build_repair_prompt(prompt, repair_reason, response.response or "")),
            conversation.UserContent(content=request.goal),
        ]
        try:
            repaired_response = await self._client._generate(repair_messages, None, request.agent_id, model_options)
        except Exception as error:
            fallback = self._scene_fallback(
                request.goal,
                template.name if template else None,
                snapshot.entities,
                f"repair_{type(error).__name__}",
                model_attempts=2,
                repair_reason=repair_reason,
            )
            return fallback or PlanningResult(
                status=PlanningStatus.UNAVAILABLE,
                reason=type(error).__name__,
                scene=scene,
                context_entities=snapshot.entities,
                model_attempts=2,
                repair_reason=repair_reason,
            )
        repaired = self._parse_model_response(repaired_response.response or "", snapshot.entities)
        repaired_constraint_error = self._constraint_error(repaired.plan, request.goal, snapshot.entities)
        if repaired.status == PlanParseStatus.ACCEPTED and repaired.plan is not None and repaired_constraint_error is None:
            return PlanningResult(
                status=PlanningStatus.ACCEPTED,
                plan=repaired.plan,
                scene=scene,
                context_entities=snapshot.entities,
                source=PlanningSource.MODEL_REPAIRED,
                model_attempts=2,
                repair_reason=repair_reason,
            )
        final_reason = repaired_constraint_error or repaired.reason or repaired.status.value
        fallback = self._scene_fallback(
            request.goal,
            template.name if template else None,
            snapshot.entities,
            final_reason,
            model_attempts=2,
            repair_reason=repair_reason,
        )
        return fallback or PlanningResult(
            status=PlanningStatus.REJECTED,
            reason=final_reason,
            scene=scene,
            context_entities=snapshot.entities,
            model_attempts=2,
            repair_reason=repair_reason,
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
        *,
        model_attempts: int = 0,
        repair_reason: str | None = None,
    ) -> PlanningResult | None:
        """Use a versioned virtual-demo template only after the model output was rejected."""
        if scene is None:
            return None
        # A deterministic demo template is useful for a plain scene request,
        # but it must never silently replace a model plan that carried an
        # explicit preference, prohibition, target, or safety boundary.  Such
        # a request either receives a validated model plan or no device action.
        if self._has_explicit_goal_constraints(goal):
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
            model_attempts=model_attempts,
            repair_reason=repair_reason,
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

    def _build_prompt(
        self,
        goal: str,
        snapshot: dict[str, Any],
        scene_guidance: str | None,
        *,
        read_only_domains: frozenset[str] = frozenset(),
    ) -> str:
        guidance = scene_guidance or "没有场景模板；不得假定用户没有提及的设备。"
        entity_ids = [
            entity.get("entity_id")
            for entity in snapshot.get("entities", [])
            if isinstance(entity, dict) and isinstance(entity.get("entity_id"), str)
        ]
        capabilities = self._semantic_entity_map_from_payload(snapshot)
        return "\n".join(
            [
                "你是 EdgeHome 的受限任务规划器。只返回一个 JSON 对象，不要 Markdown、解释、工具调用或代码。",
                "你只生成语义任务草案，不能调用 Home Assistant 服务、工具或代码；系统会把草案编译为受验证的执行计划。",
                "只能使用状态快照中的 entity_id；不得编造实体、服务、参数或步骤。",
                "必须把用户明确提出的偏好、禁止动作和安全边界写入 constraints；不得杜撰约束。",
                "steps 是有向无环计划：状态读取步骤不依赖其他步骤；控制步骤必须通过 depends_on 指向读取或前置控制步骤。",
                "若用户禁止某设备或动作，计划中不得出现与该禁止项冲突的控制步骤；信息不足时必须 need_clarification=true。",
                (
                    "本请求中的只读设备域（来自用户明确否定）："
                    f"{json.dumps(sorted(read_only_domains), ensure_ascii=False)}。"
                    "这些域的设备只能用于 read_* action，不能出现任何 turn_*、set_*、"
                    "enable_security_mode 或其他控制 action；不需要控制时不要为其创建步骤。"
                ),
                f"用户目标：{goal}",
                f"场景约束：{guidance}",
                f"任务相关状态快照：{json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))}",
                f"唯一可用 entity_id（必须逐字复制，不能简写或自造）：{json.dumps(entity_ids, ensure_ascii=False)}",
                f"可用语义设备键（仅可使用下列键，系统会映射到同一行的已暴露实体）：{json.dumps(capabilities, ensure_ascii=False)}",
                "输出字段：goal、scene、need_clarification、clarification_question、rationale、constraints、steps。",
                "每个 step 仅含 id、action、entity_id、room、value、depends_on、on_failure、optional。"
                "action 只能是 read_home、read_room、read_device、turn_on、turn_off、set_brightness、"
                "set_temperature、set_hvac_mode、set_curtain_position、enable_security_mode。",
                "read_home 不填 entity_id；read_room 的 room 只能是 living_room、bedroom、home；"
                "read_device 和控制动作必须填快照内 entity_id。",
                "set_brightness 和 set_curtain_position 的 value 是 0 到 100 的整数；"
                "set_temperature 是 16 到 30 的数字；set_hvac_mode 的 value 只能是 off、cool、heat。",
                "格式示例（仅说明 JSON 结构，不能照抄其中的占位实体）："
                '{"goal":"示例","rationale":"说明","constraints":[{"statement":"用户约束","source":"user","hard":true}],'
                '"steps":[{"id":"read_target","action":"read_device","entity_id":"快照实体ID","depends_on":[]},'
                '{"id":"adjust_target","action":"set_brightness","entity_id":"同一快照实体ID","value":40,'
                '"depends_on":["read_target"]}]}。',
            ]
        )

    def _parse_model_response(self, response: str, entities: list[EntitySnapshot]) -> ParsedPlan:
        """Accept either a legacy fixed plan or the smaller semantic draft contract."""
        direct = self._parser.parse(response)
        if direct.status == PlanParseStatus.ACCEPTED:
            return direct
        raw = StructuredPlanParser._decode(response)
        if raw is None:
            return direct
        # Preserve the precise closed-tool validation error for a legacy plan
        # instead of obscuring it behind draft-schema diagnostics.
        raw_steps = raw.get("steps")
        if isinstance(raw_steps, list) and any(isinstance(step, dict) and "tool" in step for step in raw_steps):
            return direct
        try:
            draft = ModelPlanDraft.model_validate(raw)
            draft = self._normalize_draft_entities(draft, entities)
            compiled = self._draft_compiler.compile(draft)
        except (ValueError, DraftCompileError) as error:
            return ParsedPlan(status=PlanParseStatus.INVALID_PLAN, reason=str(error))
        return self._parser.parse(compiled.model_dump(mode="json"))

    def _normalize_draft_entities(self, draft: ModelPlanDraft, entities: list[EntitySnapshot]) -> ModelPlanDraft:
        """Resolve a model semantic key only when this snapshot exposes exactly one target."""
        capabilities = self._semantic_entity_map(entities)
        steps = [
            step.model_copy(update={"entity_id": capabilities.get(step.entity_id, step.entity_id)})
            if step.entity_id is not None
            else step
            for step in draft.steps
        ]
        return draft.model_copy(update={"steps": steps})

    @classmethod
    def _semantic_entity_map(cls, entities: list[EntitySnapshot]) -> dict[str, str]:
        candidates: dict[str, list[str]] = {}
        for entity in entities:
            room = cls._semantic_room(entity.entity_id)
            if room is None:
                continue
            device_type = cls._semantic_device_type(entity)
            for key in cls._semantic_keys(room, device_type, entity.domain):
                candidates.setdefault(key, []).append(entity.entity_id)
        return {key: values[0] for key, values in candidates.items() if len(values) == 1}

    @classmethod
    def _semantic_entity_map_from_payload(cls, snapshot: dict[str, Any]) -> dict[str, str]:
        entities: list[EntitySnapshot] = []
        for payload in snapshot.get("entities", []):
            if not isinstance(payload, dict):
                continue
            try:
                entities.append(EntitySnapshot.model_validate(payload))
            except ValueError:
                continue
        return cls._semantic_entity_map(entities)

    @staticmethod
    def _semantic_room(entity_id: str) -> str | None:
        object_id = entity_id.partition(".")[2]
        for room in ("living_room", "bedroom"):
            if room in object_id:
                return room
        return None

    @staticmethod
    def _semantic_device_type(entity: EntitySnapshot) -> str:
        object_id = entity.entity_id.partition(".")[2]
        name = (entity.friendly_name or "").lower()
        if entity.domain == "cover":
            return "curtain"
        if entity.domain == "climate":
            return "climate"
        if entity.domain == "light":
            return "light"
        if entity.domain == "switch" and ("tv" in object_id or "电视" in name):
            return "tv"
        return entity.domain

    @staticmethod
    def _semantic_keys(room: str, device_type: str, domain: str) -> tuple[str, ...]:
        """Expose a few adapter-level aliases only when they map unambiguously."""
        keys = [f"{room}.{device_type}", f"{domain}.{room}"]
        if device_type == "curtain":
            keys.append("window.curtain_position")
        return tuple(keys)

    def _constraint_error(
        self,
        plan: TaskPlan | None,
        goal: str,
        entities: list[EntitySnapshot],
    ) -> str | None:
        """Keep natural-language prohibitions outside of the model's trust boundary."""
        return self._goal_guard.validate(plan, goal, entities) if plan is not None else None

    @staticmethod
    def _has_explicit_goal_constraints(goal: str) -> bool:
        """Recognize modifiers that a generic demonstration template must not guess."""
        normalized = "".join(goal.split())
        return bool(
            re.search(
                r"(?:不要|别|禁止|无需|不需要|不得|至少|最低|不低于|不高于|最多|"
                r"\d{1,3}\s*(?:%|％|成|度|℃)|窗帘|主灯|空调|电视|冰箱|路由器)",
                normalized,
            )
        )

    @staticmethod
    def _build_repair_prompt(prompt: str, rejection_reason: str, prior_output: str) -> str:
        """Ask for one bounded schema repair without broadening planning authority."""
        return "\n".join(
            [
                prompt,
                "上一次模型输出未通过确定性校验。请仅返回修复后的一个语义任务草案 JSON 对象。",
                f"校验失败原因：{rejection_reason}",
                f"上一次输出：{prior_output[:1600]}",
                "不得添加状态快照或工具清单之外的实体、工具、服务或参数。",
            ]
        )
