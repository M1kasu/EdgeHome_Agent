"""Goal Compiler: natural language -> structured goal constraints.

自然语言表达的是目标而不是设备指令。"别太亮" 编译成亮度区间（如 [20, 45]）
而不是 brightness == 20；"不要完全黑" 编译成下界约束。LLM 负责语义理解，
确定性代码负责 schema/范围校验；LLM 不可用或输出非法时使用确定性模板。
"""

from __future__ import annotations

import re
import uuid

from spacebutler.agent.models import CompiledGoal, GoalConstraint, now
from spacebutler.llm.client import LlmClient

_METRIC_RULES: dict[str, tuple[float, float]] = {
    # metric -> (hard_lower, hard_upper) applied to every compiled constraint.
    "brightness": (1, 100),
    "temperature": (16, 30),
    "curtain_position": (0, 100),
}

_ACTIVITY_WORDS = re.compile(r"看(?:会|场|个)?电影|观影|看片|追剧|看剧")
_THERMAL_WORDS = re.compile(r"有点热|太热|热|有点冷|太冷|凉快")
_CANCEL_WORDS = re.compile(r"不看了|不看电影|取消.{0,4}(任务|目标)|我要去睡觉|睡觉了")

_SYSTEM_PROMPT = (
    "你是家庭智能体的目标编译器。把用户的自然语言意图编译为结构化目标约束 JSON。"
    "只允许输出一个 JSON 对象，不要解释。格式："
    '{"activity": "watch_movie 或 thermal_comfort", "title": "简短中文标题", '
    '"constraints": [{"metric": "brightness|temperature|curtain_position", '
    '"lower": 数字或null, "upper": 数字或null, "target": 数字或null}]。'
    "metric 只能是 brightness(亮度百分比)、temperature(摄氏度)、curtain_position(窗帘开合百分比)。"
    "约束必须表达区间而不是单点，用户说'别太亮'给 [20,45] 这样的上界区间，"
    "'不要完全黑'给不低于 10 的下界。"
)


def is_goal_text(text: str) -> bool:
    return bool(_ACTIVITY_WORDS.search(text) or _THERMAL_WORDS.search(text))


def is_cancel_text(text: str) -> bool:
    return bool(_CANCEL_WORDS.search(text))


class GoalCompiler:
    def compile(
        self,
        text: str,
        llm_client: LlmClient | None = None,
    ) -> tuple[CompiledGoal, str]:
        """Return (goal, planner_mode). planner_mode is 'llm' or 'fallback'."""
        goal = None
        mode = "fallback"
        if llm_client is not None and llm_client.available():
            raw = llm_client.chat_json(_SYSTEM_PROMPT, text)
            goal = self._goal_from_llm(raw)
            if goal is not None:
                mode = "llm"
        if goal is None:
            goal = self._goal_from_template(text)
            mode = "fallback"
        return goal, mode

    def _goal_from_llm(self, raw: dict | None) -> CompiledGoal | None:
        if not isinstance(raw, dict):
            return None
        activity = str(raw.get("activity", "")).strip()
        constraints_raw = raw.get("constraints")
        if not activity or not isinstance(constraints_raw, list) or not constraints_raw:
            return None
        constraints: list[GoalConstraint] = []
        for item in constraints_raw[:6]:
            if not isinstance(item, dict):
                continue
            metric = str(item.get("metric", "")).strip()
            if metric not in _METRIC_RULES:
                continue
            lower, upper = self._clamp_metric(metric, item.get("lower"), item.get("upper"))
            target = self._clamp_value(metric, item.get("target"))
            if lower is None and upper is None and target is None:
                continue
            constraints.append(GoalConstraint(metric=metric, lower=lower, upper=upper, target=target))
        if not constraints:
            return None
        title = str(raw.get("title", "")).strip() or activity
        return CompiledGoal(
            goal_id=f"goal_{uuid.uuid4().hex[:10]}",
            activity=activity,
            room="living_room",
            title=title[:40],
            constraints=constraints,
        )

    def _goal_from_template(self, text: str) -> CompiledGoal:
        constraints: list[GoalConstraint] = []
        if _ACTIVITY_WORDS.search(text):
            # 观影：亮度区间 + 避免全黑 + 窗帘遮光 + 热舒适目标。
            if re.search(r"完全黑|全黑|太暗|别太暗|不要暗", text):
                constraints.append(GoalConstraint(metric="brightness", lower=10, upper=60))
            elif re.search(r"别太亮|不要太亮|暗一点|调暗", text) or "灯" in text:
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
        activity = "watch_movie" if _ACTIVITY_WORDS.search(text) else "thermal_comfort"
        title = "客厅舒适观影" if activity == "watch_movie" else "客厅热舒适"
        return CompiledGoal(
            goal_id=f"goal_{uuid.uuid4().hex[:10]}",
            activity=activity,
            room="living_room",
            title=title,
            constraints=constraints,
            expires_at=now() + 30 * 60,
        )

    def _clamp_metric(self, metric: str, lower: object, upper: object) -> tuple[float | None, float | None]:
        hard_lower, hard_upper = _METRIC_RULES[metric]
        return self._clamp_value(metric, lower, hard_lower), self._clamp_value(metric, upper, hard_upper)

    def _clamp_value(self, metric: str, value: object, hard_lower: float | None = None) -> float | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        clamped = float(value)
        if hard_lower is not None:
            clamped = max(clamped, hard_lower)
        clamped = min(max(clamped, _METRIC_RULES[metric][0]), _METRIC_RULES[metric][1])
        return round(clamped, 1)
