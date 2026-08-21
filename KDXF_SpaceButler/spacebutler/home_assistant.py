"""Home Assistant REST execution and spatial snapshot adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import (
    ActionResult,
    DeviceState,
    EnvironmentState,
    ExecutionStatus,
    PlanAction,
    RoomState,
    SpatialSnapshot,
)


class HomeAssistantRequestError(RuntimeError):
    """An authenticated Home Assistant request failed."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HomeAssistantClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        request_timeout_seconds: float = 10,
        token_refresher: Callable[[], str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._request_timeout_seconds = request_timeout_seconds
        self._token_refresher = token_refresher
        self._token_lock = threading.Lock()

    def state(self, entity_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/api/states/{entity_id}")
        if not isinstance(response, dict):
            raise HomeAssistantRequestError(f"invalid state response for {entity_id}")
        return response

    def states(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/api/states")
        if not isinstance(response, list) or not all(isinstance(item, dict) for item in response):
            raise HomeAssistantRequestError("invalid Home Assistant states response")
        return response

    def call_service(self, domain: str, service: str, data: dict[str, object]) -> object:
        return self._request("POST", f"/api/services/{domain}/{service}", data)

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
        stale_token = self._token
        try:
            return self._request_once(method, path, body)
        except HomeAssistantRequestError as error:
            if error.status_code != 401 or self._token_refresher is None:
                raise
        self._refresh_token(stale_token)
        return self._request_once(method, path, body)

    def _request_once(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
        data = None
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self._request_timeout_seconds) as response:
                payload = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise HomeAssistantRequestError(
                f"Home Assistant returned HTTP {error.code}: {detail}",
                status_code=error.code,
            ) from error
        except (TimeoutError, socket.timeout) as error:
            raise TimeoutError("Home Assistant request timed out") from error
        except URLError as error:
            raise HomeAssistantRequestError(f"Home Assistant connection failed: {error.reason}") from error
        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise HomeAssistantRequestError("Home Assistant returned invalid JSON") from error

    def _refresh_token(self, stale_token: str) -> None:
        with self._token_lock:
            if self._token != stale_token:
                return
            refreshed = self._token_refresher() if self._token_refresher else ""
            if not isinstance(refreshed, str) or not refreshed.strip():
                raise HomeAssistantRequestError("Home Assistant token refresh returned an empty token", status_code=401)
            self._token = refreshed.strip()


class HomeAssistantRuntime:
    """Execute a ServicePlan action through HA and read the entity back."""

    def __init__(
        self,
        client: HomeAssistantClient,
        verification_timeout_seconds: float = 5,
        poll_interval_seconds: float = 0.2,
    ) -> None:
        self.client = client
        self._verification_timeout_seconds = verification_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def read(self, entity_id: str) -> DeviceState:
        return _device_state(self.client.state(entity_id))

    def execute(self, action: PlanAction) -> ActionResult:
        try:
            before = self.read(action.entity_id)
        except TimeoutError:
            return _failure(action, ExecutionStatus.TIMEOUT, "state read timed out")
        except HomeAssistantRequestError as error:
            return _failure(action, ExecutionStatus.DEVICE_UNAVAILABLE, str(error))
        if before.state in {"unavailable", "unknown"}:
            return _failure(
                action,
                ExecutionStatus.DEVICE_UNAVAILABLE,
                f"entity state is {before.state}",
                before,
            )
        feedback_before = self._read_protocol_feedback(action.entity_id)

        service = _service_call(action)
        if service is None:
            return _failure(action, ExecutionStatus.EXECUTION_FAILED, "unsupported capability", before)
        domain, service_name, data = service
        try:
            self.client.call_service(domain, service_name, data)
        except TimeoutError:
            return _failure(action, ExecutionStatus.TIMEOUT, "service call timed out", before)
        except HomeAssistantRequestError as error:
            return _failure(action, ExecutionStatus.EXECUTION_FAILED, str(error), before)

        deadline = time.monotonic() + self._verification_timeout_seconds
        after = before
        terminal_feedback: dict[str, str] | None = None
        while time.monotonic() < deadline:
            try:
                after = self.read(action.entity_id)
            except (HomeAssistantRequestError, TimeoutError):
                time.sleep(self._poll_interval_seconds)
                continue
            if _matches(after, action):
                break
            feedback_after = self._read_protocol_feedback(action.entity_id)
            if _is_new_terminal_feedback(feedback_before, feedback_after):
                terminal_feedback = feedback_after
                break
            time.sleep(self._poll_interval_seconds)
        if not _matches(after, action):
            feedback_after = terminal_feedback or self._read_protocol_feedback(action.entity_id)
            if _is_new_terminal_feedback(feedback_before, feedback_after):
                return _feedback_failure(action, before, after, feedback_after)
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.TIMEOUT,
                success=False,
                before=before,
                after=after,
                message=f"state did not reach target within {self._verification_timeout_seconds:g} seconds",
            )
        return ActionResult(
            entity_id=action.entity_id,
            capability=action.capability,
            expected_value=action.value,
            status=ExecutionStatus.SUCCESS,
            success=True,
            before=before,
            after=after,
            message="Home Assistant accepted the service call and state was read back",
        )

    def _read_protocol_feedback(self, entity_id: str) -> dict[str, str] | None:
        feedback_entity = _feedback_entity_id(entity_id)
        try:
            payload = self.client.state(feedback_entity)
        except (HomeAssistantRequestError, TimeoutError):
            return None
        attributes = payload.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        status = str(payload.get("state", "")).strip().lower()
        if not status or status in {"unknown", "unavailable"}:
            return None
        return {
            "status": status,
            "reason": str(attributes.get("reason", "")).strip(),
            "request_id": str(attributes.get("request_id", "")).strip(),
            "updated_at": str(attributes.get("updated_at", "")).strip(),
        }


@dataclass(frozen=True)
class EnergyEntityMap:
    climate: str = "climate.spacebutler_living_room_ac"
    presence: str = "input_boolean.spacebutler_living_room_presence"
    window: str = "input_boolean.spacebutler_living_room_window_open"
    ac_rated_power_w: float = 1_050
    room: str = "living_room"


class HomeAssistantSpaceAdapter:
    """Build the energy-loop spatial snapshot from independently read HA entities."""

    def __init__(self, client: HomeAssistantClient, entities: EnergyEntityMap | None = None) -> None:
        self._client = client
        self._entities = entities or EnergyEntityMap()

    def capture_energy_snapshot(
        self,
        unoccupied_minutes: int,
        *,
        indoor_temperature: float = 27,
        outdoor_temperature: float = 34,
        humidity: float = 62,
        electricity_price_level: str = "normal",
    ) -> SpatialSnapshot:
        climate = _device_state(self._client.state(self._entities.climate))
        presence = self._client.state(self._entities.presence)
        window = self._client.state(self._entities.window)
        occupied = _is_on(presence.get("state"))
        window_open = _is_on(window.get("state"))
        power_w = self._entities.ac_rated_power_w if climate.state != "off" else 0.0
        climate_attributes = dict(climate.attributes)
        climate_attributes["power_w"] = power_w
        climate = DeviceState(
            climate.entity_id,
            climate.domain,
            self._entities.room,
            climate.state,
            climate_attributes,
        )
        window_device = DeviceState(
            self._entities.window,
            "window",
            self._entities.room,
            "open" if window_open else "closed",
            dict(window.get("attributes") or {}),
        )
        return SpatialSnapshot(
            scene="daily",
            time_of_day="afternoon",
            members=(),
            environment=EnvironmentState(
                indoor_temperature,
                outdoor_temperature,
                humidity,
                500,
                18,
                electricity_price_level,
            ),
            devices=(climate, window_device),
            rooms=(
                RoomState(
                    self._entities.room,
                    occupied=occupied,
                    unoccupied_minutes=0 if occupied else unoccupied_minutes,
                    temperature=indoor_temperature,
                    humidity=humidity,
                    illuminance=500,
                    window_state="open" if window_open else "closed",
                    power_w=power_w,
                    source="home_assistant_rest",
                ),
            ),
            snapshot_id=f"ha-energy-{int(time.time() * 1000)}",
            source="home_assistant_rest",
        )


def _device_state(payload: dict[str, Any]) -> DeviceState:
    entity_id = str(payload.get("entity_id", ""))
    domain = entity_id.partition(".")[0]
    room = "living_room" if "living_room" in entity_id else "home"
    raw_attributes = payload.get("attributes")
    attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
    brightness = attributes.get("brightness")
    if domain == "light" and isinstance(brightness, (int, float)):
        attributes["brightness_pct"] = round(brightness * 100 / 255)
    return DeviceState(
        entity_id=entity_id,
        domain=domain,
        room=room,
        state=str(payload.get("state", "unknown")),
        attributes=attributes,
    )


def _service_call(action: PlanAction) -> tuple[str, str, dict[str, object]] | None:
    domain = action.entity_id.partition(".")[0]
    if action.capability in {"turn_on", "turn_off"}:
        return domain, action.capability, {"entity_id": action.entity_id}
    if action.capability == "set_temperature":
        return "climate", "set_temperature", {"entity_id": action.entity_id, "temperature": action.value}
    if action.capability == "set_brightness":
        return "light", "turn_on", {"entity_id": action.entity_id, "brightness_pct": action.value}
    return None


def _matches(device: DeviceState, action: PlanAction) -> bool:
    if action.capability == "turn_off":
        return device.state == "off"
    if action.capability == "turn_on":
        return device.state == "on"
    if action.capability == "set_temperature":
        return device.attributes.get("temperature") == action.value
    if action.capability == "set_brightness":
        return device.state == "on" and device.attributes.get("brightness_pct") == action.value
    return False


def _feedback_entity_id(entity_id: str) -> str:
    object_id = entity_id.partition(".")[2]
    return f"sensor.{object_id}_feedback"


def _is_new_terminal_feedback(before: dict[str, str] | None, after: dict[str, str] | None) -> bool:
    if after is None:
        return False
    if after["status"] not in {"ack_without_state_change", "invalid_state", "offline", "rejected", "stuck"}:
        return False
    if before is None:
        return True
    keys = ("status", "reason", "request_id", "updated_at")
    return any(before.get(key) != after.get(key) for key in keys)


def _feedback_failure(
    action: PlanAction,
    before: DeviceState,
    after: DeviceState,
    feedback: dict[str, str],
) -> ActionResult:
    status = feedback["status"]
    reason = feedback["reason"] or status
    if status == "ack_without_state_change":
        return ActionResult(
            entity_id=action.entity_id,
            capability=action.capability,
            expected_value=action.value,
            status=ExecutionStatus.VALIDATION_FAILED,
            success=True,
            before=before,
            after=after,
            message=f"protocol feedback: {reason}",
        )
    if status in {"offline"}:
        result_status = ExecutionStatus.DEVICE_UNAVAILABLE
    elif status in {"invalid_state"}:
        result_status = ExecutionStatus.VALIDATION_FAILED
    else:
        result_status = ExecutionStatus.EXECUTION_FAILED
    return ActionResult(
        entity_id=action.entity_id,
        capability=action.capability,
        expected_value=action.value,
        status=result_status,
        success=False,
        before=before,
        after=after,
        message=f"protocol feedback: {reason}",
    )


def _failure(
    action: PlanAction,
    status: ExecutionStatus,
    message: str,
    before: DeviceState | None = None,
) -> ActionResult:
    return ActionResult(
        entity_id=action.entity_id,
        capability=action.capability,
        expected_value=action.value,
        status=status,
        success=False,
        before=before,
        after=before,
        message=message,
    )


def _is_on(value: object) -> bool:
    return str(value).lower() in {"on", "open", "home", "occupied", "true"}
