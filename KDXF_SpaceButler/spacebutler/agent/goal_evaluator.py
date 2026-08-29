"""LLM Goal Evaluator: semantic judgement of whether the goal still holds.

数值约束违反检测保留为 signal 与 fallback；最终的语义评估交给模型——
结合目标、当前状态、趋势、上一个计划、观测结果与人工纠正判断
SATISFIED / DEGRADED / ACTIVE 与是否需要重规划。Runtime 仍然用
cooldown / debounce / max-retries 约束重规划频率（guardrail）。
"""

from __future__ import annotations

from dataclasses import dataclass

from spacebutler.agent.models import CompiledGoal, GoalStatus, RoomState
from spacebutler.llm.client import LlmClient

_EVALUATOR_SYSTEM_PROMPT = (
    "你是家庭智能体的目标评估器。根据目标、当前真实状态、趋势、上一个计划的"
    "执行结果和用户的人工纠正，语义判断目标当前是否仍被满足、是否需要重新规划。"
    "只输出一个 JSON 对象：\n"
    '{"status": "SATISFIED|DEGRADED|ACTIVE", "need_replan": true/false, '
    '"reason_summary": "一句话中文理由"}\n'
    "判断要点：\n"
    "- 设备正在朝目标方向改善（例如空调制冷中、温度在下降）不是偏离；\n"
    "- 人工纠正表达的用户偏好优先于原计划；\n"
    "- 目标已满足或正在自然满足时输出 NO ACTION 语义（need_replan=false）。"
)


@dataclass
class GoalEvaluation:
    status: GoalStatus
    need_replan: bool
    reason: str
    by_llm: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "need_replan": self.need_replan,
            "reason": self.reason,
            "by_llm": self.by_llm,
        }


class GoalEvaluator:
    def __init__(self) -> None:
        self.last_by_llm = False

    def evaluate(
        self,
        goal: CompiledGoal,
        state: RoomState,
        previous_summary: str,
        recent_overrides: list[str],
        llm_client: LlmClient | None,
    ) -> GoalEvaluation:
        if llm_client is not None and llm_client.available():
            raw = llm_client.chat_json(
                _EVALUATOR_SYSTEM_PROMPT,
                (
                    f"目标：{goal.title}；约束：{[item.to_dict() for item in goal.constraints]}\n"
                    f"当前状态：温度 {state.temperature}°C，灯 {state.brightness_pct}%，"
                    f"窗帘 {state.curtain_position}%，空调 {state.ac_mode}（设定 {state.ac_target}°C），"
                    f"有人={state.presence}\n"
                    f"上一个计划与结果：{previous_summary}\n"
                    f"近期人工纠正：{recent_overrides}\n"
                    "请语义评估目标状态。"
                ),
            )
            if isinstance(raw, dict) and str(raw.get("status", "")).upper() in {"SATISFIED", "DEGRADED", "ACTIVE"}:
                status = GoalStatus(str(raw["status"]).upper())
                self.last_by_llm = True
                return GoalEvaluation(
                    status=status,
                    need_replan=bool(raw.get("need_replan")),
                    reason=str(raw.get("reason_summary", "")),
                    by_llm=True,
                )
        self.last_by_llm = False
        return self._numeric_fallback(goal, state)

    def _numeric_fallback(self, goal: CompiledGoal, state: RoomState) -> GoalEvaluation:
        values = {
            "brightness": state.brightness_pct,
            "temperature": state.temperature,
            "curtain_position": state.curtain_position,
        }
        unmet = [item for item in goal.constraints if not item.satisfied_by(values.get(item.metric))]
        if not unmet:
            return GoalEvaluation(GoalStatus.SATISFIED, False, "全部约束数值满足（确定性回退评估）。", False)
        if any(item.distance(values.get(item.metric)) > 5.0 for item in unmet):
            return GoalEvaluation(GoalStatus.DEGRADED, True, "存在明显未满足约束（确定性回退评估）。", False)
        return GoalEvaluation(GoalStatus.ACTIVE, False, "轻微偏离，容忍范围内（确定性回退评估）。", False)
