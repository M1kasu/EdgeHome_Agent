"""Home World Model: the single source of truth every planner reads.

All state comes from the live workbench inventory (HA + ESPHome merged);
the model also tracks the active goal, the agent's own action log (for
state-change attribution), counterfactual predictions and a bounded
event timeline for the workbench UI.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
from typing import Any

from spacebutler.agent.models import (
    ActionRecord,
    AgentEvent,
    CompiledGoal,
    FreshObservation,
    GoalConstraint,
    GoalStatus,
    LlmContribution,
    OverrideSuppression,
    RoomState,
    now,
)


class HomeWorldModel:
    """Thread-safe world state + agent memory."""

    def __init__(self, timeline_max: int = 200, action_log_max: int = 100) -> None:
        self._lock = threading.RLock()
        self._state = RoomState()
        self._active_goal: CompiledGoal | None = None
        self._goal_status: GoalStatus = GoalStatus.CREATED
        self._events: deque[AgentEvent] = deque(maxlen=timeline_max)
        self._actions: deque[ActionRecord] = deque(maxlen=action_log_max)
        self._predictions: deque[dict[str, Any]] = deque(maxlen=20)
        self._suppressions: dict[tuple[str, str, str], OverrideSuppression] = {}
        self._planner_mode: str = "fallback"
        self._contribution = LlmContribution()

    # ---- perception -------------------------------------------------
    def update_state(self, devices: list[dict[str, object]]) -> RoomState:
        state = _perceive(devices)
        with self._lock:
            self._state = state
        return state

    def refresh_from_home_assistant(
        self,
        devices: list[dict[str, object]],
        observed_at: float | None = None,
    ) -> FreshObservation:
        """Atomically install a post-action HA snapshot and mark its provenance."""
        captured_at = observed_at or now()
        state = _perceive(devices, captured_at=captured_at)
        entity_states = {
            str(item.get("entity_id")): item["ha_state"]
            for item in devices
            if isinstance(item, dict)
            and item.get("entity_id")
            and isinstance(item.get("ha_state"), dict)
        }
        with self._lock:
            self._state = state
        return FreshObservation(
            observed_at=captured_at,
            source="HOME_ASSISTANT",
            state=state,
            entity_states=entity_states,
        )

    def current_state(self) -> RoomState:
        with self._lock:
            return self._state

    # ---- goal -------------------------------------------------------
    def set_active_goal(self, goal: CompiledGoal | None, status: GoalStatus) -> None:
        with self._lock:
            self._active_goal = goal
            self._goal_status = status

    def active_goal(self) -> tuple[CompiledGoal | None, GoalStatus]:
        with self._lock:
            return self._active_goal, self._goal_status

    # ---- human override suppression ---------------------------------
    def apply_human_override(
        self,
        goal: CompiledGoal,
        device_id: str,
        dimension: str,
        observed_value: object,
        ttl_seconds: float,
        at: float | None = None,
    ) -> OverrideSuppression:
        created_at = at or now()
        suppression = OverrideSuppression(
            goal_id=goal.goal_id,
            device_id=device_id,
            dimension=dimension,
            observed_value=observed_value,
            created_at=created_at,
            expires_at=created_at + ttl_seconds,
        )
        with self._lock:
            self._suppressions[(goal.goal_id, device_id, dimension)] = suppression
            _absorb_override_constraint(goal, dimension, observed_value)
        return suppression

    def active_suppressions(self, goal_id: str | None = None) -> list[OverrideSuppression]:
        at = now()
        with self._lock:
            expired = [key for key, item in self._suppressions.items() if not item.active(at)]
            for key in expired:
                self._suppressions.pop(key, None)
            return [
                item
                for item in self._suppressions.values()
                if goal_id is None or item.goal_id == goal_id
            ]

    def clear_suppressions(self, goal_id: str | None = None) -> None:
        with self._lock:
            if goal_id is None:
                self._suppressions.clear()
                return
            for key in [key for key in self._suppressions if key[0] == goal_id]:
                self._suppressions.pop(key, None)

    # ---- agent action log (attribution source) ----------------------
    def record_action(self, record: ActionRecord) -> None:
        with self._lock:
            self._actions.append(record)

    def recent_actions(self, device_id: str, within_seconds: float, at: float) -> list[ActionRecord]:
        with self._lock:
            return [
                item
                for item in self._actions
                if item.device_id == device_id and at - item.sent_at <= within_seconds
            ]

    def update_action(self, action_id: str, **fields: Any) -> None:
        with self._lock:
            for item in self._actions:
                if item.action_id == action_id:
                    for key, value in fields.items():
                        setattr(item, key, value)
                    return

    def latest_prediction(self) -> dict[str, Any] | None:
        with self._lock:
            return self._predictions[-1] if self._predictions else None

    def record_prediction(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._predictions.append(payload)

    # ---- timeline ---------------------------------------------------
    def record_event(self, kind: str, message: str, **data: Any) -> AgentEvent:
        event = AgentEvent(ts=now(), kind=kind, message=message, data=data)
        with self._lock:
            self._events.append(event)
        return event

    def set_planner_mode(self, mode: str) -> None:
        with self._lock:
            self._planner_mode = mode

    def planner_mode(self) -> str:
        with self._lock:
            return self._planner_mode

    # ---- LLM contribution metrics ------------------------------------
    def set_contribution(self, contribution: LlmContribution) -> None:
        with self._lock:
            self._contribution = contribution

    def contribution(self) -> LlmContribution:
        with self._lock:
            return self._contribution

    # ---- UI snapshot ------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            goal, status = self._active_goal, self._goal_status
            return {
                "current_state": self._state.to_dict(),
                "active_goal": goal.to_dict() if goal else None,
                "goal_status": status.value,
                "planner_mode": self._planner_mode,
                "llm_contribution": self._contribution.to_dict(),
                "recent_actions": [item.to_dict() for item in list(self._actions)[-12:]],
                "last_prediction": self._predictions[-1] if self._predictions else None,
                "override_suppressions": [
                    item.to_dict() for item in self.active_suppressions(goal.goal_id if goal else None)
                ],
                "timeline": [item.to_dict() for item in list(self._events)[-40:]],
            }


def _perceive(devices: list[dict[str, object]], captured_at: float | None = None) -> RoomState:
    """Project the merged inventory into one room state (living room first)."""
    state = RoomState(captured_at=captured_at or now())
    living = [
        item
        for item in devices
        if isinstance(item, dict) and str(item.get("room", "")) == "living_room"
    ]

    def pick(device_type: str) -> dict[str, object] | None:
        for item in living:
            if item.get("type") == device_type and item.get("online"):
                return item
        return None

    climate = pick("climate")
    if climate is not None:
        climate_state = climate.get("state") if isinstance(climate.get("state"), dict) else {}
        state.ac_mode = str(climate_state.get("mode", "off"))
        target = climate_state.get("temperature")
        state.ac_target = float(target) if isinstance(target, (int, float)) else None
        current = climate_state.get("current_temperature")
        if isinstance(current, (int, float)):
            state.temperature = float(current)

    temperature = pick("temperature")
    if temperature is not None:
        temperature_state = temperature.get("state") if isinstance(temperature.get("state"), dict) else {}
        value = temperature_state.get("temperature")
        if isinstance(value, (int, float)):
            state.temperature = float(value)

    presence = pick("presence")
    if presence is not None:
        presence_state = presence.get("state") if isinstance(presence.get("state"), dict) else {}
        state.presence = bool(presence_state.get("occupied"))

    light = pick("light")
    if light is not None:
        light_state = light.get("state") if isinstance(light.get("state"), dict) else {}
        state.light_on = str(light_state.get("power", "")).upper() == "ON"
        brightness = light_state.get("brightness")
        if isinstance(brightness, (int, float)):
            state.brightness_pct = round(float(brightness) * 100 / 255, 1)

    curtain = pick("curtain")
    if curtain is not None:
        curtain_state = curtain.get("state") if isinstance(curtain.get("state"), dict) else {}
        position = curtain_state.get("position")
        if isinstance(position, (int, float)):
            state.curtain_position = float(position)
    return state


def _absorb_override_constraint(goal: CompiledGoal, dimension: str, value: object) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        semantic = dict(goal.semantic_view or {})
        overrides = list(semantic.get("human_overrides", []))
        overrides.append({"dimension": dimension, "value": value})
        semantic["human_overrides"] = overrides[-5:]
        goal.semantic_view = semantic
        return
    tolerances = {"brightness": 2.0, "curtain_position": 2.0, "temperature": 0.5}
    limits = {
        "brightness": (1.0, 100.0),
        "curtain_position": (0.0, 100.0),
        "temperature": (16.0, 30.0),
    }
    if dimension not in tolerances:
        return
    numeric = float(value)
    low_limit, high_limit = limits[dimension]
    tolerance = tolerances[dimension]
    replacement = GoalConstraint(
        metric=dimension,
        lower=max(low_limit, numeric - tolerance),
        upper=min(high_limit, numeric + tolerance),
        target=numeric,
    )
    for index, constraint in enumerate(goal.constraints):
        if constraint.metric == dimension:
            goal.constraints[index] = replacement
            break
    else:
        goal.constraints.append(replacement)
    semantic = dict(goal.semantic_view or {})
    overrides = list(semantic.get("human_overrides", []))
    overrides.append({"dimension": dimension, "value": numeric})
    semantic["human_overrides"] = overrides[-5:]
    goal.semantic_view = semantic
