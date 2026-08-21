from __future__ import annotations

from typing import Any

from agent.plan_schema import PlanStep, TaskPlan
from home.client import SimulatedHomeClient
from home.entity_registry import EntityRegistry


class ScenePlanner:
    """Offline template planner.

    Templates are deliberately structured rather than executable prose, so a local
    LLM can later suggest a plan without ever bypassing the validator/executor.
    """

    def __init__(self, registry: EntityRegistry, home: SimulatedHomeClient) -> None:
        self.registry = registry
        self.home = home

    @staticmethod
    def _step(number: int, tool: str, arguments: dict[str, Any], verify: dict[str, Any] | None = None, *, optional: bool = False) -> PlanStep:
        return PlanStep(f"step_{number}", tool, arguments, verify or {}, optional)

    def simple(self, intent: str, slots: dict[str, Any]) -> TaskPlan:
        room, device = slots.get("room"), slots.get("device")
        entity = self.registry.resolve(room=room, device=device)
        if intent == "query_state":
            if entity:
                return TaskPlan("查询设备状态", [self._step(1, "get_device_state", {"entity_id": entity.entity_id})])
            if room:
                return TaskPlan("查询房间状态", [self._step(1, "get_room_state", {"room": room})])
            return TaskPlan("查询家庭状态", [self._step(1, "get_home_state", {})])
        if not entity:
            return TaskPlan("澄清设备", [], need_clarification=True, explanation="请说明要控制的房间和设备。")
        mapping = {
            "turn_on": ("turn_on_device", {}, {"state": "on"}),
            "turn_off": ("turn_off_device", {}, {"state": "off"}),
            "set_brightness": ("set_light_brightness", {"brightness": slots.get("brightness")}, {"brightness": slots.get("brightness")}),
            "set_temperature": ("set_temperature", {"temperature": slots.get("temperature")}, {"temperature": slots.get("temperature")}),
            "control_curtain": ("control_curtain", {"position": slots.get("position")}, {"position": slots.get("position")}),
        }
        if intent not in mapping:
            return TaskPlan("澄清操作", [], need_clarification=True, explanation="我还需要知道要执行什么操作。")
        tool, args, verify = mapping[intent]
        args = {"entity_id": entity.entity_id, **args}
        plan = TaskPlan(f"控制{entity.name}", [self._step(1, tool, args, verify)])
        if tool == "turn_off_device" and entity.protected:
            plan.need_confirmation = True
            plan.explanation = f"{entity.name} 是受保护设备，需确认后才能关闭。"
        return plan

    def movie(self, slots: dict[str, Any]) -> TaskPlan:
        room = "living_room"
        state = self.home.get_room_state(room)
        requested = slots.get("temperature")
        target_temperature = max(25, requested if requested is not None else 25)
        steps = [
            self._step(1, "get_room_state", {"room": room}),
        ]
        # Daylight requires privacy/darkness; at night the curtain is left untouched.
        if state["illuminance"] >= 30:
            steps.append(self._step(2, "control_curtain", {"entity_id": "cover.living_room_curtain", "position": 0}, {"position": 0}, optional=True))
        step_no = len(steps) + 1
        steps.extend([
            self._step(step_no, "set_light_brightness", {"entity_id": "light.living_room_main", "brightness": 20}, {"brightness": 20}),
            self._step(step_no + 1, "turn_on_device", {"entity_id": "light.living_room_ambient"}, {"state": "on"}),
            self._step(step_no + 2, "turn_on_device", {"entity_id": "media_player.living_room_tv"}, {"state": "on"}),
            self._step(step_no + 3, "set_temperature", {"entity_id": "climate.living_room_ac", "temperature": target_temperature}, {"temperature": target_temperature}),
        ])
        return TaskPlan("建立客厅观影环境", steps, explanation="根据客厅光照、温度和你的温度下限生成计划。")

    def away(self, slots: dict[str, Any]) -> TaskPlan:
        excluded = set(slots.get("exclude_entities", [])) | {entity.entity_id for entity in self.registry.entities if entity.protected}
        states = self.home.get_home_state()
        steps = [self._step(1, "get_home_state", {})]
        step_no = 2
        # A populated home triggers clarification rather than a surprise whole-home shutdown.
        occupancy = any(room["occupancy"] for room in states["rooms"].values())
        if occupancy:
            return TaskPlan("离家检查", steps, need_clarification=True, explanation="检测到家中仍有人，未自动关闭设备；请确认是否继续离家模式。")
        for entity in self.registry.entities:
            state = self.home.get_state(entity.entity_id)
            if entity.entity_id in excluded or state is None or state["state"] in {"off", "closed", "docked"}:
                continue
            if entity.kind in {"light", "air_conditioner", "television", "switch"}:
                steps.append(self._step(step_no, "turn_off_device", {"entity_id": entity.entity_id}, {"state": "off"}))
                step_no += 1
        return TaskPlan("执行离家节能检查", steps, explanation="受保护设备和你指定保留的设备已从关闭列表中排除。")

    def sleep(self, slots: dict[str, Any], memory: dict[str, Any]) -> TaskPlan:
        steps = [
            self._step(1, "turn_off_device", {"entity_id": "media_player.living_room_tv"}, {"state": "off"}),
            self._step(2, "turn_off_device", {"entity_id": "light.living_room_main"}, {"state": "off"}),
            self._step(3, "set_light_brightness", {"entity_id": "light.bedroom_main", "brightness": 10}, {"brightness": 10}),
        ]
        if slots.get("temperature") is not None:
            steps.append(self._step(4, "set_temperature", {"entity_id": "climate.bedroom_ac", "temperature": slots["temperature"]}, {"temperature": slots["temperature"]}))
        if slots.get("delay_minutes") is not None:
            steps.append(self._step(len(steps) + 1, "create_delayed_task", {
                "entity_id": "climate.living_room_ac",
                "action": "turn_off_device",
                "delay_minutes": slots["delay_minutes"],
            }))
        return TaskPlan("配置睡眠场景", steps, explanation="已关闭客厅娱乐设备并保留卧室低亮度照明。")

    def delayed(self, slots: dict[str, Any], memory: dict[str, Any]) -> TaskPlan:
        entity = self.registry.resolve(room=slots.get("room"), device=slots.get("device"))
        entity_id = entity.entity_id if entity else memory.get("last_entity_id")
        if not entity_id:
            return TaskPlan("澄清延时任务", [], need_clarification=True, explanation="请说明多久后操作哪一台设备。")
        action = "turn_off_device" if any(word in str(slots.get("raw_text", "")) for word in ("关", "关闭")) else "turn_off_device"
        return TaskPlan("创建延时任务", [self._step(1, "create_delayed_task", {
            "entity_id": entity_id, "action": action, "delay_minutes": slots["delay_minutes"],
        })])

    def plan(self, intent: str, slots: dict[str, Any], memory: dict[str, Any]) -> TaskPlan:
        if slots.get("scene") == "movie":
            return self.movie(slots)
        if slots.get("scene") == "away":
            return self.away(slots)
        if slots.get("scene") == "sleep" or (
            memory.get("active_scene") == "sleep"
            and (intent == "complex_task" or slots.get("delay_minutes") is not None or slots.get("temperature") is not None)
        ):
            return self.sleep(slots, memory)
        if intent == "create_delayed_task":
            return self.delayed(slots, memory)
        return self.simple(intent, slots)
