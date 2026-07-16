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
            }

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
        with self._lock:
            result = self._simulator(
                "POST",
                "/admin/devices/living_room_ac/fault",
                {"mode": mode, "delay_ms": delay_ms},
            )
            return {"fault": result, "status": self.status()}

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
        with urlopen(request, timeout=15) as response:
            decoded = json.loads(response.read().decode("utf-8"))
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


def serve_workbench(
    controller: WorkbenchController,
    static_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    static_root = static_root.resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/status":
                self._api(controller.status)
                return
            if self.path.startswith("/api/events"):
                self._api(controller.events)
                return
            self._static()

        def do_POST(self) -> None:  # noqa: N802
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
            route = routes.get(self.path)
            if route is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            self._api(lambda: route(self._body()))

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
            relative = "index.html" if self.path in {"/", ""} else self.path.lstrip("/")
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
