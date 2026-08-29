"""Goal lifecycle + anti-oscillation guards.

 CREATED -> ACTIVE -> (SATISFIED | DEGRADED -> REPLANNING -> ACTIVE) -> CANCELLED / EXPIRED

防止震荡：无意义小变化不触发重规划（min change）、偏离需要连续多次观测
确认（debounce）、两次重规划之间有冷却时间（cooldown）、重试有上限、
目标有超时。
"""

from __future__ import annotations

from spacebutler.agent.models import CompiledGoal, GoalConstraint, GoalStatus, RoomState, now

_DEBOUNCE_TICKS = 2
_DEBOUNCE_INTERVAL_SECONDS = 3.0
_REPLAN_COOLDOWN_SECONDS = 45.0
_MAX_REPLANS = 6
_MIN_DEVIATION = 2.0
_GOAL_TIMEOUT_SECONDS = 30 * 60.0


class GoalManager:
    def __init__(
        self,
        world: object,
        debounce_ticks: int = _DEBOUNCE_TICKS,
        cooldown_seconds: float = _REPLAN_COOLDOWN_SECONDS,
        max_replans: int = _MAX_REPLANS,
    ) -> None:
        self._world = world
        self._debounce_ticks = debounce_ticks
        self._cooldown_seconds = cooldown_seconds
        self._max_replans = max_replans
        self._deviation_streak = 0
        self._last_replan_at = 0.0
        self._replan_count = 0
        self._was_satisfied = False

    def reset_tracking(self) -> None:
        self._deviation_streak = 0
        self._last_replan_at = 0.0
        self._replan_count = 0
        self._was_satisfied = False

    def evaluate(self, goal: CompiledGoal, state: RoomState) -> tuple[GoalStatus, str]:
        """One goal-evaluation tick. Returns (new_status, reason)."""
        at = now()
        if goal.expires_at is not None and at > goal.expires_at:
            return GoalStatus.EXPIRED, "目标超时，自动结束。"
        values = _constraint_values(state)
        unmet = [c for c in goal.constraints if not c.satisfied_by(values.get(c.metric))]
        if not unmet:
            self._deviation_streak = 0
            self._was_satisfied = True
            return GoalStatus.SATISFIED, "全部目标约束已满足。"
        significant = any(_significant_distance(c, values.get(c.metric)) for c in unmet)
        if not significant:
            return GoalStatus.ACTIVE, "存在轻微偏离，仍在容忍范围内，不动作。"
        if not self._was_satisfied:
            return GoalStatus.ACTIVE, "目标尚未达成过，首次规划仍在执行中。"
        # 曾经满足过，现在显著偏离 -> 可能需要重规划，但要有 debounce + cooldown。
        self._deviation_streak += 1
        if self._deviation_streak < self._debounce_ticks:
            return GoalStatus.DEGRADED, f"检测到目标偏离（连续 {self._deviation_streak} 次观测，等待确认）。"
        if at - self._last_replan_at < self._cooldown_seconds:
            return GoalStatus.DEGRADED, "偏离已确认，但处于重规划冷却期，暂不动作。"
        if self._replan_count >= self._max_replans:
            return GoalStatus.PAUSED, "重规划次数已达上限，暂停自动调整，等待用户新指令。"
        self._deviation_streak = 0
        self._replan_count += 1
        self._last_replan_at = at
        return GoalStatus.REPLANNING, f"目标偏离已确认，触发第 {self._replan_count} 次重规划。"

    def note_replanned(self) -> None:
        self._was_satisfied = True


def _constraint_values(state: RoomState) -> dict[str, float | None]:
    return {
        "brightness": state.brightness_pct,
        "temperature": state.temperature,
        "curtain_position": state.curtain_position,
    }


def _significant_distance(constraint: GoalConstraint, value: float | None) -> bool:
    return constraint.distance(value) > _MIN_DEVIATION
