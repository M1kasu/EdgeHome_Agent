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
from .edge_language import EdgeLanguageRouter
from .home_assistant import EnergyEntityMap, HomeAssistantClient, HomeAssistantRuntime, HomeAssistantSpaceAdapter
from .interaction import InteractionResponse, SpaceButlerSession
from .memory import HouseholdMemory
from .proactive import ENERGY_RULE_ID, ProactiveRuleStore


class WorkbenchController:
    def __init__(
        self,
        ha_client: HomeAssistantClient,
        simulator_url: str,
        memory: HouseholdMemory,
        language_router: EdgeLanguageRouter | None = None,
    ) -> None:
        self._ha = ha_client
        self._simulator_url = simulator_url.rstrip("/")
        self._memory = memory
        self._agent = SpaceButlerAgent(memory)
        self._language_router = language_router
        self._session = self._new_session()
        self._last_response: object | None = None
        self._unoccupied_minutes = 23
        self._rule_store = ProactiveRuleStore(memory.database_path)
        self._lock = threading.RLock()

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
            if not entity_id or not bool(device.get("online", False)):
                raise ValueError("device is not available for control")
            if device_type in {"presence", "contact"}:
                reported = self._simulator("POST", f"/admin/devices/{device_id}/state", payload)
                active = (
                    bool(reported.get("state", {}).get("occupied"))
                    if device_type == "presence"
                    else bool(reported.get("state", {}).get("open"))
                )
                expected = {"state": "on" if active else "off"}
            else:
                expected = self._call_device_service(entity_id, device_type, payload)
            self._wait_for(
                lambda: _control_matches(self._ha.state(entity_id), expected),
                timeout_seconds=12,
            )
            return {"controlled": device_id, "expected": expected, **self.devices()}

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


def _presence_occupied(source: object) -> bool:
    record = _source_record(source)
    state = record.get("state")
    return bool(state.get("occupied")) if isinstance(state, dict) else False


def _contact_open(source: object) -> bool:
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


def _condition(
    key: str,
    label: str,
    actual: str,
    required: str,
    met: bool,
    source: object,
) -> dict[str, object]:
    record = _source_record(source)
    return {
        "key": key,
        "label": label,
        "actual": actual,
        "required": required,
        "met": met,
        "device_id": record.get("device_id"),
        "entity_id": record.get("entity_id"),
        "updated_at": record.get("updated_at"),
    }


def _require_source(sources: object, role: str) -> dict[str, object]:
    if not isinstance(sources, dict):
        raise ValueError("proactive rule sources are invalid")
    source = sources.get(role)
    if not isinstance(source, dict):
        raise ValueError(f"proactive rule is missing its {role} source")
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
