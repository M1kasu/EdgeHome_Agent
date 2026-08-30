"""Deterministic multi-objective plan scorer.

"LLM 负责提出可行候选，确定性多目标评分选择执行计划。"
权重放在模块常量里，答辩时可解释、可调整。
"""

from __future__ import annotations

from spacebutler.agent.models import CandidatePlan, CompiledGoal, PreferenceEstimate, PredictedState, RoomState
from spacebutler.agent.preference_memory import preference_is_eligible

WEIGHTS = {
    "goal_satisfaction": 0.35,
    "comfort": 0.20,
    "preference": 0.20,
    "safety": 0.15,
    "energy": 0.05,
    "action": 0.05,
}

_AC_RATED_W = 1050.0
_LIGHT_MAX_W = 12.0


class PlanScorer:
    def score(
        self,
        candidate_id: str,
        label: str,
        actions: list[dict[str, object]],
        predicted: PredictedState,
        goal: CompiledGoal,
        state: RoomState,
        preference: PreferenceEstimate | None,
        rationale: str = "",
    ) -> CandidatePlan:
        goal_satisfaction = self._goal_satisfaction(predicted, goal)
        comfort = self._comfort(predicted)
        preference_match = self._preference_match(predicted, preference)
        safety = self._safety(predicted, state)
        energy_cost = self._energy_cost(predicted)
        action_cost = min(1.0, len(actions) / 4.0)
        total = (
            WEIGHTS["goal_satisfaction"] * goal_satisfaction
            + WEIGHTS["comfort"] * comfort
            + WEIGHTS["preference"] * preference_match
            + WEIGHTS["safety"] * safety
            - WEIGHTS["energy"] * energy_cost
            - WEIGHTS["action"] * action_cost
        )
        scores = {
            "goal_satisfaction": goal_satisfaction,
            "comfort": comfort,
            "preference_match": preference_match,
            "safety": safety,
            "energy_cost": energy_cost,
            "action_cost": action_cost,
            "total": total,
        }
        from spacebutler.agent.models import PlanScores, PlannedAction  # local import: avoid cycle

        planned = [
            PlannedAction(
                action_id=f"{candidate_id}_a{index}",
                device_id=str(item.get("device_id", "")),
                device_name=str(item.get("device_name", "")),
                entity_id=str(item.get("entity_id", "")),
                capability=str(item.get("capability", "")),
                value=item.get("value"),
                mode=str(item["mode"]) if item.get("mode") else None,
            )
            for index, item in enumerate(actions, start=1)
            if isinstance(item, dict)
        ]
        return CandidatePlan(
            candidate_id=candidate_id,
            label=label,
            actions=planned,
            predicted=predicted,
            scores=PlanScores(**scores),
            rationale=rationale,
        )

    def _goal_satisfaction(self, predicted: PredictedState, goal: CompiledGoal) -> float:
        values = {
            "brightness": predicted.brightness_pct,
            "temperature": predicted.temperature,
            "curtain_position": predicted.curtain_position,
        }
        if not goal.constraints:
            return 0.5
        total = 0.0
        for constraint in goal.constraints:
            value = values.get(constraint.metric)
            distance = constraint.distance(value)
            # 满足=1.0；每偏离 1 单位衰减，偏离 25 单位归零。
            total += max(0.0, 1.0 - distance / 25.0)
        return total / len(goal.constraints)

    def _comfort(self, predicted: PredictedState) -> float:
        temperature = predicted.temperature
        thermal = _gauss(temperature, mu=26.0, sigma=2.0)
        if predicted.brightness_pct is None:
            return round(thermal, 4)
        visual = _gauss(predicted.brightness_pct, mu=30.0, sigma=18.0)
        return round((thermal + visual) / 2.0, 4)

    def _preference_match(
        self,
        predicted: PredictedState,
        preference: PreferenceEstimate | None,
    ) -> float:
        if not preference_is_eligible(preference):
            return 0.5  # 无偏好证据时中性，不奖不罚。
        assert preference is not None
        brightness = predicted.brightness_pct
        if brightness is None:
            return 0.5
        distance = abs(brightness - preference.mean)
        raw_match = max(0.0, 1.0 - distance / 30.0)
        # 低置信偏好向中性分数收缩，避免刚过门槛的证据与稳定偏好同权。
        return round(0.5 + (raw_match - 0.5) * preference.confidence, 4)

    def _safety(self, predicted: PredictedState, state: RoomState) -> float:
        # 无人在还开制冷是能量浪费（安全扣分）；其他组合安全。
        if state.presence is False and predicted.ac_mode != "off":
            return 0.4
        return 1.0

    def _energy_cost(self, predicted: PredictedState) -> float:
        cost = 0.0
        if predicted.ac_mode != "off":
            cost += 0.9 if predicted.ac_mode == "cool" else 1.0
        if predicted.brightness_pct is not None:
            cost += _LIGHT_MAX_W * predicted.brightness_pct / 255.0 / _AC_RATED_W
        return round(min(1.0, cost), 4)


def _gauss(value: float, mu: float, sigma: float) -> float:
    return pow(2.718281828459045, -((value - mu) ** 2) / (2 * sigma * sigma))
