"""Home Assistant REST execution and spatial snapshot adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import time
from typing import Any
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
    def __init__(self, base_url: str, token: str, request_timeout_seconds: float = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._request_timeout_seconds = request_timeout_seconds

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
        while time.monotonic() < deadline:
            try:
                after = self.read(action.entity_id)
            except (HomeAssistantRequestError, TimeoutError):
                time.sleep(self._poll_interval_seconds)
                continue
            if _matches(after, action):
                break
            time.sleep(self._poll_interval_seconds)
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


@dataclass(frozen=True)
class EnergyEntityMap:
    climate: str = "climate.spacebutler_living_room_ac"
    presence: str = "input_boolean.spacebutler_living_room_presence"
    window: str = "input_boolean.spacebutler_living_room_window_open"
    ac_rated_power_w: float = 1_050


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
            "living_room",
            climate.state,
            climate_attributes,
        )
        window_device = DeviceState(
            self._entities.window,
            "window",
            "living_room",
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
                    "living_room",
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
    attributes = payload.get("attributes")
    return DeviceState(
        entity_id=entity_id,
        domain=domain,
        room=room,
        state=str(payload.get("state", "unknown")),
        attributes=dict(attributes) if isinstance(attributes, dict) else {},
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
        brightness = device.attributes.get("brightness")
        return device.state == "on" and isinstance(brightness, (int, float)) and round(brightness * 100 / 255) == action.value
    return False


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
