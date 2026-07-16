"""Local HTTP workbench backed by the real HA/MQTT SpaceButler stack."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
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
from .home_assistant import HomeAssistantClient, HomeAssistantRuntime, HomeAssistantSpaceAdapter
from .interaction import SpaceButlerSession
from .memory import HouseholdMemory


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
        self._adapter = HomeAssistantSpaceAdapter(ha_client)
        self._language_router = language_router
        self._session = self._new_session()
        self._last_response: object | None = None
        self._unoccupied_minutes = 23
        self._lock = threading.RLock()

    def status(self) -> dict[str, object]:
        with self._lock:
            climate = self._ha.state("climate.spacebutler_living_room_ac")
            presence = self._ha.state("input_boolean.spacebutler_living_room_presence")
            window = self._ha.state("input_boolean.spacebutler_living_room_window_open")
            simulator = self._simulator("GET", "/devices/living_room_ac")
            feedback = self._optional_ha_state("sensor.spacebutler_living_room_ac_feedback")
            return {
                "healthy": climate.get("state") not in {"unknown", "unavailable"} and simulator.get("online") is True,
                "unoccupied_minutes": self._unoccupied_minutes,
                "climate": climate,
                "presence": presence,
                "window": window,
                "simulator": simulator,
                "feedback": feedback,
                "last_response": to_jsonable(self._last_response),
                "preferences": [to_jsonable(item) for item in self._memory.export_preferences()],
                "device_summary": self._device_summary(),
            }

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

    def reset_scene(self, unoccupied_minutes: int = 23) -> dict[str, object]:
        if not 0 <= unoccupied_minutes <= 240:
            raise ValueError("unoccupied_minutes must be between 0 and 240")
        with self._lock:
            self._simulator("POST", "/admin/devices/living_room_ac/fault", {"mode": "none", "delay_ms": 0})
            self._simulator("POST", "/admin/devices/living_room_ac/reset", {})
            self._ha.call_service(
                "input_boolean",
                "turn_off",
                {"entity_id": "input_boolean.spacebutler_living_room_presence"},
            )
            self._ha.call_service(
                "input_boolean",
                "turn_on",
                {"entity_id": "input_boolean.spacebutler_living_room_window_open"},
            )
            self._wait_for(lambda: self._ha.state("climate.spacebutler_living_room_ac").get("state") == "cool")
            self._unoccupied_minutes = unoccupied_minutes
            self._session = self._new_session()
            self._last_response = None
            return self.status()

    def observe(self, unoccupied_minutes: int | None = None) -> dict[str, object]:
        with self._lock:
            if unoccupied_minutes is not None:
                if not 0 <= unoccupied_minutes <= 240:
                    raise ValueError("unoccupied_minutes must be between 0 and 240")
                self._unoccupied_minutes = unoccupied_minutes
            snapshot = self._adapter.capture_energy_snapshot(self._unoccupied_minutes)
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
        result = self.set_device_fault("living_room_ac", mode, delay_ms)
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
                "/api/scene/reset": lambda body: controller.reset_scene(int(body.get("unoccupied_minutes", 23))),
                "/api/observe": lambda body: controller.observe(int(body.get("unoccupied_minutes", 23))),
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
