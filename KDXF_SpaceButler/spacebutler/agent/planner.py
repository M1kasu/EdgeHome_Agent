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
import re

from spacebutler.agent.capabilities import DeviceCapability
from spacebutler.agent.counterfactual import CounterfactualPredictor
from spacebutler.agent.models import (
    CandidatePlan,
    CompiledGoal,
    LlmContribution,
    OverrideSuppression,
    PreferenceEstimate,
    RoomState,
)
from spacebutler.agent.policy_guard import PolicyGuard, classify_issue
from spacebutler.agent.preference_memory import preference_is_eligible
from spacebutler.agent.scorer import PlanScorer
from spacebutler.llm.client import LlmClient

_MAX_CANDIDATES = 5

_PLANNER_SYSTEM_PROMPT = (
    "你是家庭智能体的规划器。没有任何'看电影必须关窗帘、热必须开空调'的预设。"
    "根据当前目标、家庭真实状态、设备能力、近期事件与用户偏好，推理出多个思路不同的候选计划。"
    "只能用给定设备（device_id 必须原样照抄）；capability 必须从该设备的 actions 列表里选；"
    "数值用数字；目标约束已满足的设备不要输出动作；"
    "每个候选必须直接改善至少一个尚未满足的 metric。只能使用输入中给出的 metric→设备能力路由；"
    "与未满足 metric 无关的动作不能代替目标动作。"
    "用户历史偏好应反映在至少一个候选中；目标已全部满足时输出空数组。"
)

def _coerce_number(value: object) -> object:
    """LLM 常把数字输出成 "32"、"32%"、"26°C"；尽力提取数字，失败交 Policy Guard。"""
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group())
    return value


def _normalize_action_value(capability: str, value: object) -> tuple[str, object, str | None]:
    """把模型常见输出风格归一化为内部动作 schema（纯格式/词汇对齐，非业务决策）：
    - 参数对象风格 {"brightness": 0.8} / {"hvac_mode": "off"} -> 标量值
    - turn_on 携带亮度参数 -> 升级为 set_brightness（模型意图是调亮度）
    - 空对象/空占位 -> value=None（调用方丢弃该动作）
    - 0~1 比例亮度 -> 百分比
    返回 (capability, value, extra_mode)。"""
    if isinstance(value, dict):
        brightness = value.get("brightness")
        if isinstance(brightness, (int, float)) and not isinstance(brightness, bool):
            number = float(brightness)
            if 0 < number <= 1.0:
                number = round(number * 100, 1)
            return "set_brightness", number, None
        for key in ("position", "temperature"):
            if key in value and isinstance(value[key], (int, float)) and not isinstance(value[key], bool):
                return capability, float(value[key]), None
        for key in ("hvac_mode", "mode"):
            if key in value and isinstance(value[key], str):
                upgraded = "set_hvac_mode" if capability != "set_temperature" else capability
                return upgraded, value[key], None
        for inner in value.values():
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return capability, float(inner), None
            if isinstance(inner, str):
                return capability, inner, None
        return capability, None, None
    coerced = _coerce_number(value)
    if capability == "turn_on" and isinstance(coerced, (int, float)) and not isinstance(coerced, bool):
        brightness = float(coerced)
        if 0 < brightness <= 1.0:
            brightness = round(brightness * 100, 1)
        return "set_brightness", brightness, None
    return capability, coerced, None


def _normalize_mode(value: object) -> str | None:
    """mode 可能是 "cool" 或嵌套 {"hvac_mode": "off"}；归一化为合法字符串。"""
    if isinstance(value, dict):
        value = value.get("hvac_mode") or value.get("mode")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


_PLANNER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "device_id": {"type": "string"},
                                "capability": {
                                    "type": "string",
                                    "enum": [
                                        "set_brightness",
                                        "set_position",
                                        "set_temperature",
                                        "set_hvac_mode",
                                        "turn_on",
                                        "turn_off",
                                    ],
                                },
                                "value": {"type": ["number", "string"]},
                                "mode": {"type": "string"},
                            },
                            "required": ["device_id", "capability", "value"],
                        },
                    },
                },
                "required": ["actions"],
            },
        }
    },
    "required": ["candidates"],
}


@dataclass
class PlanningOutcome:
    planner_mode: str  # llm | fallback | deterministic
    no_action: bool
    reason: str
    candidates: list[CandidatePlan] = field(default_factory=list)
    selected: CandidatePlan | None = None
    llm_metrics: LlmContribution = field(default_factory=LlmContribution)
    llm_rejections: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "planner_mode": self.planner_mode,
            "no_action": self.no_action,
            "reason": self.reason,
            "candidates": [item.to_dict() for item in self.candidates],
            "selected": self.selected.to_dict() if self.selected is not None else None,
            "llm_metrics": self.llm_metrics.to_dict(),
            "llm_rejections": self.llm_rejections,
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
        override_suppressions: list[OverrideSuppression] | None = None,
    ) -> PlanningOutcome:
        guard = PolicyGuard(registry, override_suppressions)
        metrics = LlmContribution(
            goal_compiled_by_llm=self.contribution.goal_compiled_by_llm,
            goal_evaluation_by_llm=self.contribution.goal_evaluation_by_llm,
            replan_decision_by_llm=self.contribution.replan_decision_by_llm,
        )
        llm_rejections: list[dict[str, object]] = []
        current = self._constraint_values(state)

        if all(constraint.satisfied_by(current.get(constraint.metric)) for constraint in goal.constraints):
            return PlanningOutcome(
                planner_mode="deterministic",
                no_action=True,
                reason=_satisfied_reason(goal, state),
                llm_metrics=metrics,
                llm_rejections=llm_rejections,
            )

        raw_candidates: list[tuple[list[dict[str, object]], str, str, str]] = []
        planner_mode = "fallback"
        empty_llm_candidates = False
        if llm_client is not None and llm_client.available():
            llm_raw = self._llm_candidates(
                goal,
                state,
                registry,
                preferences,
                recent_events,
                override_suppressions,
                llm_client,
            )
            if not llm_raw:
                empty_llm_candidates = True
                llm_rejections.append({"code": "empty_candidates", "reason": "模型返回空候选或输出未通过 schema/解析"})
            generated = len(llm_raw)
            valid = 0
            rejected = 0
            for index, (summary, actions) in enumerate(llm_raw, start=1):
                problems = guard.issues(actions)
                if problems:
                    rejected += 1
                    llm_rejections.append(
                        {
                            "code": classify_issue(problems[0]),
                            "reason": problems[0],
                            "normalized_actions": actions,
                        }
                    )
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

        baseline_goal_score = self._scorer.score(
            "baseline",
            "当前状态",
            [],
            self._predictor.predict([], state),
            goal,
            state,
            preferences,
        ).scores.goal_satisfaction
        def improves_goal(item: CandidatePlan) -> bool:
            predicted_values = {
                "brightness": item.predicted.brightness_pct,
                "temperature": item.predicted.temperature,
                "curtain_position": item.predicted.curtain_position,
            }
            reaches_hard_bounds = all(
                constraint.satisfied_by(predicted_values.get(constraint.metric))
                for constraint in goal.constraints
            )
            return reaches_hard_bounds or item.scores.goal_satisfaction > baseline_goal_score + 1e-6

        actionable = [
            item
            for item in scored
            if item.actions and improves_goal(item)
        ]
        if planner_mode == "llm" and not actionable:
            repaired_raw = self._llm_candidates(
                goal,
                state,
                registry,
                preferences,
                recent_events,
                override_suppressions,
                llm_client,
                repair_instruction=(
                    "上一轮候选通过了格式校验，但反事实预测表明没有任何候选改善目标满足度。"
                    "这次每个候选必须至少包含一个直接改善尚未满足约束的动作；"
                    "不要操作与目标约束无关的设备。"
                ),
            ) if llm_client is not None else []
            metrics.llm_candidates_generated += len(repaired_raw)
            for index, (summary, actions) in enumerate(repaired_raw, start=1):
                problems = guard.issues(actions)
                if problems:
                    metrics.llm_candidates_rejected += 1
                    llm_rejections.append(
                        {
                            "code": classify_issue(problems[0]),
                            "reason": problems[0],
                            "normalized_actions": actions,
                        }
                    )
                    continue
                metrics.llm_candidates_valid += 1
                enriched = [self._enrich(action, registry) for action in actions]
                scored.append(
                    self._score_plan(
                        f"c{len(scored) + 1}",
                        f"候选 R{index} · 模型修复",
                        enriched,
                        self._predictor.predict(enriched, state),
                        goal,
                        state,
                        preferences,
                        summary,
                        "llm",
                    )
                )
            actionable = [
                item
                for item in scored
                if item.actions and improves_goal(item)
            ]
        if planner_mode == "llm" and not actionable:
            llm_rejections.append(
                {
                    "code": "no_goal_improvement",
                    "reason": "模型候选均未改善目标满足度，确定性 fallback 接管",
                }
            )
            planner_mode = "fallback"
            for actions, label, summary in self._baseline_candidates(goal, state, registry, preferences):
                problems = guard.issues(actions)
                if problems:
                    continue
                enriched = [self._enrich(action, registry) for action in actions]
                predicted = self._predictor.predict(enriched, state)
                scored.append(
                    self._score_plan(
                        f"c{len(scored) + 1}",
                        label,
                        enriched,
                        predicted,
                        goal,
                        state,
                        preferences,
                        summary,
                        "fallback",
                    )
                )
            actionable = [
                item
                for item in scored
                if item.actions and improves_goal(item)
            ]
        if not actionable:
            return PlanningOutcome(
                planner_mode=planner_mode,
                no_action=True,
                reason="所有候选均被 Policy Guard 拒绝或不能改善目标，未执行任何动作。",
                candidates=scored,
                llm_metrics=metrics,
                llm_rejections=llm_rejections,
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
            llm_rejections=llm_rejections,
        )

    # ---- model-driven candidate generation ---------------------------
    def _llm_candidates(
        self,
        goal: CompiledGoal,
        state: RoomState,
        registry: dict[str, DeviceCapability],
        preferences: PreferenceEstimate | None,
        recent_events: list[dict[str, object]] | None,
        override_suppressions: list[OverrideSuppression] | None,
        llm_client: LlmClient,
        repair_instruction: str = "",
    ) -> list[tuple[str, list[dict[str, object]]]]:
        capabilities = {key: value.to_dict() for key, value in registry.items() if not value.readonly}
        preference_view = (
            {"metric": preferences.metric, "preferred_mean": preferences.mean, "samples": preferences.count}
            if preference_is_eligible(preferences)
            else None
        )
        repair_note = f"本轮修复要求：{repair_instruction}\n" if repair_instruction else ""
        current_values = self._constraint_values(state)
        unmet_constraints = [
            item.to_dict()
            for item in goal.constraints
            if not item.satisfied_by(current_values.get(item.metric))
        ]
        route_capabilities = {
            "brightness": "set_brightness",
            "temperature": "set_temperature",
            "curtain_position": "set_position",
        }
        control_routes = {
            metric: [
                {"device_id": device.device_id, "capability": capability}
                for device in registry.values()
                if capability in device.actions and not device.readonly
            ]
            for metric, capability in route_capabilities.items()
        }
        # 目标亮度为 0 表示关灯。set_brightness 的载荷硬编码 power=on，无法表达
        # 关闭，因此把 turn_off 一并作为可选路由暴露给规划模型。
        if any(
            item.metric == "brightness" and item.upper <= 0 for item in goal.constraints
        ):
            control_routes["brightness"] = [
                {"device_id": device.device_id, "capability": "turn_off"}
                for device in registry.values()
                if "turn_off" in device.actions and not device.readonly
            ] or control_routes["brightness"]
        user_prompt = (
            f"用户目标：{goal.title}\n"
            f"目标约束（区间）：{[item.to_dict() for item in goal.constraints]}\n"
            f"尚未满足的约束：{unmet_constraints}\n"
            f"metric 到实时设备能力路由：{control_routes}\n"
            f"模型对目标的理解：{goal.semantic_view}\n"
            f"当前家庭状态：温度 {state.temperature}°C，灯亮度 {state.brightness_pct}%，"
            f"窗帘 {state.curtain_position}%，空调 {state.ac_mode}"
            f"（设定 {state.ac_target}°C），有人={state.presence}\n"
            f"可用设备与能力：{capabilities}\n"
            f"用户历史偏好：{preference_view}\n"
            f"近期事件：{(recent_events or [])[-5:]}\n"
            f"当前人工接管抑制：{[item.to_dict() for item in (override_suppressions or [])]}\n"
            "人工接管抑制表示用户刚刚主动设置了该设备；除非新的明确目标或安全约束解除抑制，"
            "候选计划不得覆盖这些设备维度。\n"
            f"{repair_note}"
            "请根据以上真实上下文生成 3~5 个思路不同的候选计划。"
        )
        raw = llm_client.chat_json(_PLANNER_SYSTEM_PROMPT, user_prompt, schema=_PLANNER_SCHEMA)
        candidates_raw = raw.get("candidates") if isinstance(raw, dict) else None
        if not candidates_raw:
            # 空候选 repair（一次）：把上次失败原因与状态再给模型，要求必须给出候选。
            repair = llm_client.chat_json(
                _PLANNER_SYSTEM_PROMPT,
                (
                    f"{user_prompt}\n\n你上一次没有生成任何候选"
                    "（可能误判为目标已满足）。目标约束尚未全部满足，"
                    "请必须生成 2~3 个候选计划。"
                ),
                schema=_PLANNER_SCHEMA,
            )
            if isinstance(repair, dict) and isinstance(repair.get("candidates"), list) and repair["candidates"]:
                candidates_raw = repair["candidates"]
        if not candidates_raw:
            return []
        results: list[tuple[str, list[dict[str, object]]]] = []
        for item in candidates_raw[:_MAX_CANDIDATES]:
            if not isinstance(item, dict):
                continue
            actions = []
            for entry in item.get("actions", [])[:8]:
                if not isinstance(entry, dict):
                    continue
                capability, value, extra_mode = _normalize_action_value(
                    str(entry.get("capability", "")),
                    entry.get("value"),
                )
                if value is None:
                    # 模型给了空占位（如 {}）时丢弃该条动作，而不是连坐整个候选。
                    continue
                actions.append(
                    {
                        "device_id": str(entry.get("device_id", "")),
                        "capability": capability,
                        "value": value,
                        "mode": _normalize_mode(entry.get("mode")) or extra_mode,
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
        preference_brightness = preferences.mean if preference_is_eligible(preferences) else None
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
            if (
                action.get("capability") == "set_temperature"
                and device.temperature_step is not None
                and isinstance(action.get("value"), (int, float))
            ):
                value = float(action["value"])
                action["value"] = round(round(value / device.temperature_step) * device.temperature_step, 3)
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


def _satisfied_reason(goal: CompiledGoal, state: RoomState) -> str:
    """Describe the observed satisfied state without relying on a scene template."""
    room_name = {"living_room": "客厅", "bedroom": "卧室", "home": "家中"}.get(goal.room, goal.room)
    observations: list[str] = []
    metrics = {constraint.metric for constraint in goal.constraints}
    if "curtain_position" in metrics and state.curtain_position is not None:
        observations.append(
            "窗帘已关闭" if state.curtain_position <= 5 else f"窗帘位置为 {state.curtain_position:g}%"
        )
    if "brightness" in metrics and state.brightness_pct is not None:
        observations.append(f"灯光已为 {state.brightness_pct:g}%")
    if "temperature" in metrics:
        if state.ac_mode == "cool" and state.ac_target is not None:
            observations.append(f"空调正在向 {state.ac_target:g}℃ 制冷")
        elif state.temperature is not None:
            observations.append(f"当前温度为 {state.temperature:g}℃")
    detail = "；".join(observations) or "全部环境约束均已满足"
    return f"当前{room_name}已经满足“{goal.title}”：{detail}，无需重复调整。"
