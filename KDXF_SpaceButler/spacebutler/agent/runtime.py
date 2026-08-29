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
from spacebutler.agent.goal_compiler import GoalCompiler, is_cancel_text, is_goal_text
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
        self._detector = OverrideDetector(self._world, self._preferences)
        self._goal_manager = GoalManager(self._world)
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
    def handles(self, text: str) -> bool:
        return is_goal_text(text) or is_cancel_text(text)

    def submit_goal(self, text: str) -> dict[str, object]:
        """Compile -> plan -> execute -> verify. Returns a chat-shaped payload."""
        if is_cancel_text(text):
            return self._cancel_goal()
        with self._lock:
            self._world.update_state(self._devices_provider()["devices"])
            registry = build_capability_registry(self._devices_provider()["devices"])
            goal, compile_mode = self._compiler.compile(text, self._llm)
            self._world.set_active_goal(goal, GoalStatus.ACTIVE)
            self._goal_manager.reset_tracking()
            self._world.record_event(
                "goal_received",
                f"收到目标：{goal.title}",
                goal_id=goal.goal_id,
                compile_mode=compile_mode,
            )
            self._world.record_event("goal_compiled", f"目标编译为 {len(goal.constraints)} 条约束", planner_mode=compile_mode)
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
        outcome = self._planner.plan(goal, state, registry, preference, self._llm)
        self._world.set_planner_mode(outcome.planner_mode)
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
        new_status, reason = self._goal_manager.evaluate(goal, state)
        if new_status == status:
            return
        self._world.record_event("goal_evaluated", reason, from_status=status.value, to_status=new_status.value)
        if new_status == GoalStatus.REPLANNING:
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
            goal.activity if goal else "watch_movie",
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
