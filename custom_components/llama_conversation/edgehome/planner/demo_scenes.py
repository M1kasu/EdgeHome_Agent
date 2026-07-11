"""Offline-safe structured scene plans for the tracked virtual EdgeHome home.

These plans are deliberately built *inside* the planning boundary.  They are only a
fallback when the local model cannot produce valid JSON, and are parsed through the
same registry, schema, safety guard, and executor as model-produced plans.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from ..models import OnFailurePolicy, PlanStep, TaskPlan, VerificationRule
from .templates import SceneName


class DemoSceneDraft(BaseModel):
    """A validated-plan candidate plus an optional separately scheduled follow-up."""

    model_config = ConfigDict(extra="forbid")

    plan: TaskPlan
    delayed_plan: TaskPlan | None = None
    delay_seconds: int | None = Field(default=None, ge=1, le=7 * 24 * 60 * 60)


class DemoScenePlanFactory:
    """Build minimal plans from scene semantics, never by matching a whole sentence."""

    _LIVING_MAIN = "light.edgehome_living_room_main"
    _LIVING_AMBIENT = "light.edgehome_living_room_ambient"
    _LIVING_TV = "switch.edgehome_living_room_tv"
    _LIVING_AC = "climate.edgehome_living_room_ac"
    _LIVING_CURTAIN = "cover.edgehome_living_room_curtain"
    _BEDROOM_MAIN = "light.edgehome_bedroom_main"
    _BEDROOM_AC = "climate.edgehome_bedroom_ac"
    _TEST_SOCKET = "switch.edgehome_test_socket"
    _SECURITY = "input_boolean.edgehome_security_mode"

    def build(self, goal: str, scene: SceneName) -> DemoSceneDraft | None:
        """Return one explicit virtual-demo plan for a supported scene semantic."""
        if scene == SceneName.MOVIE:
            return DemoSceneDraft(plan=self._movie(goal))
        if scene == SceneName.LEAVING:
            return DemoSceneDraft(plan=self._leaving(goal))
        if scene == SceneName.SLEEPING:
            return self._sleeping(goal)
        return None

    def _movie(self, goal: str) -> TaskPlan:
        return TaskPlan(
            goal=goal,
            scene=SceneName.MOVIE.value,
            steps=[
                PlanStep(id="movie_read_living", tool="get_room_state", arguments={"room": "living_room"}),
                self._curtain_step("movie_close_curtain", 0, on_failure=OnFailurePolicy.SKIP),
                self._brightness_step("movie_dim_main", self._LIVING_MAIN, 30),
                self._on_step("movie_enable_ambient", self._LIVING_AMBIENT),
                self._on_step("movie_enable_tv", self._LIVING_TV),
                self._temperature_step("movie_set_ac_temperature", self._LIVING_AC, 25.5),
                self._on_step("movie_enable_ac", self._LIVING_AC, expected_state="cool"),
            ],
        )

    def _leaving(self, goal: str) -> TaskPlan:
        return TaskPlan(
            goal=goal,
            scene=SceneName.LEAVING.value,
            steps=[
                PlanStep(id="leave_read_home", tool="get_home_state", arguments={}),
                PlanStep(id="leave_read_living", tool="get_room_state", arguments={"room": "living_room"}),
                self._off_step("leave_main_light", self._LIVING_MAIN),
                self._off_step("leave_ambient_light", self._LIVING_AMBIENT),
                self._off_step("leave_tv", self._LIVING_TV),
                self._off_step("leave_living_ac", self._LIVING_AC),
                self._off_step("leave_bedroom_light", self._BEDROOM_MAIN),
                self._off_step("leave_bedroom_ac", self._BEDROOM_AC),
                self._off_step("leave_test_socket", self._TEST_SOCKET),
                PlanStep(
                    id="leave_read_window",
                    tool="get_device_state",
                    arguments={"entity_id": "binary_sensor.edgehome_window"},
                ),
                PlanStep(
                    id="leave_enable_security",
                    tool="activate_security_mode",
                    arguments={"entity_id": self._SECURITY},
                    verify=VerificationRule(entity_id=self._SECURITY, state="on"),
                ),
            ],
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
    def _curtain_step(cls, step_id: str, position: int, on_failure: OnFailurePolicy) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="control_curtain",
            arguments={"entity_id": cls._LIVING_CURTAIN, "position": position},
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
