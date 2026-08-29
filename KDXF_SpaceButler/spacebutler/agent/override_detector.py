"""State-change attribution: agent vs human vs environment.

绝对不能出现 "AI 调灯 30% → 用户调到 45% → AI 又调回 30%"：
- Agent 自己刚发的命令（action log 时间窗 + expected 匹配）→ AGENT_ACTION
- 可控设备的其他变化 → HUMAN_OVERRIDE（优先级高于 Agent：暂停自动纠正、
  记录偏好观测、更新本次目标约束认知）
- 传感器数值自然漂移 → ENVIRONMENT_CHANGE
"""

from __future__ import annotations

from spacebutler.agent.models import ActionAttribution, GoalStatus
from spacebutler.agent.preference_memory import PreferenceMemory
from spacebutler.agent.world_model import HomeWorldModel

_AGENT_ACTION_WINDOW_SECONDS = 90.0
_METRIC_BY_CAPABILITY = {
    "set_brightness": "brightness",
    "set_position": "curtain_position",
    "set_temperature": "temperature",
    "set_hvac_mode": "ac_mode",
}
_SENSORS = {"presence", "contact", "illuminance", "temperature"}


class OverrideDetector:
    def __init__(
        self,
        world: HomeWorldModel,
        preferences: PreferenceMemory,
        activity: str = "watch_movie",
        room: str = "living_room",
    ) -> None:
        self._world = world
        self._preferences = preferences
        self._activity = activity
        self._room = room

    def classify(
        self,
        device_id: str,
        device_type: str,
        capability: str,
        previous_value: object,
        new_value: object,
        at: float,
    ) -> ActionAttribution:
        if previous_value is None or new_value is None or previous_value == new_value:
            return ActionAttribution.UNKNOWN
        if device_type in _SENSORS:
            return ActionAttribution.ENVIRONMENT_CHANGE
        recent = self._world.recent_actions(device_id, _AGENT_ACTION_WINDOW_SECONDS, at)
        for record in recent:
            if _matches_expectation(record.capability, record.value, capability, new_value):
                return ActionAttribution.AGENT_ACTION
        metric = _METRIC_BY_CAPABILITY.get(capability)
        if metric is not None and isinstance(new_value, (int, float)):
            # 人工纠正：仅在目标活跃期间写入隐式偏好观测（上下文绑定，
            # 避免演示重置等无目标场景的状态恢复污染偏好）。
            _, status = self._world.active_goal()
            goal_active = status in {
                GoalStatus.ACTIVE,
                GoalStatus.DEGRADED,
                GoalStatus.REPLANNING,
                GoalStatus.SATISFIED,
            }
            if metric == "brightness" and goal_active:
                self._preferences.observe(self._activity, self._room, metric, float(new_value), at)
            self._world.record_event(
                "human_override",
                f"检测到人工调整 {device_id}：{previous_value} → {new_value}",
                device_id=device_id,
                metric=metric,
                previous=previous_value,
                new=new_value,
                attribution=ActionAttribution.HUMAN_OVERRIDE.value,
                preference_recorded=metric == "brightness" and goal_active,
            )
            return ActionAttribution.HUMAN_OVERRIDE
        self._world.record_event(
            "device_change",
            f"{device_id} 状态变化：{previous_value} → {new_value}",
            device_id=device_id,
            attribution=ActionAttribution.UNKNOWN.value,
        )
        return ActionAttribution.UNKNOWN


def _matches_expectation(
    record_capability: str,
    record_value: object,
    change_capability: str,
    new_value: object,
) -> bool:
    if record_capability != change_capability:
        return False
    try:
        return abs(float(record_value) - float(new_value)) <= 2.0
    except (TypeError, ValueError):
        return str(record_value) == str(new_value)
