"""Run one MQTT-native simulated light with independent state and a test-only admin API."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import random
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

import yaml
from paho.mqtt import client as mqtt

from .store import DeviceStateStore


_LOGGER = logging.getLogger(__name__)
_HA_BIRTH_TOPIC = "homeassistant/status"
_FAULT_DELAY_MS = 5_000


@dataclass(frozen=True, slots=True)
class LightDevice:
    """A validated light definition loaded from the simulator-owned configuration."""

    device_id: str
    name: str
    room: str
    initial_state: dict[str, Any]
    command_delay_ms: int
    failure_rate: float

    @property
    def command_topic(self) -> str:
        return f"edgehome/devices/{self.device_id}/command"

    @property
    def state_topic(self) -> str:
        return f"edgehome/devices/{self.device_id}/state"

    @property
    def availability_topic(self) -> str:
        return f"edgehome/devices/{self.device_id}/availability"

    @property
    def discovery_topic(self) -> str:
        return "homeassistant/light/edgehome_living_room_main/config"


class DeviceSimulator:
    """Bridge MQTT commands to one device-owned SQLite state machine."""

    def __init__(self, devices: dict[str, LightDevice], store: DeviceStateStore, mqtt_host: str, mqtt_port: int) -> None:
        if not devices:
            raise ValueError("at least one simulated device is required")
        self._devices = devices
        self._store = store
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="edgehome-device-simulator")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        first_device = next(iter(devices.values()))
        self._client.will_set(first_device.availability_topic, "offline", qos=1, retain=True)
        self._server: ThreadingHTTPServer | None = None

        for device in devices.values():
            self._store.ensure_device(device.device_id, device.initial_state)

    def run(self, admin_host: str, admin_port: int) -> None:
        """Start test-only HTTP management and the MQTT client loop."""
        self._server = ThreadingHTTPServer((admin_host, admin_port), _admin_handler(self))
        threading.Thread(target=self._server.serve_forever, name="simulator-admin", daemon=True).start()
        _LOGGER.info("Admin API listening on %s:%s", admin_host, admin_port)
        self._client.connect_async(self._mqtt_host, self._mqtt_port, keepalive=30)
        self._client.loop_forever(retry_first_connection=True)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._client.disconnect()

    def device_records(self) -> list[dict[str, Any]]:
        return [self._require_record(device_id) for device_id in self._devices]

    def device_record(self, device_id: str) -> dict[str, Any]:
        self._require_device(device_id)
        return self._require_record(device_id)

    def event_records(self, limit: int) -> list[dict[str, Any]]:
        return self._store.events(limit)

    def set_fault(self, device_id: str, mode: str, delay_ms: int) -> dict[str, Any]:
        device = self._require_device(device_id)
        record = self._store.set_fault(device_id, mode, delay_ms)
        self._publish_availability(device, record["online"])
        if record["online"]:
            self._publish_state(device, record["state"], record["last_command_id"])
        return record

    def reset_device(self, device_id: str) -> dict[str, Any]:
        device = self._require_device(device_id)
        record = self._store.reset_device(device_id, device.initial_state)
        self._publish_availability(device, True)
        self._publish_state(device, record["state"], None)
        return record

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code.is_failure:
            _LOGGER.error("MQTT connection failed: %s", reason_code)
            return
        _LOGGER.info("Connected to MQTT broker at %s:%s", self._mqtt_host, self._mqtt_port)
        self._client.subscribe(_HA_BIRTH_TOPIC, qos=1)
        for device in self._devices.values():
            self._client.subscribe(device.command_topic, qos=1)
        self._publish_discovery_and_states()

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if message.topic == _HA_BIRTH_TOPIC:
            if message.payload.decode("utf-8", errors="replace").strip().lower() == "online":
                self._publish_discovery_and_states()
            return
        device = next((candidate for candidate in self._devices.values() if candidate.command_topic == message.topic), None)
        if device is None:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("command payload must be a JSON object")
            self._handle_light_command(device, payload)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            self._store.append_event(device.device_id, "command_rejected", {"error": str(error)})
            _LOGGER.warning("Rejected command for %s: %s", device.device_id, error)

    def _handle_light_command(self, device: LightDevice, payload: dict[str, Any]) -> None:
        record = self._require_record(device.device_id)
        command_id = _command_id(payload)
        self._store.append_event(device.device_id, "command_received", {"command_id": command_id, "payload": payload})
        if not record["online"]:
            self._store.append_event(device.device_id, "command_ignored_offline", {"command_id": command_id})
            return

        failure_mode = str(record["fault_mode"])
        delay_ms = _command_delay(device, record)
        if delay_ms:
            time.sleep(delay_ms / 1000)
        if failure_mode in {"reject", "stuck"}:
            self._store.append_event(
                device.device_id,
                "command_rejected",
                {"command_id": command_id, "fault_mode": failure_mode},
            )
            return
        if failure_mode == "random_failure" and random.random() < device.failure_rate:
            self._store.append_event(device.device_id, "command_rejected", {"command_id": command_id, "fault_mode": failure_mode})
            return
        if failure_mode == "ack_without_state_change":
            self._store.append_event(
                device.device_id,
                "ack_without_state_change",
                {"command_id": command_id, "state": record["state"]},
            )
            self._publish_state(device, record["state"], command_id)
            return
        if failure_mode == "invalid_state":
            self._store.append_event(device.device_id, "invalid_state_published", {"command_id": command_id})
            self._publish_payload(device.state_topic, {"state": "BROKEN", "request_id": command_id})
            return

        new_state = _light_state(record["state"], payload)
        changed = self._store.update_state(device.device_id, new_state, command_id)
        self._publish_state(device, changed["state"], command_id)

    def _publish_discovery_and_states(self) -> None:
        for device in self._devices.values():
            record = self._require_record(device.device_id)
            self._publish_payload(device.discovery_topic, _light_discovery(device), retain=True)
            self._publish_availability(device, record["online"])
            if record["online"]:
                self._publish_state(device, record["state"], record["last_command_id"])

    def _publish_availability(self, device: LightDevice, online: bool) -> None:
        self._client.publish(device.availability_topic, "online" if online else "offline", qos=1, retain=True)

    def _publish_state(self, device: LightDevice, state: dict[str, Any], request_id: str | None) -> None:
        payload = {"state": state["power"], "brightness": state["brightness"], "updated_at": _timestamp()}
        if request_id is not None:
            payload["request_id"] = request_id
        self._publish_payload(device.state_topic, payload, retain=True)

    def _publish_payload(self, topic: str, payload: dict[str, Any], retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), qos=1, retain=retain)

    def _require_device(self, device_id: str) -> LightDevice:
        if device := self._devices.get(device_id):
            return device
        raise KeyError(f"unknown device: {device_id}")

    def _require_record(self, device_id: str) -> dict[str, Any]:
        if record := self._store.get_device(device_id):
            return record
        raise KeyError(f"device state is missing: {device_id}")


def _light_discovery(device: LightDevice) -> dict[str, Any]:
    return {
        "name": device.name,
        "unique_id": "edgehome_mqtt_living_room_main",
        "default_entity_id": "light.edgehome_living_room_main",
        "schema": "json",
        "command_topic": device.command_topic,
        "state_topic": device.state_topic,
        "availability_topic": device.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "brightness": True,
        "brightness_scale": 255,
        "device": {
            "identifiers": ["edgehome_device_simulator_living_room"],
            "name": "EdgeHome 客厅设备组",
            "manufacturer": "EdgeHome Simulator",
            "model": "Docker MQTT Device Simulator",
        },
    }


def _light_state(previous: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    state = {"power": str(previous.get("power", "OFF")).upper(), "brightness": int(previous.get("brightness", 0))}
    if "state" in payload:
        requested_power = str(payload["state"]).upper()
        if requested_power not in {"ON", "OFF"}:
            raise ValueError("state must be ON or OFF")
        state["power"] = requested_power
    if "brightness" in payload:
        brightness = int(payload["brightness"])
        if not 0 <= brightness <= 255:
            raise ValueError("brightness must be between 0 and 255")
        state["brightness"] = brightness
        if brightness > 0 and "state" not in payload:
            state["power"] = "ON"
    return state


def _command_delay(device: LightDevice, record: dict[str, Any]) -> int:
    if record["fault_mode"] == "delay":
        return record["fault_delay_ms"] or _FAULT_DELAY_MS
    return device.command_delay_ms


def _command_id(payload: dict[str, Any]) -> str:
    requested = payload.get("request_id")
    return requested if isinstance(requested, str) and requested.strip() else f"req-{uuid4().hex}"


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_devices(path: Path) -> dict[str, LightDevice]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("devices"), dict):
        raise ValueError("devices.yaml must contain a devices mapping")
    devices: dict[str, LightDevice] = {}
    for device_id, raw in loaded["devices"].items():
        if not isinstance(device_id, str) or not isinstance(raw, dict) or raw.get("type") != "light":
            raise ValueError("the first simulator version supports only light devices")
        initial_state = raw.get("initial_state")
        behavior = raw.get("behavior", {})
        if not isinstance(initial_state, dict) or not isinstance(behavior, dict):
            raise ValueError(f"invalid definition for {device_id}")
        initial_command = dict(initial_state)
        if "power" in initial_command and "state" not in initial_command:
            initial_command["state"] = initial_command["power"]
        state = _light_state({"power": "OFF", "brightness": 0}, initial_command)
        devices[device_id] = LightDevice(
            device_id=device_id,
            name=str(raw.get("name", device_id)),
            room=str(raw.get("room", "home")),
            initial_state=state,
            command_delay_ms=int(behavior.get("command_delay_ms", 0)),
            failure_rate=float(behavior.get("failure_rate", 0)),
        )
    return devices


def _admin_handler(simulator: DeviceSimulator) -> type[BaseHTTPRequestHandler]:
    class AdminHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._write_json(HTTPStatus.OK, {"status": "ok"})
                return
            if parsed.path == "/devices":
                self._write_json(HTTPStatus.OK, {"devices": simulator.device_records()})
                return
            if parsed.path == "/events":
                limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
                self._write_json(HTTPStatus.OK, {"events": simulator.event_records(limit)})
                return
            prefix = "/devices/"
            if parsed.path.startswith(prefix):
                self._respond_device(unquote(parsed.path.removeprefix(prefix)))
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            prefix = "/admin/devices/"
            if not parsed.path.startswith(prefix):
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            suffix = unquote(parsed.path.removeprefix(prefix))
            device_id, separator, action = suffix.rpartition("/")
            if not separator:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                if action == "fault":
                    payload = self._read_json()
                    record = simulator.set_fault(device_id, str(payload.get("mode", "none")), int(payload.get("delay_ms", 0)))
                elif action == "reset":
                    record = simulator.reset_device(device_id)
                else:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
            except (KeyError, ValueError) as error:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._write_json(HTTPStatus.OK, record)

        def _respond_device(self, device_id: str) -> None:
            try:
                self._write_json(HTTPStatus.OK, simulator.device_record(device_id))
            except KeyError as error:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": str(error)})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            _LOGGER.info("admin %s - %s", self.address_string(), format % args)

    return AdminHandler


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    config_path = Path(os.environ.get("DEVICE_CONFIG", "/app/config/devices.yaml"))
    database_path = Path(os.environ.get("STATE_DB", "/app/data/device_state.db"))
    simulator = DeviceSimulator(
        _load_devices(config_path),
        DeviceStateStore(database_path),
        os.environ.get("MQTT_HOST", "mqtt"),
        int(os.environ.get("MQTT_PORT", "1883")),
    )
    try:
        simulator.run(os.environ.get("ADMIN_HOST", "0.0.0.0"), int(os.environ.get("ADMIN_PORT", "8090")))
    finally:
        simulator.stop()


if __name__ == "__main__":
    main()
