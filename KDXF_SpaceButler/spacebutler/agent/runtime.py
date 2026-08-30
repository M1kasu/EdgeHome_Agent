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
import json
from pathlib import Path
import threading
import uuid

from spacebutler.agent.capabilities import build_capability_registry
from spacebutler.agent.executor import AgentExecutor
from spacebutler.agent.goal_compiler import GoalCompiler, is_cancel_text
from spacebutler.agent.goal_evaluator import GoalEvaluator
from spacebutler.agent.goal_manager import GoalManager
from spacebutler.agent.models import (
    ActionAttribution,
    CandidatePlan,
    CompiledGoal,
    FreshObservation,
    GoalStatus,
    RoomState,
    now,
)
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
        override_suppression_seconds: float = 5 * 60.0,
    ) -> None:
        self._devices_provider = devices_provider
        self._control_device = control_device
        self._llm = llm_client
        self._database_path = database_path
        self._poll_interval = poll_interval_seconds
        self._world = HomeWorldModel()
        self._preferences = PreferenceMemory(database_path)
        self._compiler = GoalCompiler()
        self._planner = CounterfactualPlanner()
        self._executor = AgentExecutor(control_device)
        self._verifier = Verifier()
        self._detector = OverrideDetector(
            self._world,
            self._preferences,
            llm_client=llm_client,
            suppression_seconds=override_suppression_seconds,
        )
        self._goal_manager = GoalManager(self._world)
        self._evaluator = GoalEvaluator()
        self._last_outcome: PlanningOutcome | None = None
        self._last_observed: dict[str, object] | None = None
        self._last_signature: dict[tuple[str, str], dict[str, object]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick_at: float | None = None
        self._fatal_error: str | None = None
        self._last_runtime_error: str | None = None
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
            if not self._last_signature:
                self._last_signature = _device_signatures(devices)
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
            old_goal, old_status = self._world.active_goal()
            self._world.clear_suppressions()
            if (
                old_goal is not None
                and old_goal.goal_id != goal.goal_id
                and old_status
                in {GoalStatus.ACTIVE, GoalStatus.DEGRADED, GoalStatus.REPLANNING, GoalStatus.SATISFIED}
            ):
                # 新目标替换旧目标：旧目标明确收尾，避免生命周期泄漏。
                self._world.set_active_goal(old_goal, GoalStatus.CANCELLED)
                self._world.record_event(
                    "goal_cancelled",
                    f"目标「{old_goal.title}」被新目标替换",
                    replaced_by=goal.goal_id,
                )
            self._world.set_active_goal(goal, GoalStatus.ACTIVE)
            self._persist_goal_event(goal, GoalStatus.ACTIVE, "goal_received")
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
        suppressions = self._world.active_suppressions(goal.goal_id)
        outcome = self._planner.plan(
            goal,
            state,
            registry,
            preference,
            self._llm,
            recent_events,
            suppressions,
        )
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
        observation = self._fresh_observation()
        self._world.record_event(
            "fresh_observation",
            "动作后已从 Home Assistant 获取新状态" if observation.fresh else "动作后 Home Assistant 新状态不可用",
            **observation.to_dict(),
        )
        failures = self._verifier.verify_actions(records, observation.state_for)
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
        error = (
            self._verifier.prediction_error(selected.predicted, observation.state)
            if observation.fresh
            else None
        )
        self._world.record_prediction(
            {
                "predicted": selected.predicted.to_dict(),
                "observed": observation.to_dict(),
                "observed_temperature": observation.state.temperature,
                "prediction_error": error,
                "at": observation.observed_at,
            }
        )
        # Online Calibration：只允许所有动作验证成功后的 fresh HA snapshot。
        calibration_allowed = bool(
            error is not None
            and failures == 0
            and observation.is_fresh_after(records)
            and all(record.verified is True for record in records)
            and any(record.capability in {"set_temperature", "set_hvac_mode"} for record in records)
        )
        if calibration_allowed:
            rate = self._planner._predictor.calibrate(
                selected.predicted.temperature,
                observation.state.temperature
                if observation.state.temperature is not None
                else selected.predicted.temperature,
                selected.predicted.temperature_trend == "decreasing",
            )
            self._world.record_event(
                "predictor_calibrated",
                f"预测器在线校准完成（采样 #{self._planner._predictor.calibration_samples}，"
                f"制冷速率 {rate:.1f}°C/min，预测误差 {error}°C）",
            )
        else:
            self._world.record_event(
                "calibration_skipped",
                "未获得通过验证的动作后新状态，跳过预测器校准",
                fresh=observation.fresh,
                failures=failures,
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
        if failures == 0:
            self._goal_manager.note_replanned()
        resulting_status = GoalStatus.ACTIVE if failures == 0 else GoalStatus.DEGRADED
        self._world.set_active_goal(goal, resulting_status)
        self._persist_goal_event(goal, resulting_status, "execution_verified" if failures == 0 else "verification_failed")
        self._last_outcome = outcome
        self._last_observed = observation.to_dict()
        return outcome

    def _fresh_observation(self) -> FreshObservation:
        try:
            payload = self._devices_provider()
            devices = payload.get("devices")
            if not isinstance(devices, list):
                raise RuntimeError("device inventory is invalid")
            return self._world.refresh_from_home_assistant(devices, now())
        except (RuntimeError, TimeoutError, ValueError, OSError) as error:
            self._world.record_event("observation_error", f"动作后状态读取失败：{error}")
            return FreshObservation(
                observed_at=now(),
                source="UNAVAILABLE",
                state=self._world.current_state(),
                fresh=False,
            )

    # ---- autonomous loop ---------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._fatal_error = None
        self._thread = threading.Thread(target=self._loop, name="spacebutler-agent-runtime", daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except (RuntimeError, TimeoutError, ValueError, OSError) as error:
                self._last_runtime_error = str(error)
                self._world.record_event("runtime_error", f"运行时错误：{error}")
            except Exception as error:  # noqa: BLE001 - unexpected errors are fatal and health-visible
                self._fatal_error = f"{type(error).__name__}: {error}"
                self._world.record_event("runtime_fatal", f"运行时线程已停止：{self._fatal_error}")
                break
            finally:
                self._last_tick_at = now()
            if self._stop.wait(self._poll_interval):
                break

    def _tick(self) -> None:
        payload = self._devices_provider()
        devices = payload["devices"]
        state = self._world.update_state(devices)
        self._detect_changes(devices)
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

    def _detect_changes(self, devices: list[dict[str, object]]) -> None:
        signature_new = _device_signatures(devices)
        old = dict(self._last_signature)
        self._last_signature = signature_new
        if not old:
            return
        for key, current in signature_new.items():
            prior = old.get(key)
            if prior is None:
                continue
            device_id = str(current["device_id"])
            device_type = str(current["device_type"])
            capability = str(current["capability"])
            before = prior.get("value")
            after = current.get("value")
            if before is None or after is None:
                continue
            try:
                drift = abs(float(before) - float(after))
            except (TypeError, ValueError):
                drift = 0.0 if before == after else _CHANGE_TOLERANCE + 1
            categorical_change = capability == "set_hvac_mode" or device_type in {"presence", "contact"}
            if categorical_change:
                if before == after:
                    continue
            elif drift <= _change_tolerance(capability, device_type):
                continue
            attribution = self._detector.classify(device_id, device_type, capability, before, after, now())
            if attribution != ActionAttribution.UNKNOWN:
                self._world.record_event(
                    "attribution",
                    f"{device_id} 变化归因为 {attribution.value}",
                    attribution=attribution.value,
                    capability=capability,
                    before=before,
                    after=after,
                )

    # ---- cancel & views ----------------------------------------------
    def _persist_goal_event(self, goal: CompiledGoal, status: GoalStatus, note: str = "") -> None:
        """Goal History 持久化：重启后仍可回溯目标生命周期（P1-3）。"""
        if self._database_path is None:
            return
        try:
            history_path = Path(self._database_path).parent / "goal_history.jsonl"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps(
                {
                    "goal_id": goal.goal_id,
                    "title": goal.title,
                    "activity": goal.activity,
                    "status": status.value,
                    "note": note,
                    "ts": now(),
                },
                ensure_ascii=False,
            )
            with open(history_path, "a", encoding="utf-8") as handle:
                handle.write(entry + "\n")
        except OSError:
            pass

    def _cancel_goal(self) -> dict[str, object]:
        goal, _ = self._world.active_goal()
        if goal is None:
            self._append("assistant", "当前没有进行中的自治目标。")
        else:
            self._world.set_active_goal(goal, GoalStatus.CANCELLED)
            self._world.clear_suppressions(goal.goal_id)
            self._persist_goal_event(goal, GoalStatus.CANCELLED, "user_cancelled")
            self._world.record_event("goal_cancelled", f"目标 {goal.title} 已取消，停止自动调整")
            self._append("assistant", f"已结束「{goal.title}」目标，设备保持当前状态，不再自动调整。")
        return self.history()

    def _goal_reply(self, goal: CompiledGoal, outcome: PlanningOutcome) -> str:
        if outcome.no_action:
            return f"目标「{goal.title}」当前已经满足，无需调整。" if outcome.candidates else outcome.reason
        selected = outcome.selected
        failed = [action for action in selected.actions if action.verified is False]
        if failed:
            names = "、".join(action.device_name for action in failed)
            return (
                f"注意：{len(failed)} 项动作（{names}）执行后未通过 Home Assistant 回读验证，"
                f"目标「{goal.title}」未完全达成。系统已记录，将不会假报成功。"
            )
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
        preference = (
            self._preferences.estimate(goal.activity, goal.room, "brightness")
            if goal is not None
            else None
        )
        snapshot.update(
            {
                "preference": preference.to_dict() if preference else None,
                "preferences": self._preferences.summary(),
                "last_outcome": self._plan_view(),
                "llm_available": bool(self._llm and self._llm.available()),
                "goal_status_detail": status.value,
                "llm_contribution": self._world.contribution().to_dict(),
                "runtime": self.runtime_health(),
            }
        )
        return snapshot

    def runtime_health(self) -> dict[str, object]:
        thread_alive = bool(self._thread and self._thread.is_alive())
        tick_age = max(0.0, now() - self._last_tick_at) if self._last_tick_at is not None else None
        recent_limit = max(5.0, self._poll_interval * 3.0)
        recent_tick = tick_age is not None and tick_age <= recent_limit
        return {
            "initialized": self._thread is not None,
            "thread_alive": thread_alive,
            "last_tick": self._last_tick_at,
            "last_tick_age_seconds": round(tick_age, 3) if tick_age is not None else None,
            "recent_tick": recent_tick,
            "fatal_error": self._fatal_error,
            "last_error": self._last_runtime_error,
            "status": "ready" if thread_alive and recent_tick and self._fatal_error is None else "degraded",
        }


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


def _device_signatures(
    devices: list[dict[str, object]],
) -> dict[tuple[str, str], dict[str, object]]:
    """Build attribution inputs from live inventory identities, never aliases."""
    signatures: dict[tuple[str, str], dict[str, object]] = {}
    for item in devices:
        if not isinstance(item, dict) or not item.get("online"):
            continue
        device_id = str(item.get("device_id", ""))
        device_type = str(item.get("type", ""))
        entity_id = str(item.get("entity_id", ""))
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        dimensions: list[tuple[str, str, object]] = []
        if device_type == "light":
            brightness = state.get("brightness")
            if isinstance(brightness, (int, float)):
                dimensions.append(("brightness", "set_brightness", round(float(brightness) * 100 / 255, 1)))
        elif device_type == "curtain":
            dimensions.append(("curtain_position", "set_position", state.get("position")))
        elif device_type == "climate":
            dimensions.extend(
                [
                    ("temperature", "set_temperature", state.get("temperature")),
                    ("hvac_mode", "set_hvac_mode", state.get("mode")),
                ]
            )
        elif device_type == "temperature":
            dimensions.append(("temperature", "state_change", state.get("temperature")))
        elif device_type in {"presence", "contact", "illuminance"}:
            sensor_value = next(iter(state.values()), None)
            dimensions.append((device_type, "state_change", sensor_value))
        for dimension, capability, value in dimensions:
            signatures[(device_id, dimension)] = {
                "device_id": device_id,
                "entity_id": entity_id,
                "device_type": device_type,
                "capability": capability,
                "value": value,
            }
    return signatures


def _change_tolerance(capability: str, device_type: str) -> float:
    if capability == "set_temperature" or device_type == "temperature":
        return 0.1
    return _CHANGE_TOLERANCE
