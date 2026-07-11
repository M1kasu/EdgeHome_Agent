"""受代码白名单约束的 EdgeHome Demo 自动化管理器。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from pydantic import ValidationError

from .schemas import EnergyAutomation, EnergyAutomationResult, EnergyPlan, EnergyRecommendation


_AUTOMATION_TARGETS = {
    "unoccupied_ac": ("climate.edgehome_living_room_ac", "climate", "turn_off"),
    "tv_standby": ("switch.edgehome_test_socket", "switch", "turn_off"),
    "night_light": ("light.edgehome_living_room_main", "light", "turn_off"),
}


class EnergyAutomationManager:
    """管理可查询、停用、删除的运行时自动化，绝不解释或执行模型 YAML。"""

    _KEY = "edgehome_energy_automations"
    _VERSION = 1

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store[dict[str, object]] = Store(hass, self._VERSION, self._KEY)
        self._automations: dict[str, EnergyAutomation] = {}
        self._unsubscribers: dict[str, Callable[[], None]] = {}
        self._loaded = False

    async def async_apply(
        self,
        plan: EnergyPlan,
        selected_recommendation_ids: list[str],
        confirmed: bool,
    ) -> EnergyAutomationResult:
        """只有显式确认和方案中的选中项才能创建实际可执行规则。"""
        if not confirmed:
            return EnergyAutomationResult(success=False, error_code="CONFIRMATION_REQUIRED")
        selected = [item for item in plan.recommendations if item.id in set(selected_recommendation_ids)]
        if not selected:
            return EnergyAutomationResult(success=False, error_code="NO_RECOMMENDATION_SELECTED")
        if len(selected) != len(set(selected_recommendation_ids)):
            return EnergyAutomationResult(success=False, error_code="UNKNOWN_RECOMMENDATION")
        await self._async_load()
        created: list[EnergyAutomation] = []
        for recommendation in selected:
            if not self._valid_recommendation(recommendation):
                return EnergyAutomationResult(success=False, error_code="AUTOMATION_SCHEMA_REJECTED")
            automation = EnergyAutomation(
                id=f"edgehome-demo-{uuid4().hex[:12]}",
                name=f"EdgeHome Demo {recommendation.title}",
                rule_type=recommendation.rule_type,
                target_entity_id=recommendation.target_entity_id,
                parameters=recommendation.parameters,
                created_at=datetime.now(timezone.utc),
            )
            self._automations[automation.id] = automation
            self._install_runtime_listener(automation)
            created.append(automation)
        await self._async_save()
        return EnergyAutomationResult(success=True, automation_ids=[item.id for item in created], automations=created)

    async def async_list(self) -> EnergyAutomationResult:
        """列出所有 EdgeHome Demo 规则，不暴露其他 Home Assistant 自动化。"""
        await self._async_load()
        automations = sorted(self._automations.values(), key=lambda item: item.created_at, reverse=True)
        return EnergyAutomationResult(success=True, automation_ids=[item.id for item in automations], automations=automations)

    async def async_disable(self, automation_id: str) -> EnergyAutomationResult:
        await self._async_load()
        automation = self._automations.get(automation_id)
        if automation is None:
            return EnergyAutomationResult(success=False, error_code="AUTOMATION_NOT_FOUND")
        updated = automation.model_copy(update={"enabled": False})
        self._automations[automation_id] = updated
        self._remove_runtime_listener(automation_id)
        await self._async_save()
        return EnergyAutomationResult(success=True, automation_ids=[automation_id], automations=[updated])

    async def async_delete(self, automation_id: str) -> EnergyAutomationResult:
        await self._async_load()
        automation = self._automations.pop(automation_id, None)
        if automation is None:
            return EnergyAutomationResult(success=False, error_code="AUTOMATION_NOT_FOUND")
        self._remove_runtime_listener(automation_id)
        await self._async_save()
        return EnergyAutomationResult(success=True, automation_ids=[automation_id], automations=[automation])

    async def async_undo_last(self) -> EnergyAutomationResult:
        await self._async_load()
        if not self._automations:
            return EnergyAutomationResult(success=False, error_code="NO_AUTOMATION_TO_UNDO")
        latest = max(self._automations.values(), key=lambda item: item.created_at)
        return await self.async_delete(latest.id)

    async def async_evaluate(self, automation_id: str, signal: dict[str, object]) -> EnergyAutomationResult:
        """让实时采集层传入已结构化信号；满足条件才调用固定 HA 服务。"""
        await self._async_load()
        automation = self._automations.get(automation_id)
        if automation is None:
            return EnergyAutomationResult(success=False, error_code="AUTOMATION_NOT_FOUND")
        if not automation.enabled:
            return EnergyAutomationResult(success=False, error_code="AUTOMATION_DISABLED")
        if not self._condition_matches(automation, signal):
            return EnergyAutomationResult(success=False, error_code="CONDITION_NOT_MET")
        current = self._hass.states.get(automation.target_entity_id)
        if current is not None and current.state != "on":
            return EnergyAutomationResult(success=False, error_code="TARGET_NOT_ON")
        _, domain, service = _AUTOMATION_TARGETS[automation.rule_type]
        await self._hass.services.async_call(domain, service, {"entity_id": automation.target_entity_id}, blocking=True)
        return EnergyAutomationResult(success=True, automation_ids=[automation.id], automations=[automation])

    @staticmethod
    def _valid_recommendation(recommendation: EnergyRecommendation) -> bool:
        expected = _AUTOMATION_TARGETS.get(recommendation.rule_type)
        return expected is not None and expected[0] == recommendation.target_entity_id and recommendation.protected_excluded

    @staticmethod
    def _condition_matches(automation: EnergyAutomation, signal: dict[str, object]) -> bool:
        if automation.rule_type == "unoccupied_ac":
            required = int(automation.parameters.get("absence_minutes", 15))
            return signal.get("occupancy") is False and _as_int(signal.get("absence_minutes"), 0) >= required
        if automation.rule_type == "tv_standby":
            minimum = float(automation.parameters.get("minimum_power_w", 18))
            return signal.get("tv_state") == "off" and _as_float(signal.get("socket_power_w"), 0.0) >= minimum
        hour = _as_int(signal.get("hour"), -1)
        return signal.get("occupancy") is False and (hour >= 23 or 0 <= hour < 6)

    async def _async_load(self) -> None:
        if self._loaded:
            return
        payload = await self._store.async_load() or {}
        values = payload.get("automations", []) if isinstance(payload, dict) else []
        if isinstance(values, list):
            for value in values:
                try:
                    automation = EnergyAutomation.model_validate(value)
                except ValidationError:
                    continue
                self._automations[automation.id] = automation
        self._loaded = True
        for automation in self._automations.values():
            self._install_runtime_listener(automation)

    async def _async_save(self) -> None:
        await self._store.async_save({"automations": [item.model_dump(mode="json") for item in self._automations.values()]})

    def _install_runtime_listener(self, automation: EnergyAutomation) -> None:
        """用 HA 事件总线安装受控规则；不创建、解析或执行任意 YAML。"""
        if not automation.enabled or automation.id in self._unsubscribers:
            return
        if automation.rule_type == "unoccupied_ac":
            self._unsubscribers[automation.id] = async_track_state_change_event(
                self._hass,
                ["binary_sensor.edgehome_living_room_presence"],
                lambda event: self._schedule_absence_check(automation, event),
            )
            return
        if automation.rule_type == "night_light":
            self._unsubscribers[automation.id] = async_track_state_change_event(
                self._hass,
                ["light.edgehome_living_room_main"],
                lambda event: self._evaluate_night_light(automation, event),
            )
            return
        self._unsubscribers[automation.id] = self._hass.bus.async_listen(
            "edgehome_energy_sample",
            lambda event: self._evaluate_tv_standby(automation, event),
        )

    def _remove_runtime_listener(self, automation_id: str) -> None:
        unsubscribe = self._unsubscribers.pop(automation_id, None)
        if unsubscribe is not None:
            unsubscribe()

    @staticmethod
    def _new_state_is(event: Event[EventStateChangedData], expected: str) -> bool:
        state = event.data.get("new_state")
        return getattr(state, "state", None) == expected

    def _schedule_absence_check(self, automation: EnergyAutomation, event: Event[EventStateChangedData]) -> None:
        if not self._new_state_is(event, "off"):
            return
        minutes = int(automation.parameters.get("absence_minutes", 15))

        def _run(_now: datetime) -> None:
            self._hass.async_create_task(
                self.async_evaluate(automation.id, {"occupancy": False, "absence_minutes": minutes})
            )

        async_call_later(self._hass, minutes * 60, _run)

    def _evaluate_night_light(self, automation: EnergyAutomation, event: Event[EventStateChangedData]) -> None:
        if not self._new_state_is(event, "on"):
            return
        presence = self._hass.states.get("binary_sensor.edgehome_living_room_presence")
        self._hass.async_create_task(
            self.async_evaluate(
                automation.id,
                {"occupancy": presence is not None and presence.state == "on", "hour": datetime.now().hour},
            )
        )

    def _evaluate_tv_standby(self, automation: EnergyAutomation, event: Event) -> None:
        payload = event.data
        self._hass.async_create_task(
            self.async_evaluate(
                automation.id,
                {"tv_state": payload.get("tv_state"), "socket_power_w": payload.get("socket_power_w", 0.0)},
            )
        )


def _as_int(value: object, default: int) -> int:
    try:
        return int(value) if isinstance(value, (int, float, str)) else default
    except ValueError:
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value) if isinstance(value, (int, float, str)) else default
    except ValueError:
        return default
