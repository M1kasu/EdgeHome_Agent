"""AgentRuntime: the autonomous loop.

Chat 是入口，不是主循环：
  submit_goal(text) -> GoalCompiler -> Active Goal -> Counterfactual Planner
    -> Policy Guard -> Executor -> HA/ESPHome -> Verifier（回读 + 预测误差）
  后台线程每 tick：
    poll inventory -> World Model 更新 -> 状态变化归因（Override/环境）
      -> Goal Evaluator -> 偏离确认（debounce/cooldown）-> Replanning

LLM 参与 Goal 编译与候选生成；评分、边界与安全全部是确定性代码。
自然语言目标必须由可用模型编译；模型不可用时安全拒绝执行，不退回场景脚本。
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
from spacebutler.agent.graph import AgentGraphHandlers, AgentGraphState, LangGraphAgentWorkflow
from spacebutler.agent.goal_manager import GoalManager
from spacebutler.agent.models import (
    ActionAttribution,
    CandidatePlan,
    CompiledGoal,
    FreshObservation,
    GoalStatus,
    PlannedAction,
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

# 会话面板展示的思考阶段。措辞对应 LangGraph 的真实节点，不额外编造步骤。
_STAGE_LABELS: dict[str, str] = {
    "observe": "读取设备实时状态",
    "understand": "理解意图与语义目标",
    "activate": "确认目标并接管",
    "plan": "生成候选计划并评分",
    "execute": "下发设备命令",
    "verify": "回读校验执行结果",
    "respond": "组织回复",
}


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
        self._last_graph_trace: list[str] = []
        self._lock = threading.RLock()
        self._turn_lock = threading.Lock()
        self._messages: list[dict[str, object]] = [
            {"role": "assistant", "content": "自治智能体已就绪：说出目标即可，我会规划、执行并持续盯住环境。", "ts": now()}
        ]
        self._turn: dict[str, object] | None = None
        self._turn_seq = 0
        self._workflow = LangGraphAgentWorkflow(
            AgentGraphHandlers(
                observe=self._graph_observe,
                understand=self._graph_understand,
                activate=self._graph_activate,
                plan=self._graph_plan,
                execute=self._graph_execute,
                verify=self._graph_verify,
                respond=self._graph_respond,
            ),
            on_stage=self._note_stage,
        )

    # ---- chat entry --------------------------------------------------
    def submit_text(self, text: str) -> dict[str, object]:
        """Run one user turn through the compiled LangGraph workflow."""
        normalized = text.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("message must contain 1 to 500 characters")
        if is_cancel_text(text):
            return self._cancel_goal()
        self._begin_turn(normalized)
        try:
            with self._lock:
                result = self._workflow.invoke({"text": normalized, "background": False, "trace": []})
                self._last_graph_trace = list(result.get("trace", []))
                return self.history()
        finally:
            self._finish_turn()

    # ---- streaming progress ------------------------------------------
    # 进度用独立的锁：规划期间 _lock 被 invoke 持有，轮询线程必须仍能读到
    # "现在走到哪一步"，否则前端只能干等到全部结束。
    def _begin_turn(self, text: str) -> None:
        with self._turn_lock:
            self._turn_seq += 1
            self._turn = {
                "turn_id": f"turn_{self._turn_seq}",
                "text": text,
                "stage": "queued",
                "stages": [],
                "started_at": now(),
                "done": False,
            }

    def _note_stage(self, name: str) -> None:
        if name not in _STAGE_LABELS:
            return
        with self._turn_lock:
            turn = self._turn
            if turn is None or turn["done"]:
                return
            turn["stage"] = name
            stages = turn["stages"]
            if isinstance(stages, list) and name not in stages:
                stages.append(name)

    def _finish_turn(self) -> None:
        with self._turn_lock:
            if self._turn is not None:
                self._turn["done"] = True
                self._turn["stage"] = "done"

    def progress(self) -> dict[str, object]:
        """Which graph stage the in-flight turn is on, readable while it runs."""
        with self._turn_lock:
            turn = dict(self._turn) if self._turn is not None else None
        if turn is None:
            return {"busy": False, "turn": None, "stage": "", "stage_label": "", "stages": []}
        stage = str(turn["stage"])
        stages = [
            {"name": name, "label": _STAGE_LABELS[name]}
            for name in turn["stages"]
            if name in _STAGE_LABELS
        ]
        return {
            "busy": not bool(turn["done"]),
            "turn": turn["turn_id"],
            "text": turn["text"],
            "stage": stage,
            "stage_label": _STAGE_LABELS.get(stage, "已完成" if stage == "done" else ""),
            "stages": stages,
            "started_at": turn["started_at"],
        }

    def history(self) -> dict[str, object]:
        # 会话与进度分开取：规划期间 _lock 被占用，历史只在拿到锁后读，
        # 而 progress() 不需要 _lock，因此轮询永远能拿到最新阶段。
        progress = self.progress()
        with self._lock:
            llm_provider, llm_model = _llm_identity(self._llm)
            return {
                "conversation_id": "agent",
                "messages": list(self._messages[-30:]),
                "plans": [],
                "active_plan": self._plan_view(),
                "task_plan": self._task_plan_view(),
                "busy": bool(progress["busy"]),
                "progress": progress,
                "llm_available": bool(self._llm and self._llm.available()),
                "llm_provider": llm_provider,
                "llm_model": llm_model,
                "orchestrator": "langgraph",
                "graph_trace": list(self._last_graph_trace),
            }

    def history_snapshot(self) -> dict[str, object]:
        """History for pollers: never waits on the planning lock.

        While a turn is in flight ``_lock`` is held for seconds, so the polling
        endpoint returns the pending turn plus live stage progress instead of
        blocking until planning ends.
        """
        progress = self.progress()
        if not progress["busy"]:
            return self.history()
        llm_provider, llm_model = _llm_identity(self._llm)
        messages = list(self._messages[-30:])
        pending_text = str(progress.get("text", ""))
        # 用户这句话此刻还没写进会话（respond 节点才落库），先乐观显示出来。
        if pending_text and not any(
            item.get("role") == "user" and item.get("content") == pending_text for item in messages[-2:]
        ):
            messages.append({"role": "user", "content": pending_text, "ts": progress["started_at"], "pending": True})
        return {
            "conversation_id": "agent",
            "messages": messages,
            "plans": [],
            "active_plan": None,
            # 轮询期间保留上一回合的计划：置 None 会让前端每次轮询都把
            # "意图与任务拆解"面板刷成空态，并盖掉 POST 返回的真实计划。
            "task_plan": self._task_plan_view(),
            "busy": True,
            "progress": progress,
            "llm_available": bool(self._llm and self._llm.available()),
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "orchestrator": "langgraph",
            "graph_trace": list(self._last_graph_trace),
        }

    def clear(self) -> dict[str, object]:
        """Clear visible conversation state without changing physical devices."""
        with self._lock:
            self._messages = [
                {"role": "assistant", "content": "LangGraph Agent 会话已清空，设备状态保持不变。", "ts": now()}
            ]
            self._last_outcome = None
            self._last_graph_trace = []
            return self.history()

    # ---- planning core (shared by submit & replan) -------------------
    def _plan_and_execute(
        self,
        goal: CompiledGoal,
        state: object,
        registry: dict[str, object],
    ) -> PlanningOutcome:
        result = self._workflow.invoke({"goal": goal, "background": True, "trace": []})
        self._last_graph_trace = list(result.get("trace", []))
        outcome = result.get("outcome")
        if not isinstance(outcome, PlanningOutcome):
            raise RuntimeError("LangGraph workflow completed without a planning outcome")
        return outcome

    def _graph_observe(self, graph_state: AgentGraphState) -> dict[str, object]:
        payload = self._devices_provider()
        devices = payload.get("devices")
        if not isinstance(devices, list):
            raise RuntimeError("device inventory is invalid")
        room_state = self._world.update_state(devices)
        if not self._last_signature:
            self._last_signature = _device_signatures(devices)
        return {
            "devices": devices,
            "room_state": room_state,
            "registry": build_capability_registry(devices),
        }

    def _graph_understand(self, graph_state: AgentGraphState) -> dict[str, object]:
        text = str(graph_state.get("text", ""))
        room_state = graph_state["room_state"]
        active_goal, active_status = self._world.active_goal()
        semantic_context: dict[str, object] = {
            "current_state": room_state.to_dict(),
            "active_goal": active_goal.to_dict() if active_goal is not None else None,
            "active_goal_status": active_status.value,
            "recent_dialogue": [
                {"role": item.get("role"), "content": item.get("content")}
                for item in self._messages[-4:]
            ],
        }
        goal, compile_mode, semantic = self._compiler.compile_with_state(
            text,
            self._llm,
            room_state.temperature,
            room_state.brightness_pct,
            semantic_context,
        )
        if goal is None:
            rejection_reason = {
                "unavailable": "本地模型不可用，未执行",
                "invalid": "模型输出未通过目标校验，未执行",
            }.get(compile_mode, "模型判断这不是家居目标")
            self._world.record_event(
                "goal_rejected",
                rejection_reason,
                semantic=semantic,
                planner_mode=compile_mode,
            )
        return {
            "goal": goal,
            "compile_mode": compile_mode,
            "semantic": semantic,
            "accepted": goal is not None,
        }

    def _graph_activate(self, graph_state: AgentGraphState) -> dict[str, object]:
        goal = graph_state["goal"]
        if goal is None:
            return {"accepted": False}
        compile_mode = str(graph_state.get("compile_mode", "llm"))
        old_goal, old_status = self._world.active_goal()
        self._world.clear_suppressions()
        if (
            old_goal is not None
            and old_goal.goal_id != goal.goal_id
            and old_status in {GoalStatus.ACTIVE, GoalStatus.DEGRADED, GoalStatus.REPLANNING, GoalStatus.SATISFIED}
        ):
            self._world.set_active_goal(old_goal, GoalStatus.CANCELLED)
            self._world.record_event(
                "goal_cancelled",
                f"目标「{old_goal.title}」被新目标替换",
                replaced_by=goal.goal_id,
            )
        self._world.set_active_goal(goal, GoalStatus.ACTIVE)
        self._persist_goal_event(goal, GoalStatus.ACTIVE, "goal_received")
        self._goal_manager.reset_tracking()
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
        return {"accepted": True}

    def _graph_plan(self, graph_state: AgentGraphState) -> dict[str, object]:
        goal = graph_state["goal"]
        if goal is None:
            raise RuntimeError("LangGraph plan node requires a compiled goal")
        room_state = graph_state["room_state"]
        registry = graph_state["registry"]
        self._world.record_event("world_snapshot", "已捕获真实环境快照", **room_state.to_dict())
        preference = self._preferences.estimate(goal.activity, goal.room, "brightness")
        recent_events = self._world.snapshot()["timeline"][-5:]
        suppressions = self._world.active_suppressions(goal.goal_id)
        outcome = self._planner.plan(
            goal,
            room_state,
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
            self._persist_goal_event(
                goal,
                GoalStatus.SATISFIED if outcome.no_action else GoalStatus.ACTIVE,
                "goal_already_satisfied" if outcome.no_action else "no_safe_plan",
            )
            self._last_outcome = outcome
            return {"outcome": outcome}
        selected: CandidatePlan = outcome.selected
        self._world.record_event(
            "plan_selected",
            outcome.reason,
            candidate_id=selected.candidate_id,
            total_score=selected.scores.total,
        )
        self._world.record_event("policy_check", "Policy Guard 校验通过", actions=len(selected.actions))
        return {"outcome": outcome}

    def _graph_execute(self, graph_state: AgentGraphState) -> dict[str, object]:
        outcome = graph_state["outcome"]
        selected = outcome.selected
        if selected is None:
            raise RuntimeError("LangGraph execute node requires a selected plan")
        records = self._executor.execute(selected, self._world)
        self._world.record_event("execution", f"已发送 {len(records)} 条设备命令")
        return {"records": records}

    def _graph_verify(self, graph_state: AgentGraphState) -> dict[str, object]:
        goal = graph_state["goal"]
        outcome = graph_state["outcome"]
        selected = outcome.selected
        if goal is None or selected is None:
            raise RuntimeError("LangGraph verify node requires a goal and selected plan")
        records = graph_state.get("records", [])
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
        return {"observation": observation, "failures": failures, "outcome": outcome}

    def _graph_respond(self, graph_state: AgentGraphState) -> dict[str, object]:
        text = str(graph_state.get("text", ""))
        goal = graph_state.get("goal")
        background = bool(graph_state.get("background"))
        if goal is None:
            mode = str(graph_state.get("compile_mode", "unavailable"))
            reply = (
                "当前没有可用的本地模型，语义目标未解析，未向任何设备发送命令。"
                if mode == "unavailable"
                else "模型输出未通过目标校验，未向任何设备发送命令。"
                if mode == "invalid"
                else "这条输入没有被模型识别为可执行的家居目标，未向设备发送命令。"
            )
            if not background:
                self._append("user", text)
                self._append("assistant", reply)
            return {"accepted": False, "reply": reply}
        outcome = graph_state.get("outcome")
        if not isinstance(outcome, PlanningOutcome):
            raise RuntimeError("LangGraph respond node requires a planning outcome")
        reply = self._goal_reply(goal, outcome)
        if not background:
            self._append("user", text)
            self._append("assistant", reply)
        return {"accepted": True, "reply": reply}

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

    def observe_now(self) -> dict[str, object]:
        """Run one observation/decision cycle immediately, outside the poll loop.

        演示时扰动注入后需要立刻看到决策链刷新，不能等一个轮询周期。
        错误按后台循环的同一套口径记录，避免调用方拿到半截状态。
        """
        try:
            self._tick()
        except (RuntimeError, TimeoutError, ValueError, OSError) as error:
            self._last_runtime_error = str(error)
            self._world.record_event("runtime_error", f"运行时错误：{error}")
        finally:
            self._last_tick_at = now()
        return self.status()

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
        self._append("user", "取消当前目标")
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

    def _task_plan_view(self) -> dict[str, object] | None:
        """Project the planning outcome into the 意图与任务拆解 panel's shape.

        The panel needs intent/summary/steps; ``PlanningOutcome`` speaks
        candidates/selected/actions. Without this projection every field the
        panel reads is undefined and it renders empty.
        """
        outcome = self._last_outcome
        if outcome is None:
            return None
        goal, goal_status = self._world.active_goal()
        selected = outcome.selected
        actions = list(selected.actions) if selected is not None else []
        if outcome.no_action:
            status = "completed"
        elif not actions:
            status = "clarification"
        elif all(action.verified is True for action in actions):
            status = "completed"
        elif any(action.verified is False for action in actions):
            status = "partial_success" if any(action.verified for action in actions) else "failed"
        else:
            status = "executing"
        constraints = [item.to_dict() for item in goal.constraints] if goal is not None else []
        return {
            "plan_id": goal.goal_id if goal is not None else "--",
            "intent": "device_control" if actions else "query_state",
            "route": f"langgraph · {outcome.planner_mode}",
            "status": status,
            "summary": goal.title if goal is not None else outcome.reason,
            "reasoning": outcome.reason,
            "goal_status": goal_status.value,
            "activity": goal.activity if goal is not None else "",
            "room": goal.room if goal is not None else "",
            "constraints": constraints,
            "candidate_count": len(outcome.candidates),
            "planner_mode": outcome.planner_mode,
            "steps": [_task_step(action) for action in actions],
        }

    def _append(self, role: str, content: str) -> None:
        with self._lock:
            self._messages.append({"role": role, "content": content, "ts": now()})

    def record_turn(self, role: str, content: str) -> None:
        """Record a turn that bypassed planning, such as a workflow trigger.

        Deterministic workflow execution does not go through the graph, but the
        conversation still has to show what the user said and what ran.
        """
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        self._append(role, content)

    def status(self) -> dict[str, object]:
        snapshot = self._world.snapshot()
        llm_provider, llm_model = _llm_identity(self._llm)
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
                "llm_provider": llm_provider,
                "llm_model": llm_model,
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
            "orchestrator": "langgraph",
            "graph_nodes": list(self._workflow.node_names),
            "last_graph_trace": list(self._last_graph_trace),
            "thread_alive": thread_alive,
            "last_tick": self._last_tick_at,
            "last_tick_age_seconds": round(tick_age, 3) if tick_age is not None else None,
            "recent_tick": recent_tick,
            "fatal_error": self._fatal_error,
            "last_error": self._last_runtime_error,
            "status": "ready" if thread_alive and recent_tick and self._fatal_error is None else "degraded",
        }

def _llm_identity(client: object | None) -> tuple[str, str]:
    if client is None:
        return "", ""
    provider = str(getattr(client, "provider_label", "") or "")
    model = str(getattr(client, "model", "") or getattr(client, "_model", "") or "")
    if provider:
        return provider, model
    channels = getattr(client, "channels", None)
    if isinstance(channels, list):
        ready = next((item for item in channels if isinstance(item, dict) and item.get("ready")), None)
        if isinstance(ready, dict):
            return str(ready.get("label", "")), str(ready.get("model", ""))
    return "LLM", model


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


def _task_step(action: PlannedAction) -> dict[str, object]:
    """One PlannedAction as the task-breakdown panel's step record."""
    verified = getattr(action, "verified", None)
    status = "pending" if verified is None else "success" if verified else "failed"
    observed = getattr(action, "observed", None)
    error = ""
    if isinstance(observed, dict) and isinstance(observed.get("error"), str):
        error = str(observed["error"])
    return {
        "device_id": getattr(action, "device_id", ""),
        "device_name": getattr(action, "device_name", "") or getattr(action, "device_id", ""),
        "entity_id": getattr(action, "entity_id", ""),
        "action": getattr(action, "capability", ""),
        "value": getattr(action, "value", None),
        "mode": getattr(action, "mode", None),
        "status": status,
        "verified": bool(verified),
        "reason": _step_reason(action),
        "after": _observed_evidence(observed) if not error else None,
        "error": error,
    }


def _observed_evidence(observed: object) -> dict[str, object] | None:
    """Reshape a raw HA entity payload into the panel's evidence shape.

    ``observed`` is what HA returned (``{"state": ..., "attributes": {...}}``);
    the panel reads a flat ``{state: {power|mode|position|...}}`` record, so the
    read-back line renders words instead of dumped JSON.
    """
    if not isinstance(observed, dict):
        return None
    raw_state = observed.get("state")
    attributes = observed.get("attributes") if isinstance(observed.get("attributes"), dict) else {}
    state: dict[str, object] = {}
    if isinstance(attributes.get("current_position"), (int, float)):
        state["position"] = round(float(attributes["current_position"]))
    elif isinstance(attributes.get("temperature"), (int, float)):
        state["mode"] = str(raw_state or "")
        state["temperature"] = float(attributes["temperature"])
    else:
        state["power"] = "ON" if str(raw_state or "").lower() == "on" else "OFF"
        if isinstance(attributes.get("brightness"), (int, float)):
            state["brightness"] = float(attributes["brightness"])
    return {"state": state}


def _step_reason(action: PlannedAction) -> str:
    capability = str(getattr(action, "capability", ""))
    value = getattr(action, "value", None)
    mode = getattr(action, "mode", None)
    if capability == "set_brightness":
        return f"将亮度调整到 {value:g}%" if isinstance(value, (int, float)) else "调整亮度"
    if capability == "set_position":
        return f"将窗帘调整到 {value:g}%" if isinstance(value, (int, float)) else "调整窗帘"
    if capability == "set_temperature":
        mode_label = {"cool": "制冷", "heat": "制热"}.get(str(mode or ""), str(mode or ""))
        return f"设定 {value:g}°C{f'（{mode_label}）' if mode_label else ''}" if isinstance(value, (int, float)) else "设定温度"
    if capability == "set_hvac_mode":
        return {
            "cool": "切换到制冷",
            "heat": "切换到制热",
            "off": "关闭空调",
        }.get(str(value or ""), f"切换模式 {value}")
    if capability == "turn_off":
        return "关闭设备"
    if capability == "turn_on":
        return "开启设备"
    return capability


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
