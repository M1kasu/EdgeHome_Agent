"""Counterfactual Planner: candidates -> predicted futures -> scored choice.

流程（与交付方案第 24 节一致）：
  用户目标（CompiledGoal）+ World Model 当前状态 + Capability Registry
    -> 候选动作集（确定性基线若干 + 可选 LLM 创意候选）
    -> 逐个反事实预测未来状态
    -> 确定性多目标评分
    -> 选出最优计划；目标已满足时输出 NO ACTION。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spacebutler.agent.capabilities import DeviceCapability
from spacebutler.agent.counterfactual import CounterfactualPredictor
from spacebutler.agent.models import CompiledGoal, PreferenceEstimate, RoomState
from spacebutler.agent.policy_guard import PolicyGuard
from spacebutler.agent.scorer import PlanScorer
from spacebutler.llm.client import LlmClient

_CANDIDATE_SYSTEM_PROMPT = (
    "你是家庭智能体的候选计划生成器。只能输出一个 JSON 对象："
    '{"actions": [{"device_id": "...", "capability": "set_brightness|set_position|'
    'set_temperature|set_hvac_mode|turn_off", "value": 数字或字符串, "mode": "cool|heat|null"}]}。'
    "只允许使用给定的设备与能力，不要发明设备。目标已满足的动作不要输出。"
)


def _coerce_number(value: object) -> object:
    """LLM 常把数字输出成字符串；尽力转换，保留原值交给 Policy Guard 判断。"""
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return value
    return value


@dataclass
class PlanningOutcome:
    planner_mode: str  # llm | fallback
    no_action: bool
    reason: str
    candidates: list[object] = field(default_factory=list)
    selected: object | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "planner_mode": self.planner_mode,
            "no_action": self.no_action,
            "reason": self.reason,
            "candidates": [item.to_dict() for item in self.candidates if hasattr(item, "to_dict")],
            "selected": self.selected.to_dict() if self.selected is not None and hasattr(self.selected, "to_dict") else None,
        }


class CounterfactualPlanner:
    def __init__(
        self,
        predictor: CounterfactualPredictor | None = None,
        scorer: PlanScorer | None = None,
    ) -> None:
        self._predictor = predictor or CounterfactualPredictor()
        self._scorer = scorer or PlanScorer()

    def plan(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preferences: PreferenceEstimate | None = None,
        llm_client: LlmClient | None = None,
    ) -> PlanningOutcome:
        guard = PolicyGuard(registry)
        current = self._constraint_values(state)

        # 1. NO ACTION：所有约束已被现实满足。
        if all(constraint.satisfied_by(current.get(constraint.metric)) for constraint in goal.constraints):
            return PlanningOutcome(
                planner_mode="deterministic",
                no_action=True,
                reason="Goal already satisfied：当前状态已满足全部目标约束，无需动作。",
            )

        raw_candidates = self._baseline_candidates(goal, state, registry, preferences)
        planner_mode = "fallback"
        llm_actions, llm_label = self._llm_candidate(goal, state, registry, llm_client)
        if llm_actions is not None:
            raw_candidates.append((llm_actions, llm_label, "由本地大模型在能力白名单内提出"))
            planner_mode = "llm"

        scored: list[object] = []
        for index, (actions, label, rationale) in enumerate(raw_candidates):
            problems = guard.issues(actions)
            if problems:
                # Policy Guard 拒绝的候选保留展示（透明），但不参与选优。
                scored.append(
                    self._scorer.score(
                        f"c{index + 1}",
                        f"{label}（被 Policy Guard 拒绝：{'；'.join(problems[:2])}）",
                        [],
                        self._predictor.predict([], state),
                        goal,
                        state,
                        preferences,
                        rationale=rationale,
                    )
                )
                continue
            # 补全 device_name/entity_id 供执行与展示。
            enriched = [self._enrich(action, registry) for action in actions]
            predicted = self._predictor.predict(enriched, state)
            scored.append(
                self._scorer.score(
                    f"c{index + 1}",
                    label,
                    enriched,
                    predicted,
                    goal,
                    state,
                    preferences,
                    rationale=rationale,
                )
            )

        actionable = [item for item in scored if item.actions]
        if not actionable:
            return PlanningOutcome(
                planner_mode=planner_mode,
                no_action=True,
                reason="所有候选均被 Policy Guard 拒绝，未执行任何动作。",
                candidates=scored,
            )
        best = max(actionable, key=lambda item: (item.scores.total, -len(item.actions)))
        best.selected = True
        return PlanningOutcome(
            planner_mode=planner_mode,
            no_action=False,
            reason=self._selection_reason(best, scored),
            candidates=scored,
            selected=best,
        )

    # ---- candidate generation ---------------------------------------
    def _baseline_candidates(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preferences: PreferenceEstimate | None,
    ) -> list[tuple[list[dict[str, object]], str, str]]:
        actions_center = self._actions_for(goal, state, registry, preference_offset=0.0)
        actions_efficient = self._actions_for(goal, state, registry, efficiency_bias=1.0, preference_offset=0.0)
        actions_comfort = self._actions_for(goal, state, registry, comfort_bias=1.0, preference_offset=0.0)
        candidates: list[tuple[list[dict[str, object]], str, str]] = [
            (actions_center, "候选 A · 目标居中", "按目标区间中点设定，平衡舒适与动作成本"),
        ]
        preference_brightness = preferences.mean if (preferences and preferences.count >= 2) else None
        if actions_efficient != actions_center:
            candidates.append((actions_efficient, "候选 B · 保守节能", "亮度取区间上界、温度目标上调，降低能耗与动作数量"))
        if actions_comfort != actions_center:
            candidates.append((actions_comfort, "候选 C · 强舒适", "亮度取区间下界、温度目标下调，优先体感"))
        if preference_brightness is not None:
            actions_pref = self._actions_for(goal, state, registry, preference_offset=preference_brightness)
            if actions_pref != actions_center:
                candidates.append(
                    (
                        actions_pref,
                        f"候选 D · 偏好匹配（{preference_brightness:.0f}%）",
                        "使用从人工纠正中学到的亮度偏好",
                    )
                )
        return [item for item in candidates if item[0]]

    def _actions_for(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preference_offset: float | None = None,
        efficiency_bias: float = 0.0,
        comfort_bias: float = 0.0,
    ) -> list[dict[str, object]]:
        """Build minimal actions that steer current state into the goal bounds."""
        actions: list[dict[str, object]] = []
        values = self._constraint_values(state)
        for constraint in goal.constraints:
            device, capability = self._device_for_metric(registry, constraint.metric, goal.room)
            if device is None:
                continue
            current = values.get(constraint.metric)
            if constraint.satisfied_by(current):
                continue  # 状态驱动的最小动作集：已满足的约束不产生动作。
            if constraint.metric == "brightness":
                target = self._constraint_point(constraint, preference_offset, efficiency_bias, comfort_bias)
                actions.append(self._action(device, "set_brightness", target))
            elif constraint.metric == "curtain_position":
                target = constraint.upper if constraint.upper is not None else 30.0
                actions.append(self._action(device, "set_position", target))
            elif constraint.metric == "temperature":
                target = self._temperature_point(constraint, efficiency_bias, comfort_bias)
                if state.ac_mode == "off":
                    actions.append(self._action(device, "set_temperature", target, mode="cool"))
                else:
                    actions.append(self._action(device, "set_temperature", target, mode=None))
        return actions

    def _constraint_point(
        self,
        constraint: object,
        preference_offset: float | None,
        efficiency_bias: float,
        comfort_bias: float,
    ) -> float:
        lower = constraint.lower if constraint.lower is not None else 20.0
        upper = constraint.upper if constraint.upper is not None else 45.0
        if preference_offset:
            return round(min(max(preference_offset, lower), upper), 1)
        if efficiency_bias:
            return round(upper - 2.0, 1)
        if comfort_bias:
            return round(lower + 2.0, 1)
        return round((lower + upper) / 2.0, 1)

    def _temperature_point(
        self,
        constraint: object,
        efficiency_bias: float,
        comfort_bias: float,
    ) -> float:
        target = constraint.target if constraint.target is not None else 26.0
        return round(target + efficiency_bias - comfort_bias, 1)

    def _device_for_metric(
        self,
        registry: dict[str, DeviceCapability],
        metric: str,
        room: str = "",
    ) -> tuple[DeviceCapability, str] | tuple[None, None]:
        wanted = {
            "brightness": ("light", "set_brightness"),
            "curtain_position": ("cover", "set_position"),
            "temperature": ("climate", "set_temperature"),
        }.get(metric)
        if wanted is None:
            return None, None
        domain, capability = wanted
        matches = [
            device
            for device in registry.values()
            if device.domain == domain and capability in device.actions and not device.readonly
        ]
        if not matches:
            return None, None
        # 目标房间优先（观影必须用客厅主灯，而不是卧室阅读灯）。
        for device in matches:
            if device.room == room:
                return device, capability
        return matches[0], capability

    def _action(
        self,
        device: DeviceCapability,
        capability: str,
        value: float | str,
        mode: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "device_id": device.device_id,
            "device_name": device.name,
            "entity_id": device.entity_id,
            "capability": capability,
            "value": value,
        }
        if mode:
            payload["mode"] = mode
        return payload

    def _enrich(self, action: dict[str, object], registry: dict[str, DeviceCapability]) -> dict[str, object]:
        device = registry.get(str(action.get("device_id")))
        if device is not None:
            action.setdefault("device_name", device.name)
            action.setdefault("entity_id", device.entity_id)
        return action

    def _llm_candidate(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        llm_client: LlmClient | None,
    ) -> tuple[list[dict[str, object]] | None, str]:
        if llm_client is None or not llm_client.available():
            return None, ""
        capabilities = {key: value.to_dict() for key, value in registry.items() if not value.readonly}
        user_prompt = (
            f"用户目标：{goal.title}\n"
            f"目标约束：{[item.to_dict() for item in goal.constraints]}\n"
            f"当前状态：温度 {state.temperature}°C，亮度 {state.brightness_pct}%，"
            f"窗帘 {state.curtain_position}%，空调 {state.ac_mode}，有人={state.presence}\n"
            f"允许的设备与能力：{capabilities}\n"
            "生成一个最能满足目标的候选动作列表（已满足的约束不要输出动作）。"
        )
        raw = llm_client.chat_json(_CANDIDATE_SYSTEM_PROMPT, user_prompt)
        if not isinstance(raw, dict) or not isinstance(raw.get("actions"), list):
            return None, ""
        actions: list[dict[str, object]] = []
        for item in raw["actions"][:8]:
            if not isinstance(item, dict):
                continue
            actions.append(
                {
                    "device_id": str(item.get("device_id", "")),
                    "capability": str(item.get("capability", "")),
                    "value": _coerce_number(item.get("value")),
                    "mode": item.get("mode"),
                }
            )
        if not actions:
            return None, ""
        return actions, "候选 L · 本地模型提出"

    def _constraint_values(self, state: RoomState) -> dict[str, float | None]:
        """规划视角的状态：空调已按目标制冷时，温度约束视为"正在达成"。

        这正是交付方案 Demo 2 的语义（"空调正在向 26°C 制冷，无需重复调整"）：
        新目标规划只看是否还需要动作；运行中的目标评估（GoalManager）仍用
        实际温度来检测真实偏离。
        """
        temperature = state.temperature
        if state.ac_mode == "cool" and isinstance(state.ac_target, (int, float)):
            temperature = float(state.ac_target)
        return {
            "brightness": state.brightness_pct,
            "temperature": temperature,
            "curtain_position": state.curtain_position,
        }

    def _selection_reason(self, best: object, all_candidates: list[object]) -> str:
        scores = best.scores
        reason = (
            f"选中 {best.label}：目标满足 {scores.goal_satisfaction:.2f}、"
            f"舒适 {scores.comfort:.2f}、偏好匹配 {scores.preference_match:.2f}、"
            f"能耗成本 {scores.energy_cost:.2f}、动作数 {len(best.actions)}。"
        )
        others = [item for item in all_candidates if item is not best and item.actions]
        if others:
            runner = max(others, key=lambda item: item.scores.total)
            gap = best.scores.total - runner.scores.total
            reason += f" 次优 {runner.label} 综合分低 {gap:.2f}。"
        return reason
