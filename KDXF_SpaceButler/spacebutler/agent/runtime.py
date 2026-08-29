"""AgentRuntime: the autonomous loop.

Chat 是入口，不是主循环：
  submit_goal(text) -> GoalCompiler -> Active Goal -> Counterfactual Planner
    -> Policy Guard -> Executor -> HA/ESPHome -> Verifier（回读 + 预测误差）
  后台线程每 tick：
    poll inventory -> World Model 更新 -> 状态变化归因（Override/环境）
      -> Goal Evaluator -> 偏离确认（debounce/cooldown）-> Replanning

LLM 参与 Goal 编译与候选生成；评分、边界与安全全部是确定性代码。
LLM 不可用时如实进入 fallback（planner_mode 会真实反映，不冒充）。
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import uuid

from spacebutler.agent.capabilities import build_capability_registry
from spacebutler.agent.executor import AgentExecutor
from spacebutler.agent.goal_compiler import GoalCompiler, is_cancel_text
from spacebutler.agent.goal_evaluator import GoalEvaluator
from spacebutler.agent.goal_manager import GoalManager
from spacebutler.agent.models import ActionAttribution, CandidatePlan, CompiledGoal, GoalStatus, now
from spacebutler.agent.override_detector import OverrideDetector
from spacebutler.agent.planner import CounterfactualPlanner, PlanningOutcome
from spacebutler.agent.preference_memory import PreferenceMemory
from spacebutler.agent.verifier import Verifier
from spacebutler.agent.world_model import HomeWorldModel
from spacebutler.llm.client import LlmClient

_CHANGE_TOLERANCE = 1.5


class AgentRuntime:
    def __init__(
        self,
        devices_provider: Callable[[], dict[str, object]],
        control_device: Callable[[str, dict[str, object]], dict[str, object]],
        llm_client: LlmClient | None = None,
        database_path: str | None = None,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._devices_provider = devices_provider
        self._control_device = control_device
        self._llm = llm_client
        self._poll_interval = poll_interval_seconds
        self._world = HomeWorldModel()
        self._preferences = PreferenceMemory(database_path)
        self._compiler = GoalCompiler()
        self._planner = CounterfactualPlanner()
        self._executor = AgentExecutor(control_device)
        self._verifier = Verifier()
        self._detector = OverrideDetector(self._world, self._preferences, llm_client=llm_client)
        self._goal_manager = GoalManager(self._world)
        self._evaluator = GoalEvaluator()
        self._last_outcome: PlanningOutcome | None = None
        self._last_observed: dict[str, object] | None = None
        self._last_signature: dict[str, object] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._messages: list[dict[str, object]] = [
            {"role": "assistant", "content": "自治智能体已就绪：说出目标即可，我会规划、执行并持续盯住环境。", "ts": now()}
        ]

    # ---- chat entry --------------------------------------------------
    def submit_text(self, text: str) -> dict[str, object] | None:
        """所有用户文本的入口。由 LLM Goal Reasoner 判断是否为家居目标：
        是 -> 走自治链路并返回 chat 载荷；否/模型不可用且非取消 -> None（调用方转普通对话）。"""
        if is_cancel_text(text):
            return self._cancel_goal()
        with self._lock:
            devices = self._devices_provider()["devices"]
            state = self._world.update_state(devices)
            goal, compile_mode, semantic = self._compiler.compile_with_state(
                text,
                self._llm,
                state.temperature,
                state.brightness_pct,
            )
            if goal is None:
                if compile_mode == "llm":
                    self._world.record_event(
                        "goal_rejected",
                        "模型判断这不是家居目标，转交普通对话",
                        semantic=semantic,
                    )
                return None
            registry = build_capability_registry(devices)
            self._world.set_active_goal(goal, GoalStatus.ACTIVE)
            self._goal_manager.reset_tracking()
            if compile_mode == "llm":
                self._world.record_event("goal_compiled_by_llm", "模型完成了目标语义理解")
            self._world.record_event(
                "goal_received",
                f"收到目标：{goal.title}",
                goal_id=goal.goal_id,
                planner_mode=compile_mode,
                interpretation=(goal.semantic_view or {}).get("intent_summary", ""),
            )
            self._world.record_event(
                "goal_compiled",
                f"目标编译为 {len(goal.constraints)} 条约束（{compile_mode}）",
                planner_mode=compile_mode,
            )
            outcome = self._plan_and_execute(goal, self._world.current_state(), registry)
            reply = self._goal_reply(goal, outcome)
            self._append("assistant", reply)
            return self.history()

    def history(self) -> dict[str, object]:
        with self._lock:
            return {
                "conversation_id": "agent",
                "messages": list(self._messages[-30:]),
                "plans": [],
                "active_plan": self._plan_view(),
                "busy": False,
                "llm_available": bool(self._llm and self._llm.available()),
            }

    # ---- planning core (shared by submit & replan) -------------------
    def _plan_and_execute(
        self,
        goal: CompiledGoal,
        state: object,
        registry: dict[str, object],
    ) -> PlanningOutcome:
        self._world.record_event("world_snapshot", "已捕获真实环境快照", **state.to_dict())
        preference = self._preferences.estimate(goal.activity, goal.room, "brightness")
        recent_events = self._world.snapshot()["timeline"][-5:]
        outcome = self._planner.plan(goal, state, registry, preference, self._llm, recent_events)
        self._world.set_planner_mode(outcome.planner_mode)
        self._world.set_contribution(outcome.llm_metrics)
        if outcome.planner_mode == "llm":
            self._world.record_event("llm_planning", "模型生成了候选计划集合")
        self._world.record_event(
            "candidates_generated",
            f"生成 {len(outcome.candidates)} 个候选方案"
            + ("（含本地模型候选）" if outcome.planner_mode == "llm" else "（LLM 不可用，确定性候选）"),
            planner_mode=outcome.planner_mode,
        )
        if outcome.no_action or outcome.selected is None:
            self._world.record_event("no_action", outcome.reason)
            self._world.set_active_goal(goal, GoalStatus.SATISFIED if outcome.no_action else GoalStatus.ACTIVE)
            self._last_outcome = outcome
            return outcome
        selected: CandidatePlan = outcome.selected
        self._world.record_event(
            "plan_selected",
            outcome.reason,
            candidate_id=selected.candidate_id,
            total_score=selected.scores.total,
        )
        self._world.record_event("policy_check", "Policy Guard 校验通过", actions=len(selected.actions))
        records = self._executor.execute(selected, self._world)
        self._world.record_event("execution", f"已发送 {len(records)} 条设备命令")
        failures = self._verifier.verify_actions(records, self._read_ha_state)
        for record in records:
            self._world.update_action(
                record.action_id,
                verified=record.verified,
                observed=record.observed,
            )
        # 回写选中计划，让 UI 与检查看到每步的真实回读结果。
        for action, record in zip(selected.actions, records):
            action.verified = record.verified
            action.observed = record.observed
        error = self._verifier.prediction_error(selected.predicted, self._world.current_state())
        self._world.record_prediction(
            {
                "predicted": selected.predicted.to_dict(),
                "observed_temperature": self._world.current_state().temperature,
                "prediction_error": error,
                "at": now(),
            }
        )
        # Online Calibration：用真实反馈校准物理预测速率（EMA）。
        if error is not None:
            rate = self._planner._predictor.calibrate(
                selected.predicted.temperature,
                self._world.current_state().temperature or selected.predicted.temperature,
                selected.predicted.temperature_trend == "decreasing",
            )
            self._world.record_event(
                "predictor_calibrated",
                f"预测器在线校准完成（采样 #{self._planner._predictor.calibration_samples}，"
                f"制冷速率 {rate:.1f}°C/min，预测误差 {error}°C）",
            )
        for record in records:
            if record.prediction_error is None:
                self._world.update_action(record.action_id, prediction_error=error)
        self._world.record_event(
            "verification",
            "全部动作通过 HA 回读" if failures == 0 else f"{failures} 个动作回读未通过",
            failures=failures,
            prediction_error=error,
        )
        self._goal_manager.note_replanned()
        self._world.set_active_goal(goal, GoalStatus.ACTIVE)
        self._last_outcome = outcome
        self._last_observed = None
        return outcome

    def _read_ha_state(self, entity_id: str) -> dict[str, object] | None:
        for item in self._devices_provider()["devices"]:
            if isinstance(item, dict) and str(item.get("entity_id")) == entity_id:
                ha_state = item.get("ha_state")
                return ha_state if isinstance(ha_state, dict) else None
        return None

    # ---- autonomous loop ---------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="spacebutler-agent-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self._tick()
            except (RuntimeError, TimeoutError, ValueError, OSError) as error:
                self._world.record_event("runtime_error", f"运行时错误：{error}")

    def _tick(self) -> None:
        payload = self._devices_provider()
        devices = payload["devices"]
        previous = self._world.current_state()
        state = self._world.update_state(devices)
        self._detect_changes(previous, state)
        goal, status = self._world.active_goal()
        if goal is None or status in {GoalStatus.CANCELLED, GoalStatus.EXPIRED, GoalStatus.PAUSED}:
            return
        # 模型语义评估为主，数值违反检测保留为 signal（GoalEvaluator fallback）。
        recent_overrides = [
            str(event.get("message", ""))
            for event in self._world.snapshot()["timeline"]
            if event["kind"] == "human_override"
        ][-3:]
        previous_summary = self._last_outcome.reason if self._last_outcome else ""
        evaluation = self._evaluator.evaluate(goal, state, previous_summary, recent_overrides, self._llm)
        contribution = self._world.contribution()
        if evaluation.by_llm:
            contribution.goal_evaluation_by_llm += 1
            self._world.set_contribution(contribution)
        # guardrail：语义结论仍要经过 debounce / cooldown / max-retries。
        new_status, reason = self._goal_manager.apply_evaluation(goal, state, evaluation.status, evaluation.need_replan, evaluation.reason)
        if new_status == status:
            return
        self._world.record_event(
            "goal_evaluated",
            reason,
            from_status=status.value,
            to_status=new_status.value,
            by_llm=evaluation.by_llm,
        )
        if new_status == GoalStatus.REPLANNING:
            if evaluation.by_llm:
                contribution.replan_decision_by_llm += 1
                self._world.set_contribution(contribution)
            self._world.set_active_goal(goal, GoalStatus.REPLANNING)
            registry = build_capability_registry(devices)
            self._plan_and_execute(goal, self._world.current_state(), registry)
        else:
            self._world.set_active_goal(goal, new_status)

    def _detect_changes(self, previous: object, state: object) -> None:
        signature_new = {
            "brightness": state.brightness_pct,
            "curtain": state.curtain_position,
            "temperature": round(state.temperature, 1) if state.temperature is not None else None,
            "ac": state.ac_mode,
        }
        old = dict(self._last_signature)
        self._last_signature = signature_new
        if not old:
            return
        changes = [
            ("light", "set_brightness", old.get("brightness"), signature_new["brightness"]),
            ("living_room_curtain", "set_position", old.get("curtain"), signature_new["curtain"]),
            ("living_room_ac", "set_temperature", old.get("temperature"), signature_new["temperature"]),
            ("living_room_ac", "set_hvac_mode", old.get("ac"), signature_new["ac"]),
        ]
        for device_id, capability, before, after in changes:
            if before is None or after is None:
                continue
            try:
                drift = abs(float(before) - float(after))
            except (TypeError, ValueError):
                drift = 0.0 if before == after else _CHANGE_TOLERANCE + 1
            if capability == "set_hvac_mode":
                if before == after:
                    continue
            elif drift <= _CHANGE_TOLERANCE:
                continue
            device_type = "temperature" if capability == "set_temperature" else "climate" if capability == "set_hvac_mode" else "light" if capability == "set_brightness" else "curtain"
            attribution = self._detector.classify(device_id, device_type, capability, before, after, now())
            if attribution in {ActionAttribution.HUMAN_OVERRIDE, ActionAttribution.ENVIRONMENT_CHANGE}:
                self._world.record_event(
                    "attribution",
                    f"{device_id} 变化归因为 {attribution.value}",
                    attribution=attribution.value,
                    capability=capability,
                    before=before,
                    after=after,
                )

    # ---- cancel & views ----------------------------------------------
    def _cancel_goal(self) -> dict[str, object]:
        goal, _ = self._world.active_goal()
        if goal is None:
            self._append("assistant", "当前没有进行中的自治目标。")
        else:
            self._world.set_active_goal(goal, GoalStatus.CANCELLED)
            self._world.record_event("goal_cancelled", f"目标 {goal.title} 已取消，停止自动调整")
            self._append("assistant", f"已结束「{goal.title}」目标，设备保持当前状态，不再自动调整。")
        return self.history()

    def _goal_reply(self, goal: CompiledGoal, outcome: PlanningOutcome) -> str:
        if outcome.no_action:
            return f"目标「{goal.title}」当前已经满足，无需调整。" if outcome.candidates else outcome.reason
        selected = outcome.selected
        actions = "、".join(f"{action.device_name}{_action_text(action)}" for action in selected.actions)
        mode_text = "本地模型参与规划" if outcome.planner_mode == "llm" else "确定性规划（LLM 未参与）"
        return (
            f"好的，目标「{goal.title}」已接受。{mode_text}：选出 {selected.label}，"
            f"将执行 {len(selected.actions)} 项动作（{actions}），并逐项回读验证。"
        )

    def _plan_view(self) -> dict[str, object] | None:
        outcome = self._last_outcome
        return outcome.to_dict() if outcome is not None else None

    def _append(self, role: str, content: str) -> None:
        with self._lock:
            self._messages.append({"role": role, "content": content, "ts": now()})

    def status(self) -> dict[str, object]:
        snapshot = self._world.snapshot()
        goal, status = self._world.active_goal()
        preference = self._preferences.estimate(
            goal.activity if goal else "watch_content",
            goal.room if goal else "living_room",
            "brightness",
        )
        snapshot.update(
            {
                "preference": preference.to_dict() if preference else None,
                "preferences": self._preferences.summary(),
                "last_outcome": self._plan_view(),
                "llm_available": bool(self._llm and self._llm.available()),
                "goal_status_detail": status.value,
                "llm_contribution": self._world.contribution().to_dict(),
            }
        )
        return snapshot


def _action_text(action: object) -> str:
    mapping = {
        "set_brightness": "亮度调至 {value}%",
        "set_position": "开合调至 {value}%",
        "set_temperature": "设定 {value}°C 制冷",
        "set_hvac_mode": "模式 {value}",
        "turn_off": "关闭",
    }
    template = mapping.get(action.capability, action.capability)
    try:
        return template.format(value=action.value)
    except (KeyError, IndexError, ValueError):
        return template
