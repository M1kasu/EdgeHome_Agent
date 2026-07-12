"""Resolve structured conversation references without matching whole user sentences."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from ..context.models import ConversationState
from ..entity_resolver import EntityNameResolver
from ..light import brightness_verification
from ..models import PlanStep, TaskPlan, VerificationRule


class ContextIntent(StrEnum):
    """The narrow set of conversation semantics that need a prior verified state."""

    NONE = "none"
    LAST_SETTING = "last_setting"
    CURRENT_STATE = "current_state"
    LAST_ENTITY = "last_entity"
    LAST_SUCCESS = "last_success"
    RELATIVE_BRIGHTNESS = "relative_brightness"
    TURN_OFF_REFERENCE = "turn_off_reference"
    COPY_LAST_VALUE = "copy_last_value"


@dataclass(frozen=True, slots=True)
class ContextExpression:
    """Parsed reference semantics, independent from an entity name or test text."""

    intent: ContextIntent
    increase: bool | None = None
    amount: int | None = None


_LAST_MARKER = re.compile(r"(?:刚才|刚刚|上一次|上回|之前)")
_QUERY_MARKER = re.compile(r"(?:多少|状态|情况|成功|哪个|哪盏|哪一个|什么)")
_SETTING_MARKER = re.compile(r"(?:设置|设为|调到|调成|亮度|参数)")
_CURRENT_MARKER = re.compile(r"(?:当前|现在|目前)")
_ENTITY_MARKER = re.compile(r"(?:设备|灯|灯光|照明|哪个|哪盏)")
_SUCCESS_MARKER = re.compile(r"(?:成功|完成|执行结果)")
_REFERENCE_MARKER = re.compile(r"(?:它|这个|那个|刚才|目前|上一)")
_TURN_OFF = re.compile(r"(?:关(?:掉|闭|上)?(?:吧)?|熄灭)")
_COPY_MARKER = re.compile(r"(?:一样|同样|这个值)")
_RELATIVE = re.compile(
    r"(?P<direction>调高|提高|增加|升高|调低|降低|减少|降|调亮|调暗|亮|暗)"
    r"(?:\s*(?P<amount>\d{1,3})\s*(?:%|％|个?百分点)?)?"
)
_INCREASE_WORDS = frozenset({"调高", "提高", "增加", "升高", "调亮", "亮"})


class ReferencePlanResolver:
    """Build safe plans or read-only answers from one session's verified state."""

    def __init__(self, entity_resolver: EntityNameResolver) -> None:
        self._entity_resolver = entity_resolver

    def expression(self, text: str) -> ContextExpression:
        """Parse general context grammar without encoding any complete acceptance phrase."""
        normalized = "".join(text.strip().split()).rstrip("。！!？?")
        if not normalized:
            return ContextExpression(ContextIntent.NONE)
        if _LAST_MARKER.search(normalized) and _SUCCESS_MARKER.search(normalized):
            return ContextExpression(ContextIntent.LAST_SUCCESS)
        if _LAST_MARKER.search(normalized) and _ENTITY_MARKER.search(normalized) and _QUERY_MARKER.search(normalized):
            return ContextExpression(ContextIntent.LAST_ENTITY)
        if _LAST_MARKER.search(normalized) and (_SETTING_MARKER.search(normalized) or _QUERY_MARKER.search(normalized)):
            return ContextExpression(ContextIntent.LAST_SETTING)
        if _CURRENT_MARKER.search(normalized) and _QUERY_MARKER.search(normalized):
            return ContextExpression(ContextIntent.CURRENT_STATE)
        relative = _RELATIVE.search(normalized)
        if relative is not None:
            direction = relative.group("direction")
            amount = relative.group("amount")
            # A directional adjective without an explicit target only becomes a
            # context command when it carries an incremental modifier or amount.
            if amount is not None or "再" in normalized or "一点" in normalized or "一些" in normalized:
                return ContextExpression(
                    ContextIntent.RELATIVE_BRIGHTNESS,
                    increase=direction in _INCREASE_WORDS,
                    amount=int(amount) if amount is not None else 10,
                )
        if _COPY_MARKER.search(normalized) and (_REFERENCE_MARKER.search(normalized) or "另一个" in normalized or "的" in normalized):
            return ContextExpression(ContextIntent.COPY_LAST_VALUE)
        if _TURN_OFF.search(normalized) and (_REFERENCE_MARKER.search(normalized) or normalized.endswith("吧")):
            return ContextExpression(ContextIntent.TURN_OFF_REFERENCE)
        return ContextExpression(ContextIntent.NONE)

    def requires_prior_context(self, text: str) -> bool:
        """Return true for any expression that must not guess a target in a new session."""
        return self.expression(text).intent != ContextIntent.NONE

    def query_speech(self, text: str, state: ConversationState) -> str | None:
        """Answer a query from verified state and a fresh HA read; never call a device service."""
        expression = self.expression(text)
        if expression.intent == ContextIntent.LAST_SETTING:
            return self._last_setting_speech(state)
        if expression.intent == ContextIntent.CURRENT_STATE:
            return self._current_state_speech(state)
        if expression.intent == ContextIntent.LAST_ENTITY:
            return self._last_entity_speech(state)
        if expression.intent == ContextIntent.LAST_SUCCESS:
            return self._last_success_speech(state)
        return None

    def resolve(self, text: str, state: ConversationState) -> TaskPlan | None:
        """Build only one validated reference plan for a prior verified control target."""
        expression = self.expression(text)
        entity_id = state.last_entity_id
        if entity_id is None:
            return None
        if expression.intent == ContextIntent.TURN_OFF_REFERENCE:
            return self._turn_off_last(text, entity_id)
        if expression.intent == ContextIntent.RELATIVE_BRIGHTNESS:
            return self._relative_brightness(text, state, expression)
        if expression.intent == ContextIntent.COPY_LAST_VALUE:
            target = self._target_entity(text, entity_id)
            if target is not None and state.last_plan is not None:
                return self._copy_with_entity(text, state.last_plan, entity_id, target)
        return None

    def _last_setting_speech(self, state: ConversationState) -> str:
        entity_id = state.last_entity_id
        if entity_id is None:
            return "当前会话没有已验证的设备设置，请说明要查询的设备。"
        name = self._entity_name(entity_id)
        requested = state.last_requested_parameters or state.last_parameters
        actual = self._fresh_actual_state(entity_id) or state.last_actual_state or self._last_result_actual(state)
        requested_text = _parameter_speech(requested)
        actual_text = _actual_state_speech(actual)
        return f"上次已验证设置：{name}{requested_text}；{actual_text}。"

    def _current_state_speech(self, state: ConversationState) -> str:
        entity_id = state.last_entity_id
        if entity_id is None:
            return "当前会话没有可查询的设备，请说明设备名称。"
        name = self._entity_name(entity_id)
        actual = self._fresh_actual_state(entity_id)
        if actual is None:
            return f"{name}当前状态不可读取。"
        return f"{name}{_actual_state_speech(actual)}。"

    def _last_entity_speech(self, state: ConversationState) -> str:
        if state.last_entity_id is None:
            return "当前会话没有已验证的设备控制记录。"
        return f"上次已验证控制的设备是{self._entity_name(state.last_entity_id)}。"

    def _last_success_speech(self, state: ConversationState) -> str:
        if state.last_successful_action or state.last_action:
            return "上一次已验证设备操作成功。"
        return "当前会话没有已验证的成功操作。"

    def _relative_brightness(self, text: str, state: ConversationState, expression: ContextExpression) -> TaskPlan | None:
        if self._last_action(state) != "set_light_brightness" or expression.amount is None or expression.increase is None:
            return None
        brightness = self._current_brightness(state.last_entity_id)
        if brightness is None:
            brightness = self._stored_brightness(state)
        if brightness is None:
            return None
        entity_id = state.last_entity_id
        if entity_id is None:
            return None
        delta = expression.amount if expression.increase else -expression.amount
        target = max(0, min(100, brightness + delta))
        return TaskPlan(
            goal=text,
            scene="dialogue_reference",
            steps=[
                PlanStep(
                    id="reference_1",
                    tool="set_light_brightness",
                    arguments={"entity_id": entity_id, "brightness": target},
                    verify=brightness_verification(entity_id, target),
                )
            ],
        )

    def _target_entity(self, text: str, source_entity_id: str) -> str | None:
        source_domain = source_entity_id.split(".", maxsplit=1)[0]
        try:
            target = self._entity_resolver.resolve_name(text, {source_domain}).snapshot.entity_id
        except Exception:
            target = None
        if target is not None:
            return target if target != source_entity_id else None

        area_match = re.match(r"\s*([^的\s]+)的?(?:也|同).+", text)
        if area_match is None:
            return None
        area_hint = area_match.group(1)
        candidates: list[str] = []
        for candidate in self._entity_resolver.hass.states.async_all():
            if candidate.domain != source_domain or candidate.entity_id == source_entity_id:
                continue
            friendly_name = candidate.attributes.get("friendly_name")
            if isinstance(friendly_name, str) and area_hint in friendly_name:
                candidates.append(candidate.entity_id)
                continue
            try:
                snapshot = self._entity_resolver.snapshot(candidate.entity_id, {source_domain})
            except Exception:
                continue
            if snapshot.area_name and area_hint in snapshot.area_name:
                candidates.append(snapshot.entity_id)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _copy_with_entity(text: str, plan: TaskPlan, source_entity_id: str, target_entity_id: str) -> TaskPlan:
        steps = [_replace_entity(step, source_entity_id, target_entity_id) for step in plan.steps]
        return plan.model_copy(update={"goal": text, "scene": "dialogue_reference", "steps": steps})

    @staticmethod
    def _turn_off_last(text: str, entity_id: str) -> TaskPlan:
        return TaskPlan(
            goal=text,
            scene="dialogue_reference",
            steps=[PlanStep(id="reference_1", tool="turn_off_device", arguments={"entity_id": entity_id}, verify=VerificationRule(entity_id=entity_id, state="off"))],
        )

    def _current_brightness(self, entity_id: str | None) -> int | None:
        if entity_id is None:
            return None
        state = self._entity_resolver.hass.states.get(entity_id)
        if state is None or state.domain != "light":
            return None
        brightness = state.attributes.get("brightness")
        if not isinstance(brightness, (int, float)):
            return None
        return round(float(brightness) * 100 / 255)

    def _fresh_actual_state(self, entity_id: str) -> dict[str, object] | None:
        try:
            snapshot = self._entity_resolver.snapshot(entity_id)
        except Exception:
            return None
        return {"state": snapshot.state, **dict(snapshot.attributes)}

    def _entity_name(self, entity_id: str) -> str:
        try:
            snapshot = self._entity_resolver.snapshot(entity_id)
        except Exception:
            return entity_id
        return snapshot.friendly_name or entity_id

    @staticmethod
    def _last_action(state: ConversationState) -> str | None:
        if state.last_successful_action or state.last_action:
            return state.last_successful_action or state.last_action
        if state.last_plan is not None:
            for step in reversed(state.last_plan.steps):
                if "entity_id" in step.arguments:
                    return step.tool
        return None

    @staticmethod
    def _stored_brightness(state: ConversationState) -> int | None:
        for parameters in (state.last_requested_parameters, state.last_parameters):
            brightness = parameters.get("brightness")
            if isinstance(brightness, int):
                return brightness
        if state.last_plan is not None:
            for step in reversed(state.last_plan.steps):
                brightness = step.arguments.get("brightness")
                if isinstance(brightness, int):
                    return brightness
        return None

    @staticmethod
    def _last_result_actual(state: ConversationState) -> dict[str, object]:
        return state.recent_results[-1].actual if state.recent_results else {}


def _parameter_speech(parameters: dict[str, object]) -> str:
    brightness = parameters.get("brightness")
    if isinstance(brightness, int):
        return f"请求亮度为{brightness}%"
    temperature = parameters.get("temperature")
    if isinstance(temperature, (int, float)):
        return f"请求温度为{temperature:g}°C"
    position = parameters.get("position")
    if isinstance(position, int):
        return f"请求位置为{position}%"
    return "已记录请求参数"


def _actual_state_speech(actual: dict[str, object]) -> str:
    brightness = actual.get("brightness")
    if isinstance(brightness, (int, float)):
        return f"当前实际亮度为{round(float(brightness) * 100 / 255)}%"
    state = actual.get("state")
    return f"当前实际状态为{state}" if isinstance(state, str) else "当前实际状态不可用"


def _replace_entity(step: PlanStep, source_entity_id: str, target_entity_id: str) -> PlanStep:
    arguments = dict(step.arguments)
    if arguments.get("entity_id") == source_entity_id:
        arguments["entity_id"] = target_entity_id
    verify = step.verify
    if verify and verify.entity_id == source_entity_id:
        verify = verify.model_copy(update={"entity_id": target_entity_id})
    preconditions = [
        condition.model_copy(update={"entity_id": target_entity_id}) if condition.entity_id == source_entity_id else condition
        for condition in step.preconditions
    ]
    return step.model_copy(update={"arguments": arguments, "verify": verify, "preconditions": preconditions})
