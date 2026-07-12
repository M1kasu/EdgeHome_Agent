"""Offline-safe structured scene plans for the tracked virtual EdgeHome home.

These plans are deliberately built *inside* the planning boundary.  They are only a
fallback when the local model cannot produce valid JSON, and are parsed through the
same registry, schema, safety guard, and executor as model-produced plans.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from ..entity_registry import EdgeHomeEntityRegistry, build_demo_entity_registry
from ..models import EntitySnapshot, OnFailurePolicy, PlanStep, TaskPlan, VerificationRule
from .templates import SceneName


class DemoSceneDraft(BaseModel):
    """A validated-plan candidate plus an optional separately scheduled follow-up."""

    model_config = ConfigDict(extra="forbid")

    plan: TaskPlan
    delayed_plan: TaskPlan | None = None
    delay_seconds: int | None = Field(default=None, ge=1, le=7 * 24 * 60 * 60)


class DemoScenePlanFactory:
    """Build minimal plans from scene semantics, never by matching a whole sentence."""

    def __init__(self, registry: EdgeHomeEntityRegistry | None = None) -> None:
        self._registry = registry or build_demo_entity_registry()

    _LIVING_MAIN = "light.edgehome_living_room_main"
    _LIVING_AMBIENT = "light.edgehome_living_room_ambient"
    _LIVING_TV = "switch.edgehome_living_room_tv"
    _LIVING_AC = "climate.edgehome_living_room_ac"
    _LIVING_CURTAIN = "cover.edgehome_living_room_curtain"
    _BEDROOM_MAIN = "light.edgehome_bedroom_main"
    _BEDROOM_AC = "climate.edgehome_bedroom_ac"
    _TEST_SOCKET = "switch.edgehome_test_socket"
    _SECURITY = "input_boolean.edgehome_security_mode"

    def build(
        self,
        goal: str,
        scene: SceneName,
        snapshots: list[EntitySnapshot] | None = None,
    ) -> DemoSceneDraft | None:
        """Return one explicit virtual-demo plan for a supported scene semantic."""
        if scene == SceneName.MOVIE:
            return DemoSceneDraft(plan=self._movie(goal, snapshots or []))
        if scene == SceneName.LEAVING:
            return DemoSceneDraft(plan=self._leaving(goal, snapshots or []))
        if scene == SceneName.SLEEPING:
            return self._sleeping(goal)
        return None

    def _movie(self, goal: str, snapshots: list[EntitySnapshot]) -> TaskPlan:
        """Create a state-aware scene using only independently verified MQTT devices."""
        devices = {record.device_type: record.entity_id for record in self._registry.for_scene("movie", protocol="mqtt")}
        expected_domains = {"light", "cover", "switch", "climate"}
        if set(devices) != expected_domains:
            return TaskPlan(
                goal=goal,
                scene=SceneName.MOVIE.value,
                need_clarification=True,
                clarification_question="观影场景所需 MQTT 设备尚未全部就绪，请先检查主灯、窗帘、电视和空调。",
                steps=[PlanStep(id="movie_read_living", tool="get_room_state", arguments={"room": "living_room"})],
            )
        current = {snapshot.entity_id: snapshot for snapshot in snapshots}
        light_id, curtain_id, tv_id, climate_id = (devices["light"], devices["cover"], devices["switch"], devices["climate"])
        brightness, temperature = self._movie_brightness(goal), self._movie_temperature(goal)
        steps = [PlanStep(id="movie_read_living", tool="get_room_state", arguments={"room": "living_room"})]
        if _current_position(current.get(curtain_id)) != 0:
            steps.append(self._curtain_step("movie_close_curtain", curtain_id, 0, on_failure=OnFailurePolicy.SKIP))
        if not _brightness_matches(current.get(light_id), brightness):
            steps.append(self._brightness_step("movie_dim_main", light_id, brightness))
        if _state(current.get(tv_id)) != "on":
            steps.append(self._on_step("movie_enable_tv", tv_id))
        if _state(current.get(climate_id)) != "cool":
            steps.append(self._hvac_mode_step("movie_enable_ac", climate_id, "cool"))
        if not _temperature_matches(current.get(climate_id), temperature):
            steps.append(self._temperature_step("movie_set_ac_temperature", climate_id, temperature))
        return TaskPlan(
            goal=goal,
            scene=SceneName.MOVIE.value,
            steps=steps,
        )

    def _leaving(self, goal: str, snapshots: list[EntitySnapshot]) -> TaskPlan:
        """Turn off only verified MQTT devices after M6 safety prechecks pass."""
        devices = {record.device_type: record.entity_id for record in self._registry.for_scene("leaving", protocol="mqtt")}
        expected_domains = {"light", "cover", "switch", "climate"}
        if set(devices) != expected_domains:
            return TaskPlan(
                goal=goal,
                scene=SceneName.LEAVING.value,
                need_clarification=True,
                clarification_question="离家保护所需 MQTT 设备尚未全部就绪，请先检查客厅设备连接。",
                steps=[PlanStep(id="leave_read_home", tool="get_home_state", arguments={})],
            )
        current = {snapshot.entity_id: snapshot for snapshot in snapshots}
        light_id, curtain_id, tv_id, climate_id = (devices["light"], devices["cover"], devices["switch"], devices["climate"])
        safety_sensors = self._registry.for_scene("leaving", protocol="safety_sensor")
        policies = self._registry.for_scene("leaving", protocol="safety_policy")
        steps = [PlanStep(id="leave_read_home", tool="get_home_state", arguments={}), PlanStep(id="leave_read_living", tool="get_room_state", arguments={"room": "living_room"})]
        steps.extend(PlanStep(id=f"leave_read_{index}", tool="get_device_state", arguments={"entity_id": record.entity_id}) for index, record in enumerate(safety_sensors, start=1))
        if _state(current.get(light_id)) != "off":
            steps.append(self._off_step("leave_main_light", light_id))
        if _state(current.get(tv_id)) != "off":
            steps.append(self._off_step("leave_tv", tv_id))
        if _state(current.get(climate_id)) != "off":
            steps.append(self._off_step("leave_living_ac", climate_id))
        if _current_position(current.get(curtain_id)) != 0:
            steps.append(self._curtain_step("leave_close_curtain", curtain_id, 0, on_failure=OnFailurePolicy.STOP))
        for policy in policies:
            if _state(current.get(policy.entity_id)) != "on":
                steps.append(
                    PlanStep(
                        id="leave_enable_security",
                        tool="activate_security_mode",
                        arguments={"entity_id": policy.entity_id},
                        verify=VerificationRule(entity_id=policy.entity_id, state="on"),
                    )
                )
        return TaskPlan(
            goal=goal,
            scene=SceneName.LEAVING.value,
            steps=steps,
        )

    def _sleeping(self, goal: str) -> DemoSceneDraft:
        delay_seconds = self._delay_seconds(goal)
        if delay_seconds is None:
            return DemoSceneDraft(
                plan=TaskPlan(
                    goal=goal,
                    scene=SceneName.SLEEPING.value,
                    need_clarification=True,
                    clarification_question="客厅空调是否关闭？如需延时，请告诉我等待多久。",
                    steps=[PlanStep(id="sleep_read_living", tool="get_room_state", arguments={"room": "living_room"})],
                )
            )
        temperature = self._bedroom_temperature(goal)
        delayed_plan = TaskPlan(
            goal="睡眠场景延时关闭客厅空调",
            scene="sleeping_delayed",
            steps=[self._off_step("sleep_delayed_living_ac", self._LIVING_AC)],
        )
        return DemoSceneDraft(
            plan=TaskPlan(
                goal=goal,
                scene=SceneName.SLEEPING.value,
                steps=[
                    PlanStep(id="sleep_read_bedroom", tool="get_room_state", arguments={"room": "bedroom"}),
                    self._off_step("sleep_turn_off_tv", self._LIVING_TV),
                    self._temperature_step("sleep_set_bedroom_temperature", self._BEDROOM_AC, temperature),
                    self._on_step("sleep_enable_bedroom_ac", self._BEDROOM_AC, expected_state="cool"),
                ],
            ),
            delayed_plan=delayed_plan,
            delay_seconds=delay_seconds,
        )

    @classmethod
    def _on_step(cls, step_id: str, entity_id: str, expected_state: str = "on") -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="turn_on_device",
            arguments={"entity_id": entity_id},
            verify=VerificationRule(entity_id=entity_id, state=expected_state),
        )

    @classmethod
    def _off_step(cls, step_id: str, entity_id: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="turn_off_device",
            arguments={"entity_id": entity_id},
            verify=VerificationRule(entity_id=entity_id, state="off"),
        )

    @classmethod
    def _brightness_step(cls, step_id: str, entity_id: str, brightness: int) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="set_light_brightness",
            arguments={"entity_id": entity_id, "brightness": brightness},
            verify=VerificationRule(
                entity_id=entity_id,
                state="on",
                attributes={"brightness": round(brightness * 255 / 100)},
                tolerance=2.0,
            ),
        )

    @classmethod
    def _temperature_step(cls, step_id: str, entity_id: str, temperature: float) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="set_temperature",
            arguments={"entity_id": entity_id, "temperature": temperature},
            verify=VerificationRule(entity_id=entity_id, attributes={"temperature": temperature}, tolerance=0.5),
        )

    @classmethod
    def _hvac_mode_step(cls, step_id: str, entity_id: str, mode: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="set_hvac_mode",
            arguments={"entity_id": entity_id, "hvac_mode": mode},
            verify=VerificationRule(entity_id=entity_id, state=mode),
        )

    @classmethod
    def _curtain_step(cls, step_id: str, entity_id: str, position: int, on_failure: OnFailurePolicy) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="control_curtain",
            arguments={"entity_id": entity_id, "position": position},
            verify=VerificationRule(
                entity_id=cls._LIVING_CURTAIN,
                attributes={"current_position": position},
                tolerance=1.0,
            ),
            on_failure=on_failure,
        )

    @staticmethod
    def _bedroom_temperature(goal: str) -> float:
        match = re.search(r"卧室(?:空调)?[^。；，,]*?(\d{2}(?:\.\d+)?)\s*(?:度|℃)?", goal)
        if match:
            value = float(match.group(1))
            if 16.0 <= value <= 30.0:
                return value
        return 26.0

    @staticmethod
    def _movie_brightness(goal: str) -> int:
        match = re.search(r"(?:灯|亮度|照明)[^。；，,]*?(\d{1,3})\s*(?:%|％|百分之)", goal)
        if match and 0 <= int(match.group(1)) <= 100:
            return int(match.group(1))
        return 30

    @staticmethod
    def _movie_temperature(goal: str) -> float:
        match = re.search(r"(?:空调|制冷|温度)[^。；，,]*?(\d{2}(?:\.\d+)?)\s*(?:度|℃)", goal)
        if match and 16.0 <= float(match.group(1)) <= 30.0:
            return float(match.group(1))
        return 25.5

    @staticmethod
    def _delay_seconds(goal: str) -> int | None:
        normalized = "".join(goal.split())
        if "半小时" in normalized:
            return 30 if "测试模式" in normalized else 30 * 60
        match = re.search(r"(\d+)\s*(秒钟?|分钟|小时)后", normalized)
        if match is None:
            return None
        value = int(match.group(1))
        unit = match.group(2)
        multiplier = 1 if unit.startswith("秒") else 60 if unit == "分钟" else 60 * 60
        seconds = value * multiplier
        return seconds if 1 <= seconds <= 7 * 24 * 60 * 60 else None


def _state(snapshot: EntitySnapshot | None) -> str | None:
    return snapshot.state if snapshot is not None else None


def _current_position(snapshot: EntitySnapshot | None) -> int | None:
    value = snapshot.attributes.get("current_position") if snapshot is not None else None
    return int(value) if isinstance(value, (int, float)) else None


def _brightness_matches(snapshot: EntitySnapshot | None, target: int) -> bool:
    if snapshot is None or snapshot.state != "on":
        return False
    value = snapshot.attributes.get("brightness")
    return isinstance(value, (int, float)) and abs(int(value) - round(target * 255 / 100)) <= 2


def _temperature_matches(snapshot: EntitySnapshot | None, target: float) -> bool:
    value = snapshot.attributes.get("temperature") if snapshot is not None else None
    return isinstance(value, (int, float)) and abs(float(value) - target) <= 0.1
