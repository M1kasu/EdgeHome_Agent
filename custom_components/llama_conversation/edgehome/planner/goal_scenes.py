"""Goal-driven scene policies used only after model planning is unavailable."""

from __future__ import annotations

import re

from ..entity_registry import EdgeHomeEntityRegistry, build_demo_entity_registry
from ..light import brightness_verification
from ..models import (
    ConstraintType,
    EntitySnapshot,
    GoalSpec,
    OnFailurePolicy,
    PlanConstraint,
    PlanStep,
    RiskAssessment,
    RiskLevel,
    TaskPlan,
    VerificationRule,
)
from .demo_scenes import DemoSceneDraft
from .templates import SceneName


class GoalDrivenScenePlanner:
    """Build a constrained policy plan from a GoalSpec and a live state snapshot."""

    def __init__(self, registry: EdgeHomeEntityRegistry | None = None) -> None:
        self._registry = registry or build_demo_entity_registry()

    def build(
        self,
        goal: str,
        goal_spec: GoalSpec,
        scene: SceneName,
        snapshots: list[EntitySnapshot],
    ) -> DemoSceneDraft | None:
        """Return a state-aware plan or a clarification, never a fixed sentence match."""
        if scene == SceneName.MOVIE:
            return DemoSceneDraft(plan=self._movie(goal, goal_spec, snapshots))
        if scene == SceneName.LEAVING:
            return DemoSceneDraft(plan=self._leaving(goal, goal_spec, snapshots))
        return None

    def _movie(self, goal: str, goal_spec: GoalSpec, snapshots: list[EntitySnapshot]) -> TaskPlan:
        if (
            goal_spec.target_rooms
            and "bedroom" in goal_spec.target_rooms
            and "living_room" not in goal_spec.target_rooms
            or self._is_forbidden(goal_spec, "room:living_room")
        ):
            return TaskPlan(
                goal=goal,
                scene=SceneName.MOVIE.value,
                goal_spec=goal_spec,
                need_clarification=True,
                clarification_question="卧室尚未接入独立 MQTT 观影设备，请确认是否改为执行客厅观影场景。",
                constraints=self._constraints(goal_spec),
                steps=[self._read_room("movie_read_bedroom", "bedroom")],
            )

        devices = self._scene_devices(SceneName.MOVIE, "mqtt")
        missing = {"light", "cover", "switch", "climate"} - set(devices)
        if missing:
            return TaskPlan(
                goal=goal,
                scene=SceneName.MOVIE.value,
                goal_spec=goal_spec,
                need_clarification=True,
                clarification_question="观影场景所需 MQTT 设备尚未全部就绪，请先检查主灯、窗帘、电视和空调。",
                constraints=self._constraints(goal_spec),
                steps=[self._read_room("movie_read_living", "living_room")],
            )

        current = {snapshot.entity_id: snapshot for snapshot in snapshots}
        light_id, curtain_id, tv_id, climate_id = (
            devices["light"],
            devices["cover"],
            devices["switch"],
            devices["climate"],
        )
        illuminance = self._first_numeric(snapshots, "illuminance")
        room_temperature = self._first_numeric(snapshots, "temperature")
        target_brightness = self._movie_brightness(goal, goal_spec, illuminance)
        target_temperature = self._movie_temperature(goal, goal_spec, room_temperature)
        skip_climate = self._is_forbidden(goal_spec, "climate")

        steps = [self._read_room("movie_read_living", "living_room")]
        curtain_position = self._current_position(current.get(curtain_id))
        if curtain_position is None or curtain_position > (15 if illuminance and illuminance > 250 else 45):
            steps.append(
                self._curtain_step(
                    "movie_adjust_curtain",
                    curtain_id,
                    0,
                    reason="观影目标要求降低眩光，当前窗帘位置或光照需要调整。",
                    on_failure=OnFailurePolicy.SKIP,
                )
            )
        if not self._brightness_matches(current.get(light_id), target_brightness):
            steps.append(
                self._brightness_step(
                    "movie_set_light",
                    light_id,
                    target_brightness,
                    reason=f"根据当前光照与用户约束设置主灯到 {target_brightness}%。",
                )
            )
        if self._state(current.get(tv_id)) != "on":
            steps.append(self._on_step("movie_enable_tv", tv_id, reason="观影目标需要打开影音设备。"))
        if not skip_climate and target_temperature is not None:
            if self._state(current.get(climate_id)) != "cool":
                steps.append(self._hvac_mode_step("movie_enable_ac", climate_id, "cool", reason="当前室温或舒适目标需要制冷。"))
            if not self._temperature_matches(current.get(climate_id), target_temperature):
                steps.append(
                    self._temperature_step(
                        "movie_set_ac_temperature",
                        climate_id,
                        target_temperature,
                        reason=f"根据环境温度与用户目标设置空调到 {target_temperature:g}℃。",
                    )
                )

        return TaskPlan(
            goal=goal,
            scene=SceneName.MOVIE.value,
            goal_spec=goal_spec,
            rationale="基于当前光照、温度、设备状态和用户约束生成观影计划。",
            constraints=self._constraints(goal_spec),
            risk_assessment=RiskAssessment(level=RiskLevel.LOW, reasons=["仅控制独立 MQTT 客厅设备"]),
            steps=steps,
        )

    def _leaving(self, goal: str, goal_spec: GoalSpec, snapshots: list[EntitySnapshot]) -> TaskPlan:
        devices = self._scene_devices(SceneName.LEAVING, "mqtt")
        current = {snapshot.entity_id: snapshot for snapshot in snapshots}
        safety_entities = list(self._registry.for_scene(SceneName.LEAVING.value, protocol="safety_sensor"))
        policy_entities = list(self._registry.for_scene(SceneName.LEAVING.value, protocol="safety_policy"))
        safety_blockers = self._safety_blockers(snapshots)
        read_steps = [PlanStep(id="leave_read_home", tool="get_home_state", arguments={}, reason="离家目标先读取全屋状态。")]
        read_steps.extend(self._read_room("leave_read_living", "living_room") for _ in (0,))
        read_steps.extend(
            PlanStep(
                id=f"leave_read_safety_{index}",
                tool="get_device_state",
                arguments={"entity_id": record.entity_id},
                reason="离家前检查门窗、人员或安防相关状态。",
            )
            for index, record in enumerate(safety_entities, start=1)
        )
        if safety_blockers:
            return TaskPlan(
                goal=goal,
                scene=SceneName.LEAVING.value,
                goal_spec=goal_spec,
                need_clarification=True,
                clarification_question="离家保护暂不执行："
                + "；".join(safety_blockers)
                + "。请处理后再确认是否执行离家保护。",
                constraints=self._constraints(goal_spec),
                risk_assessment=RiskAssessment(level=RiskLevel.HIGH, reasons=safety_blockers, blocked=True),
                steps=read_steps,
            )

        steps = list(read_steps)
        for domain, step_id in (("light", "leave_turn_off_light"), ("switch", "leave_turn_off_tv"), ("climate", "leave_turn_off_ac")):
            entity_id = devices.get(domain)
            if entity_id and self._state(current.get(entity_id)) != "off":
                steps.append(self._off_step(step_id, entity_id, reason="离家目标关闭非必要客厅设备。"))
        curtain_id = devices.get("cover")
        if curtain_id and self._current_position(current.get(curtain_id)) != 0:
            steps.append(
                self._curtain_step(
                    "leave_close_curtain",
                    curtain_id,
                    0,
                    reason="离家安全目标要求关闭客厅窗帘。",
                    on_failure=OnFailurePolicy.STOP,
                )
            )
        for record in policy_entities:
            if self._state(current.get(record.entity_id)) != "on":
                steps.append(
                    PlanStep(
                        id="leave_enable_security",
                        tool="activate_security_mode",
                        arguments={"entity_id": record.entity_id},
                        verify=VerificationRule(entity_id=record.entity_id, state="on"),
                        reason="离家目标需要启用安防模式。",
                        expected_outcome="安防模式开启",
                        evidence_required=["ha_state"],
                    )
                )
        return TaskPlan(
            goal=goal,
            scene=SceneName.LEAVING.value,
            goal_spec=goal_spec,
            rationale="基于在家状态、门窗状态、保护设备和当前设备状态生成离家计划。",
            constraints=self._constraints(goal_spec),
            risk_assessment=RiskAssessment(level=RiskLevel.LOW, reasons=["保护设备未纳入控制步骤"]),
            steps=steps,
        )

    def _scene_devices(self, scene: SceneName, protocol: str) -> dict[str, str]:
        return {record.device_type: record.entity_id for record in self._registry.for_scene(scene.value, protocol=protocol)}

    @staticmethod
    def _constraints(goal_spec: GoalSpec) -> list[PlanConstraint]:
        return [PlanConstraint.model_validate(constraint.model_dump(mode="json")) for constraint in goal_spec.constraints[:8]]

    @staticmethod
    def _is_forbidden(goal_spec: GoalSpec, target: str) -> bool:
        return any(
            constraint.constraint_type == ConstraintType.PROHIBITION and constraint.target in {target, "protected_device"}
            for constraint in goal_spec.constraints
        )

    @staticmethod
    def _movie_brightness(goal: str, goal_spec: GoalSpec, illuminance: float | None) -> int:
        explicit = re.search(r"(?:灯|亮度|照明|主灯)[^。；，,]*?(\d{1,3})\s*(?:%|％|百分之|成)?", goal)
        if explicit is not None:
            value = int(explicit.group(1))
            if value <= 10 and "成" in explicit.group(0):
                value *= 10
            return max(0, min(100, value))
        minimum = next(
            (
                int(constraint.value)
                for constraint in goal_spec.constraints
                if constraint.constraint_type == ConstraintType.MIN_VALUE and constraint.target == "light" and isinstance(constraint.value, int)
            ),
            None,
        )
        if illuminance is None:
            value = 30
        elif illuminance >= 700:
            value = 20
        elif illuminance >= 300:
            value = 30
        else:
            value = 45
        return max(value, minimum or 0)

    @staticmethod
    def _movie_temperature(goal: str, goal_spec: GoalSpec, room_temperature: float | None) -> float | None:
        explicit = re.search(r"(?:空调|制冷|温度)[^。；，,]*?(\d{2}(?:\.\d+)?)\s*(?:度|℃)", goal)
        minimum = next(
            (
                float(constraint.value)
                for constraint in goal_spec.constraints
                if constraint.constraint_type == ConstraintType.MIN_VALUE and constraint.target == "climate" and isinstance(constraint.value, (int, float))
            ),
            None,
        )
        if explicit is not None and 16.0 <= float(explicit.group(1)) <= 30.0:
            value = float(explicit.group(1))
            return max(value, minimum) if minimum is not None else value
        if room_temperature is not None and room_temperature >= 29:
            value = 25.0
        elif room_temperature is not None and room_temperature >= 27:
            value = 26.0
        else:
            return None
        return max(value, minimum) if minimum is not None else value

    @staticmethod
    def _safety_blockers(snapshots: list[EntitySnapshot]) -> list[str]:
        blockers: list[str] = []
        for snapshot in snapshots:
            device_class = snapshot.attributes.get("device_class")
            if snapshot.domain in {"person", "device_tracker"} and snapshot.state == "home":
                blockers.append(f"{snapshot.friendly_name or snapshot.entity_id} 仍在家")
            elif snapshot.domain == "binary_sensor" and device_class in {"door", "window", "garage_door"}:
                if snapshot.state == "on":
                    blockers.append(f"{snapshot.friendly_name or snapshot.entity_id} 仍开启")
                elif snapshot.state == "unavailable":
                    blockers.append(f"{snapshot.friendly_name or snapshot.entity_id} 不可用")
            elif snapshot.domain == "binary_sensor" and device_class == "presence" and snapshot.state == "on":
                blockers.append(f"{snapshot.friendly_name or snapshot.entity_id} 检测到有人")
        return blockers[:6]

    @staticmethod
    def _first_numeric(snapshots: list[EntitySnapshot], device_class: str) -> float | None:
        for snapshot in snapshots:
            if snapshot.domain == "sensor" and snapshot.attributes.get("device_class") == device_class:
                try:
                    return float(snapshot.state)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _state(snapshot: EntitySnapshot | None) -> str | None:
        return snapshot.state if snapshot is not None else None

    @staticmethod
    def _current_position(snapshot: EntitySnapshot | None) -> int | None:
        value = snapshot.attributes.get("current_position") if snapshot is not None else None
        return int(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _brightness_matches(snapshot: EntitySnapshot | None, target: int) -> bool:
        if snapshot is None or snapshot.state != "on":
            return False
        value = snapshot.attributes.get("brightness")
        return isinstance(value, (int, float)) and abs(int(value) - round(target * 255 / 100)) <= 2

    @staticmethod
    def _temperature_matches(snapshot: EntitySnapshot | None, target: float) -> bool:
        if snapshot is None:
            return False
        value = snapshot.attributes.get("temperature")
        return isinstance(value, (int, float)) and abs(float(value) - target) <= 0.5

    @staticmethod
    def _read_room(step_id: str, room: str) -> PlanStep:
        return PlanStep(id=step_id, tool="get_room_state", arguments={"room": room}, reason="读取目标房间当前状态。")

    @staticmethod
    def _on_step(step_id: str, entity_id: str, *, reason: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="turn_on_device",
            arguments={"entity_id": entity_id},
            verify=VerificationRule(entity_id=entity_id, state="on"),
            reason=reason,
            expected_outcome="设备开启",
            evidence_required=["ha_state", "mqtt_state"],
        )

    @staticmethod
    def _off_step(step_id: str, entity_id: str, *, reason: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="turn_off_device",
            arguments={"entity_id": entity_id},
            verify=VerificationRule(entity_id=entity_id, state="off"),
            reason=reason,
            expected_outcome="设备关闭",
            evidence_required=["ha_state", "mqtt_state"],
        )

    @staticmethod
    def _brightness_step(step_id: str, entity_id: str, brightness: int, *, reason: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="set_light_brightness",
            arguments={"entity_id": entity_id, "brightness": brightness},
            verify=brightness_verification(entity_id, brightness),
            reason=reason,
            expected_outcome=f"亮度达到 {brightness}%",
            evidence_required=["ha_state", "mqtt_state"],
        )

    @staticmethod
    def _temperature_step(step_id: str, entity_id: str, temperature: float, *, reason: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="set_temperature",
            arguments={"entity_id": entity_id, "temperature": temperature},
            verify=VerificationRule(entity_id=entity_id, attributes={"temperature": temperature}, tolerance=0.5),
            reason=reason,
            expected_outcome=f"目标温度达到 {temperature:g}℃",
            evidence_required=["ha_state", "mqtt_state"],
        )

    @staticmethod
    def _hvac_mode_step(step_id: str, entity_id: str, mode: str, *, reason: str) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="set_hvac_mode",
            arguments={"entity_id": entity_id, "hvac_mode": mode},
            verify=VerificationRule(entity_id=entity_id, state=mode),
            reason=reason,
            expected_outcome=f"空调模式为 {mode}",
            evidence_required=["ha_state", "mqtt_state"],
        )

    @staticmethod
    def _curtain_step(step_id: str, entity_id: str, position: int, *, reason: str, on_failure: OnFailurePolicy) -> PlanStep:
        return PlanStep(
            id=step_id,
            tool="control_curtain",
            arguments={"entity_id": entity_id, "position": position},
            verify=VerificationRule(entity_id=entity_id, attributes={"current_position": position}, tolerance=1.0),
            on_failure=on_failure,
            reason=reason,
            expected_outcome=f"窗帘位置达到 {position}%",
            evidence_required=["ha_state", "mqtt_state"],
        )
