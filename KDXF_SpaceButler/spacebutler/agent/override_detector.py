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
from spacebutler.llm.client import LlmClient

_AGENT_ACTION_WINDOW_SECONDS = 90.0
_INTERPRET_MIN_CONFIDENCE = 0.6
_METRIC_BY_DIMENSION = {"lighting.brightness": "brightness", "thermal.temperature": "temperature", "glare.position": "curtain_position"}
_INTERPRET_PROMPT = (
    "你是家庭智能体的纠正解释器。Agent 设置了设备状态，随后用户手动改成了另一个值。"
    "判断这次人工纠正的含义。只输出一个 JSON 对象：\n"
    '{"override_interpretation": "preference_correction|transient_operation", '
    '"dimension": "lighting.brightness|thermal.temperature|glare.position|null", '
    '"direction": "higher|lower|null", "confidence": 0.0~1.0, '
    '"reason": "一句话理由"}\n'
    "例如用户把灯从 30% 调到 45% 且当前有观影目标，通常是偏好纠正（希望更亮一点）。"
    "上下文不足或像误触时给 transient_operation 与低置信度。"
)
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
        activity: str = "watch_content",
        room: str = "living_room",
        llm_client: LlmClient | None = None,
    ) -> None:
        self._world = world
        self._preferences = preferences
        self._activity = activity
        self._room = room
        self._llm = llm_client

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
            # 人工纠正：先由模型语义解释（偏好纠正 vs 偶发操作），
            # 置信度达标且目标活跃才写入偏好观测（上下文绑定 + 防误触）。
            _, status = self._world.active_goal()
            goal_active = status in {
                GoalStatus.ACTIVE,
                GoalStatus.DEGRADED,
                GoalStatus.REPLANNING,
                GoalStatus.SATISFIED,
            }
            interpretation = self._interpret(capability, previous_value, new_value) if goal_active else None
            # LLM 可用：置信度门控（偏好纠正且 >=0.6 才写入）；LLM 不可用：
            # 退回确定性规则（目标活跃即记录，统计层仍有最少样本约束）。
            trusted = (
                (bool(interpretation)
                 and float(interpretation.get("confidence", 0.0)) >= _INTERPRET_MIN_CONFIDENCE
                 and interpretation.get("override_interpretation") == "preference_correction")
                if interpretation is not None
                else True
            )
            dimension_metric = _METRIC_BY_DIMENSION.get(str(interpretation.get("dimension", "")), metric) if interpretation else metric
            if goal_active and trusted and dimension_metric == "brightness":
                self._preferences.observe(self._activity, self._room, dimension_metric, float(new_value), at)
            self._world.record_event(
                "human_override",
                f"检测到人工调整 {device_id}：{previous_value} → {new_value}"
                + (f"（{interpretation.get('override_interpretation')}，置信 {interpretation.get('confidence')}）" if interpretation else ""),
                device_id=device_id,
                metric=metric,
                previous=previous_value,
                new=new_value,
                attribution=ActionAttribution.HUMAN_OVERRIDE.value,
                interpretation=interpretation,
                preference_recorded=bool(goal_active and trusted and dimension_metric == "brightness"),
            )
            return ActionAttribution.HUMAN_OVERRIDE
        self._world.record_event(
            "device_change",
            f"{device_id} 状态变化：{previous_value} → {new_value}",
            device_id=device_id,
            attribution=ActionAttribution.UNKNOWN.value,
        )
        return ActionAttribution.UNKNOWN


    def _interpret(self, capability: str, previous_value: object, new_value: object) -> dict | None:
        """模型语义解释这次人工纠正；不可用/非法输出返回 None（退回确定性记录）。"""
        if self._llm is None or not self._llm.available():
            return None
        goal, _ = self._world.active_goal()
        raw = self._llm.chat_json(
            _INTERPRET_PROMPT,
            (
                f"当前目标：{goal.title if goal else '无'}\n"
                f"设备：{capability}；Agent 设置值：{previous_value}；用户改为：{new_value}"
            ),
        )
        return raw if isinstance(raw, dict) and raw.get("override_interpretation") else None


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
