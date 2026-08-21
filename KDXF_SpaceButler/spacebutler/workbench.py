"""Local HTTP workbench backed by the real HA/MQTT SpaceButler stack."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from .agent import SpaceButlerAgent
from .control_context import ControlContextRegistry, ManualControlContext
from .conversation import ConversationAgent
from .edge_language import EdgeLanguageRouter, EdgeLlmClient
from .home_assistant import EnergyEntityMap, HomeAssistantClient, HomeAssistantRuntime, HomeAssistantSpaceAdapter
from .interaction import InteractionResponse, SpaceButlerSession
from .memory import HouseholdMemory
from .models import DeviceState, EnvironmentState, HouseholdMember, MemberRole, SpatialSnapshot
from .proactive import ENERGY_RULE_ID, NIGHT_SAFETY_RULE_ID, ProactiveRuleStore
from .runtime import execute_and_verify


class WorkbenchController:
    def __init__(
        self,
        ha_client: HomeAssistantClient,
        simulator_url: str,
        memory: HouseholdMemory,
        language_router: EdgeLanguageRouter | None = None,
        edge_llm_client: EdgeLlmClient | None = None,
    ) -> None:
        self._ha = ha_client
        self._simulator_url = simulator_url.rstrip("/")
        self._memory = memory
        self._agent = SpaceButlerAgent(memory)
        self._language_router = language_router
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
        self._lock = threading.RLock()
        self._conversation = ConversationAgent(
            self.devices,
            self.control_device,
            self.observe,
            edge_llm_client,
            memory.database_path,
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
            simulator = _source_record(climate_source)
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
                "simulator": simulator,
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
                "data_source": "device_registry + Home Assistant REST + MQTT",
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
                "data_source": "presence + illuminance entities / Home Assistant REST + MQTT",
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
            except Exception as error:  # keep monitoring through transient HA/MQTT outages
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
            night_safety = self.night_safety()
            path_lights = night_safety["path_lights"]
            if not isinstance(path_lights, list) or not path_lights:
                raise ValueError("night safety has no path lights")
            manual_device_id = str(payload.get("manual_device_id", "")).strip()
            selected_ids = {str(item.get("device_id")) for item in path_lights}
            if manual_device_id and manual_device_id not in selected_ids:
                raise ValueError("manual_device_id must be one of the path lights")
            illuminance = _bounded_int(payload.get("illuminance", 8), 0, 500, "illuminance")
            presence = _require_source(night_safety["sources"], "presence")
            illuminance_sensor = _require_source(night_safety["sources"], "illuminance")
            self._simulator(
                "POST",
                f"/admin/devices/{presence['device_id']}/state",
                {"occupied": False},
            )
            self._simulator(
                "POST",
                f"/admin/devices/{illuminance_sensor['device_id']}/state",
                {"illuminance": illuminance},
            )
            self._wait_for(lambda: self._ha.state(str(presence["entity_id"])).get("state") == "off")
            self._wait_for(
                lambda: _ha_numeric_state(self._ha.state(str(illuminance_sensor["entity_id"]))) == illuminance
            )
            self._night_presence_state = False
            for device_id in selected_ids:
                self._control_context.clear_manual(device_id)
            for light in path_lights:
                device_id = str(light["device_id"])
                entity_id = str(light["entity_id"])
                is_manual = device_id == manual_device_id
                expected = self._call_device_service(
                    entity_id,
                    "light",
                    {"power": "on", "brightness": 55} if is_manual else {"power": "off"},
                )
                self._wait_for(
                    lambda entity_id=entity_id, expected=expected: _control_matches(
                        self._ha.state(entity_id),
                        expected,
                    )
                )
            if manual_device_id:
                self._control_context.set_manual(
                    manual_device_id,
                    source="night_safety_test_takeover",
                    ttl_seconds=1800,
                )
            self._last_night_response = None
            return {
                "night_safety": self.night_safety(),
                "status": self.status(),
                **self.devices(),
            }

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
            simulator_devices = self._simulator("GET", "/devices").get("devices", [])
            if not isinstance(simulator_devices, list):
                raise RuntimeError("simulator devices response is invalid")
            ha_states = {
                str(item.get("entity_id")): item
                for item in self._ha.states()
                if isinstance(item.get("entity_id"), str)
            }
            devices = []
            for item in simulator_devices:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entity_id", ""))
                feedback_entity_id = str(item.get("feedback_entity_id", ""))
                devices.append(
                    {
                        **item,
                        "ha_state": ha_states.get(entity_id),
                        "feedback": ha_states.get(feedback_entity_id),
                        "discovered": entity_id in ha_states,
                    }
                )
            rooms = _room_summaries(devices)
            return {"devices": devices, "rooms": rooms}

    def add_device(self, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            created = self._simulator("POST", "/admin/devices", payload)
            entity_id = str(created.get("entity_id", ""))
            self._wait_for(
                lambda: self._ha_state_available(entity_id),
                timeout_seconds=25,
            )
            return {"created": created, **self.devices()}

    def remove_device(self, device_id: str) -> dict[str, object]:
        with self._lock:
            removed = self._simulator("DELETE", f"/admin/devices/{device_id}")
            return {"removed": removed.get("removed"), **self.devices()}

    def control_device(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        with self._lock:
            device = self._simulator("GET", f"/devices/{device_id}")
            entity_id = str(device.get("entity_id", ""))
            device_type = str(device.get("type", ""))
            state_before = device.get("state")
            was_occupied = (
                bool(state_before.get("occupied"))
                if device_type == "presence" and isinstance(state_before, dict)
                else False
            )
            if not entity_id or not bool(device.get("online", False)):
                raise ValueError("device is not available for control")
            if device_type in {"presence", "contact", "illuminance"}:
                reported = self._simulator("POST", f"/admin/devices/{device_id}/state", payload)
                reported_state = reported.get("state", {})
                if not isinstance(reported_state, dict):
                    raise RuntimeError("sensor returned an invalid state")
                if device_type == "illuminance":
                    active = False
                    expected = {"numeric_state": int(reported_state["illuminance"])}
                else:
                    active = (
                        bool(reported_state.get("occupied"))
                        if device_type == "presence"
                        else bool(reported_state.get("open"))
                    )
                    expected = {"state": "on" if active else "off"}
            else:
                active = False
                expected = self._call_device_service(entity_id, device_type, payload)
            self._wait_for(
                lambda: _control_matches(self._ha.state(entity_id), expected),
                timeout_seconds=12,
            )
            if device_type == "light":
                path_ids = self.night_safety()["config"].get("path_light_device_ids", [])
                if isinstance(path_ids, list) and device_id in path_ids:
                    if str(payload.get("power", "")).lower() == "off":
                        self._control_context.clear_manual(device_id)
                    elif str(payload.get("power", "")).lower() == "on":
                        self._control_context.set_manual(device_id, source="direct_device_control")
            night_response = None
            if device_type == "presence":
                night_config = self.night_safety()["config"]
                if device_id == night_config.get("presence_device_id"):
                    self._night_presence_state = active
                    if active and not was_occupied:
                        night_response = self.run_night_safety()["response"]
            return {
                "controlled": device_id,
                "expected": expected,
                "night_response": night_response,
                **self.devices(),
            }

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
        with self._lock:
            result = self._simulator(
                "POST",
                f"/admin/devices/{device_id}/fault",
                {"mode": mode, "delay_ms": delay_ms},
            )
            return {"fault": result, **self.devices()}

    def reset_scene(
        self,
        unoccupied_minutes: int = 23,
        *,
        occupied: bool = False,
        window_open: bool = True,
        climate_mode: str = "cool",
    ) -> dict[str, object]:
        if not 0 <= unoccupied_minutes <= 240:
            raise ValueError("unoccupied_minutes must be between 0 and 240")
        if climate_mode not in {"off", "cool", "heat"}:
            raise ValueError("climate_mode must be off, cool, or heat")
        with self._lock:
            proactive = self.proactive()
            sources = proactive["sources"]
            presence = _require_source(sources, "presence")
            contact = _require_source(sources, "contact")
            climate = _require_source(sources, "climate")
            self._simulator(
                "POST",
                f"/admin/devices/{climate['device_id']}/fault",
                {"mode": "none", "delay_ms": 0},
            )
            self._simulator(
                "POST",
                f"/admin/devices/{presence['device_id']}/state",
                {
                    "occupied": occupied,
                    **({"unoccupied_minutes": unoccupied_minutes} if not occupied else {}),
                },
            )
            self._simulator(
                "POST",
                f"/admin/devices/{contact['device_id']}/state",
                {"open": window_open},
            )
            self._call_device_service(
                str(climate["entity_id"]),
                "climate",
                {"mode": climate_mode, "temperature": 24},
            )
            self._wait_for(
                lambda: self._ha.state(str(presence["entity_id"])).get("state")
                == ("on" if occupied else "off")
            )
            self._wait_for(
                lambda: self._ha.state(str(contact["entity_id"])).get("state")
                == ("on" if window_open else "off")
            )
            self._wait_for(
                lambda: self._ha.state(str(climate["entity_id"])).get("state") == climate_mode
            )
            self._unoccupied_minutes = 0 if occupied else unoccupied_minutes
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
        return self._conversation.submit(text)

    def chat_history(self) -> dict[str, object]:
        return self._conversation.history()

    def confirm_chat(self) -> dict[str, object]:
        return self._conversation.confirm()

    def cancel_chat(self) -> dict[str, object]:
        return self._conversation.cancel()

    def clear_chat(self) -> dict[str, object]:
        return self._conversation.clear()

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
        capped = min(max(limit, 1), 100)
        return self._simulator("GET", f"/events?limit={capped}")

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

    def _simulator(self, method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = Request(f"{self._simulator_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            try:
                decoded_error = json.loads(detail)
            except json.JSONDecodeError:
                decoded_error = {}
            message = decoded_error.get("error") if isinstance(decoded_error, dict) else None
            raise RuntimeError(str(message or f"simulator returned HTTP {error.code}")) from error
        if not isinstance(decoded, dict):
            raise RuntimeError("simulator returned a non-object response")
        return decoded

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
) -> ThreadingHTTPServer:
    static_root = static_root.resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/status":
                self._api(controller.status)
                return
            if path == "/api/devices":
                self._api(controller.devices)
                return
            if path == "/api/proactive":
                self._api(controller.proactive)
                return
            if path == "/api/night-safety":
                self._api(controller.night_safety)
                return
            if path == "/api/chat":
                self._api(controller.chat_history)
                return
            if path == "/api/events":
                self._api(controller.events)
                return
            self._static()

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
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
                "/api/chat/confirm": lambda _body: controller.confirm_chat(),
                "/api/chat/cancel": lambda _body: controller.cancel_chat(),
                "/api/chat/clear": lambda _body: controller.clear_chat(),
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
            parts = path.strip("/").split("/")
            if len(parts) != 3 or parts[:2] != ["api", "devices"]:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._api(lambda: controller.remove_device(unquote(parts[2])))

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

    return ThreadingHTTPServer((host, port), Handler)


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
