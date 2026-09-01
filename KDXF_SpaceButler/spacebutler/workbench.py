"""Local HTTP workbench backed by Home Assistant and real ESPHome entities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .agent import SpaceButlerAgent
from .agent.runtime import AgentRuntime
from .brands import (
    BrandBindingStore,
    brand_ecosystems,
    brand_for_device,
    brand_record,
    brand_summary,
    brand_supports_device_type,
    translate_command,
)
from .conversation import ConversationAgent
from .control_context import ControlContextRegistry, ManualControlContext
from .edge_language import EdgeLanguageRouter
from .esphome_registry import (
    DEFAULT_ESPHOME_HOST,
    NODE_ROLE_BY_DEVICE,
    device_record,
    discover_entities,
    esphome_nodes,
)
from .home_assistant import EnergyEntityMap, HomeAssistantClient, HomeAssistantRuntime, HomeAssistantSpaceAdapter
from .interaction import InteractionResponse, SpaceButlerSession
from .memory import HouseholdMemory
from .models import DeviceState, EnvironmentState, HouseholdMember, MemberRole, SpatialSnapshot
from .proactive import ENERGY_RULE_ID, NIGHT_SAFETY_RULE_ID, ProactiveRuleStore
from .runtime import execute_and_verify
from .voice import CHUNK_BYTES, VoiceError, VoiceService
from .workflows import WorkflowStore, match_trigger


class WorkbenchController:
    def __init__(
        self,
        ha_client: HomeAssistantClient,
        memory: HouseholdMemory,
        language_router: EdgeLanguageRouter | None = None,
        agent_llm_client: object | None = None,
    ) -> None:
        self._ha = ha_client
        self._memory = memory
        self._agent = SpaceButlerAgent(memory)
        self._language_router = language_router
        # 语义兜底触发复用规划模型；离线时三级匹配自动退化为前两级确定性匹配。
        self._agent_llm_client = agent_llm_client
        self._session = self._new_session()
        self._last_response: object | None = None
        self._last_night_response: dict[str, object] | None = None
        self._control_context = ControlContextRegistry(memory.database_path)
        self._night_presence_state: bool | None = None
        self._night_monitor_error: str | None = None
        self._night_monitor_stop = threading.Event()
        self._night_monitor_thread: threading.Thread | None = None
        self._unoccupied_minutes = 23
        self._rule_store = ProactiveRuleStore(memory.database_path)
        # 品牌归属和工作流都要跨重启保留，并且必须在服务端——对话框触发要看得到它们。
        self._brand_bindings = BrandBindingStore(memory.database_path)
        self._workflows = WorkflowStore(memory.database_path)
        self._pending_trigger: dict[str, object] | None = None
        self._lock = threading.RLock()
        # 自治智能体运行时：反事实规划 + 偏好学习 + 物理反馈重规划。
        self._agent_runtime = AgentRuntime(
            self.devices,
            self.control_device,
            agent_llm_client,
            memory.database_path,
        )
        self._agent_runtime.start()
        self._workflow_planner = ConversationAgent(
            self.devices,
            self.control_device,
            self.proactive,
            language_router.client if language_router is not None else None,
        )

    def status(self) -> dict[str, object]:
        with self._lock:
            proactive = self.proactive()
            sources = proactive["sources"]
            climate_source = sources.get("climate")
            presence_source = sources.get("presence")
            contact_source = sources.get("contact")
            climate = _source_ha_state(climate_source)
            presence = _source_ha_state(presence_source)
            window = _source_ha_state(contact_source)
            climate_device = _source_record(climate_source)
            feedback = (
                climate_source.get("feedback")
                if isinstance(climate_source, dict)
                else None
            )
            return {
                "healthy": bool(proactive["ready"]),
                "unoccupied_minutes": proactive["unoccupied_minutes"],
                "climate": climate,
                "presence": presence,
                "window": window,
                "climate_device": climate_device,
                "feedback": feedback,
                "proactive": proactive,
                "night_safety": self.night_safety(),
                "last_response": to_jsonable(self._last_response),
                "preferences": [to_jsonable(item) for item in self._memory.export_preferences()],
                "device_summary": self._device_summary(),
            }

    def proactive(self) -> dict[str, object]:
        with self._lock:
            inventory = self.devices()
            devices = inventory["devices"]
            rooms = inventory["rooms"]
            if not isinstance(devices, list) or not isinstance(rooms, list):
                raise RuntimeError("device inventory is invalid")
            config = _resolve_rule_config(self._rule_store.load(ENERGY_RULE_ID), devices)
            sources = {
                "presence": _device_by_id(devices, str(config.get("presence_device_id", ""))),
                "contact": _device_by_id(devices, str(config.get("contact_device_id", ""))),
                "climate": _device_by_id(devices, str(config.get("climate_device_id", ""))),
            }
            room = str(config.get("room", ""))
            room_name = next(
                (str(item.get("name", room)) for item in rooms if item.get("room") == room),
                _room_name(room),
            )
            occupied = _presence_occupied(sources["presence"])
            window_open = _contact_open(sources["contact"])
            climate_running = _climate_running(sources["climate"])
            unoccupied_minutes = 0 if occupied else _unoccupied_minutes(sources["presence"])
            power_w = _estimated_device_power(_source_record(sources["climate"]))
            threshold_minutes = int(
                self._memory.recall_number(
                    "household",
                    ENERGY_RULE_ID,
                    "unoccupied_minutes",
                    20,
                )
            )
            conditions = [
                _condition(
                    "presence",
                    "空间无人",
                    "无人" if not occupied else "有人",
                    "无人",
                    sources["presence"] is not None and not occupied,
                    sources["presence"],
                ),
                _condition(
                    "duration",
                    "连续无人时长",
                    f"{unoccupied_minutes} 分钟",
                    f"至少 {threshold_minutes} 分钟",
                    unoccupied_minutes >= threshold_minutes,
                    sources["presence"],
                ),
                _condition(
                    "contact",
                    "门窗状态",
                    "打开" if window_open else "关闭",
                    "打开",
                    sources["contact"] is not None and window_open,
                    sources["contact"],
                ),
                _condition(
                    "climate",
                    "空调运行",
                    _climate_label(sources["climate"]),
                    "制冷或制热",
                    sources["climate"] is not None and climate_running,
                    sources["climate"],
                ),
                _condition(
                    "power",
                    "估算功率",
                    f"{power_w:g} W",
                    "至少 600 W",
                    power_w >= 600,
                    sources["climate"],
                ),
            ]
            complete = all(source is not None for source in sources.values())
            ready = complete and all(
                bool(source.get("online")) and bool(source.get("discovered"))
                for source in sources.values()
                if isinstance(source, dict)
            )
            monitoring = bool(config.get("enabled", True)) and ready
            return {
                "rule_id": ENERGY_RULE_ID,
                "rule_name": "无人开窗空调节能保护",
                "config": config,
                "room": {"room": room, "name": room_name},
                "sources": sources,
                "conditions": conditions,
                "available_rooms": rooms,
                "available_devices": {
                    device_type: [
                        _source_option(item)
                        for item in devices
                        if item.get("type") == device_type
                    ]
                    for device_type in ("presence", "contact", "climate")
                },
                "complete": complete,
                "ready": ready,
                "monitoring": monitoring,
                "trigger_ready": monitoring and all(bool(item["met"]) for item in conditions),
                "unoccupied_minutes": unoccupied_minutes,
                "threshold_minutes": threshold_minutes,
                "power_w": power_w,
                "data_source": "device_registry + Home Assistant REST + ESPHome",
            }

    def night_safety(self) -> dict[str, object]:
        with self._lock:
            inventory = self.devices()
            devices = inventory["devices"]
            rooms = inventory["rooms"]
            if not isinstance(devices, list) or not isinstance(rooms, list):
                raise RuntimeError("device inventory is invalid")
            config = _resolve_night_safety_config(
                self._rule_store.load(NIGHT_SAFETY_RULE_ID),
                devices,
            )
            selected_ids = [str(item) for item in config["path_light_device_ids"]]
            path_lights = [
                device
                for device_id in selected_ids
                if (device := _device_by_id(devices, device_id)) is not None
            ]
            presence_source = _device_by_id(devices, str(config["presence_device_id"]))
            illuminance_source = _device_by_id(devices, str(config["illuminance_device_id"]))
            sources = {
                "presence": presence_source,
                "illuminance": illuminance_source,
            }
            ready = bool(path_lights) and all(
                bool(device.get("online")) and bool(device.get("discovered"))
                for device in [*path_lights, presence_source, illuminance_source]
                if isinstance(device, dict)
            ) and all(isinstance(source, dict) for source in sources.values())
            occupied = _presence_occupied(presence_source)
            current_illuminance = _illuminance_value(illuminance_source)
            threshold = int(config["max_illuminance"])
            conditions = [
                _condition(
                    "presence",
                    "起身事件",
                    "检测到有人" if occupied else "等待起身",
                    "无人到有人",
                    occupied,
                    presence_source,
                ),
                _condition(
                    "illuminance",
                    "环境照度",
                    f"{current_illuminance} lux",
                    f"不高于 {threshold} lux",
                    current_illuminance <= threshold,
                    illuminance_source,
                ),
            ]
            active_contexts = self._control_context.active(set(selected_ids))
            room_options = [
                room
                for room in rooms
                if any(
                    device.get("type") == "light" and device.get("room") == room.get("room")
                    for device in devices
                )
            ]
            return {
                "rule_id": NIGHT_SAFETY_RULE_ID,
                "rule_name": "老人夜间起身安全路径",
                "config": config,
                "ready": ready,
                "monitoring": bool(config["enabled"]) and ready,
                "trigger_ready": bool(config["enabled"]) and ready and all(item["met"] for item in conditions),
                "sources": sources,
                "conditions": conditions,
                "occupied": occupied,
                "current_illuminance": current_illuminance,
                "path_lights": [
                    _night_light_option(item, active_contexts.get(str(item.get("device_id"))))
                    for item in path_lights
                ],
                "available_lights": [
                    _night_light_option(item, active_contexts.get(str(item.get("device_id"))))
                    for item in devices
                    if item.get("type") == "light"
                ],
                "available_rooms": room_options,
                "available_presence_sensors": [
                    _source_option(item) for item in devices if item.get("type") == "presence"
                ],
                "available_illuminance_sensors": [
                    _source_option(item) for item in devices if item.get("type") == "illuminance"
                ],
                "manual_lights": len(active_contexts),
                "last_response": self._last_night_response,
                "listener": {
                    "active": bool(
                        self._night_monitor_thread
                        and self._night_monitor_thread.is_alive()
                    ),
                    "last_occupied": self._night_presence_state,
                    "error": self._night_monitor_error,
                },
                "data_source": "presence + illuminance entities / Home Assistant REST + ESPHome",
            }

    def start_night_monitor(self, interval_seconds: float = 1.0) -> None:
        """Watch the bound presence entity and execute only on a real rising edge."""
        if interval_seconds < 0.2:
            raise ValueError("interval_seconds must be at least 0.2")
        with self._lock:
            if self._night_monitor_thread and self._night_monitor_thread.is_alive():
                return
            self._night_monitor_stop.clear()
            self._night_monitor_thread = threading.Thread(
                target=self._night_monitor_loop,
                args=(interval_seconds,),
                name="spacebutler-night-safety-monitor",
                daemon=True,
            )
            self._night_monitor_thread.start()

    def stop_night_monitor(self) -> None:
        self._night_monitor_stop.set()
        thread = self._night_monitor_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def poll_night_safety(self) -> dict[str, object] | None:
        """Evaluate one sensor sample and return a response only for a rising edge."""
        with self._lock:
            night_safety = self.night_safety()
            occupied = bool(night_safety["occupied"])
            previous = self._night_presence_state
            self._night_presence_state = occupied
            self._night_monitor_error = None
            if previous is not False or not occupied or not bool(night_safety["monitoring"]):
                return None
            return self.run_night_safety()["response"]

    def _night_monitor_loop(self, interval_seconds: float) -> None:
        while not self._night_monitor_stop.wait(interval_seconds):
            try:
                self.poll_night_safety()
            except Exception as error:  # keep monitoring through transient HA/ESPHome outages
                with self._lock:
                    self._night_monitor_error = str(error)

    def configure_night_safety(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            inventory = self.devices()
            devices = inventory["devices"]
            if not isinstance(devices, list):
                raise RuntimeError("device inventory is invalid")
            raw_ids = payload.get("path_light_device_ids")
            if not isinstance(raw_ids, list):
                raise ValueError("path_light_device_ids must be a list")
            path_ids = list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip()))
            if not 1 <= len(path_ids) <= 6:
                raise ValueError("night safety requires between 1 and 6 path lights")
            for device_id in path_ids:
                device = _device_by_id(devices, device_id)
                if device is None or device.get("type") != "light":
                    raise ValueError(f"path light is invalid: {device_id}")
            member_name = str(payload.get("member_name", "爷爷")).strip()
            if not member_name or len(member_name) > 40:
                raise ValueError("member_name must contain 1 to 40 characters")
            origin_room = str(payload.get("origin_room", "")).strip()
            if not origin_room:
                raise ValueError("origin_room is required")
            light_rooms = {
                str(device.get("room"))
                for device in devices
                if device.get("type") == "light"
            }
            if origin_room not in light_rooms:
                raise ValueError("origin_room must contain an available light")
            source_ids: dict[str, str] = {}
            for role, device_type in (
                ("presence_device_id", "presence"),
                ("illuminance_device_id", "illuminance"),
            ):
                requested = str(payload.get(role, "")).strip()
                source = _device_by_id(devices, requested)
                if source is None or source.get("type") != device_type:
                    raise ValueError(f"{role} must reference a {device_type} sensor")
                if source.get("room") != origin_room:
                    raise ValueError(f"{role} must be in {origin_room}")
                source_ids[role] = requested
            config: dict[str, object] = {
                "enabled": _body_bool(payload.get("enabled", True)),
                "member_id": "elder_01",
                "member_name": member_name,
                "origin_room": origin_room,
                **source_ids,
                "path_light_device_ids": path_ids,
                "brightness": _bounded_int(payload.get("brightness"), 5, 40, "brightness"),
                "max_illuminance": _bounded_int(
                    payload.get("max_illuminance"),
                    1,
                    200,
                    "max_illuminance",
                ),
            }
            self._rule_store.save(NIGHT_SAFETY_RULE_ID, config)
            self._control_context.clear_all()
            self._memory.learn_preference(
                "elder_01",
                "night_walk",
                "path_brightness",
                config["brightness"],
                source="explicit_rule_config",
            )
            self._memory.learn_preference(
                "elder_01",
                "night_walk",
                "max_illuminance",
                config["max_illuminance"],
                source="explicit_rule_config",
            )
            self._last_night_response = None
            return {"night_safety": self.night_safety(), "status": self.status()}

    def prepare_night_safety(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            raise ValueError(
                "硬编码传感器模拟已禁用；请通过真实 presence/illuminance ESPHome 实体触发夜间守护"
            )

    def run_night_safety(self, payload: dict[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            night_safety = self.night_safety()
            config = night_safety["config"]
            if not bool(config.get("enabled", True)):
                self._last_night_response = to_jsonable(
                    InteractionResponse(status="disabled", message="夜间安全规则已停用。")
                )
                return {"response": self._last_night_response, "status": self.status()}
            if not night_safety["ready"]:
                raise ValueError("night safety sensors or path lights are not ready")
            if not bool(night_safety["occupied"]):
                self._last_night_response = to_jsonable(
                    InteractionResponse(status="ignored", message="起身传感器尚未检测到无人到有人事件。")
                )
                return {"response": self._last_night_response, "status": self.status()}
            illuminance = int(night_safety["current_illuminance"])
            path_lights = night_safety["path_lights"]
            if not isinstance(path_lights, list):
                raise RuntimeError("night safety path is invalid")
            member_id = str(config["member_id"])
            snapshot = SpatialSnapshot(
                scene="night_safety",
                time_of_day="night",
                members=(
                    HouseholdMember(
                        member_id,
                        str(config["member_name"]),
                        MemberRole.ELDER,
                        str(config["origin_room"]),
                        "night_walk",
                    ),
                ),
                environment=EnvironmentState(23, 18, 52, illuminance, 8),
                devices=tuple(
                    _night_device_state(
                        light,
                        member_id,
                        index,
                        manual_control=bool(light.get("manual_override")),
                    )
                    for index, light in enumerate(path_lights, start=1)
                ),
                snapshot_id=f"night-safety-{int(time.time() * 1000)}",
                source="presence_rising_edge + illuminance_entity",
            )
            plan = next(
                (
                    item
                    for item in self._agent.observe_and_plan(snapshot)
                    if item.plan_id == NIGHT_SAFETY_RULE_ID
                ),
                None,
            )
            if plan is None:
                threshold = int(config["max_illuminance"])
                message = (
                    f"当前照度 {illuminance} lux，高于 {threshold} lux，无需补光。"
                    if illuminance > threshold
                    else "路径灯均已手动点亮或不可自动调整，保持当前状态。"
                )
                self._last_night_response = to_jsonable(
                    InteractionResponse(status="ignored", message=message)
                )
            else:
                report = execute_and_verify(
                    HomeAssistantRuntime(self._ha, verification_timeout_seconds=5),
                    plan,
                )
                self._last_night_response = to_jsonable(
                    InteractionResponse(
                        status="executed" if report.verified else "execution_failed",
                        message=report.verification_summary,
                        plan=plan,
                        report=report,
                    )
                )
            return {"response": self._last_night_response, "status": self.status()}

    def trigger_night_safety(self, _payload: dict[str, object]) -> dict[str, object]:
        """Generate a real presence rising edge; control_device performs automatic evaluation."""
        with self._lock:
            night_safety = self.night_safety()
            presence = _require_source(night_safety["sources"], "presence")
            device_id = str(presence["device_id"])
            if bool(night_safety["occupied"]):
                self.control_device(device_id, {"occupied": False})
            result = self.control_device(device_id, {"occupied": True})
            return {
                "response": result.get("night_response"),
                "status": self.status(),
                **self.devices(),
            }

    def configure_proactive(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            inventory = self.devices()
            devices = inventory["devices"]
            if not isinstance(devices, list):
                raise RuntimeError("device inventory is invalid")
            room = str(payload.get("room", "")).strip()
            if not room:
                raise ValueError("room is required")
            config: dict[str, object] = {
                "enabled": _body_bool(payload.get("enabled", True)),
                "room": room,
            }
            for role, device_type in (
                ("presence_device_id", "presence"),
                ("contact_device_id", "contact"),
                ("climate_device_id", "climate"),
            ):
                requested = str(payload.get(role, "")).strip()
                device = _device_by_id(devices, requested)
                if device is None:
                    raise ValueError(f"{role} is missing")
                if device.get("type") != device_type or device.get("room") != room:
                    raise ValueError(f"{role} must reference a {device_type} device in {room}")
                config[role] = requested
            self._rule_store.save(ENERGY_RULE_ID, config)
            self._session = self._new_session()
            self._last_response = None
            self._last_night_response = None
            return self.status()

    def devices(self) -> dict[str, object]:
        with self._lock:
            ha_states = {
                str(item.get("entity_id")): item
                for item in self._ha.states()
                if isinstance(item.get("entity_id"), str)
            }
            resolved, diagnostics = discover_entities(list(ha_states.values()))
            bindings = self._brand_bindings.bindings()
            devices = self._projected_devices(ha_states, resolved, bindings)
            rooms = _room_summaries(devices)
            dashboard = _dashboard_projection(devices, rooms)
            favorite_ids = set(dashboard["favorite_device_ids"])
            for device in devices:
                ui = device.get("ui")
                if isinstance(ui, dict):
                    ui["favorite"] = str(device.get("device_id", "")) in favorite_ids
            unbound = [
                {"device_id": device["device_id"], "name": device["name"],
                 "room_name": device["room_name"], "type": device["type"],
                 "capabilities": device["capabilities"]}
                for device in devices
                if device.get("brand") is None
            ]
            return {"devices": devices, "rooms": rooms, "dashboard": dashboard,
                    "brands": brand_summary(bindings),
                    "unbound_devices": unbound,
                    "ecosystems": brand_ecosystems(),
                    "diagnostics": {key: value for key, value in diagnostics.items() if key != "ha_entities"}}

    def device_diagnostics(self) -> dict[str, object]:
        """Read HA identities without creating integrations or sending controls."""
        with self._lock:
            _, diagnostics = discover_entities(self._ha.states())
            base_url = getattr(self._ha, "base_url", "")
            if isinstance(base_url, str):
                diagnostics["ha_host"] = urlparse(base_url).hostname
            return diagnostics

    @classmethod
    def _projected_devices(
        cls,
        ha_states: dict[str, dict[str, object]],
        resolved_entities: dict[str, str] | None = None,
        brand_bindings: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Project every deployed ESPHome entity that HA already exposes."""
        if resolved_entities is None:
            resolved_entities, _ = discover_entities([
                {**state, "entity_id": entity_id}
                for entity_id, state in ha_states.items()
            ])
        return cls._resolved_esphome_devices(ha_states, resolved_entities, brand_bindings)

    @staticmethod
    def _resolved_esphome_devices(
        ha_states: dict[str, dict[str, object]],
        resolved_entities: dict[str, str],
        brand_bindings: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        """Project ESPHome devices from the unique mapping used by control and diagnostics."""
        devices: list[dict[str, object]] = []
        for device_id, entity_id in sorted(resolved_entities.items()):
            record = device_record(device_id)
            ha_state = ha_states.get(entity_id)
            if record is None or ha_state is None:
                continue
            device_type = str(record["type"])
            value = str(ha_state.get("state", "unavailable"))
            room = str(record["room"])
            device: dict[str, object] = {
                "device_id": device_id,
                "entity_id": entity_id,
                "type": device_type,
                "name": record["name"],
                "room": room,
                "room_name": _ESPHOME_ROOM_NAMES.get(room, room),
                "source": "esphome",
                "online": value not in {"unknown", "unavailable"},
                "readonly": device_type in _ESPHOME_READONLY_TYPES,
                "state": _projected_device_state(device_type, ha_state, record, ha_states),
                "ha_state": ha_state,
                "feedback": None,
                "discovered": True,
                "capabilities": list(record.get("capabilities", [])),
                "brand": brand_for_device(device_id, brand_bindings),
                "node_role": NODE_ROLE_BY_DEVICE.get(device_id),
            }
            device["ui"] = _device_ui(device)
            devices.append(device)
        return devices

    def add_device(self, payload: dict[str, object]) -> dict[str, object]:
        raise ValueError("工作台不创建设备；请在 ESPHome 节点固件中注册真实设备")

    def remove_device(self, device_id: str) -> dict[str, object]:
        raise ValueError("设备由节点固件和设备容器定义，不能从工作台删除")

    def brand_inventory(self) -> dict[str, object]:
        """Ecosystems the adapter layer supports, plus current binding state."""
        with self._lock:
            bindings = self._brand_bindings.bindings()
            devices = self._projected_devices(self._ha_state_index(), None, bindings)
            by_brand: dict[str, list[dict[str, object]]] = {}
            unbound: list[dict[str, object]] = []
            for device in devices:
                entry = {
                    "device_id": device["device_id"],
                    "name": device["name"],
                    "room_name": device["room_name"],
                    "type": device["type"],
                    "capabilities": device["capabilities"],
                    "online": device["online"],
                }
                brand = device.get("brand")
                if isinstance(brand, dict):
                    by_brand.setdefault(str(brand["brand_id"]), []).append(entry)
                else:
                    unbound.append(entry)
            return {
                "ecosystems": [
                    {**record, "devices": by_brand.get(str(record["brand_id"]), [])}
                    for record in brand_ecosystems()
                ],
                "unbound_devices": unbound,
                "bound_total": len(devices) - len(unbound),
                "device_total": len(devices),
            }

    def discover_brand_devices(self, brand_id: str) -> dict[str, object]:
        """Report which real devices this ecosystem could adopt right now.

        Discovery reads Home Assistant only. It reports capabilities the ESPHome
        entity actually exposes and how this brand would address them; it does not
        contact any vendor gateway or cloud, and it changes no binding.
        """
        with self._lock:
            brand = brand_record(brand_id)
            if brand is None:
                raise ValueError(f"未知品牌生态：{brand_id}")
            bindings = self._brand_bindings.bindings()
            devices = self._projected_devices(self._ha_state_index(), None, bindings)
            candidates: list[dict[str, object]] = []
            for device in devices:
                device_id = str(device["device_id"])
                device_type = str(device["type"])
                owner = device.get("brand")
                compatible = brand_supports_device_type(brand_id, device_type)
                sample_translation = None
                if compatible and device.get("capabilities"):
                    sample_translation = translate_command(
                        device_id,
                        _sample_payload(device_type),
                        {device_id: brand_id},
                    )
                candidates.append({
                    "device_id": device_id,
                    "name": device["name"],
                    "room_name": device["room_name"],
                    "type": device_type,
                    "capabilities": device["capabilities"],
                    "online": device["online"],
                    "entity_id": device["entity_id"],
                    "compatible": compatible,
                    "adoptable": owner is None and compatible,
                    "current_brand": owner,
                    # 纳管后同一个意图会被翻译成什么，配对前就能看到。
                    "sample_translation": sample_translation,
                    "incompatibility_reason": (
                        None if compatible else f"{brand['name']} 不支持 {device_type} 类型"
                    ),
                })
            return {
                "brand": {**brand, "pairing_steps": list(brand.get("pairing_steps", ()))},
                "candidates": candidates,
                "adoptable_count": sum(1 for item in candidates if item["adoptable"]),
            }

    def adopt_brand_devices(self, brand_id: str, device_ids: list[str]) -> dict[str, object]:
        """Bind real devices to one ecosystem and report the translated commands."""
        with self._lock:
            brand = brand_record(brand_id)
            if brand is None:
                raise ValueError(f"未知品牌生态：{brand_id}")
            if not device_ids:
                raise ValueError("请至少选择一台设备")
            bindings = self._brand_bindings.bindings()
            known = {str(record["device_id"]) for record in self._projected_devices(
                self._ha_state_index(), None, bindings,
            )}
            adopted: list[dict[str, object]] = []
            for device_id in device_ids:
                if device_id not in known:
                    raise ValueError(f"未在 Home Assistant 中发现该真实设备：{device_id}")
                current_brand_id = bindings.get(device_id)
                if current_brand_id:
                    current = brand_record(current_brand_id) or {"name": current_brand_id}
                    raise ValueError(
                        f"{device_id} 已归属 {current['name']}，请先释放后再纳管"
                    )
                record = device_record(device_id) or {}
                device_type = str(record.get("type", ""))
                if not brand_supports_device_type(brand_id, device_type):
                    raise ValueError(f"{brand['name']} 不支持 {device_type or '未知'} 类型设备：{device_id}")
                self._brand_bindings.adopt(device_id, brand_id)
                adopted.append({
                    "device_id": device_id,
                    "name": record.get("name", device_id),
                    "translation": translate_command(
                        device_id,
                        _sample_payload(str(record.get("type", ""))),
                        self._brand_bindings.bindings(),
                    ),
                })
            return {"ok": True, "brand": brand, "adopted": adopted, **self.devices()}

    def release_brand_device(self, device_id: str) -> dict[str, object]:
        """Unbind one device. The ESPHome entity keeps working and stays controllable."""
        with self._lock:
            if not self._brand_bindings.release(device_id):
                raise ValueError(f"该设备当前未绑定任何品牌生态：{device_id}")
            return {"ok": True, "released": device_id, **self.devices()}

    def reset_brand_bindings(self) -> dict[str, object]:
        """Restore factory brand mapping so the onboarding demo is repeatable."""
        with self._lock:
            self._brand_bindings.reset()
            return {"ok": True, **self.devices()}

    def _ha_state_index(self) -> dict[str, dict[str, object]]:
        return {
            str(item.get("entity_id")): item
            for item in self._ha.states()
            if isinstance(item.get("entity_id"), str)
        }

    def control_device(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            esphome_entity_id = self._esphome_entity_id(device_id)
            if esphome_entity_id is not None:
                return self._control_esphome_device(device_id, esphome_entity_id, payload)
            raise ValueError(f"未在 Home Assistant 中发现该真实设备：{device_id}")

    def set_device_fault(self, device_id: str, mode: str, delay_ms: int = 0) -> dict[str, object]:
        if mode not in {
            "none",
            "offline",
            "delay",
            "reject",
            "ack_without_state_change",
            "invalid_state",
            "random_failure",
            "stuck",
        }:
            raise ValueError("unsupported fault mode")
        raise ValueError("真实设备不支持模拟故障注入")

    def reset_scene(
        self,
        unoccupied_minutes: int = 23,
        *,
        occupied: bool = False,
        window_open: bool = True,
        climate_mode: str = "cool",
    ) -> dict[str, object]:
        if climate_mode not in {"off", "cool", "heat"}:
            raise ValueError("climate_mode must be off, cool, or heat")
        with self._lock:
            # 外部 ESPHome 节点的存在传感器是只读实体；这里只重置可控设备，
            # 不再通过模拟器伪造 presence/window 状态。
            for device_id, payload in (
                ("living_room_main_light", {"power": "on", "brightness": 80}),
                ("living_room_curtain", {"position": 100}),
                ("living_room_ac", {"mode": climate_mode, "temperature": 26}),
            ):
                entity_id = self._esphome_entity_id(device_id)
                if entity_id is None:
                    raise ValueError(f"真实设备未注册：{device_id}")
                self._control_esphome_device(device_id, entity_id, payload)
            self._session = self._new_session()
            self._last_response = None
            self._last_night_response = None
            self._control_context.clear_all()
            return self.status()

    def observe(self, unoccupied_minutes: int | None = None) -> dict[str, object]:
        with self._lock:
            proactive = self.proactive()
            if not bool(proactive["config"].get("enabled", True)):
                self._last_response = InteractionResponse(
                    status="disabled",
                    message="主动规则已停用，没有执行分析。",
                )
                return {"response": to_jsonable(self._last_response), "status": self.status()}
            if not proactive["complete"]:
                raise ValueError("主动规则缺少存在传感器、门窗传感器或空调绑定")
            sources = proactive["sources"]
            presence = _require_source(sources, "presence")
            contact = _require_source(sources, "contact")
            climate = _require_source(sources, "climate")
            duration = int(proactive["unoccupied_minutes"])
            self._unoccupied_minutes = duration
            adapter = HomeAssistantSpaceAdapter(
                self._ha,
                EnergyEntityMap(
                    climate=str(climate["entity_id"]),
                    presence=str(presence["entity_id"]),
                    window=str(contact["entity_id"]),
                    ac_rated_power_w=max(float(proactive["power_w"]), 1_050),
                    room=str(proactive["config"]["room"]),
                ),
            )
            snapshot = adapter.capture_energy_snapshot(duration)
            self._last_response = self._session.observe(snapshot)
            return {"response": to_jsonable(self._last_response), "status": self.status()}

    def message(self, text: str) -> dict[str, object]:
        if not text.strip() or len(text) > 500:
            raise ValueError("message must contain 1 to 500 characters")
        with self._lock:
            self._last_response = self._session.user_message("household", text)
            return {"response": to_jsonable(self._last_response), "status": self.status()}

    def chat(self, text: str) -> dict[str, object]:
        """Route one utterance: saved workflow trigger first, else Agent planning.

        Fixed actions the user already fixed into a workflow should not be
        re-planned — that costs seconds and can produce a different plan. So the
        deterministic tiers run first. A semantic match asks for confirmation
        instead of moving devices on a probabilistic decision.
        """
        normalized = text.strip()
        if not normalized:
            raise ValueError("message must contain 1 to 500 characters")
        match = match_trigger(self._workflows, normalized, self._agent_llm_client)
        if match is None:
            self._pending_trigger = None
            return self._agent_runtime.submit_text(normalized)
        if match.needs_confirmation:
            self._pending_trigger = {"text": normalized, "match": match.to_json()}
            self._agent_runtime.record_turn("user", normalized)
            self._agent_runtime.record_turn(
                "assistant",
                f"这听起来像「{match.workflow.name}」（{match.reason}）。"
                f"确认后我按 {len(match.workflow.steps)} 个固定动作执行。",
            )
            return {**self.chat_history(), "pending_trigger": match.to_json()}
        return self._run_matched_workflow(normalized, match)

    def confirm_trigger(self) -> dict[str, object]:
        """Execute the workflow a semantic match proposed."""
        pending = self._pending_trigger
        if pending is None:
            raise ValueError("当前没有待确认的工作流触发")
        raw_match = pending["match"]
        workflow_id = str(raw_match["workflow"]["workflow_id"])  # type: ignore[index]
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            self._pending_trigger = None
            raise ValueError("该工作流已被删除")
        self._pending_trigger = None
        run = self.run_workflow(workflow_id)
        self._agent_runtime.record_turn("assistant", _workflow_run_summary(workflow.name, run))
        return {**self.chat_history(), "workflow_run": run, "trigger": raw_match}

    def dismiss_trigger(self) -> dict[str, object]:
        """Drop the proposal without touching any device."""
        if self._pending_trigger is None:
            raise ValueError("当前没有待确认的工作流触发")
        self._pending_trigger = None
        self._agent_runtime.record_turn("assistant", "已取消，没有执行任何动作。")
        return self.chat_history()

    def _run_matched_workflow(self, text: str, match: object) -> dict[str, object]:
        workflow = match.workflow  # type: ignore[attr-defined]
        self._agent_runtime.record_turn("user", text)
        run = self.run_workflow(workflow.workflow_id)
        self._agent_runtime.record_turn("assistant", _workflow_run_summary(workflow.name, run))
        return {
            **self.chat_history(),
            "workflow_run": run,
            "trigger": match.to_json(),  # type: ignore[attr-defined]
        }

    def workflows(self) -> dict[str, object]:
        """Saved workflows, with the trigger texts the chat box will honour."""
        with self._lock:
            return {
                "workflows": [item.to_json() for item in self._workflows.all()],
                "pending_trigger": (
                    self._pending_trigger["match"] if self._pending_trigger else None
                ),
            }

    def save_workflow(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            workflow = self._workflows.save(payload)
            return {"ok": True, "workflow": workflow.to_json(), **self.workflows()}

    def delete_workflow(self, workflow_id: str) -> dict[str, object]:
        with self._lock:
            if not self._workflows.delete(workflow_id):
                raise ValueError(f"工作流不存在：{workflow_id}")
            return {"ok": True, **self.workflows()}

    def run_workflow(self, workflow_id: str) -> dict[str, object]:
        """Execute one saved workflow step by step through the real control path.

        Steps run sequentially and每步 verifies real Home Assistant state, exactly
        like a manual control. A failed step is recorded and execution continues,
        so one offline device does not silently swallow the rest of the scene.
        Step delays are honoured only up to a demo-safe cap; longer waits belong
        to the proactive rules, not a blocking HTTP request.
        """
        workflow = self._workflows.get(workflow_id)
        if workflow is None:
            raise ValueError(f"工作流不存在：{workflow_id}")
        started = time.monotonic()
        results: list[dict[str, object]] = []
        for index, step in enumerate(workflow.steps):
            entry: dict[str, object] = {
                "index": index,
                "device_id": step.device_id,
                "action": step.action,
                "delay_minutes": step.delay_minutes,
            }
            payload = _workflow_step_payload(step)
            if payload is None:
                entry.update({"status": "failed", "error": f"不支持的动作：{step.action}"})
                results.append(entry)
                continue
            try:
                response = self.control_device(step.device_id, payload)
                entry.update({
                    "status": "done",
                    "expected": response.get("expected"),
                    "brand_translation": response.get("brand_translation"),
                })
            except (ValueError, RuntimeError, OSError) as error:
                entry.update({"status": "failed", "error": str(error)})
            results.append(entry)
        done = sum(1 for item in results if item["status"] == "done")
        return {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "steps": results,
            "done_count": done,
            "failed_count": len(results) - done,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }

    def preview_workflow(self, text: str) -> dict[str, object]:
        """Generate a grounded workflow plan without executing any device action."""
        return self._workflow_planner.preview(text)

    def energy_history(self, hours: int = 6, samples: int = 72) -> dict[str, object]:
        """Rebuild a power curve from recorder history using the rated-power model.

        Power is an estimate derived from device state, not meter readings; the
        response says so explicitly so the UI can label it honestly.
        """
        if not 1 <= hours <= 48:
            raise ValueError("hours must be between 1 and 48")
        if not 2 <= samples <= 500:
            raise ValueError("samples must be between 2 and 500")
        with self._lock:
            ha_states = {
                str(item.get("entity_id")): item
                for item in self._ha.states()
                if isinstance(item.get("entity_id"), str)
            }
            devices = self._projected_devices(ha_states)
            tracked = [
                device
                for device in devices
                if _device_contributes_power(device)
            ]
            entity_ids = [str(device["entity_id"]) for device in tracked]
            now = datetime.now().astimezone()
            start = now - timedelta(hours=hours)
            series = self._ha.history(entity_ids, start) if entity_ids else []
            by_entity = {
                str(points[0].get("entity_id")): points
                for points in series
                if points and isinstance(points[0].get("entity_id"), str)
            }
            points = _power_timeline(tracked, by_entity, start, now, samples)
            return {
                "hours": hours,
                "model": "rated_estimate",
                "model_label": "按设备额定功率估算，非电表实测",
                "points": points,
                "current_power_w": points[-1]["power_w"] if points else 0.0,
                "period_kwh": _trapezoid_kwh(points),
                "tracked_devices": [
                    {
                        "device_id": str(device["device_id"]),
                        "name": str(device["name"]),
                        "room_name": str(device["room_name"]),
                        "entity_id": str(device["entity_id"]),
                        "recorded": str(device["entity_id"]) in by_entity,
                    }
                    for device in tracked
                ],
            }

    def esphome_nodes(self) -> dict[str, object]:
        """Report the configured ESPHome endpoints and whether HA resolved each one."""
        with self._lock:
            ha_states = {
                str(item.get("entity_id")): item
                for item in self._ha.states()
                if isinstance(item.get("entity_id"), str)
            }
            devices = self._projected_devices(ha_states)
            online_devices = [device for device in devices if device.get("online")]
            resolved_roles = {
                NODE_ROLE_BY_DEVICE.get(str(device["device_id"]), "")
                for device in online_devices
            }
            return {
                "host_hint": DEFAULT_ESPHOME_HOST,
                "nodes": [
                    {
                        "role": role,
                        "label": _ESPHOME_NODE_LABELS.get(role, role),
                        "host": host,
                        "port": port,
                        "connected": role in resolved_roles,
                    }
                    for role, host, port in esphome_nodes()
                ],
                "projected": {"esphome": len(devices), "online": len(online_devices)},
            }

    def agent_status(self) -> dict[str, object]:
        return self._agent_runtime.status()

    def health(self) -> dict[str, object]:
        """System health for demo readiness（/api/health）。"""
        workbench = "ok"
        home_assistant = "ok"
        agent_snapshot = self._agent_runtime.status()
        llm_available = bool(agent_snapshot.get("llm_available"))
        llm = "ok" if llm_available else "unavailable"
        runtime_health = self._agent_runtime.runtime_health()
        try:
            self._ha.states()
        except (RuntimeError, OSError):
            home_assistant = "unavailable"
        devices = {"light": "unknown", "curtain": "unknown", "climate": "unknown", "temperature": "unknown", "presence": "unknown"}
        type_to_key = {"light": "light", "curtain": "curtain", "climate": "climate", "temperature": "temperature", "presence": "presence"}
        try:
            for device in self.devices()["devices"]:
                device_type = str(device.get("type", ""))
                key = type_to_key.get(device_type)
                if key and str(device.get("room", "")) == "living_room" and devices[key] == "unknown":
                    devices[key] = "ok" if bool(device.get("online")) else "unavailable"
        except (RuntimeError, OSError):
            pass
        dependencies_ready = home_assistant == "ok" and all(value == "ok" for value in devices.values())
        runtime_ready = runtime_health.get("status") == "ready"
        if dependencies_ready and runtime_ready:
            agent_status = "ready" if llm_available else "unavailable"
        else:
            agent_status = "degraded"
        agent = {
            **runtime_health,
            "status": agent_status,
            "mode": "llm" if llm_available else "unavailable",
        }
        planning_client = getattr(self, "_agent_llm_client", None)
        return {
            "workbench": workbench,
            "home_assistant": home_assistant,
            "llm": llm,
            "llm_channels": self._llm_channels(),
            "llm_last_channel": getattr(planning_client, "last_channel", None),
            "llm_last_latency_seconds": getattr(planning_client, "last_latency_seconds", None),
            "esphome_devices": devices,
            "agent": agent,
        }

    def _llm_channels(self) -> list[dict[str, object]]:
        """Describe the configured planning channels; empty when single-channel."""
        channels = getattr(getattr(self, "_agent_llm_client", None), "channels", None)
        return list(channels) if isinstance(channels, list) else []

    def demo_override(self, brightness: int) -> dict[str, object]:
        """Simulate a human override by REALLY changing the light through HA."""
        if not 1 <= int(brightness) <= 100:
            raise ValueError("brightness must be between 1 and 100")
        result = self.control_device("living_room_main_light", {"power": "on", "brightness": int(brightness)})
        # 立刻跑一轮观测，human_override 检测当场出现在决策链上，不用等轮询。
        return {
            "override_sent": {"brightness": int(brightness)},
            **result,
            "status": self._agent_runtime.observe_now(),
        }

    def demo_disturbance(self, temperature: float) -> dict[str, object]:
        """Inject heat through the ESPHome firmware service (real physical-path disturbance)."""
        if not 16 <= float(temperature) <= 40:
            raise ValueError("temperature must be between 16 and 40")
        # 空调制冷时固件每秒降温 0.20°C，注入的热量会在约 35s 内被压回目标值，
        # 智能体来观测时偏离已经消失。先停制冷，扰动才能持续到被观测到。
        cooling_stopped = False
        try:
            self.control_device("living_room_ac", {"mode": "off"})
            cooling_stopped = True
        except (RuntimeError, ValueError, OSError) as error:
            self._agent_runtime.record_turn(
                "assistant", f"扰动注入前停止制冷失败，温度可能被空调压回：{error}")
        self._ha.call_service(
            "esphome",
            os.environ.get("ESPHOME_HEAT_INJECTION_SERVICE", "living_room_ac_demo_heat_injection"),
            {"target_temperature": float(temperature)},
        )
        return {
            "disturbance_sent": {"temperature": float(temperature), "cooling_stopped": cooling_stopped},
            "status": self._agent_runtime.observe_now(),
        }

    def demo_reset(self) -> dict[str, object]:
        """Restore the recording start state through the real device path.

        每一步都是真实的 HA 服务调用，不是界面上的假状态。窗帘全行程 10s，
        因此本调用可能耗时十几秒；前端需要给出进行中提示。
        """
        steps: list[dict[str, object]] = []

        def run(label: str, action: Any) -> None:
            try:
                action()
                steps.append({"step": label, "ok": True})
            except (RuntimeError, ValueError, OSError) as error:
                steps.append({"step": label, "ok": False, "error": str(error)})

        # 环境温度先回到 30.2°C，观影场景才有"有点热"的真实前提。
        run("环境温度 30.2°C", lambda: self._ha.call_service(
            "esphome",
            os.environ.get("ESPHOME_HEAT_INJECTION_SERVICE", "living_room_ac_demo_heat_injection"),
            {"target_temperature": 30.2},
        ))
        run("客厅主灯 80%", lambda: self.control_device(
            "living_room_main_light", {"power": "on", "brightness": 80}))
        run("客厅窗帘全开", lambda: self.control_device(
            "living_room_curtain", {"position": 100}))
        run("客厅空调关闭", lambda: self.control_device(
            "living_room_ac", {"mode": "off"}))
        run("客厅电视开启", lambda: self.control_device(
            "living_room_tv", {"power": "on"}))
        run("客厅窗户打开", lambda: self._ha.call_service(
            "esphome", "living_room_aux_demo_set_window", {"opened": True}))
        run("卧室阅读灯关闭", lambda: self.control_device(
            "bedroom_reading_light", {"power": "off"}))
        run("卧室起身传感器复位", lambda: self._ha.call_service(
            "esphome", "bedroom_demo_set_presence", {"occupied": False}))
        run("卧室照度 8 lx", lambda: self._ha.call_service(
            "esphome", "bedroom_demo_set_illuminance", {"lux": 8.0}))
        # 清空会话与学习到的偏好，让每次录制从同一起点开始。
        run("清空对话", self.clear_chat)
        run("清空家庭偏好", self.clear_preferences)
        return {"reset": steps, "ok": all(bool(item["ok"]) for item in steps), **self.devices()}

    def demo_presence(self, occupied: bool) -> dict[str, object]:
        """Drive a real presence edge on the bedroom node for the night-safety demo."""
        self._ha.call_service(
            "esphome", "bedroom_demo_set_presence", {"occupied": bool(occupied)})
        return {"presence_sent": {"occupied": bool(occupied)}}

    def demo_illuminance(self, lux: float) -> dict[str, object]:
        """Set the bedroom illuminance reading through the firmware service."""
        if not 0 <= float(lux) <= 2000:
            raise ValueError("lux must be between 0 and 2000")
        self._ha.call_service(
            "esphome", "bedroom_demo_set_illuminance", {"lux": float(lux)})
        return {"illuminance_sent": {"lux": float(lux)}}

    def demo_readiness(self) -> dict[str, object]:
        """One aggregated go/no-go check so recording never starts on a broken stack."""
        health = self.health()
        agent = health.get("agent") if isinstance(health.get("agent"), dict) else {}
        payload = self.devices()
        devices = payload["devices"] if isinstance(payload["devices"], list) else []
        online = [item for item in devices if item.get("online")]
        missing = payload["diagnostics"]["missing_devices"]  # type: ignore[index]
        context_window = self._planner_context_window()
        # 槽位上下文装不下带设备清单的规划 prompt 时，结构化调用会 500 并静默退回
        # 确定性规划——录出来就是"AI 没参与"。自检必须在开拍前拦住这种情况。
        context_ok = context_window is None or context_window >= _MIN_PLANNER_CONTEXT
        checks = [
            {"name": "工作台", "ok": health.get("workbench") == "ok"},
            {"name": "Home Assistant", "ok": health.get("home_assistant") == "ok"},
            {"name": "规划模型", "ok": health.get("llm") == "ok",
             "detail": _channel_detail(health)},
            {"name": "模型上下文容量", "ok": context_ok,
             "detail": (f"每槽 {context_window} tokens"
                        if context_window is not None else "服务端未上报，跳过检查")
             + (f"，规划需要 ≥ {_MIN_PLANNER_CONTEXT}" if not context_ok else "")},
            {"name": "Agent 运行时", "ok": _agent_runtime_ready(agent),
             "detail": _agent_runtime_detail(agent)},
            {"name": "设备全部在线", "ok": len(devices) > 0 and len(online) == len(devices),
             "detail": f"{len(online)}/{len(devices)} 在线"},
            {"name": "实体绑定无缺失", "ok": not missing},
        ]
        return {
            "ready": all(bool(item["ok"]) for item in checks),
            "checks": checks,
            "device_total": len(devices),
            "device_online": len(online),
            "brands": brand_summary(self._brand_bindings.bindings()),
            "planner_context_window": context_window,
        }

    def _planner_context_window(self) -> int | None:
        """Per-slot context of the channel that actually plans, or None when unknown.

        规划走 ``_agent_llm_client``（双通道时是路由器）。云端通道没有槽位上限，
        路由器会返回 None，自检据此跳过这项检查——那是为 llama.cpp 设计的。
        """
        getter = getattr(getattr(self, "_agent_llm_client", None), "context_window", None)
        if not callable(getter):
            # 兼容旧的单通道注入：语言路由器包着 client。
            router = getattr(self, "_language_router", None)
            getter = getattr(getattr(router, "client", None), "context_window", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except (RuntimeError, OSError):
            return None

    def chat_history(self) -> dict[str, object]:
        return self._agent_runtime.history()

    def chat_snapshot(self) -> dict[str, object]:
        """Poll-friendly history: returns live stage progress mid-turn."""
        payload = self._agent_runtime.history_snapshot()
        if self._pending_trigger is not None:
            return {**payload, "pending_trigger": self._pending_trigger["match"]}
        return payload

    def confirm_chat(self) -> dict[str, object]:
        return self._agent_runtime.history()

    def cancel_chat(self) -> dict[str, object]:
        return self._agent_runtime.submit_text("取消当前目标")

    def clear_chat(self) -> dict[str, object]:
        return self._agent_runtime.clear()

    def set_fault(self, mode: str, delay_ms: int = 0) -> dict[str, object]:
        if mode not in {
            "none",
            "offline",
            "delay",
            "reject",
            "ack_without_state_change",
            "invalid_state",
        }:
            raise ValueError("unsupported fault mode")
        proactive = self.proactive()
        climate = _require_source(proactive["sources"], "climate")
        result = self.set_device_fault(str(climate["device_id"]), mode, delay_ms)
        return {"fault": result["fault"], "status": self.status()}

    def clear_preferences(self) -> dict[str, object]:
        with self._lock:
            self._memory.clear()
            self._agent = SpaceButlerAgent(self._memory)
            self._session = self._new_session()
            self._last_response = None
            self._last_night_response = None
            self._control_context.clear_all()
            return self.status()

    def events(self, limit: int = 30) -> dict[str, object]:
        """Device events from the agent timeline, newest first.

        The timeline is the only place that records real device activity: the
        commands the agent sent, the HA read-back verdicts and the state changes
        attributed to a person. Sensor drift is included so the events view can
        show what actually changed, not just what was commanded.
        """
        snapshot = self._agent_runtime.status()
        raw_timeline = snapshot.get("timeline")
        timeline = raw_timeline if isinstance(raw_timeline, list) else []
        raw_actions = snapshot.get("recent_actions")
        actions = raw_actions if isinstance(raw_actions, list) else []
        names = self._device_names()
        events: list[dict[str, object]] = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            device_id = str(action.get("device_id", ""))
            verified = action.get("verified")
            events.append(
                {
                    "timestamp": _event_timestamp(float(action.get("sent_at", 0.0) or 0.0)),
                    "ts": float(action.get("sent_at", 0.0) or 0.0),
                    "event_type": "command_sent" if verified is None else "state_verified" if verified else "state_failed",
                    "device_id": device_id,
                    "device_name": names.get(device_id, device_id),
                    "summary": _action_event_summary(action),
                    "state_change": _action_state_change(action),
                    "outcome": "pending" if verified is None else "success" if verified else "failed",
                    "payload": {"entity_id": action.get("entity_id"), "capability": action.get("capability")},
                }
            )
        for event in timeline:
            if not isinstance(event, dict) or event.get("kind") not in _EVENT_KINDS:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            device_id = str(data.get("device_id", "") or _event_device_id(event))
            events.append(
                {
                    "timestamp": _event_timestamp(float(event.get("ts", 0.0) or 0.0)),
                    "ts": float(event.get("ts", 0.0) or 0.0),
                    "event_type": str(event.get("kind", "")),
                    "device_id": device_id,
                    "device_name": names.get(device_id, device_id or "全屋"),
                    "summary": str(event.get("message", "")),
                    "state_change": _timeline_state_change(data),
                    "outcome": _EVENT_KINDS[str(event.get("kind", ""))],
                    "payload": data,
                }
            )
        events.sort(key=lambda item: float(item.get("ts", 0.0) or 0.0), reverse=True)
        return {"events": events[:limit]}

    def _device_names(self) -> dict[str, str]:
        payload = self.devices()
        devices = payload.get("devices")
        if not isinstance(devices, list):
            return {}
        return {
            str(item.get("device_id")): str(item.get("name") or item.get("device_id"))
            for item in devices
            if isinstance(item, dict)
        }

    def _device_summary(self) -> dict[str, int]:
        payload = self.devices()
        devices = payload["devices"]
        if not isinstance(devices, list):
            return {"total": 0, "online": 0, "rooms": 0}
        return {
            "total": len(devices),
            "online": sum(bool(item.get("online")) for item in devices if isinstance(item, dict)),
            "rooms": len(payload.get("rooms", [])),
        }

    def _esphome_entity_id(self, device_id: str) -> str | None:
        # devices() 使用可重入锁，运行时解析实际 entity_id（可能带 _2 后缀）。
        for item in self.devices()["devices"]:
            if str(item.get("device_id")) == device_id and item.get("source") == "esphome":
                return str(item["entity_id"])
        return None

    def _control_esphome_device(
        self,
        device_id: str,
        entity_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Control an ESPHome Host device through HA services with state read-back."""
        record = _esphome_record(device_id)
        if record is None:
            raise RuntimeError(f"unknown ESPHome device: {device_id}")
        device_type = str(record["type"])
        if device_type in _ESPHOME_READONLY_TYPES:
            raise ValueError("该设备是只读传感器，不支持控制指令")
        ha_state = self._optional_ha_state(entity_id)
        if ha_state is None or str(ha_state.get("state", "")) in {"unknown", "unavailable"}:
            raise ValueError("device is not available for control")
        expected = self._call_device_service(entity_id, device_type, payload)
        # time_based 窗帘全行程 10s，回读窗口放宽到 30s。
        self._wait_for(
            lambda: _control_matches(self._ha.state(entity_id), expected),
            timeout_seconds=30,
        )
        return {
            "controlled": device_id,
            "expected": expected,
            # 适配器翻译结果：同一个规范化指令在该设备所属生态里的原生报文。
            # 描述性字段，执行仍然走上面的 Home Assistant 服务调用。
            "brand_translation": translate_command(
                device_id, payload, self._brand_bindings.bindings()
            ),
            "night_response": None,
            **self.devices(),
        }

    def _call_device_service(
        self,
        entity_id: str,
        device_type: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if device_type in {"light", "switch"}:
            power = str(payload.get("power", "")).lower()
            if power not in {"on", "off"}:
                raise ValueError("power must be on or off")
            data: dict[str, object] = {"entity_id": entity_id}
            if device_type == "light" and power == "on" and "brightness" in payload:
                brightness = int(payload["brightness"])
                if not 1 <= brightness <= 100:
                    raise ValueError("brightness must be between 1 and 100")
                data["brightness_pct"] = brightness
            self._ha.call_service(device_type, f"turn_{power}", data)
            return {"state": power, **({"brightness": data.get("brightness_pct")} if "brightness_pct" in data else {})}
        if device_type == "curtain":
            position = int(payload.get("position", -1))
            if not 0 <= position <= 100:
                raise ValueError("position must be between 0 and 100")
            self._ha.call_service(
                "cover",
                "set_cover_position",
                {"entity_id": entity_id, "position": position},
            )
            return {"position": position}
        if device_type == "climate":
            mode = str(payload.get("mode", "")).lower()
            if mode not in {"off", "cool", "heat"}:
                raise ValueError("mode must be off, cool, or heat")
            self._ha.call_service(
                "climate",
                "set_hvac_mode",
                {"entity_id": entity_id, "hvac_mode": mode},
            )
            expected: dict[str, object] = {"state": mode}
            if mode != "off" and "temperature" in payload:
                temperature = float(payload["temperature"])
                if not 16 <= temperature <= 30:
                    raise ValueError("temperature must be between 16 and 30")
                self._ha.call_service(
                    "climate",
                    "set_temperature",
                    {"entity_id": entity_id, "temperature": temperature},
                )
                expected["temperature"] = temperature
            return expected
        raise ValueError("unsupported device type")

    def _ha_state_available(self, entity_id: str) -> bool:
        if not entity_id:
            return False
        try:
            return self._ha.state(entity_id).get("state") not in {"unknown", "unavailable"}
        except RuntimeError:
            return False

    def _new_session(self) -> SpaceButlerSession:
        return SpaceButlerSession(
            self._agent,
            HomeAssistantRuntime(self._ha, verification_timeout_seconds=5),
            self._language_router,
        )

    def _optional_ha_state(self, entity_id: str) -> dict[str, object] | None:
        try:
            return self._ha.state(entity_id)
        except RuntimeError:
            return None

    def _wait_for(self, predicate: Any, timeout_seconds: float = 15) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise TimeoutError("workbench scene did not stabilize")


# ESPHome 实体按 friendly_name 别名 + domain 匹配；entity_id 始终运行时解析。
# 全部设备统一由 ESPHome 节点提供，不再有 MQTT 来源。
_ESPHOME_NODE_LABELS = {
    "light": "客厅主灯节点",
    "curtain": "客厅窗帘节点",
    "ac": "客厅空调节点",
    "presence": "客厅人体存在节点",
    "bedroom": "卧室节点",
    "living_room_aux": "客厅电视与窗户节点",
}
# 规划 prompt 含全部设备清单与实时状态；低于这个槽位上下文，结构化调用会 500。
# llama.cpp 把 `-c` 均分给 slot，所以 2048/4=512 是不够的。
_MIN_PLANNER_CONTEXT = 4096
# 一次本地规划要 75–145 秒，期间后台 tick 必然滞后，runtime_health 会转 degraded。
# 那只说明它在忙，不是坏了；超过这个秒数才当作真的卡死。
_AGENT_STALL_SECONDS = 420.0


def _channel_detail(health: dict[str, object]) -> str:
    """Say which planning channels exist and which one served last."""
    channels = health.get("llm_channels")
    if not isinstance(channels, list) or not channels:
        return "单通道；模型不可用时 Agent 会安全拒绝执行，不会退回固定脚本"
    parts = []
    for channel in channels:
        mark = "可用" if channel.get("ready") else "不可用"
        parts.append(f"{channel.get('label')}（{mark}）")
    served = health.get("llm_last_channel")
    latency = health.get("llm_last_latency_seconds")
    tail = ""
    if served:
        label = "云端" if served == "cloud" else "本地"
        tail = f"；上次走{label}"
        if isinstance(latency, (int, float)):
            tail += f" {latency:.1f}s"
    return " + ".join(parts) + tail


def _agent_runtime_ready(agent: dict[str, object]) -> bool:
    """Busy planning is not a failure; a dead thread or fatal error is.

    录完一个场景马上点自检时 tick 正好滞后，不能因此告诉用户"不能开拍"。
    """
    if not agent.get("thread_alive") or agent.get("fatal_error"):
        return False
    if str(agent.get("mode")) != "llm":
        return False
    age = agent.get("last_tick_age_seconds")
    return not isinstance(age, (int, float)) or age <= _AGENT_STALL_SECONDS


def _agent_runtime_detail(agent: dict[str, object]) -> str:
    age = agent.get("last_tick_age_seconds")
    if agent.get("fatal_error"):
        return f"致命错误：{agent['fatal_error']}"
    if not agent.get("thread_alive"):
        return "后台线程未存活"
    if str(agent.get("mode")) != "llm":
        return f"mode={agent.get('mode')}，模型未参与规划"
    if isinstance(age, (int, float)) and age > _AGENT_STALL_SECONDS:
        return f"tick 已滞后 {age:.0f}s，可能卡死"
    if isinstance(age, (int, float)) and age > 10:
        return f"正在规划中（tick 滞后 {age:.0f}s）"
    return "空闲待命"
_ESPHOME_ROOM_NAMES = {"living_room": "客厅", "bedroom": "卧室", "home": "全屋"}
_ESPHOME_READONLY_TYPES = {"presence", "contact", "illuminance", "temperature"}

# 配对预览用的代表性指令：让用户在纳管前就看清该品牌的报文长什么样。
_SAMPLE_PAYLOADS: dict[str, dict[str, object]] = {
    "light": {"power": "on", "brightness": 45},
    "switch": {"power": "on"},
    "curtain": {"position": 30},
    "climate": {"mode": "cool", "temperature": 25.0},
}


def _sample_payload(device_type: str) -> dict[str, object]:
    """A representative normalized command for this device type."""
    return dict(_SAMPLE_PAYLOADS.get(device_type, {"power": "on"}))


def _workflow_step_payload(step: object) -> dict[str, object] | None:
    """Translate one saved workflow step into a device control payload.

    Action ids mirror the ``ui.workflow_actions`` the device projection publishes.
    Returns ``None`` when the action does not exist for this device type, so the
    run records an explicit failure instead of sending something unintended.
    """
    record = device_record(str(step.device_id))  # type: ignore[attr-defined]
    if record is None:
        return None
    device_type = str(record["type"])
    action = str(step.action)  # type: ignore[attr-defined]
    value = step.value  # type: ignore[attr-defined]
    if device_type in {"light", "switch"}:
        if action == "turn_on":
            return {"power": "on"}
        if action == "turn_off":
            return {"power": "off"}
        if action == "set_brightness" and device_type == "light":
            brightness = _coerce_int(value)
            return None if brightness is None else {"power": "on", "brightness": brightness}
        return None
    if device_type == "curtain":
        if action == "turn_on":
            return {"position": 100}
        if action == "turn_off":
            return {"position": 0}
        if action == "set_position":
            position = _coerce_int(value)
            return None if position is None else {"position": position}
        return None
    if device_type == "climate":
        if action == "turn_off":
            return {"mode": "off"}
        if action == "set_hvac_mode":
            mode = str(value or "").strip().lower()
            return {"mode": mode} if mode in {"cool", "heat"} else None
        if action == "set_temperature":
            temperature = _coerce_float(value)
            return None if temperature is None else {"mode": "cool", "temperature": temperature}
        return None
    return None


def _coerce_int(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _workflow_run_summary(name: str, run: dict[str, object]) -> str:
    """Describe a workflow run honestly: failures are named, not smoothed over."""
    done = int(run.get("done_count", 0))
    failed = int(run.get("failed_count", 0))
    if failed == 0:
        return f"已执行工作流「{name}」，{done} 个动作全部完成并回读验证。"
    reasons = "；".join(
        str(step.get("error", "未知原因"))
        for step in run.get("steps", [])  # type: ignore[union-attr]
        if isinstance(step, dict) and step.get("status") == "failed"
    )
    return f"工作流「{name}」完成 {done} 个动作，{failed} 个失败：{reasons}"


def _esphome_record(device_id: str) -> dict[str, object] | None:
    """Return the ESPHome registry record for a projected device id."""
    return device_record(device_id)


def _projected_device_state(
    device_type: str,
    ha_state: dict[str, object],
    record: dict[str, object],
    ha_states: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Project one entity state, folding in a companion power meter when configured."""
    state = _esphome_device_state(device_type, ha_state)
    power_entity_id = _resolved_power_entity(record)
    if power_entity_id is not None:
        measured = _numeric_entity_state(ha_states.get(power_entity_id))
        if measured is not None:
            state["power_w"] = measured
    return state


def _resolved_power_entity(record: dict[str, object]) -> str | None:
    """Resolve a companion power meter, letting .env override a suffixed entity_id."""
    override_env = record.get("power_entity_env")
    if isinstance(override_env, str):
        override = os.environ.get(override_env, "").strip()
        if override:
            return override
    default = record.get("power_entity_id")
    return default if isinstance(default, str) else None


def _numeric_entity_state(ha_state: dict[str, object] | None) -> float | None:
    """Read one HA entity state as a float, or None when it is not numeric."""
    if not isinstance(ha_state, dict):
        return None
    try:
        return float(str(ha_state.get("state", "")))
    except ValueError:
        return None


def _esphome_device_state(device_type: str, ha_state: dict[str, object]) -> dict[str, object]:
    """Project one HA entity state into the fleet-style state dict used by the UI."""
    value = str(ha_state.get("state", "unavailable"))
    attributes = ha_state.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    if device_type == "light":
        brightness = attributes.get("brightness")
        if not isinstance(brightness, (int, float)):
            brightness = 255 if value == "on" else 0
        return {"power": "ON" if value == "on" else "OFF", "brightness": float(brightness)}
    if device_type == "curtain":
        position = attributes.get("current_position")
        return {"position": int(float(position)) if isinstance(position, (int, float)) else 0}
    if device_type == "climate":
        return {
            "mode": value if value in {"off", "cool", "heat"} else "off",
            "temperature": float(attributes.get("temperature", 26) or 26),
            "current_temperature": float(attributes.get("current_temperature", 0) or 0),
        }
    if device_type == "presence":
        return {"occupied": value == "on"}
    if device_type == "switch":
        return {"power": "ON" if value == "on" else "OFF"}
    if device_type == "contact":
        return {"open": value == "on"}
    if device_type == "temperature":
        try:
            return {"temperature": float(value)}
        except ValueError:
            return {"temperature": 0.0}
    if device_type == "illuminance":
        try:
            return {"illuminance": float(value)}
        except ValueError:
            return {"illuminance": 0.0}
    return {}


_DEVICE_UI_META: dict[str, tuple[str, str, str, str]] = {
    "light": ("灯光", "lightbulb", "amber", "lamp"),
    "switch": ("智能开关", "toggle-right", "blue", "switch"),
    "climate": ("空调", "air-vent", "cyan", "thermostat"),
    "curtain": ("窗帘", "blinds", "green", "cover"),
    "presence": ("存在传感器", "user-round-check", "green", "presence"),
    "contact": ("门窗传感器", "panel-top-open", "amber", "contact"),
    "illuminance": ("照度传感器", "sun-medium", "amber", "meter"),
    "temperature": ("温度传感器", "thermometer", "cyan", "meter"),
}


def _device_icon(device: dict[str, object], default: str) -> str:
    """Resolve presentation from device metadata; the browser never knows device IDs."""
    configured = str(device.get("icon", "")).strip()
    if configured:
        return configured
    name = str(device.get("name", "")).casefold()
    device_type = str(device.get("type", ""))
    if device_type == "switch" and any(token in name for token in ("电视", "tv", "screen", "display")):
        return "monitor"
    if device_type == "light" and any(token in name for token in ("阅读", "台灯", "desk", "reading")):
        return "lamp-desk"
    return default


_SOURCE_LABELS = {"esphome": "ESPHome 固件"}


# 进入「设备事件」表格的 timeline 事件种类 -> 结果语义。
_EVENT_KINDS: dict[str, str] = {
    "execution": "info",
    "verification": "info",
    "attribution": "info",
    "human_override": "warning",
    "goal_cancelled": "info",
    "no_action": "info",
    "observation_error": "failed",
    "runtime_error": "failed",
    "runtime_fatal": "failed",
}

_CAPABILITY_LABELS: dict[str, str] = {
    "set_brightness": "设置亮度",
    "set_position": "设置窗帘位置",
    "set_temperature": "设置温度",
    "set_hvac_mode": "设置运行模式",
    "turn_on": "开启",
    "turn_off": "关闭",
}


def _event_timestamp(ts: float) -> str:
    """Full ISO timestamp: the events table parses this with ``new Date``."""
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat()


def _capability_value(capability: str, value: object) -> str:
    if value is None:
        return ""
    if capability in {"set_brightness", "set_position"}:
        return f"{float(value):g}%" if isinstance(value, (int, float)) else str(value)
    if capability == "set_temperature":
        return f"{float(value):g}°C" if isinstance(value, (int, float)) else str(value)
    if capability == "set_hvac_mode":
        return {"cool": "制冷", "heat": "制热", "off": "关闭"}.get(str(value), str(value))
    return str(value)


def _action_event_summary(action: dict[str, object]) -> str:
    capability = str(action.get("capability", ""))
    label = _CAPABILITY_LABELS.get(capability, capability)
    value = _capability_value(capability, action.get("value"))
    return f"{label} {value}".strip()


def _action_state_change(action: dict[str, object]) -> str:
    """What the device state became, in words rather than raw entity payloads."""
    expected = action.get("expected") if isinstance(action.get("expected"), dict) else {}
    observed = action.get("observed") if isinstance(action.get("observed"), dict) else {}
    if isinstance(observed.get("error"), str):
        return f"未生效：{observed['error']}"
    parts: list[str] = []
    if "state" in expected:
        parts.append({"on": "已开启", "off": "已关闭", "cool": "制冷", "heat": "制热"}.get(
            str(expected["state"]), str(expected["state"])
        ))
    if "brightness" in expected:
        parts.append(f"亮度 {expected['brightness']}%")
    if "position" in expected:
        parts.append(f"位置 {expected['position']}%")
    if "temperature" in expected:
        parts.append(f"设定 {float(expected['temperature']):g}°C")
    verified = action.get("verified")
    suffix = "" if verified is None else "（回读一致）" if verified else "（回读不一致）"
    return f"{' · '.join(parts)}{suffix}" if parts else suffix


def _timeline_state_change(data: dict[str, object]) -> str:
    before, after = data.get("before"), data.get("after")
    if before is None or after is None:
        return ""
    capability = str(data.get("capability", ""))
    return f"{_capability_value(capability, before)} → {_capability_value(capability, after)}"


def _event_device_id(event: dict[str, object]) -> str:
    data = event.get("data")
    if isinstance(data, dict):
        for key in ("device_id", "entity_id"):
            if isinstance(data.get(key), str):
                return str(data[key])
    return ""


def _source_readout(device: dict[str, object], suffix: str) -> str:
    """Describe where a reading came from, using the device's real transport."""
    source = str(device.get("source", ""))
    return f"{_SOURCE_LABELS.get(source, '设备')}{suffix}"


def _device_ui(device: dict[str, object]) -> dict[str, object]:
    """Build a complete UI descriptor so the browser never knows concrete devices."""
    device_type = str(device.get("type", ""))
    type_label, icon, tone, floor_shape = _DEVICE_UI_META.get(
        device_type,
        (device_type or "设备", "cpu", "blue", "device"),
    )
    icon = _device_icon(device, icon)
    raw_state = device.get("state")
    state = raw_state if isinstance(raw_state, dict) else {}
    raw_ha_state = device.get("ha_state")
    ha_state = raw_ha_state if isinstance(raw_ha_state, dict) else {}
    raw_attributes = ha_state.get("attributes")
    attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
    online = bool(device.get("online"))
    controls: list[dict[str, object]] = []
    workflow_actions: list[dict[str, object]] = []
    primary_action: dict[str, object] | None = None
    active = False
    warning = False
    floor_readout = ""
    state_primary = "设备离线" if not online else "状态未知"
    state_secondary = f"故障：{device.get('fault_mode', 'unknown')}" if not online else "等待状态"

    if device_type in {"light", "switch"}:
        active = str(state.get("power", "")).upper() == "ON"
        state_primary = "已开启" if active else "已关闭"
        if device_type == "light":
            brightness = round(float(state.get("brightness", 0)) * 100 / 255)
            state_secondary = f"亮度 {brightness}%" if active else "功耗 0 W"
        else:
            state_secondary = f"{float(state.get('power_w', 0)):.1f} W"
        primary_action = {
            "label": "关闭" if active else "开启",
            "payload": {"power": "off" if active else "on"},
        }
        controls.append(
            {
                "kind": "toggle",
                "label": "电源",
                "status": "设备运行中" if active else "设备已关闭",
                "active": active,
                "payload": primary_action["payload"],
            }
        )
        workflow_actions.extend(
            [
                {"id": "turn_on", "label": "开启", "payload": {"power": "on"}},
                {"id": "turn_off", "label": "关闭", "payload": {"power": "off"}},
            ]
        )
        if device_type == "light":
            controls.append(
                {
                    "kind": "range",
                    "label": "亮度",
                    "field": "brightness",
                    "value": max(brightness, 1),
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "unit": "%",
                    "payload": {"power": "on"},
                }
            )
            workflow_actions.append(
                {
                    "id": "set_brightness",
                    "label": "设置亮度",
                    "payload": {"power": "on"},
                    "parameter": {
                        "field": "brightness",
                        "type": "number",
                        "min": 1,
                        "max": 100,
                        "step": 1,
                        "default": max(brightness, 60),
                        "unit": "%",
                    },
                }
            )
    elif device_type == "curtain":
        position = int(state.get("position", 0))
        active = position > 0
        state_primary = f"{position}%"
        state_secondary = "已打开" if active else "已关闭"
        primary_action = {
            "label": "关闭" if active else "打开",
            "payload": {"position": 0 if active else 100},
        }
        controls.extend(
            [
                {
                    "kind": "toggle",
                    "label": "窗帘",
                    "status": state_secondary,
                    "active": active,
                    "payload": primary_action["payload"],
                },
                {
                    "kind": "range",
                    "label": "开合位置",
                    "field": "position",
                    "value": position,
                    "min": 0,
                    "max": 100,
                    "step": 5,
                    "unit": "%",
                    "payload": {},
                },
            ]
        )
        workflow_actions.extend(
            [
                {"id": "turn_on", "label": "打开", "payload": {"position": 100}},
                {"id": "turn_off", "label": "关闭", "payload": {"position": 0}},
                {
                    "id": "set_position",
                    "label": "设置位置",
                    "payload": {},
                    "parameter": {
                        "field": "position",
                        "type": "number",
                        "min": 0,
                        "max": 100,
                        "step": 5,
                        "default": position,
                        "unit": "%",
                    },
                },
            ]
        )
    elif device_type == "climate":
        mode = str(state.get("mode", "off"))
        temperature = float(state.get("temperature", 24))
        current_temperature = float(state.get("current_temperature", 0))
        live_modes = attributes.get("hvac_modes")
        supported_modes = (
            [str(value).lower() for value in live_modes if str(value).strip()]
            if isinstance(live_modes, list)
            else ["off", "cool", "heat"]
        )
        turn_on_mode = next((value for value in supported_modes if value != "off"), "cool")
        floor_readout = f"{current_temperature:g}°"
        active = mode != "off"
        mode_label = {"cool": "制冷", "heat": "制热", "off": "已关闭"}.get(mode, mode)
        state_primary = mode_label
        state_secondary = f"设定 {temperature:g}°C · 室温 {current_temperature:g}°C"
        primary_action = {
            "label": "关闭" if active else "开启",
            "payload": {
                "mode": "off" if active else turn_on_mode,
                "temperature": temperature,
            },
        }
        controls.extend(
            [
                {
                    "kind": "toggle",
                    "label": "电源",
                    "status": mode_label,
                    "active": active,
                    "payload": primary_action["payload"],
                },
                {
                    "kind": "segmented",
                    "label": "空调模式",
                    "options": [
                        {
                            "label": {"off": "关闭", "cool": "制冷", "heat": "制热"}.get(value, value),
                            "value": value,
                            "active": mode == value,
                            "payload": {"mode": value, "temperature": temperature},
                        }
                        for value in supported_modes
                    ],
                },
                {
                    "kind": "stepper",
                    "label": "设定温度",
                    "field": "temperature",
                    "value": temperature,
                    "min": 16,
                    "max": 30,
                    "step": 0.5,
                    "unit": "°C",
                    "payload": {"mode": mode if active else turn_on_mode},
                },
            ]
        )
        mode_options = [
            {"value": value, "label": {"cool": "制冷", "heat": "制热"}.get(value, value)}
            for value in supported_modes
            if value != "off"
        ]
        workflow_actions.append({"id": "turn_off", "label": "关闭", "payload": {"mode": "off"}})
        if mode_options:
            workflow_actions.append(
                {
                    "id": "set_hvac_mode",
                    "label": "设置模式",
                    "payload": {"temperature": temperature},
                    "parameter": {
                        "field": "mode",
                        "type": "select",
                        "options": mode_options,
                        "default": turn_on_mode,
                    },
                }
            )
            workflow_actions.append(
                {
                    "id": "set_temperature",
                    "label": "设置温度",
                    "payload": {"mode": mode if active else turn_on_mode},
                    "parameter": {
                        "field": "temperature",
                        "type": "number",
                        "min": 16,
                        "max": 30,
                        "step": 0.5,
                        "default": temperature,
                        "unit": "°C",
                    },
                }
            )
    elif device_type in {"presence", "contact"}:
        field = "occupied" if device_type == "presence" else "open"
        active = bool(state.get(field))
        if device_type == "presence":
            state_primary = "检测到有人" if active else "空间无人"
            reported_status = "有人" if active else "无人"
        else:
            state_primary = "窗户打开" if active else "窗户关闭"
            reported_status = "打开" if active else "关闭"
        state_secondary = _source_readout(device, "上报")
        warning = device_type == "contact" and active
        if not bool(device.get("readonly")):
            controls.append(
                {
                    "kind": "toggle",
                    "label": "模拟上报",
                    "status": reported_status,
                    "active": active,
                    "payload": {field: not active},
                    "test_control": True,
                }
            )
    elif device_type == "illuminance":
        illuminance = int(state.get("illuminance", 0))
        state_primary = f"{illuminance} lux"
        state_secondary = "实时环境照度"
        if not bool(device.get("readonly")):
            controls.append(
                {
                    "kind": "range",
                    "label": "模拟照度上报",
                    "field": "illuminance",
                    "value": illuminance,
                    "min": 0,
                    "max": 500,
                    "step": 1,
                    "unit": " lux",
                    "payload": {},
                    "test_control": True,
                }
            )
    elif device_type == "temperature":
        temperature = float(state.get("temperature", 0) or 0)
        state_primary = f"{temperature:g}°C"
        state_secondary = _source_readout(device, "实时室温")
        floor_readout = f"{temperature:g}°"

    state_label = state_primary if state_secondary == "等待状态" else f"{state_primary} · {state_secondary}"
    fault_options = [
        {"value": "none", "label": "正常"},
        {"value": "offline", "label": "离线"},
    ]
    if device_type not in {"presence", "contact", "illuminance", "temperature"}:
        fault_options.extend(
            [
                {"value": "reject", "label": "拒绝命令"},
                {"value": "delay", "label": "响应延迟"},
                {"value": "ack_without_state_change", "label": "ACK 不变"},
                {"value": "invalid_state", "label": "无效状态"},
            ]
        )
    return {
        "type_label": type_label,
        "icon": icon,
        "tone": tone,
        "name": str(device.get("name", "设备")),
        "state_primary": state_primary,
        "state_secondary": state_secondary,
        "state_label": state_label,
        "active": active,
        "warning": warning,
        "controls": controls,
        "workflow_actions": workflow_actions,
        "primary_action": primary_action,
        "fault_options": fault_options,
        "floorplan": {
            "shape": floor_shape,
            "readout": floor_readout,
        },
    }


def _dashboard_projection(
    devices: list[dict[str, object]],
    rooms: list[dict[str, object]],
) -> dict[str, object]:
    """Project inventory into a generic, responsive dashboard layout."""
    candidates = [
        device
        for device in devices
        if isinstance(device.get("ui"), dict)
        and isinstance(device["ui"].get("primary_action"), dict)
    ]
    candidates.sort(
        key=lambda item: (
            not bool(item.get("online")),
            not bool(item["ui"].get("active")),
            -len(item.get("capabilities", []))
            if isinstance(item.get("capabilities"), list)
            else 0,
            str(item.get("room_name", "")),
            str(item.get("name", "")),
        )
    )
    favorite_device_ids = [str(item.get("device_id", "")) for item in candidates[:4]]

    room_count = len(rooms)
    columns = 1 if room_count <= 1 else 2
    rows = max(1, (room_count + columns - 1) // columns)
    cell_width = 100 / columns
    cell_height = 100 / rows
    projected_rooms: list[dict[str, object]] = []
    device_positions: dict[str, dict[str, float]] = {}
    for room_index, room in enumerate(rooms):
        column = room_index % columns
        row = room_index // columns
        left = column * cell_width
        top = row * cell_height
        room_devices = [item for item in devices if item.get("room") == room.get("room")]
        device_columns = min(3, max(1, len(room_devices)))
        device_rows = max(1, (len(room_devices) + device_columns - 1) // device_columns)
        header_height = min(16.0, cell_height * 0.28)
        usable_height = max(10.0, cell_height - header_height)
        for device_index, device in enumerate(room_devices):
            device_column = device_index % device_columns
            device_row = device_index // device_columns
            device_positions[str(device.get("device_id", ""))] = {
                "left": round(
                    left + (device_column + 0.5) * cell_width / device_columns,
                    2,
                ),
                "top": round(
                    top + header_height + (device_row + 0.5) * usable_height / device_rows,
                    2,
                ),
            }
        projected_rooms.append(
            {
                **room,
                "left": round(left, 2),
                "top": round(top, 2),
                "width": round(cell_width, 2),
                "height": round(cell_height, 2),
                "theme": room_index % 4,
            }
        )

    total_power = round(sum(float(room.get("power_w", 0)) for room in rooms), 1)
    return {
        "device_types": [
            {"value": device_type, "label": meta[0]}
            for device_type, meta in _DEVICE_UI_META.items()
        ],
        "favorite_device_ids": favorite_device_ids,
        "rooms": projected_rooms,
        "device_positions": device_positions,
        # 累计电量不在这里外推；由 /api/energy-history 用 recorder 真实历史积分得出。
        "energy": {
            "current_power_w": total_power,
            "model": "rated_estimate",
        },
    }


def _room_summaries(devices: list[dict[str, object]]) -> list[dict[str, object]]:
    rooms: dict[str, dict[str, object]] = {}
    for device in devices:
        room = str(device.get("room", "home"))
        summary = rooms.setdefault(
            room,
            {
                "room": room,
                "name": str(device.get("room_name", room)),
                "devices": 0,
                "online": 0,
                "power_w": 0.0,
            },
        )
        summary["devices"] = int(summary["devices"]) + 1
        summary["online"] = int(summary["online"]) + int(bool(device.get("online")))
        summary["power_w"] = round(float(summary["power_w"]) + _estimated_device_power(device), 1)
    return sorted(rooms.values(), key=lambda item: str(item["name"]))


def _device_contributes_power(device: dict[str, object]) -> bool:
    """Return whether the rated-power model attributes any draw to this device."""
    return str(device.get("type", "")) in {"light", "switch", "climate"}


def _parse_history_time(value: object) -> datetime | None:
    """Parse a recorder timestamp, tolerating the trailing Z form."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _state_at(points: list[dict[str, object]], moment: datetime) -> dict[str, object] | None:
    """Return the last recorded state at or before ``moment``."""
    resolved: dict[str, object] | None = None
    for point in points:
        changed = _parse_history_time(point.get("last_updated") or point.get("last_changed"))
        if changed is None or changed > moment:
            break
        resolved = point
    return resolved


def _power_timeline(
    devices: list[dict[str, object]],
    history: dict[str, list[dict[str, object]]],
    start: datetime,
    end: datetime,
    samples: int,
) -> list[dict[str, object]]:
    """Sample total estimated power at even intervals across the window."""
    span = (end - start).total_seconds()
    if span <= 0:
        return []
    timeline: list[dict[str, object]] = []
    for index in range(samples):
        moment = start + timedelta(seconds=span * index / (samples - 1))
        total = 0.0
        for device in devices:
            points = history.get(str(device["entity_id"]))
            if not points:
                continue
            recorded = _state_at(points, moment)
            if recorded is None:
                continue
            value = str(recorded.get("state", "unavailable"))
            if value in {"unknown", "unavailable"}:
                continue
            device_type = str(device["type"])
            state = _esphome_device_state(device_type, recorded)
            # 开关类设备的历史属性自带 power_w，优先使用真实记录值。
            attributes = recorded.get("attributes")
            if isinstance(attributes, dict) and isinstance(attributes.get("power_w"), (int, float)):
                state["power_w"] = float(attributes["power_w"])
            total += _estimated_device_power(
                {"type": device_type, "online": True, "state": state}
            )
        timeline.append({"at": moment.isoformat(), "power_w": round(total, 1)})
    return timeline


def _trapezoid_kwh(points: list[dict[str, object]]) -> float:
    """Integrate a sampled power curve into energy, in kWh."""
    total_wh = 0.0
    for earlier, later in zip(points, points[1:]):
        started = _parse_history_time(earlier.get("at"))
        ended = _parse_history_time(later.get("at"))
        if started is None or ended is None:
            continue
        hours = (ended - started).total_seconds() / 3600
        total_wh += (float(earlier["power_w"]) + float(later["power_w"])) / 2 * hours
    return round(total_wh / 1000, 3)


def _estimated_device_power(device: dict[str, object]) -> float:
    if not bool(device.get("online")):
        return 0.0
    state = device.get("state")
    if not isinstance(state, dict):
        return 0.0
    if isinstance(state.get("power_w"), (int, float)):
        return float(state["power_w"])
    if device.get("type") == "climate":
        return 0.0 if state.get("mode") == "off" else 1050.0
    if device.get("type") == "light" and str(state.get("power", "")).upper() == "ON":
        return round(12.0 * float(state.get("brightness", 0)) / 255, 1)
    return 0.0


def _resolve_rule_config(
    stored: dict[str, Any] | None,
    devices: list[dict[str, object]],
) -> dict[str, object]:
    if stored is not None:
        return {
            "enabled": bool(stored.get("enabled", True)),
            "room": str(stored.get("room", "")),
            "presence_device_id": str(stored.get("presence_device_id", "")),
            "contact_device_id": str(stored.get("contact_device_id", "")),
            "climate_device_id": str(stored.get("climate_device_id", "")),
        }
    rooms = sorted({str(item.get("room", "")) for item in devices if item.get("room")})
    selected_room = next(
        (
            room
            for room in rooms
            if all(
                any(item.get("room") == room and item.get("type") == device_type for item in devices)
                for device_type in ("presence", "contact", "climate")
            )
        ),
        rooms[0] if rooms else "",
    )
    return {
        "enabled": True,
        "room": selected_room,
        "presence_device_id": _first_device_id(devices, selected_room, "presence"),
        "contact_device_id": _first_device_id(devices, selected_room, "contact"),
        "climate_device_id": _first_device_id(devices, selected_room, "climate"),
    }


def _first_device_id(devices: list[dict[str, object]], room: str, device_type: str) -> str:
    return next(
        (
            str(item.get("device_id", ""))
            for item in devices
            if item.get("room") == room and item.get("type") == device_type
        ),
        "",
    )


def _device_by_id(devices: list[dict[str, object]], device_id: str) -> dict[str, object] | None:
    if not device_id:
        return None
    return next((item for item in devices if item.get("device_id") == device_id), None)


def _source_record(source: object) -> dict[str, object]:
    return source if isinstance(source, dict) else {}


def _source_ha_state(source: object) -> dict[str, object]:
    if not isinstance(source, dict):
        return {"state": "unavailable", "attributes": {}}
    value = source.get("ha_state")
    return value if isinstance(value, dict) else {"state": "unavailable", "attributes": {}}


def _source_option(device: dict[str, object]) -> dict[str, object]:
    return {
        "device_id": device.get("device_id"),
        "name": device.get("name"),
        "room": device.get("room"),
        "room_name": device.get("room_name"),
        "entity_id": device.get("entity_id"),
        "online": device.get("online"),
        "discovered": device.get("discovered"),
    }


def _night_light_option(
    device: dict[str, object],
    manual_context: ManualControlContext | None = None,
) -> dict[str, object]:
    return {
        **_source_option(device),
        "state": device.get("state"),
        "updated_at": device.get("updated_at"),
        "manual_override": manual_context is not None,
        "manual_source": manual_context.source if manual_context is not None else None,
        "manual_remaining_seconds": manual_context.remaining_seconds if manual_context is not None else 0,
    }


def _light_is_on(device: dict[str, object]) -> bool:
    ha_state = _source_ha_state(device)
    if ha_state.get("state") == "on":
        return True
    state = device.get("state")
    return isinstance(state, dict) and str(state.get("power", "")).upper() == "ON"


def _night_device_state(
    light: dict[str, object],
    member_id: str,
    path_order: int,
    *,
    manual_control: bool = False,
) -> DeviceState:
    ha_state = _source_ha_state(light)
    raw_attributes = ha_state.get("attributes")
    attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
    registry_state = light.get("state")
    raw_brightness = attributes.get("brightness")
    if raw_brightness is None and isinstance(registry_state, dict):
        raw_brightness = registry_state.get("brightness")
    if isinstance(raw_brightness, (int, float)):
        attributes["brightness_pct"] = round(raw_brightness * 100 / 255)
    attributes["night_path"] = [member_id]
    attributes["night_path_order"] = path_order
    attributes["manual_control"] = manual_control
    attributes["available"] = bool(light.get("online")) and bool(light.get("discovered"))
    if _light_is_on(light):
        state = "on"
    elif isinstance(registry_state, dict) and str(registry_state.get("power", "")).upper() == "OFF":
        state = "off"
    else:
        state = str(ha_state.get("state", "unknown"))
    return DeviceState(
        entity_id=str(light.get("entity_id", "")),
        domain="light",
        room=str(light.get("room", "home")),
        state=state,
        attributes=attributes,
    )


def _presence_occupied(source: object) -> bool:
    ha_state = str(_source_ha_state(source).get("state", "")).lower()
    if ha_state in {"on", "off"}:
        return ha_state == "on"
    record = _source_record(source)
    state = record.get("state")
    return bool(state.get("occupied")) if isinstance(state, dict) else False


def _contact_open(source: object) -> bool:
    ha_state = str(_source_ha_state(source).get("state", "")).lower()
    if ha_state in {"on", "off"}:
        return ha_state == "on"
    record = _source_record(source)
    state = record.get("state")
    return bool(state.get("open")) if isinstance(state, dict) else False


def _climate_running(source: object) -> bool:
    record = _source_record(source)
    state = record.get("state")
    return isinstance(state, dict) and state.get("mode") in {"cool", "heat", "on"}


def _climate_label(source: object) -> str:
    record = _source_record(source)
    state = record.get("state")
    mode = str(state.get("mode", "unknown")) if isinstance(state, dict) else "unknown"
    return {"cool": "制冷", "heat": "制热", "off": "关闭"}.get(mode, mode)


def _unoccupied_minutes(source: object) -> int:
    record = _source_record(source)
    state = record.get("state")
    if not isinstance(state, dict) or bool(state.get("occupied")):
        return 0
    since = state.get("unoccupied_since")
    if isinstance(since, str):
        try:
            parsed = datetime.fromisoformat(since)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() // 60))
        except ValueError:
            pass
    updated_at = record.get("updated_at")
    if isinstance(updated_at, str):
        try:
            parsed = datetime.fromisoformat(updated_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() // 60))
        except ValueError:
            pass
    return 0


def _resolve_night_safety_config(
    stored: dict[str, object] | None,
    devices: list[dict[str, object]],
) -> dict[str, object]:
    lights = [item for item in devices if item.get("type") == "light"]
    by_id = {str(item.get("device_id")): item for item in lights}
    requested = stored.get("path_light_device_ids") if isinstance(stored, dict) else None
    selected_ids = []
    if isinstance(requested, list):
        selected_ids = list(
            dict.fromkeys(
                str(item)
                for item in requested
                if str(item) in by_id
            )
        )
    if not selected_ids:
        room_rank = {
            "bedroom": 0,
            "primary_bedroom": 0,
            "living_room": 1,
            "hallway": 2,
            "bathroom": 3,
            "kitchen": 4,
        }
        selected_ids = [
            str(item.get("device_id"))
            for item in sorted(
                lights,
                key=lambda item: (
                    room_rank.get(str(item.get("room")), 99),
                    str(item.get("name", "")),
                ),
            )[:3]
        ]
    first_light = by_id.get(selected_ids[0]) if selected_ids else None
    default_room = str(first_light.get("room", "bedroom")) if first_light else "bedroom"
    raw = stored if isinstance(stored, dict) else {}
    origin_room = str(raw.get("origin_room", default_room))
    return {
        "enabled": bool(raw.get("enabled", True)),
        "member_id": "elder_01",
        "member_name": str(raw.get("member_name", "爷爷")),
        "origin_room": origin_room,
        "presence_device_id": str(
            raw.get("presence_device_id")
            or _first_device_id(devices, origin_room, "presence")
            or _first_device_of_type(devices, "presence")
        ),
        "illuminance_device_id": str(
            raw.get("illuminance_device_id")
            or _first_device_id(devices, origin_room, "illuminance")
            or _first_device_of_type(devices, "illuminance")
        ),
        "path_light_device_ids": selected_ids,
        "brightness": _coerce_bounded_int(raw.get("brightness"), 5, 40, 18),
        "max_illuminance": _coerce_bounded_int(raw.get("max_illuminance"), 1, 200, 50),
    }


def _bounded_int(value: object, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _coerce_bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _first_device_of_type(devices: list[dict[str, object]], device_type: str) -> str:
    return next(
        (str(item.get("device_id", "")) for item in devices if item.get("type") == device_type),
        "",
    )


def _illuminance_value(source: object) -> int:
    ha_state = _source_ha_state(source).get("state")
    try:
        if str(ha_state).lower() not in {"", "unknown", "unavailable", "none"}:
            return max(0, round(float(ha_state)))
    except (TypeError, ValueError):
        pass
    record = _source_record(source)
    state = record.get("state")
    if isinstance(state, dict) and isinstance(state.get("illuminance"), (int, float)):
        return max(0, round(float(state["illuminance"])))
    return 0


def _ha_numeric_state(state: dict[str, object]) -> float:
    try:
        return float(state.get("state", 0))
    except (TypeError, ValueError):
        return 0.0


def _condition(
    key: str,
    label: str,
    actual: str,
    required: str,
    met: bool,
    source: object,
) -> dict[str, object]:
    record = _source_record(source)
    ha_state = _source_ha_state(source)
    return {
        "key": key,
        "label": label,
        "actual": actual,
        "required": required,
        "met": met,
        "device_id": record.get("device_id"),
        "entity_id": record.get("entity_id"),
        "updated_at": ha_state.get("last_updated") or record.get("updated_at"),
    }


def _require_source(sources: object, role: str) -> dict[str, object]:
    if not isinstance(sources, dict):
        raise ValueError("rule sources are invalid")
    source = sources.get(role)
    if not isinstance(source, dict):
        raise ValueError(f"rule is missing its {role} source")
    return source


def _body_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _room_name(room: str) -> str:
    return {
        "living_room": "客厅",
        "bedroom": "卧室",
        "primary_bedroom": "主卧",
        "kitchen": "厨房",
        "study": "书房",
        "balcony": "阳台",
        "bathroom": "卫生间",
        "home": "全屋",
    }.get(room, room.replace("_", " ").title())


def _control_matches(state: dict[str, object], expected: dict[str, object]) -> bool:
    if "state" in expected and str(state.get("state", "")).lower() != str(expected["state"]).lower():
        return False
    if "numeric_state" in expected and abs(_ha_numeric_state(state) - float(expected["numeric_state"])) > 0.5:
        return False
    attributes = state.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    if "brightness" in expected:
        brightness = attributes.get("brightness")
        if not isinstance(brightness, (int, float)):
            return False
        actual_percent = round(float(brightness) * 100 / 255)
        if abs(actual_percent - int(expected["brightness"])) > 1:
            return False
    if "position" in expected:
        position = attributes.get("current_position")
        if not isinstance(position, (int, float)) or round(float(position)) != int(expected["position"]):
            return False
    if "temperature" in expected:
        temperature = attributes.get("temperature")
        if not isinstance(temperature, (int, float)) or abs(float(temperature) - float(expected["temperature"])) > 0.1:
            return False
    return True


def _device_api_route(path: str) -> tuple[str, str] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[:2] != ["api", "devices"]:
        return None
    action = parts[3]
    if action not in {"control", "fault"}:
        return None
    return unquote(parts[2]), action


def _set_fault_from_body(
    controller: WorkbenchController,
    device_id: str,
    body: dict[str, object],
) -> dict[str, object]:
    return controller.set_device_fault(
        device_id,
        str(body.get("mode", "none")),
        int(body.get("delay_ms", 0)),
    )


def serve_workbench(
    controller: WorkbenchController,
    static_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    voice_service: VoiceService | None = None,
) -> ThreadingHTTPServer:
    static_root = static_root.resolve()
    voice = voice_service if voice_service is not None else VoiceService()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path.startswith("/api/voice/"):
                self._voice("GET", path)
                return
            if path == "/api/status":
                self._api(controller.status)
                return
            if path == "/api/devices":
                self._api(controller.devices)
                return
            if path == "/api/devices/diagnostics":
                self._api(controller.device_diagnostics)
                return
            if path == "/api/proactive":
                self._api(controller.proactive)
                return
            if path == "/api/night-safety":
                self._api(controller.night_safety)
                return
            if path == "/api/chat":
                self._api(controller.chat_snapshot)
                return
            if path == "/api/agent":
                self._api(controller.agent_status)
                return
            if path == "/api/health":
                self._api(controller.health)
                return
            if path == "/api/events":
                self._api(controller.events)
                return
            if path == "/api/energy-history":
                query = parse_qs(urlparse(self.path).query)
                hours = int(query.get("hours", ["6"])[0])
                self._api(lambda: controller.energy_history(hours))
                return
            if path == "/api/esphome-nodes":
                self._api(controller.esphome_nodes)
                return
            if path == "/api/demo/readiness":
                self._api(controller.demo_readiness)
                return
            if path == "/api/brands":
                self._api(controller.brand_inventory)
                return
            if path == "/api/workflows":
                self._api(controller.workflows)
                return
            self._static()

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path.startswith("/api/voice/"):
                self._voice("POST", path)
                return
            if path == "/api/devices":
                self._api(lambda: controller.add_device(self._body()))
                return
            device_route = _device_api_route(path)
            if device_route is not None:
                device_id, action = device_route
                if action == "control":
                    self._api(lambda: controller.control_device(device_id, self._body()))
                    return
                if action == "fault":
                    self._api(
                        lambda: _set_fault_from_body(controller, device_id, self._body())
                    )
                    return
            routes = {
                "/api/proactive/config": controller.configure_proactive,
                "/api/night-safety/config": controller.configure_night_safety,
                "/api/night-safety/prepare": controller.prepare_night_safety,
                "/api/night-safety/run": controller.run_night_safety,
                "/api/night-safety/trigger": controller.trigger_night_safety,
                "/api/scene/reset": lambda body: controller.reset_scene(
                    int(body.get("unoccupied_minutes", 23)),
                    occupied=_body_bool(body.get("occupied", False)),
                    window_open=_body_bool(body.get("window_open", True)),
                    climate_mode=str(body.get("climate_mode", "cool")),
                ),
                "/api/observe": lambda body: controller.observe(
                    int(body["unoccupied_minutes"]) if "unoccupied_minutes" in body else None
                ),
                "/api/message": lambda body: controller.message(str(body.get("text", ""))),
                "/api/chat": lambda body: controller.chat(str(body.get("text", ""))),
                "/api/workflows/preview": lambda body: controller.preview_workflow(
                    str(body.get("text", ""))
                ),
                "/api/workflows": controller.save_workflow,
                "/api/workflows/run": lambda body: controller.run_workflow(
                    str(body.get("workflow_id", ""))
                ),
                "/api/chat/trigger/confirm": lambda _body: controller.confirm_trigger(),
                "/api/chat/trigger/dismiss": lambda _body: controller.dismiss_trigger(),
                "/api/brands/discover": lambda body: controller.discover_brand_devices(
                    str(body.get("brand_id", ""))
                ),
                "/api/brands/adopt": lambda body: controller.adopt_brand_devices(
                    str(body.get("brand_id", "")),
                    [str(item) for item in body.get("device_ids", [])]
                    if isinstance(body.get("device_ids"), list) else [],
                ),
                "/api/brands/release": lambda body: controller.release_brand_device(
                    str(body.get("device_id", ""))
                ),
                "/api/brands/reset": lambda _body: controller.reset_brand_bindings(),
                "/api/chat/confirm": lambda _body: controller.confirm_chat(),
                "/api/chat/cancel": lambda _body: controller.cancel_chat(),
                "/api/chat/clear": lambda _body: controller.clear_chat(),
                "/api/agent/demo-override": lambda body: controller.demo_override(int(body.get("brightness", 45))),
                "/api/agent/demo-disturbance": lambda body: controller.demo_disturbance(float(body.get("temperature", 31))),
                "/api/demo/reset": lambda _body: controller.demo_reset(),
                "/api/demo/presence": lambda body: controller.demo_presence(
                    _body_bool(body.get("occupied", True))
                ),
                "/api/demo/illuminance": lambda body: controller.demo_illuminance(
                    float(body.get("lux", 8))
                ),
                "/api/fault": lambda body: controller.set_fault(
                    str(body.get("mode", "none")),
                    int(body.get("delay_ms", 0)),
                ),
                "/api/preferences/clear": lambda _body: controller.clear_preferences(),
            }
            route = routes.get(path)
            if route is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._api(lambda: route(self._body()))

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path.startswith("/api/voice/"):
                self._voice("DELETE", path)
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "workflows"]:
                self._api(lambda: controller.delete_workflow(unquote(parts[2])))
                return
            if len(parts) != 3 or parts[:2] != ["api", "devices"]:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._api(lambda: controller.remove_device(unquote(parts[2])))

        def _voice(self, method: str, path: str) -> None:
            # Prevent another website from consuming the local user's speech quota.
            origin = self.headers.get("Origin", "")
            parsed_origin = urlparse(origin)
            if ((origin and (parsed_origin.scheme not in {"http", "https"}
                             or parsed_origin.netloc != self.headers.get("Host")))
                    or self.headers.get("Sec-Fetch-Site") == "cross-site"
                    or (method != "GET" and not origin)):
                self._json(HTTPStatus.FORBIDDEN, {"error": "语音接口只允许当前页面访问。"})
                return
            try:
                parts = path.strip("/").split("/")
                if method == "GET" and path == "/api/voice/status":
                    payload = voice.status()
                elif method == "POST" and path == "/api/voice/sessions":
                    payload = voice.create()
                elif len(parts) in (4, 5) and parts[:3] == ["api", "voice", "sessions"]:
                    session_id = parts[3]
                    if len(parts) == 4 and method == "GET":
                        payload = voice.get(session_id).snapshot()
                    elif len(parts) == 4 and method == "DELETE":
                        payload = voice.cancel(session_id)
                    elif len(parts) == 5 and parts[4] == "audio" and method == "POST":
                        length = int(self.headers.get("Content-Length", "0"))
                        if (not 0 < length <= CHUNK_BYTES or length % 2
                                or self.headers.get_content_type() != "application/octet-stream"):
                            raise VoiceError("音频分片大小或格式不正确。")
                        self.connection.settimeout(10)
                        audio = self.rfile.read(length)
                        if len(audio) != length:
                            raise VoiceError("音频分片传输不完整。")
                        payload = voice.get(session_id).send_audio(audio, int(self.headers.get("X-Audio-Sequence", "-1")))
                    elif len(parts) == 5 and parts[4] == "finish" and method == "POST":
                        payload = voice.get(session_id).finish()
                    else:
                        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                        return
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                self._json(HTTPStatus.OK, payload)
            except VoiceError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except (ValueError, TimeoutError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "语音请求无效或传输超时。"})
            except Exception:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "语音服务暂不可用，请重试。"})

        def _api(self, callback: Any) -> None:
            try:
                self._json(HTTPStatus.OK, callback())
            except (RuntimeError, TimeoutError, ValueError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception as error:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})

        def _body(self) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            decoded = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(decoded, dict):
                raise ValueError("request body must be a JSON object")
            return decoded

        def _static(self) -> None:
            path = urlparse(self.path).path
            relative = "index.html" if path in {"/", ""} else path.lstrip("/")
            candidate = (static_root / relative).resolve()
            if static_root not in candidate.parents and candidate != static_root:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            payload = candidate.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            # 演示场景禁用静态资源缓存：前端改动立即生效，避免评委看到旧样式。
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: HTTPStatus, payload: object) -> None:
            encoded = json.dumps(to_jsonable(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            return

    class WorkbenchHTTPServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            voice.close()
            super().server_close()

    return WorkbenchHTTPServer((host, port), Handler)


def to_jsonable(value: object) -> object:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value
