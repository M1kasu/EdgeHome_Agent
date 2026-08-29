"""LLM Goal Reasoner: natural language -> semantic structured goal.

模型负责理解用户想要什么（语义维度与区间），不是输出设备指令：
"别太亮" 由模型解释为亮度目标区间并给出理由，而不是查答案表。
关键词模板只作为 LLM 不可用时的 fallback（UI 如实显示 planner_mode），
禁止进入正式模型规划链路。

dimension 是系统的语义维度词汇表（系统能力定义，非场景答案）：
    thermal  -> temperature        lighting -> brightness
    glare    -> curtain_position   energy   -> 成本惩罚（不映射单一设备）
确定性代码只做：schema 校验、把模型给出的区间裁剪到设备物理边界。
"""

from __future__ import annotations

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
    "不要假设特定活动必须操作特定设备。energy 维度不设数值区间。"
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

# fallback 专用：LLM 不可用时才使用（planner_mode 如实标注 fallback）。
_ACTIVITY_WORDS = re.compile(r"看(?:会|场|个)?(?:电影|电视|剧|片)|观影|追剧|休息|放松|睡觉|工作|阅读")
_THERMAL_WORDS = re.compile(r"有点热|太热|热|有点冷|太冷|凉快|闷")
_CANCEL_WORDS = re.compile(r"不看了|不看电影|取消.{0,4}(任务|目标)|我要去睡觉|睡觉了|停止目标")


class GoalCompiler:
    """Natural language -> CompiledGoal. LLM-first, deterministic template fallback."""

    def compile(self, text: str, llm_client: LlmClient | None = None) -> tuple[CompiledGoal | None, str, dict | None]:
        """返回 (goal, planner_mode, semantic_view)。

        goal 为 None 表示模型判断这不是家居目标（或不可用且非关键词），
        调用方应把文本交回普通对话。planner_mode ∈ {llm, fallback}。
        semantic_view 是模型原始语义判断（供 UI 展示 MODEL INTERPRETATION）。
        """
        if llm_client is not None and llm_client.available():
            raw = llm_client.chat_json(_GOAL_SYSTEM_PROMPT, text, schema=_GOAL_SCHEMA)
            # 语义 retry（max 2 次）：小模型对隐含目标的布尔判定有波动，
            # 用 repair prompt 再确认一次（任务书 max_model_retry=2）。
            if isinstance(raw, dict) and raw.get("is_home_goal") is False and raw.get("constraints") in (None, []):
                repair = llm_client.chat_json(
                    _GOAL_SYSTEM_PROMPT
                    + "\n注意：用户描述身体感受（闷/热/冷/困/刺眼）或活动准备（看剧/休息/工作）时，"
                    "几乎总是表达家居环境目标。请再判断一次。",
                    text,
                    schema=_GOAL_SCHEMA,
                )
                if isinstance(repair, dict) and repair.get("is_home_goal") is True:
                    raw = repair
            goal = self._goal_from_semantics(raw, text)
            if goal is not None:
                semantic = raw if isinstance(raw, dict) else {}
                semantic.pop("is_home_goal", None)
                return goal, "llm", semantic
            if isinstance(raw, dict) and raw.get("is_home_goal") is False:
                return None, "llm", {"is_home_goal": False, "intent_summary": str(raw.get("intent_summary", ""))}
        if self._looks_like_goal_fallback(text):
            return self._goal_from_template(text), "fallback", {"is_home_goal": True, "intent_summary": "（LLM 不可用，确定性模板解析）"}
        return None, "fallback", None

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
                lower = max(lower, bounds[0])
                upper = min(upper, bounds[1])
                if lower > upper:
                    lower, upper = None, None
            direction = str(item.get("direction", "") or "").strip().lower()
            if lower is None and direction:
                lower, upper = self._range_from_direction(direction, state_hint=text)
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

    def _range_from_direction(self, direction: str, state_hint: str) -> tuple[float | None, float | None]:
        """模型只给了方向没给数值区间时，从方向关键词与当前环境推导宽区间。

        这是数学推导（方向 + 当前值锚点 + 模型给的 strength 语义），不是场景答案表。
        """
        anchor = self._state_anchor
        normalized = direction.lower()
        cooling_words = ("cool", "lower", "reduce", "闷", "热", "凉")
        dimming_words = ("dark", "black", "dim", "lower", "reduce")
        brightening_words = ("bright", "warmer", "亮")
        if any(word in normalized for word in dimming_words):
            # 调暗：从当前亮度向下推一个宽区间。
            if anchor is None:
                return 15.0, 50.0
            return (max(5.0, anchor - 50.0), max(15.0, anchor - 5.0))
        if any(word in normalized for word in cooling_words):
            if anchor is None:
                return 22.0, 27.0
            return (max(16.0, anchor - 6.0), max(23.0, anchor - 0.5))
        if any(word in normalized for word in brightening_words):
            if anchor is None:
                return 40.0, 85.0
            return (min(anchor + 5.0, 100.0), min(anchor + 40.0, 100.0))
        return None, None

    _state_anchor: float | None = None

    def compile_with_state(self, text: str, llm_client: LlmClient | None, temperature: float | None, brightness: float | None) -> tuple[CompiledGoal | None, str, dict | None]:
        self._state_anchor = temperature
        try:
            return self.compile(text, llm_client)
        finally:
            self._state_anchor = None

    # ---- fallback path（LLM 不可用时专用） ---------------------------
    def _looks_like_goal_fallback(self, text: str) -> bool:
        return bool(_ACTIVITY_WORDS.search(text) or _THERMAL_WORDS.search(text))

    def _goal_from_template(self, text: str) -> CompiledGoal:
        """fallback 模板：仅在 LLM 不可用时使用，planner_mode 如实标注。"""
        constraints: list[GoalConstraint] = []
        if _ACTIVITY_WORDS.search(text):
            if re.search(r"完全黑|全黑|太暗|别太暗|不要暗|留点光|留着", text):
                constraints.append(GoalConstraint(metric="brightness", lower=10, upper=60))
            elif re.search(r"别太亮|不要太亮|暗一点|调暗|不亮", text) or "灯" in text:
                constraints.append(GoalConstraint(metric="brightness", lower=20, upper=45))
            else:
                constraints.append(GoalConstraint(metric="brightness", lower=15, upper=50))
            if not re.search(r"别全关|留一?[点条缝]|不要全关", text):
                constraints.append(GoalConstraint(metric="curtain_position", upper=30))
        if _THERMAL_WORDS.search(text) or _ACTIVITY_WORDS.search(text):
            if re.search(r"有点冷|太冷|冷", text):
                constraints.append(GoalConstraint(metric="temperature", lower=25, upper=29, target=27))
            else:
                constraints.append(GoalConstraint(metric="temperature", lower=24, upper=27.5, target=26))
        if not constraints:
            constraints.append(GoalConstraint(metric="temperature", lower=22, upper=28, target=26))
        activity = "watch_content" if _ACTIVITY_WORDS.search(text) else "thermal_comfort"
        return CompiledGoal(
            goal_id=f"goal_{uuid.uuid4().hex[:10]}",
            activity=activity,
            room="living_room",
            title=f"{activity}（确定性回退解析）",
            constraints=constraints,
            expires_at=now() + 30 * 60,
        )

    def _room(self, location: str) -> str:
        normalized = location.strip().lower()
        return normalized if normalized in {"living_room", "bedroom", "home"} else "living_room"


def is_cancel_text(text: str) -> bool:
    """取消表述是封闭集合（确认/取消词表属于交互协议，不是场景决策）。"""
    return bool(_CANCEL_WORDS.search(text))
