"""Proactive service policies for SpaceButler."""

from __future__ import annotations

from statistics import mean

from .memory import HouseholdMemory
from .models import PlanAction, PlanPriority, ServicePlan, SpatialSnapshot


class ProactiveServiceEngine:
    """Convert spatial snapshots and memory into explainable service plans."""

    def __init__(self, memory: HouseholdMemory) -> None:
        self._memory = memory

    def propose(self, snapshot: SpatialSnapshot) -> list[ServicePlan]:
        plans = [
            self._open_window_empty_room_energy_guard(snapshot),
            self._return_home_comfort(snapshot),
            self._night_elder_safety(snapshot),
            self._comfort_energy_balance(snapshot),
            self._multi_member_temperature(snapshot),
        ]
        return [plan for plan in plans if plan is not None]

    def _open_window_empty_room_energy_guard(self, snapshot: SpatialSnapshot) -> ServicePlan | None:
        if not snapshot.is_fresh() or snapshot.missing_fields:
            return None
        rooms = {device.room for device in snapshot.devices}
        for room in sorted(rooms):
            room_state = snapshot.room_state(room)
            occupied = bool(snapshot.members_in_room(room)) if room_state is None else room_state.occupied
            if occupied:
                continue
            threshold_minutes = int(
                self._memory.recall_number(
                    "household",
                    "empty_room_open_window_energy_guard",
                    "unoccupied_minutes",
                    20,
                )
            )
            unoccupied_minutes = 0 if room_state is None else room_state.unoccupied_minutes
            if unoccupied_minutes < threshold_minutes:
                continue
            only_peak = self._memory.recall("household", "empty_room_open_window_energy_guard", "only_peak_price", False)
            if only_peak and snapshot.environment.electricity_price_level != "peak":
                continue
            if self._memory.recall("household", "empty_room_open_window_energy_guard", "suppress_reminder", False):
                continue
            running_climates = [
                device
                for device in snapshot.devices_in_room(room, "climate")
                if device.state in {"cool", "heat", "on"}
            ]
            open_windows = [
                device
                for device in snapshot.devices_in_room(room, "window")
                if device.state in {"open", "on"}
            ]
            total_power = _room_power_w(snapshot, room, running_climates)
            if total_power < 600:
                continue
            if not running_climates or not open_windows:
                continue
            room_name = "客厅" if room == "living_room" else room
            auto_execute = bool(self._memory.recall("household", "empty_room_open_window_energy_guard", "auto_execute", False))
            actions = tuple(
                PlanAction(
                    entity_id=device.entity_id,
                    capability="turn_off",
                    value="off",
                    reason=(
                        f"{room_name}连续无人{unoccupied_minutes}分钟，窗户打开，"
                        f"空调功率约{total_power:g}W，继续运行会造成明显能源浪费"
                    ),
                )
                for device in running_climates
            )
            return ServicePlan(
                plan_id="empty_room_open_window_energy_guard",
                title="无人开窗空调节能保护",
                priority=PlanPriority.ENERGY,
                proactive=True,
                target_members=(),
                actions=actions,
                explanation=(
                    "空间状态显示房间连续无人、窗户打开、空调仍在运行且功率偏高；"
                    "默认先建议并等待确认，除非用户已明确授权以后自动执行。"
                ),
                requires_confirmation=not auto_execute,
            )
        return None

    def _return_home_comfort(self, snapshot: SpatialSnapshot) -> ServicePlan | None:
        arriving = [member for member in snapshot.members if member.is_home and member.activity == "returning_home"]
        if not arriving:
            return None
        member = arriving[0]
        target_temp = self._memory.recall_number(member.member_id, "return_home", "target_temperature", 26.0)
        actions: list[PlanAction] = []
        for climate in snapshot.devices_in_room(member.location, "climate"):
            actions.append(
                PlanAction(
                    entity_id=climate.entity_id,
                    capability="set_temperature",
                    value=target_temp,
                    reason=f"{member.name}正在返家，提前恢复其偏好的{target_temp:g}度舒适环境",
                )
            )
        for light in snapshot.devices_in_room(member.location, "light"):
            if snapshot.environment.illuminance < 120:
                actions.append(
                    PlanAction(
                        entity_id=light.entity_id,
                        capability="set_brightness",
                        value=55,
                        reason="室内照度偏低，返家时提供柔和迎宾照明",
                    )
                )
        if not actions:
            return None
        return ServicePlan(
            plan_id="return_home_comfort",
            title="返家舒适预备",
            priority=PlanPriority.COMFORT,
            proactive=True,
            target_members=(member.member_id,),
            actions=tuple(actions),
            explanation="结合成员位置、返家状态、环境照度和偏好记忆，提前准备舒适空间。",
        )

    @staticmethod
    def _night_elder_safety(snapshot: SpatialSnapshot) -> ServicePlan | None:
        elders = [
            member
            for member in snapshot.members
            if member.is_home and member.role.value == "elder" and member.activity == "night_walk"
        ]
        if not elders or snapshot.time_of_day != "night":
            return None
        actions = [
            PlanAction(
                entity_id=device.entity_id,
                capability="set_brightness",
                value=25,
                reason="老人夜间起身，低亮度照明降低跌倒风险且避免强光刺激",
            )
            for member in elders
            for device in snapshot.devices_in_room(member.location, "light")
        ]
        if not actions:
            return None
        return ServicePlan(
            plan_id="night_elder_safety",
            title="夜间老人安全照明",
            priority=PlanPriority.SAFETY,
            proactive=True,
            target_members=tuple(member.member_id for member in elders),
            actions=tuple(actions),
            explanation="检测到老人夜间活动，主动联动所在空间照明。",
        )

    @staticmethod
    def _comfort_energy_balance(snapshot: SpatialSnapshot) -> ServicePlan | None:
        if snapshot.scene not in {"daily", "energy_saving"}:
            return None
        if snapshot.environment.electricity_price_level != "peak":
            return None
        if not snapshot.members:
            return None
        actions = [
            PlanAction(
                entity_id=device.entity_id,
                capability="set_temperature",
                value=27,
                reason="峰电时段且室内仍在舒适区，温度上调1度以降低能耗",
            )
            for device in snapshot.devices
            if device.domain == "climate" and device.state in {"cool", "on"}
        ]
        if not actions:
            return None
        return ServicePlan(
            plan_id="comfort_energy_balance",
            title="舒适节能平衡",
            priority=PlanPriority.ENERGY,
            proactive=True,
            target_members=tuple(member.member_id for member in snapshot.members if member.is_home),
            actions=tuple(actions),
            explanation="在峰电时段基于室内外温差和在家状态给出低打扰节能方案。",
            requires_confirmation=True,
        )

    def _multi_member_temperature(self, snapshot: SpatialSnapshot) -> ServicePlan | None:
        rooms = {member.location for member in snapshot.members if member.is_home}
        for room in rooms:
            members = snapshot.members_in_room(room)
            if len(members) < 2:
                continue
            preferred = [
                self._memory.recall_number(member.member_id, snapshot.scene, "target_temperature", 26.0)
                for member in members
            ]
            if max(preferred) - min(preferred) < 2.0:
                continue
            target = round(mean(preferred), 1)
            actions = [
                PlanAction(
                    entity_id=device.entity_id,
                    capability="set_temperature",
                    value=target,
                    reason="同一空间存在多成员温度偏好冲突，采用折中温度并保留解释",
                )
                for device in snapshot.devices_in_room(room, "climate")
            ]
            if actions:
                return ServicePlan(
                    plan_id="multi_member_temperature_balance",
                    title="多成员温度偏好调和",
                    priority=PlanPriority.COMFORT,
                    proactive=False,
                    target_members=tuple(member.member_id for member in members),
                    actions=tuple(actions),
                    explanation="根据成员偏好记忆计算折中目标，避免单一成员偏好覆盖全家体验。",
                    requires_confirmation=True,
                )
        return None


def _room_power_w(snapshot: SpatialSnapshot, room: str, devices: list) -> float:
    room_state = snapshot.room_state(room)
    if room_state is not None and room_state.power_w is not None:
        return room_state.power_w
    total = 0.0
    for device in devices:
        power = device.attributes.get("power_w")
        if isinstance(power, (int, float)):
            total += float(power)
    return total
