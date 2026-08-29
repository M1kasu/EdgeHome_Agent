"""Counterfactual Planner (model-driven): candidates -> predicted futures -> scored choice.

正式模式（LLM 可用）：候选计划全部由模型在 Capability Registry 与偏好记忆的
约束内生成（3~5 个、多样化），模型必须依据当前真实状态推理而不是套用场景；
确定性代码负责 schema 校验、能力/安全校验（Policy Guard）、反事实预测与
多目标评分。

fallback 模式（LLM 不可用）：确定性基线候选接管，planner_mode 如实标注，
UI 不冒充模型规划。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spacebutler.agent.capabilities import DeviceCapability
from spacebutler.agent.counterfactual import CounterfactualPredictor
from spacebutler.agent.models import (
    CandidatePlan,
    CompiledGoal,
    LlmContribution,
    PreferenceEstimate,
    RoomState,
)
from spacebutler.agent.policy_guard import PolicyGuard
from spacebutler.agent.scorer import PlanScorer
from spacebutler.llm.client import LlmClient

_MAX_CANDIDATES = 5

_PLANNER_SYSTEM_PROMPT = (
    "你是家庭智能体的规划器。你不是在执行固定场景——没有任何'看电影必须关窗帘、"
    "热必须开空调'的预设。你必须根据当前用户目标、家庭真实状态、设备能力、"
    "近期事件与用户偏好，重新推理出多个合理的候选计划。只输出一个 JSON 对象：\n"
    '{"candidates": [{"summary": "一句话中文说明该方案的思路", "actions": ['
    '{"device_id": "...", "capability": "能力名", "value": 数值或字符串, "mode": "cool|heat|null"}]}]}\n'
    "要求：\n"
    "1. 生成 3 到 5 个思路明显不同的候选（例如不同设备组合、不同激进程度）。\n"
    "2. 只能使用给定设备与能力；目标约束已满足的设备不要输出动作。\n"
    "3. 数值用数字而不是字符串；没有把握的动作宁可不输出。\n"
    "4. 用户偏好里学到的亮度偏好应反映在至少一个候选中。\n"
    "5. 若目标已经全部满足，输出 {\"candidates\": []}。"
)


@dataclass
class PlanningOutcome:
    planner_mode: str  # llm | fallback | deterministic
    no_action: bool
    reason: str
    candidates: list[CandidatePlan] = field(default_factory=list)
    selected: CandidatePlan | None = None
    llm_metrics: LlmContribution = field(default_factory=LlmContribution)

    def to_dict(self) -> dict[str, object]:
        return {
            "planner_mode": self.planner_mode,
            "no_action": self.no_action,
            "reason": self.reason,
            "candidates": [item.to_dict() for item in self.candidates],
            "selected": self.selected.to_dict() if self.selected is not None else None,
            "llm_metrics": self.llm_metrics.to_dict(),
        }


class CounterfactualPlanner:
    def __init__(
        self,
        predictor: CounterfactualPredictor | None = None,
        scorer: PlanScorer | None = None,
    ) -> None:
        self._predictor = predictor or CounterfactualPredictor()
        self._scorer = scorer or PlanScorer()
        self.contribution = LlmContribution()

    def plan(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preferences: PreferenceEstimate | None = None,
        llm_client: LlmClient | None = None,
        recent_events: list[dict[str, object]] | None = None,
    ) -> PlanningOutcome:
        guard = PolicyGuard(registry)
        metrics = LlmContribution(
            goal_compiled_by_llm=self.contribution.goal_compiled_by_llm,
            goal_evaluation_by_llm=self.contribution.goal_evaluation_by_llm,
            replan_decision_by_llm=self.contribution.replan_decision_by_llm,
        )
        current = self._constraint_values(state)

        if all(constraint.satisfied_by(current.get(constraint.metric)) for constraint in goal.constraints):
            return PlanningOutcome(
                planner_mode="deterministic",
                no_action=True,
                reason="Goal already satisfied：当前状态已满足全部目标约束，无需动作。",
                llm_metrics=metrics,
            )

        raw_candidates: list[tuple[list[dict[str, object]], str, str, str]] = []
        planner_mode = "fallback"
        if llm_client is not None and llm_client.available():
            llm_raw = self._llm_candidates(goal, state, registry, preferences, recent_events, llm_client)
            generated = len(llm_raw)
            valid = 0
            rejected = 0
            for index, (summary, actions) in enumerate(llm_raw, start=1):
                problems = guard.issues(actions)
                if problems:
                    rejected += 1
                    raw_candidates.append((actions, f"候选 L{index} · 模型提出", summary or "", "llm"))
                    # 非法候选不入最终候选空间，仅计入指标。
                    continue
                valid += 1
                raw_candidates.append((actions, f"候选 L{index} · 模型提出", summary or "", "llm"))
            metrics.llm_candidates_generated = generated
            metrics.llm_candidates_valid = valid
            metrics.llm_candidates_rejected = rejected
            if valid > 0:
                planner_mode = "llm"

        if planner_mode == "fallback":
            # 仅在模型不可用或模型候选全部非法时，确定性基线接管（如实标注 fallback）。
            for actions, label, summary in self._baseline_candidates(goal, state, registry, preferences):
                raw_candidates.append((actions, label, summary, "fallback"))

        scored: list[CandidatePlan] = []
        for index, (actions, label, summary, source) in enumerate(raw_candidates, start=1):
            problems = guard.issues(actions)
            if problems:
                scored.append(
                    self._score_plan(
                        f"c{index}", f"{label}（被 Policy Guard 拒绝：{'；'.join(problems[:2])}）",
                        [], self._predictor.predict([], state), goal, state, preferences,
                        summary, source,
                    )
                )
                continue
            enriched = [self._enrich(action, registry) for action in actions]
            predicted = self._predictor.predict(enriched, state)
            scored.append(
                self._score_plan(f"c{index}", label, enriched, predicted, goal, state, preferences, summary, source)
            )

        actionable = [item for item in scored if item.actions]
        if not actionable:
            return PlanningOutcome(
                planner_mode=planner_mode,
                no_action=True,
                reason="所有候选均被 Policy Guard 拒绝，未执行任何动作。",
                candidates=scored,
                llm_metrics=metrics,
            )
        best = max(actionable, key=lambda item: (item.scores.total, -len(item.actions)))
        best.selected = True
        if best.source == "llm":
            metrics.selected_llm_candidate = 1
        self.contribution = metrics
        return PlanningOutcome(
            planner_mode=planner_mode,
            no_action=False,
            reason=self._selection_reason(best, scored),
            candidates=scored,
            selected=best,
            llm_metrics=metrics,
        )

    # ---- model-driven candidate generation ---------------------------
    def _llm_candidates(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preferences: PreferenceEstimate | None,
        recent_events: list[dict[str, object]] | None,
        llm_client: LlmClient,
    ) -> list[tuple[str, list[dict[str, object]]]]:
        capabilities = {key: value.to_dict() for key, value in registry.items() if not value.readonly}
        preference_view = (
            {"metric": preferences.metric, "preferred_mean": preferences.mean, "samples": preferences.count}
            if preferences and preferences.count >= 2
            else None
        )
        user_prompt = (
            f"用户目标：{goal.title}\n"
            f"目标约束（区间）：{[item.to_dict() for item in goal.constraints]}\n"
            f"模型对目标的理解：{goal.semantic_view}\n"
            f"当前家庭状态：温度 {state.temperature}°C，灯亮度 {state.brightness_pct}%，"
            f"窗帘 {state.curtain_position}%，空调 {state.ac_mode}"
            f"（设定 {state.ac_target}°C），有人={state.presence}\n"
            f"可用设备与能力：{capabilities}\n"
            f"用户历史偏好：{preference_view}\n"
            f"近期事件：{(recent_events or [])[-5:]}\n"
            "请根据以上真实上下文生成 3~5 个思路不同的候选计划。"
        )
        raw = llm_client.chat_json(_PLANNER_SYSTEM_PROMPT, user_prompt)
        if not isinstance(raw, dict) or not isinstance(raw.get("candidates"), list):
            return []
        results: list[tuple[str, list[dict[str, object]]]] = []
        for item in raw["candidates"][:_MAX_CANDIDATES]:
            if not isinstance(item, dict):
                continue
            actions = []
            for entry in item.get("actions", [])[:8]:
                if not isinstance(entry, dict):
                    continue
                value = entry.get("value")
                if isinstance(value, str):
                    try:
                        value = float(value.strip())
                    except ValueError:
                        pass
                actions.append(
                    {
                        "device_id": str(entry.get("device_id", "")),
                        "capability": str(entry.get("capability", "")),
                        "value": value,
                        "mode": entry.get("mode"),
                    }
                )
            if actions:
                results.append((str(item.get("summary", "")), actions))
        return results

    # ---- fallback baseline（仅 LLM 不可用时） -------------------------
    def _baseline_candidates(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preferences: PreferenceEstimate | None,
    ) -> list[tuple[list[dict[str, object]], str, str]]:
        actions_center = self._actions_for(goal, state, registry, preference_offset=0.0)
        actions_efficient = self._actions_for(goal, state, registry, efficiency_bias=1.0)
        actions_comfort = self._actions_for(goal, state, registry, comfort_bias=1.0)
        candidates: list[tuple[list[dict[str, object]], str, str]] = [
            (actions_center, "候选 A · 目标居中（确定性回退）", "按目标区间中点设定"),
        ]
        preference_brightness = preferences.mean if (preferences and preferences.count >= 2) else None
        if preference_brightness is not None:
            actions_pref = self._actions_for(goal, state, registry, preference_offset=preference_brightness)
            if actions_pref != actions_center:
                candidates.append((actions_pref, "候选 D · 偏好匹配（确定性回退）", "使用学到的亮度偏好"))
        if actions_efficient and actions_efficient != actions_center:
            candidates.append((actions_efficient, "候选 B · 保守节能（确定性回退）", "降低能耗"))
        if actions_comfort and actions_comfort != actions_center:
            candidates.append((actions_comfort, "候选 C · 强舒适（确定性回退）", "优先体感"))
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
        actions: list[dict[str, object]] = []
        values = self._constraint_values(state)
        for constraint in goal.constraints:
            device, capability = self._device_for_metric(registry, constraint.metric, goal.room)
            if device is None:
                continue
            current = values.get(constraint.metric)
            if constraint.satisfied_by(current):
                continue
            if constraint.metric == "brightness":
                target = self._constraint_point(constraint, preference_offset, efficiency_bias, comfort_bias)
                actions.append(self._action(device, "set_brightness", target))
            elif constraint.metric == "curtain_position":
                target = constraint.upper if constraint.upper is not None else 30.0
                actions.append(self._action(device, "set_position", target))
            elif constraint.metric == "temperature":
                target = self._temperature_point(constraint, efficiency_bias, comfort_bias)
                mode = None if state.ac_mode != "off" else "cool"
                actions.append(self._action(device, "set_temperature", target, mode=mode))
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

    def _score_plan(
        self,
        candidate_id: str,
        label: str,
        actions: list[dict[str, object]],
        predicted: object,
        goal: CompiledGoal,
        state: RoomState,
        preferences: PreferenceEstimate | None,
        summary: str,
        source: str,
    ) -> CandidatePlan:
        plan = self._scorer.score(candidate_id, label, actions, predicted, goal, state, preferences, rationale=summary)
        plan.source = source
        plan.summary = summary
        return plan

    def _constraint_values(self, state: RoomState) -> dict[str, float | None]:
        """规划视角：空调已按目标制冷时温度约束视为正在达成（Demo 2 语义）。"""
        temperature = state.temperature
        if state.ac_mode == "cool" and isinstance(state.ac_target, (int, float)):
            temperature = float(state.ac_target)
        return {
            "brightness": state.brightness_pct,
            "temperature": temperature,
            "curtain_position": state.curtain_position,
        }

    def _selection_reason(self, best: CandidatePlan, all_candidates: list[CandidatePlan]) -> str:
        scores = best.scores
        source_text = "模型生成的候选" if best.source == "llm" else "确定性回退候选"
        reason = (
            f"选中 {best.label}（{source_text}）：目标满足 {scores.goal_satisfaction:.2f}、"
            f"舒适 {scores.comfort:.2f}、偏好匹配 {scores.preference_match:.2f}、"
            f"能耗成本 {scores.energy_cost:.2f}、动作数 {len(best.actions)}。"
        )
        others = [item for item in all_candidates if item is not best and item.actions]
        if others:
            runner = max(others, key=lambda item: item.scores.total)
            gap = best.scores.total - runner.scores.total
            reason += f" 次优 {runner.label} 综合分低 {gap:.2f}。"
        return reason
