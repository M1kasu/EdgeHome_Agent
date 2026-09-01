"""LLM Goal Reasoner: natural language -> semantic structured goal.

模型负责理解用户想要什么（语义维度与区间），不是输出设备指令：
"别太亮" 由模型解释为亮度目标区间并给出理由，而不是查答案表。
本地模型不可用时拒绝执行语义目标，不使用场景关键词模板冒充理解。

dimension 是系统的语义维度词汇表（系统能力定义，非场景答案）：
    thermal  -> temperature        lighting -> brightness
    glare    -> curtain_position   energy   -> 成本惩罚（不映射单一设备）
确定性代码只做：schema 校验、把模型给出的区间裁剪到设备物理边界。
"""

from __future__ import annotations

import json
import re
import uuid

from spacebutler.agent.models import CompiledGoal, GoalConstraint, now
from spacebutler.llm.client import LlmClient

# 语义维度 -> 物理量 + 物理边界（capability 边界，guardrail，非业务决策）。
DIMENSION_RULES: dict[str, tuple[str, tuple[float, float], str]] = {
    "thermal": ("temperature", (16, 30), "°C"),
    "lighting": ("brightness", (1, 100), "%"),
    "glare": ("curtain_position", (0, 100), "%"),
}
_METRIC_RULES: dict[str, tuple[float, float]] = {
    "temperature": (16, 30),
    "brightness": (1, 100),
    "curtain_position": (0, 100),
}

_GOAL_SYSTEM_PROMPT = (
    "你是家庭智能体的目标理解器。判断用户的话是否表达了一个可执行的家居环境目标，"
    "并编译为语义目标。约束表达语义偏好而非设备指令；数值区间由你根据语义推理给出并填写 reason。"
    "注意：身体感受（刺眼、闷、热、冷、暗）和活动准备（看电影、休息、工作）都是家居目标；"
    "'不想睡''别太黑'这类否定约束也是目标的一部分。"
    "必须逐项覆盖用户同一句话里的每个独立环境感受或限制；如果同时涉及温度、灯光、眩光等多个维度，"
    "必须分别输出对应 constraint，不能遗漏，也不能把一个维度的理由写到另一个维度。"
    "活动本身可能隐含环境目标（例如观影通常需要减少自然光干扰），可以推断相应语义维度；"
    "但不要假设必须操作某个具体设备，也不要输出设备指令。energy 维度不设数值区间。"
    "输入有时是包含 user_text、当前环境、active_goal 和最近对话的 JSON；遇到追问或指代时，"
    "只在上下文确实相关时继承 active_goal 的语义约束，并仍以本轮 user_text 为最高优先级。"
    "不是家居目标时 constraints 返回空数组。"
    "\n示例：\n"
    '用户：客厅有点刺眼，我想安静坐会 → {"is_home_goal": true, "location": "living_room", '
    '"intent_summary": "降低刺眼光线，安静休息", "activity": "relax", "constraints": '
    '[{"dimension": "lighting", "direction": "dimmer", "strength": "moderate", '
    '"target_range": [15, 40], "reason": "刺眼说明当前过亮"}]}\n'
    '用户：有点困但还不想睡 → {"is_home_goal": true, "location": null, '
    '"intent_summary": "保持清醒舒适", "activity": "rest", "constraints": '
    '[{"dimension": "lighting", "direction": "dimmer", "strength": "slight", '
    '"target_range": [30, 55], "reason": "困但不想睡，适合柔和偏暗"}, '
    '{"dimension": "thermal", "direction": "cooler", "strength": "slight", '
    '"target_range": [24, 28], "reason": "凉爽帮助清醒"}]}'
)

# JSON Schema（llama.cpp 约束解码 + 校验共用）。
_GOAL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "is_home_goal": {"type": "boolean"},
        "location": {"type": "string"},
        "intent_summary": {"type": "string"},
        "activity": {"type": "string"},
        "constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "enum": ["thermal", "lighting", "glare", "energy"]},
                    "direction": {"type": "string"},
                    "strength": {"type": "string", "enum": ["slight", "moderate", "strong"]},
                    "target_range": {"type": "array", "items": {"type": "number"}},
                    "reason": {"type": "string"},
                },
                "required": ["dimension"],
            },
        },
    },
    "required": ["is_home_goal", "intent_summary", "constraints"],
}

_DIMENSION_AUDIT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "dimensions": {
            "type": "array",
            "items": {"type": "string", "enum": ["thermal", "lighting", "glare", "energy"]},
        }
    },
    "required": ["dimensions"],
}

_CANCEL_WORDS = re.compile(r"不看了|不看电影|取消.{0,4}(任务|目标)|我要去睡觉|睡觉了|停止目标")


class GoalCompiler:
    """Natural language -> CompiledGoal through the configured local model."""

    def __init__(self) -> None:
        self.last_raw: dict | None = None  # 最后一次模型原始输出（compile 长尾诊断用）
        self._state_anchors: dict[str, float | None] = {}

    def compile(
        self,
        text: str,
        llm_client: LlmClient | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[CompiledGoal | None, str, dict | None]:
        """返回 (goal, planner_mode, semantic_view)。

        goal 为 None 表示模型判断这不是家居目标（或不可用且非关键词），
        模型不可用或输出不合法时不生成设备目标，调用方必须安全拒绝执行。
        planner_mode ∈ {llm, invalid, unavailable}。
        semantic_view 是模型原始语义判断（供 UI 展示 MODEL INTERPRETATION）。
        """
        # Goal 理解的输入就是用户的原话。3B 模型对 JSON 包装输入的语义
        # 理解会明显退化（实测判非目标率飙升），全量状态上下文属于 Planner。
        model_input = text
        if context and isinstance(context.get("active_goal"), dict):
            # 已有进行中的目标：附加紧凑摘要（仅 active_goal，不含全量状态
            # 与对话历史），让模型能判断"这句话是否仍指向该目标/是否已满足"。
            active = context["active_goal"]
            compact = json.dumps(
                {
                    "active_goal": {
                        "title": active.get("title"),
                        "activity": active.get("activity"),
                        "status": context.get("active_goal_status"),
                        "constraints": active.get("constraints"),
                    }
                },
                ensure_ascii=False,
            )
            model_input = f"{text}\n{compact}"
        if llm_client is not None and llm_client.available():
            raw = llm_client.chat_json(_GOAL_SYSTEM_PROMPT, model_input, schema=_GOAL_SCHEMA)
            self.last_raw = raw if isinstance(raw, dict) else None
            # 语义 retry（max 2 次）：小模型对隐含目标的布尔判定有波动，
            # 用 repair prompt 再确认一次（任务书 max_model_retry=2）。
            if isinstance(raw, dict) and raw.get("is_home_goal") is False and raw.get("constraints") in (None, []):
                repair = llm_client.chat_json(
                    _GOAL_SYSTEM_PROMPT
                    + "\n注意：用户描述身体感受（闷/热/冷/困/刺眼）或活动准备（看剧/休息/工作）时，"
                    "几乎总是表达家居环境目标。请再判断一次。",
                    model_input,
                    schema=_GOAL_SCHEMA,
                )
                if isinstance(repair, dict) and repair.get("is_home_goal") is True:
                    raw = repair
            if isinstance(raw, dict) and raw.get("is_home_goal") is True:
                audit = llm_client.chat_json(
                    "你是环境语义维度审计器。提取用户明确表达或其活动合理隐含的环境维度，"
                    "只判断环境目标，不指定设备。"
                    "thermal=冷热闷等温度感受，lighting=灯光明暗，glare=自然光或窗帘眩光，"
                    "energy=明确节能诉求。返回 JSON。",
                    model_input,
                    schema=_DIMENSION_AUDIT_SCHEMA,
                )
                audited_dimensions = {
                    str(item)
                    for item in (audit.get("dimensions", []) if isinstance(audit, dict) else [])
                    if str(item) in DIMENSION_RULES
                }
                draft_dimensions = _semantic_dimensions(raw)
                missing_dimensions = audited_dimensions - draft_dimensions
                if missing_dimensions:
                    reviewed = llm_client.chat_json(
                        _GOAL_SYSTEM_PROMPT
                        + "\n你现在进行完整性修复。保留正确初稿，并补齐审计确认遗漏的维度。"
                        "返回修复后的完整 JSON，不要解释。",
                        f"输入与上下文：{model_input}\n待修复初稿：{raw}\n必须补齐：{sorted(missing_dimensions)}",
                        schema=_GOAL_SCHEMA,
                    )
                    if audited_dimensions <= _semantic_dimensions(reviewed):
                        raw = reviewed
            goal = self._goal_from_semantics(raw, text)
            if goal is not None:
                semantic = raw if isinstance(raw, dict) else {}
                semantic.pop("is_home_goal", None)
                return goal, "llm", semantic
            if isinstance(raw, dict) and raw.get("is_home_goal") is False:
                return None, "llm", {"is_home_goal": False, "intent_summary": str(raw.get("intent_summary", ""))}
            return None, "invalid", {"is_home_goal": None, "intent_summary": "模型输出未通过目标 schema 校验"}
        return None, "unavailable", {"is_home_goal": None, "intent_summary": "本地模型不可用"}

    # ---- LLM semantic path ------------------------------------------
    def _goal_from_semantics(self, raw: object, text: str) -> CompiledGoal | None:
        if not isinstance(raw, dict):
            return None
        constraints_raw = raw.get("constraints")
        has_constraints = isinstance(constraints_raw, list) and any(
            isinstance(item, dict) for item in constraints_raw
        )
        # schema repair：模型布尔标志与约束自相矛盾（false 但给出有效约束）时以约束为准。
        if raw.get("is_home_goal") is not True and not has_constraints:
            return None
        constraints: list[GoalConstraint] = []
        for item in constraints_raw[:6]:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension", "")).strip().lower()
            rule = DIMENSION_RULES.get(dimension)
            if rule is None:
                continue
            metric, bounds, _unit = rule
            target_range = item.get("target_range")
            lower = upper = None
            if isinstance(target_range, list) and len(target_range) == 2 and all(
                isinstance(value, (int, float)) and not isinstance(value, bool) for value in target_range
            ):
                lower, upper = sorted(float(value) for value in target_range)
                # 亮度 0 是"关灯"这一合法诉求，不能被下界 1 夹成空区间。
                if metric == "brightness" and lower <= 0 and upper <= 0:
                    lower = upper = 0.0
                else:
                    lower = max(lower, bounds[0])
                    upper = min(upper, bounds[1])
                    if lower > upper:
                        lower, upper = None, None
            direction = str(item.get("direction", "") or "").strip().lower()
            if lower is None and direction:
                lower, upper = self._range_from_direction(direction, metric)
            if lower is None:
                continue
            constraints.append(
                GoalConstraint(
                    metric=metric,
                    lower=round(lower, 1),
                    upper=round(upper, 1),
                    target=round((lower + upper) / 2, 1),
                )
            )
        if not constraints:
            return None
        activity = str(raw.get("activity", "") or "home_comfort").strip()[:24] or "home_comfort"
        title = str(raw.get("intent_summary", "") or activity).strip()[:40] or activity
        # 模型偶发输出 "null"/"none"/"未知" 字面字符串；清洗为可读默认标题。
        if title.lower() in {"null", "none", "未知", "无", "unknown", "n/a"}:
            title = f"{activity} 环境目标"
        title = title[:40]
        goal = CompiledGoal(
            goal_id=f"goal_{uuid.uuid4().hex[:10]}",
            activity=activity,
            room=self._room(str(raw.get("location", ""))),
            title=title,
            constraints=constraints,
            expires_at=now() + 30 * 60,
        )
        goal.semantic_view = {  # type: ignore[attr-defined] — 展示用附加载荷
            "intent_summary": str(raw.get("intent_summary", "")),
            "reasons": [
                {"dimension": str(item.get("dimension", "")), "reason": str(item.get("reason", ""))}
                for item in constraints_raw
                if isinstance(item, dict) and item.get("reason")
            ],
        }
        return goal

    def _range_from_direction(self, direction: str, metric: str) -> tuple[float | None, float | None]:
        """模型只给了方向没给数值区间时，从方向关键词与当前环境推导宽区间。

        这是数学推导（方向 + 当前值锚点 + 模型给的 strength 语义），不是场景答案表。
        """
        anchor = self._state_anchors.get(metric)
        normalized = direction.lower()
        cooling_words = ("cool", "lower", "reduce", "闷", "热", "凉")
        dimming_words = ("dark", "black", "dim", "lower", "reduce")
        brightening_words = ("bright", "warmer", "亮")
        off_words = ("off", "关掉", "关闭", "关灯", "熄")
        if metric == "curtain_position" and any(
            word in normalized for word in ("close", "reduce", "lower", "关闭", "减少")
        ):
            return 0.0, 20.0
        if metric == "brightness" and any(word in normalized for word in off_words):
            # 关灯是开关意图而非调暗：目标亮度锁定 0，由 planner 路由到 turn_off。
            return 0.0, 0.0
        if metric == "brightness" and any(word in normalized for word in dimming_words):
            # 调暗：从当前亮度向下推一个宽区间。
            if anchor is None:
                return 15.0, 50.0
            return (max(5.0, anchor - 50.0), max(15.0, anchor - 5.0))
        if metric == "temperature" and any(word in normalized for word in cooling_words):
            if anchor is None:
                return 22.0, 27.0
            return (max(16.0, anchor - 6.0), max(23.0, anchor - 0.5))
        if metric == "brightness" and any(word in normalized for word in brightening_words):
            if anchor is None:
                return 40.0, 85.0
            return (min(anchor + 5.0, 100.0), min(anchor + 40.0, 100.0))
        return None, None

    def compile_with_state(
        self,
        text: str,
        llm_client: LlmClient | None,
        temperature: float | None,
        brightness: float | None,
        context: dict[str, object] | None = None,
    ) -> tuple[CompiledGoal | None, str, dict | None]:
        self._state_anchors = {"temperature": temperature, "brightness": brightness}
        try:
            return self.compile(text, llm_client, context)
        finally:
            self._state_anchors = {}

    def _room(self, location: str) -> str:
        normalized = location.strip().lower()
        return normalized if normalized in {"living_room", "bedroom", "home"} else "living_room"


def is_cancel_text(text: str) -> bool:
    """取消表述是封闭集合（确认/取消词表属于交互协议，不是场景决策）。"""
    return bool(_CANCEL_WORDS.search(text))


def _semantic_dimension_count(raw: object) -> int:
    if not isinstance(raw, dict) or raw.get("is_home_goal") is not True:
        return -1
    return len(_semantic_dimensions(raw))


def _semantic_dimensions(raw: object) -> set[str]:
    if not isinstance(raw, dict):
        return set()
    constraints = raw.get("constraints")
    if not isinstance(constraints, list):
        return set()
    return {
        str(item.get("dimension", ""))
        for item in constraints
        if isinstance(item, dict) and str(item.get("dimension", "")) in DIMENSION_RULES
    }
