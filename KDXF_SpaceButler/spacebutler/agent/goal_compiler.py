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
    "你是家庭智能体的目标理解器。判断用户这句话是否表达了一个可执行的家居环境目标，"
    "并把它编译为语义目标 JSON。只允许输出一个 JSON 对象，不要解释。格式：\n"
    '{"is_home_goal": true/false, "location": "房间id或null", '
    '"intent_summary": "一句话中文概括用户想要什么", '
    '"activity": "简短活动标签（自由命名，如 watch_content/rest/work/relax）", '
    '"constraints": [{"dimension": "thermal|lighting|glare|energy", '
    '"direction": "cooler|warmer|dimmer|brighter|reduce|conserve|null", '
    '"strength": "slight|moderate|strong", '
    '"target_range": [下界, 上界] 或 null, '
    '"reason": "模型给出该区间/方向的简短理由"}]}\n'
    "规则：\n"
    "1. 不是家居环境目标时输出 {\"is_home_goal\": false, \"constraints\": []}。\n"
    "2. 约束表达语义偏好而不是设备指令；数值区间由你根据语义推理给出"
    "（例如'别太亮'通常意味着保留可见照明但明显变暗），并填写 reason。\n"
    "3. 不要假设特定活动必须操作特定设备；设备选择由下游规划器完成。\n"
    "4. energy 维度表达节能意向，不设数值区间（target_range 填 null）。"
)

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
            raw = llm_client.chat_json(_GOAL_SYSTEM_PROMPT, text)
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
        if not isinstance(raw, dict) or raw.get("is_home_goal") is not True:
            return None
        constraints_raw = raw.get("constraints")
        if not isinstance(constraints_raw, list):
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
        """模型只给了方向没给数值时，从当前环境推导宽区间（数学推导，非场景答案）。

        需要 environment.current_state() 的温度/亮度作锚点，由调用方注入。
        """
        anchor = self._state_anchor
        if direction in {"cooler", "dimmer", "reduce"}:
            if anchor is None:
                return None, None
            return (max(anchor - 10, 1), max(anchor - 1, 2))
        if direction in {"warmer", "brighter"}:
            if anchor is None:
                return None, None
            return (min(anchor + 1, 99), min(anchor + 10, 100))
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
