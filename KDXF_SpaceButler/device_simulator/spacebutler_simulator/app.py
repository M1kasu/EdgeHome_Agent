"""Run MQTT-native simulated devices with independent SQLite-owned state."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import random
import re
import signal
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
_DEVICE_TYPES = frozenset({"light", "curtain", "climate", "switch"})
_LEGACY_OBJECT_IDS = {
    "living_room_main_light": "living_room_main",
    "living_room_curtain": "living_room_curtain",
    "living_room_ac": "living_room_ac",
    "living_room_tv": "living_room_tv",
}


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
        return f"spacebutler/devices/{self.device_id}/command"

    @property
    def state_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/state"

    @property
    def availability_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/availability"

    @property
    def feedback_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/feedback"

    @property
    def discovery_topic(self) -> str:
        return f"homeassistant/light/spacebutler_{_entity_object_id(self)}/config"

    @property
    def feedback_discovery_topic(self) -> str:
        return f"homeassistant/sensor/spacebutler_{_entity_object_id(self)}_feedback/config"


@dataclass(frozen=True, slots=True)
class CurtainDevice:
    """A cover that reports movement and final position through MQTT topics."""

    device_id: str
    name: str
    room: str
    initial_state: dict[str, Any]
    command_delay_ms: int
    failure_rate: float
    move_duration_ms: int

    @property
    def command_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/command"

    @property
    def set_position_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/set_position"

    @property
    def state_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/state"

    @property
    def availability_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/availability"

    @property
    def feedback_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/feedback"

    @property
    def discovery_topic(self) -> str:
        return f"homeassistant/cover/spacebutler_{_entity_object_id(self)}/config"

    @property
    def feedback_discovery_topic(self) -> str:
        return f"homeassistant/sensor/spacebutler_{_entity_object_id(self)}_feedback/config"


@dataclass(frozen=True, slots=True)
class ClimateDevice:
    """A MQTT Climate device with simulator-owned mode and temperature state."""

    device_id: str
    name: str
    room: str
    initial_state: dict[str, Any]
    command_delay_ms: int
    failure_rate: float

    @property
    def command_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/mode_command"

    @property
    def temperature_command_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/temperature_command"

    @property
    def state_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/state"

    @property
    def availability_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/availability"

    @property
    def feedback_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/feedback"

    @property
    def discovery_topic(self) -> str:
        return f"homeassistant/climate/spacebutler_{_entity_object_id(self)}/config"

    @property
    def feedback_discovery_topic(self) -> str:
        return f"homeassistant/sensor/spacebutler_{_entity_object_id(self)}_feedback/config"


@dataclass(frozen=True, slots=True)
class SwitchDevice:
    """A switchable MQTT appliance with device-owned instantaneous power."""

    device_id: str
    name: str
    room: str
    initial_state: dict[str, Any]
    command_delay_ms: int
    failure_rate: float

    @property
    def command_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/command"

    @property
    def state_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/state"

    @property
    def availability_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/availability"

    @property
    def feedback_topic(self) -> str:
        return f"spacebutler/devices/{self.device_id}/feedback"

    @property
    def discovery_topic(self) -> str:
        return f"homeassistant/switch/spacebutler_{_entity_object_id(self)}/config"

    @property
    def feedback_discovery_topic(self) -> str:
        return f"homeassistant/sensor/spacebutler_{_entity_object_id(self)}_feedback/config"

    @property
    def power_discovery_topic(self) -> str:
        return f"homeassistant/sensor/spacebutler_{_entity_object_id(self)}_power/config"


SimulatedDevice = LightDevice | CurtainDevice | ClimateDevice | SwitchDevice


class DeviceSimulator:
    """Bridge MQTT commands to one device-owned SQLite state machine."""

    def __init__(self, devices: dict[str, SimulatedDevice], store: DeviceStateStore, mqtt_host: str, mqtt_port: int) -> None:
        if not devices:
            raise ValueError("at least one simulated device is required")
        self._devices = devices
        self._store = store
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="spacebutler-device-simulator")
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        first_device = next(iter(devices.values()))
        self._client.will_set(first_device.availability_topic, "offline", qos=1, retain=True)
        self._server: ThreadingHTTPServer | None = None
        self._availability_announced_offline = False
        self._shutdown_requested = False
        self._connected = False
        self._lock = threading.RLock()

        for device in devices.values():
            self._store.ensure_device(device.device_id, device.initial_state)

    def run(self, admin_host: str, admin_port: int) -> None:
        """Start test-only HTTP management and the MQTT client loop."""
        self._server = ThreadingHTTPServer((admin_host, admin_port), _admin_handler(self))
        threading.Thread(target=self._server.serve_forever, name="simulator-admin", daemon=True).start()
        _LOGGER.info("Admin API listening on %s:%s", admin_host, admin_port)
        self._client.connect_async(self._mqtt_host, self._mqtt_port, keepalive=30)
        # Docker Compose sends SIGTERM on `stop`. MQTT only supports one Last
        # Will topic, so explicitly publish every device offline before exit.
        # 这样 HA 不会把仍保留的在线状态误判为可控设备。
        self._install_shutdown_handlers()
        try:
            self._client.loop_forever(retry_first_connection=True)
        finally:
            self._announce_all_offline()

    def stop(self) -> None:
        self._announce_all_offline()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._client.disconnect()

    def _install_shutdown_handlers(self) -> None:
        """Publish all availability topics before Docker terminates the process."""
        def handle_shutdown(_signum: int, _frame: Any) -> None:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
            self._announce_all_offline()
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
            # The handler runs inside Paho's network-loop thread.  Give that
            # loop one short turn to flush retained offline messages before a
            # different thread disconnects the client.
            threading.Timer(0.25, self._client.disconnect).start()

        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

    def _announce_all_offline(self) -> None:
        if self._availability_announced_offline:
            return
        self._availability_announced_offline = True
        with self._lock:
            devices = tuple(self._devices.values())
        for device in devices:
            self._client.publish(device.availability_topic, "offline", qos=1, retain=True)
        _LOGGER.info("Published retained offline availability for %d simulated devices", len(devices))

    def device_records(self) -> list[dict[str, Any]]:
        definitions = {item["device_id"]: item for item in self._store.definitions()}
        with self._lock:
            devices = tuple(sorted(self._devices.values(), key=lambda item: (item.room, item.name, item.device_id)))
        return [
            _device_record(
                device,
                self._require_record(device.device_id),
                definitions.get(device.device_id, {}).get("source", "configured"),
            )
            for device in devices
        ]

    def device_record(self, device_id: str) -> dict[str, Any]:
        device = self._require_device(device_id)
        definitions = {item["device_id"]: item for item in self._store.definitions()}
        return _device_record(
            device,
            self._require_record(device_id),
            definitions.get(device_id, {}).get("source", "configured"),
        )

    def event_records(self, limit: int) -> list[dict[str, Any]]:
        return self._store.events(limit)

    def set_fault(self, device_id: str, mode: str, delay_ms: int) -> dict[str, Any]:
        device = self._require_device(device_id)
        record = self._store.set_fault(device_id, mode, delay_ms)
        self._publish_availability(device, record["online"])
        if record["online"]:
            self._publish_state(device, record["state"], record["last_command_id"])
        self._publish_feedback(
            device,
            "offline" if not record["online"] else "fault_injected" if mode != "none" else "ok",
            reason=f"fault_mode={mode}" if mode != "none" else None,
            request_id=record["last_command_id"],
        )
        return record

    def reset_device(self, device_id: str) -> dict[str, Any]:
        device = self._require_device(device_id)
        record = self._store.reset_device(device_id, device.initial_state)
        self._publish_availability(device, True)
        self._publish_state(device, record["state"], None)
        self._publish_feedback(device, "ok", request_id=None)
        return self.device_record(device_id)

    def add_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        device_id, definition = _normalize_device_definition(payload)
        device = _device_from_definition(device_id, definition)
        with self._lock:
            if device_id in self._devices:
                raise ValueError(f"device already exists: {device_id}")
            self._store.save_definition(device_id, definition, source="runtime")
            self._store.ensure_device(device_id, device.initial_state)
            self._store.append_event(
                device_id,
                "device_registered",
                {"definition": definition, "entity_id": _entity_id(device)},
            )
            self._devices[device_id] = device
            if self._connected:
                self._subscribe_device(device)
                self._publish_device(device)
        return self.device_record(device_id)

    def remove_device(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            device = self._require_device(device_id)
            removed = self._store.delete_device(device_id)
            if self._connected:
                self._unpublish_device(device)
                self._unsubscribe_device(device)
            del self._devices[device_id]
        return removed

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
        self._availability_announced_offline = False
        self._connected = True
        self._client.subscribe(_HA_BIRTH_TOPIC, qos=1)
        with self._lock:
            devices = tuple(self._devices.values())
        for device in devices:
            self._subscribe_device(device)
        self._publish_discovery_and_states()

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        self._connected = False
        if not self._shutdown_requested:
            _LOGGER.warning("Disconnected from MQTT broker: %s", reason_code)

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if message.topic == _HA_BIRTH_TOPIC:
            if message.payload.decode("utf-8", errors="replace").strip().lower() == "online":
                self._publish_discovery_and_states()
            return
        with self._lock:
            devices = tuple(self._devices.values())
        device = next(
            (
                candidate
                for candidate in devices
                if candidate.command_topic == message.topic
                or isinstance(candidate, CurtainDevice) and candidate.set_position_topic == message.topic
                or isinstance(candidate, ClimateDevice) and candidate.temperature_command_topic == message.topic
            ),
            None,
        )
        if device is None:
            return
        try:
            raw = message.payload.decode("utf-8").strip()
            if isinstance(device, LightDevice):
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("command payload must be a JSON object")
                self._handle_light_command(device, payload)
            elif isinstance(device, CurtainDevice):
                self._handle_curtain_command(device, raw, message.topic)
            elif isinstance(device, ClimateDevice):
                self._handle_climate_command(device, raw, message.topic)
            else:
                self._handle_switch_command(device, raw)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            self._store.append_event(device.device_id, "command_rejected", {"error": str(error)})
            self._publish_feedback(device, "rejected", reason=f"invalid_command: {error}", request_id=None)
            _LOGGER.warning("Rejected command for %s: %s", device.device_id, error)

    def _handle_light_command(self, device: LightDevice, payload: dict[str, Any]) -> None:
        record = self._require_record(device.device_id)
        command_id = _command_id(payload)
        self._store.append_event(device.device_id, "command_received", {"command_id": command_id, "payload": payload})
        if not record["online"]:
            self._store.append_event(device.device_id, "command_ignored_offline", {"command_id": command_id})
            self._publish_feedback(device, "offline", reason="device_offline", request_id=command_id)
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
            self._publish_feedback(device, "rejected", reason=f"fault_mode={failure_mode}", request_id=command_id)
            return
        if failure_mode == "random_failure" and random.random() < device.failure_rate:
            self._store.append_event(device.device_id, "command_rejected", {"command_id": command_id, "fault_mode": failure_mode})
            self._publish_feedback(device, "rejected", reason="random_failure", request_id=command_id)
            return
        if failure_mode == "ack_without_state_change":
            self._store.append_event(
                device.device_id,
                "ack_without_state_change",
                {"command_id": command_id, "state": record["state"]},
            )
            self._publish_state(device, record["state"], command_id)
            self._publish_feedback(device, "ack_without_state_change", reason="fault_mode=ack_without_state_change", request_id=command_id)
            return
        if failure_mode == "invalid_state":
            self._store.append_event(device.device_id, "invalid_state_published", {"command_id": command_id})
            self._publish_payload(device.state_topic, {"state": "BROKEN", "request_id": command_id})
            self._publish_feedback(device, "invalid_state", reason="fault_mode=invalid_state", request_id=command_id)
            return

        new_state = _light_state(record["state"], payload)
        changed = self._store.update_state(device.device_id, new_state, command_id)
        self._publish_state(device, changed["state"], command_id)
        self._publish_feedback(device, "ok", request_id=command_id)

    def _handle_curtain_command(self, device: CurtainDevice, raw: str, topic: str) -> None:
        record = self._require_record(device.device_id)
        command_id = f"req-{uuid4().hex}"
        target = _curtain_target(raw, topic == device.set_position_topic)
        self._store.append_event(device.device_id, "command_received", {"command_id": command_id, "raw": raw, "target_position": target})
        if not record["online"]:
            self._store.append_event(device.device_id, "command_ignored_offline", {"command_id": command_id})
            self._publish_feedback(device, "offline", reason="device_offline", request_id=command_id)
            return
        mode = str(record["fault_mode"])
        delay_ms = _command_delay(device, record)
        if delay_ms:
            time.sleep(delay_ms / 1000)
        if mode in {"reject", "random_failure"}:
            self._store.append_event(device.device_id, "command_rejected", {"command_id": command_id, "fault_mode": mode})
            self._publish_feedback(device, "rejected", reason=f"fault_mode={mode}", request_id=command_id)
            return
        if mode == "ack_without_state_change":
            self._store.append_event(device.device_id, "ack_without_state_change", {"command_id": command_id, "state": record["state"]})
            self._publish_state(device, record["state"], command_id)
            self._publish_feedback(device, "ack_without_state_change", reason="fault_mode=ack_without_state_change", request_id=command_id)
            return
        if mode == "invalid_state":
            self._store.append_event(device.device_id, "invalid_state_published", {"command_id": command_id})
            self._publish_payload(device.state_topic, {"status": "broken", "position": "invalid", "request_id": command_id}, retain=True)
            self._publish_feedback(device, "invalid_state", reason="fault_mode=invalid_state", request_id=command_id)
            return

        previous = _curtain_state(record["state"])
        moving = {"position": previous["position"], "target_position": target, "status": "moving"}
        changed = self._store.update_state(device.device_id, moving, command_id)
        self._publish_state(device, changed["state"], command_id)
        if mode == "stuck":
            position = round((previous["position"] + target) / 2)
            stuck = {"position": position, "target_position": target, "status": "stuck"}
            changed = self._store.update_state(device.device_id, stuck, command_id)
            self._publish_state(device, changed["state"], command_id)
            self._publish_feedback(device, "stuck", reason="fault_mode=stuck", request_id=command_id)
            return
        time.sleep(device.move_duration_ms / 1000)
        final = {"position": target, "target_position": target, "status": "open" if target > 0 else "closed"}
        changed = self._store.update_state(device.device_id, final, command_id)
        self._publish_state(device, changed["state"], command_id)
        self._publish_feedback(device, "ok", request_id=command_id)

    def _handle_climate_command(self, device: ClimateDevice, raw: str, topic: str) -> None:
        record = self._require_record(device.device_id)
        command_id = f"req-{uuid4().hex}"
        self._store.append_event(device.device_id, "command_received", {"command_id": command_id, "raw": raw, "topic": topic})
        if not record["online"]:
            self._publish_feedback(device, "offline", reason="device_offline", request_id=command_id)
            return
        mode = str(record["fault_mode"])
        delay_ms = _command_delay(device, record)
        if delay_ms:
            time.sleep(delay_ms / 1000)
        if mode in {"reject", "random_failure"}:
            self._publish_feedback(device, "rejected", reason=f"fault_mode={mode}", request_id=command_id)
            return
        if mode == "ack_without_state_change":
            self._publish_state(device, record["state"], command_id)
            self._publish_feedback(device, "ack_without_state_change", reason="fault_mode=ack_without_state_change", request_id=command_id)
            return
        if mode == "invalid_state":
            self._publish_payload(device.state_topic, {"mode": "BROKEN", "temperature": "invalid"}, retain=True)
            self._publish_feedback(device, "invalid_state", reason="fault_mode=invalid_state", request_id=command_id)
            return
        state = _climate_state(record["state"])
        if topic == device.command_topic:
            normalized = raw.lower()
            if normalized not in {"off", "cool", "heat"}:
                raise ValueError("climate mode must be off, cool, or heat")
            state["mode"] = normalized
        else:
            temperature = float(raw)
            if not 16 <= temperature <= 30:
                raise ValueError("temperature must be between 16 and 30")
            state["temperature"] = temperature
        # The current temperature changes gradually enough to show a distinct
        # device report, while the requested target remains exact.
        state["current_temperature"] = round((state["current_temperature"] + state["temperature"]) / 2, 1)
        changed = self._store.update_state(device.device_id, state, command_id)
        self._publish_state(device, changed["state"], command_id)
        self._publish_feedback(device, "ok", request_id=command_id)

    def _handle_switch_command(self, device: SwitchDevice, raw: str) -> None:
        record = self._require_record(device.device_id)
        command_id = f"req-{uuid4().hex}"
        self._store.append_event(device.device_id, "command_received", {"command_id": command_id, "raw": raw})
        if not record["online"]:
            self._publish_feedback(device, "offline", reason="device_offline", request_id=command_id)
            return
        mode = str(record["fault_mode"])
        delay_ms = _command_delay(device, record)
        if delay_ms:
            time.sleep(delay_ms / 1000)
        if mode in {"reject", "random_failure"}:
            self._publish_feedback(device, "rejected", reason=f"fault_mode={mode}", request_id=command_id)
            return
        if mode == "ack_without_state_change":
            self._publish_state(device, record["state"], command_id)
            self._publish_feedback(device, "ack_without_state_change", reason="fault_mode=ack_without_state_change", request_id=command_id)
            return
        if mode == "invalid_state":
            self._publish_payload(device.state_topic, {"state": "BROKEN", "power_w": "invalid"}, retain=True)
            self._publish_feedback(device, "invalid_state", reason="fault_mode=invalid_state", request_id=command_id)
            return
        requested = raw.upper()
        if requested not in {"ON", "OFF"}:
            raise ValueError("switch command must be ON or OFF")
        state = {"power": requested, "power_w": 95.0 if requested == "ON" else 1.5}
        changed = self._store.update_state(device.device_id, state, command_id)
        self._publish_state(device, changed["state"], command_id)
        self._publish_feedback(device, "ok", request_id=command_id)

    def _publish_discovery_and_states(self) -> None:
        with self._lock:
            devices = tuple(self._devices.values())
        for device in devices:
            self._publish_device(device)

    def _publish_device(self, device: SimulatedDevice) -> None:
        record = self._require_record(device.device_id)
        discovery = (
            _light_discovery(device)
            if isinstance(device, LightDevice)
            else _curtain_discovery(device)
            if isinstance(device, CurtainDevice)
            else _climate_discovery(device)
            if isinstance(device, ClimateDevice)
            else _switch_discovery(device)
        )
        self._publish_payload(device.discovery_topic, discovery, retain=True)
        self._publish_payload(device.feedback_discovery_topic, _feedback_discovery(device), retain=True)
        if isinstance(device, SwitchDevice):
            self._publish_payload(device.power_discovery_topic, _power_discovery(device), retain=True)
        self._publish_availability(device, record["online"])
        if record["online"]:
            self._publish_state(device, record["state"], record["last_command_id"])
            self._publish_feedback(device, "ok", request_id=record["last_command_id"])
        else:
            self._publish_feedback(device, "offline", reason="device_offline", request_id=record["last_command_id"])

    def _unpublish_device(self, device: SimulatedDevice) -> None:
        self._client.publish(device.availability_topic, "offline", qos=1, retain=True)
        topics = [device.discovery_topic, device.feedback_discovery_topic]
        if isinstance(device, SwitchDevice):
            topics.append(device.power_discovery_topic)
        for topic in topics:
            self._client.publish(topic, "", qos=1, retain=True)

    def _subscribe_device(self, device: SimulatedDevice) -> None:
        self._client.subscribe(device.command_topic, qos=1)
        if isinstance(device, CurtainDevice):
            self._client.subscribe(device.set_position_topic, qos=1)
        if isinstance(device, ClimateDevice):
            self._client.subscribe(device.temperature_command_topic, qos=1)

    def _unsubscribe_device(self, device: SimulatedDevice) -> None:
        topics = [device.command_topic]
        if isinstance(device, CurtainDevice):
            topics.append(device.set_position_topic)
        if isinstance(device, ClimateDevice):
            topics.append(device.temperature_command_topic)
        self._client.unsubscribe(topics)

    def _publish_availability(self, device: SimulatedDevice, online: bool) -> None:
        self._client.publish(device.availability_topic, "online" if online else "offline", qos=1, retain=True)

    def _publish_state(self, device: SimulatedDevice, state: dict[str, Any], request_id: str | None) -> None:
        if isinstance(device, LightDevice):
            payload: dict[str, Any] = {"state": state["power"], "brightness": state["brightness"], "updated_at": _timestamp()}
        elif isinstance(device, CurtainDevice):
            normalized = _curtain_state(state)
            mqtt_status = normalized["status"]
            if mqtt_status == "moving":
                mqtt_status = "opening" if normalized["target_position"] > normalized["position"] else "closing"
            elif mqtt_status == "stuck":
                mqtt_status = "stopped"
            payload = {**normalized, "status": mqtt_status, "updated_at": _timestamp()}
        elif isinstance(device, ClimateDevice):
            payload = {**_climate_state(state), "updated_at": _timestamp()}
        else:
            payload = {"state": _switch_state(state)["power"], "power_w": _switch_state(state)["power_w"], "updated_at": _timestamp()}
        if request_id is not None:
            payload["request_id"] = request_id
        self._publish_payload(device.state_topic, payload, retain=True)

    def _publish_feedback(
        self,
        device: SimulatedDevice,
        status: str,
        *,
        reason: str | None = None,
        request_id: str | None,
    ) -> None:
        """Publish read-only protocol feedback; it never changes device-owned state."""
        payload: dict[str, Any] = {"status": status, "updated_at": _timestamp()}
        if reason is not None:
            payload["reason"] = reason
        if request_id is not None:
            payload["request_id"] = request_id
        self._publish_payload(device.feedback_topic, payload, retain=True)

    def _publish_payload(self, topic: str, payload: dict[str, Any], retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), qos=1, retain=retain)

    def _require_device(self, device_id: str) -> SimulatedDevice:
        with self._lock:
            if device := self._devices.get(device_id):
                return device
        raise KeyError(f"unknown device: {device_id}")

    def _require_record(self, device_id: str) -> dict[str, Any]:
        if record := self._store.get_device(device_id):
            return record
        raise KeyError(f"device state is missing: {device_id}")


def _device_type(device: SimulatedDevice) -> str:
    if isinstance(device, LightDevice):
        return "light"
    if isinstance(device, CurtainDevice):
        return "curtain"
    if isinstance(device, ClimateDevice):
        return "climate"
    return "switch"


def _entity_object_id(device: SimulatedDevice) -> str:
    return _LEGACY_OBJECT_IDS.get(device.device_id, device.device_id)


def _entity_id(device: SimulatedDevice) -> str:
    domain = "cover" if isinstance(device, CurtainDevice) else _device_type(device)
    return f"{domain}.spacebutler_{_entity_object_id(device)}"


def _feedback_entity_id(device: SimulatedDevice) -> str:
    return f"sensor.spacebutler_{_entity_object_id(device)}_feedback"


def _discovery_device(device: SimulatedDevice) -> dict[str, Any]:
    return {
        "identifiers": [f"spacebutler_simulator_{device.device_id}"],
        "name": device.name,
        "manufacturer": "SpaceButler Simulator",
        "model": f"Virtual {_device_type(device).title()}",
        "suggested_area": _room_name(device.room),
    }


def _device_record(device: SimulatedDevice, record: dict[str, Any], source: str) -> dict[str, Any]:
    capabilities: list[str]
    if isinstance(device, LightDevice):
        capabilities = ["turn_on", "turn_off", "set_brightness"]
    elif isinstance(device, CurtainDevice):
        capabilities = ["open", "close", "set_position"]
    elif isinstance(device, ClimateDevice):
        capabilities = ["set_mode", "set_temperature", "turn_off"]
    else:
        capabilities = ["turn_on", "turn_off"]
    return {
        **record,
        "type": _device_type(device),
        "name": device.name,
        "room": device.room,
        "room_name": _room_name(device.room),
        "entity_id": _entity_id(device),
        "feedback_entity_id": _feedback_entity_id(device),
        "capabilities": capabilities,
        "definition_source": source,
        "removable": source == "runtime",
    }


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


def _light_discovery(device: LightDevice) -> dict[str, Any]:
    object_id = _entity_object_id(device)
    return {
        "name": None,
        "unique_id": f"spacebutler_mqtt_{object_id}",
        "default_entity_id": f"light.spacebutler_{object_id}",
        "schema": "json",
        "command_topic": device.command_topic,
        "state_topic": device.state_topic,
        "availability_topic": device.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "brightness": True,
        "brightness_scale": 255,
        "device": _discovery_device(device),
    }


def _feedback_discovery(device: SimulatedDevice) -> dict[str, Any]:
    """Expose device protocol outcomes to HA without allowing the Agent to write them."""
    suffix = _entity_object_id(device)
    return {
        "name": "协议反馈",
        "unique_id": f"spacebutler_mqtt_{suffix}_feedback",
        "default_entity_id": f"sensor.spacebutler_{suffix}_feedback",
        "state_topic": device.feedback_topic,
        "value_template": "{{ value_json.status }}",
        "json_attributes_topic": device.feedback_topic,
        "device": _discovery_device(device),
    }


def _curtain_discovery(device: CurtainDevice) -> dict[str, Any]:
    object_id = _entity_object_id(device)
    return {
        "name": None,
        "unique_id": f"spacebutler_mqtt_{object_id}",
        "default_entity_id": f"cover.spacebutler_{object_id}",
        "device_class": "curtain",
        "command_topic": device.command_topic,
        "set_position_topic": device.set_position_topic,
        "state_topic": device.state_topic,
        "value_template": "{{ value_json.status }}",
        "state_open": "open",
        "state_opening": "opening",
        "state_closed": "closed",
        "state_closing": "closing",
        "state_stopped": "stopped",
        "position_topic": device.state_topic,
        "position_template": "{{ value_json.position }}",
        "availability_topic": device.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "optimistic": False,
        "json_attributes_topic": device.state_topic,
        "device": _discovery_device(device),
    }


def _climate_discovery(device: ClimateDevice) -> dict[str, Any]:
    object_id = _entity_object_id(device)
    return {
        "name": None,
        "unique_id": f"spacebutler_mqtt_{object_id}",
        "default_entity_id": f"climate.spacebutler_{object_id}",
        "mode_command_topic": device.command_topic,
        "mode_state_topic": device.state_topic,
        "mode_state_template": "{{ value_json.mode }}",
        "temperature_command_topic": device.temperature_command_topic,
        "temperature_state_topic": device.state_topic,
        "temperature_state_template": "{{ value_json.temperature }}",
        "current_temperature_topic": device.state_topic,
        "current_temperature_template": "{{ value_json.current_temperature }}",
        "modes": ["off", "cool", "heat"],
        "min_temp": 16,
        "max_temp": 30,
        "temp_step": 0.5,
        "temperature_unit": "C",
        "availability_topic": device.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "json_attributes_topic": device.state_topic,
        "device": _discovery_device(device),
    }


def _switch_discovery(device: SwitchDevice) -> dict[str, Any]:
    object_id = _entity_object_id(device)
    return {
        "name": None,
        "unique_id": f"spacebutler_mqtt_{object_id}",
        "default_entity_id": f"switch.spacebutler_{object_id}",
        "command_topic": device.command_topic,
        "state_topic": device.state_topic,
        "value_template": "{{ value_json.state }}",
        "payload_on": "ON",
        "payload_off": "OFF",
        "availability_topic": device.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "json_attributes_topic": device.state_topic,
        "device": _discovery_device(device),
    }


def _power_discovery(device: SwitchDevice) -> dict[str, Any]:
    object_id = _entity_object_id(device)
    return {
        "name": "功耗",
        "unique_id": f"spacebutler_mqtt_{object_id}_power",
        "default_entity_id": f"sensor.spacebutler_{object_id}_power",
        "state_topic": device.state_topic,
        "value_template": "{{ value_json.power_w }}",
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
        "availability_topic": device.availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": _discovery_device(device),
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


def _curtain_state(value: dict[str, Any]) -> dict[str, Any]:
    position = int(value.get("position", 0))
    target = int(value.get("target_position", position))
    if not 0 <= position <= 100 or not 0 <= target <= 100:
        raise ValueError("curtain position must be between 0 and 100")
    status = str(value.get("status", "closed")).lower()
    if status not in {"moving", "open", "closed", "stuck"}:
        raise ValueError("invalid curtain status")
    return {"position": position, "target_position": target, "status": status}


def _climate_state(value: dict[str, Any]) -> dict[str, Any]:
    mode = str(value.get("mode", "off")).lower()
    temperature = float(value.get("temperature", 24))
    current = float(value.get("current_temperature", temperature))
    if mode not in {"off", "cool", "heat"}:
        raise ValueError("invalid climate mode")
    if not 16 <= temperature <= 30 or not 0 <= current <= 50:
        raise ValueError("invalid climate temperatures")
    return {"mode": mode, "temperature": temperature, "current_temperature": current}


def _switch_state(value: dict[str, Any]) -> dict[str, Any]:
    power = str(value.get("power", "OFF")).upper()
    watts = float(value.get("power_w", 1.5 if power == "OFF" else 95.0))
    if power not in {"ON", "OFF"} or not 0 <= watts <= 5000:
        raise ValueError("invalid switch state")
    return {"power": power, "power_w": watts}


def _curtain_target(raw: str, is_position_command: bool) -> int:
    if is_position_command:
        target = int(raw)
    else:
        command = raw.upper()
        if command == "OPEN":
            target = 100
        elif command == "CLOSE":
            target = 0
        else:
            raise ValueError("curtain command must be OPEN or CLOSE")
    if not 0 <= target <= 100:
        raise ValueError("curtain position must be between 0 and 100")
    return target


def _command_delay(device: SimulatedDevice, record: dict[str, Any]) -> int:
    if record["fault_mode"] == "delay":
        return record["fault_delay_ms"] or _FAULT_DELAY_MS
    return device.command_delay_ms


def _command_id(payload: dict[str, Any]) -> str:
    requested = payload.get("request_id")
    return requested if isinstance(requested, str) and requested.strip() else f"req-{uuid4().hex}"


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_device_definitions(path: Path) -> dict[str, dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("devices"), dict):
        raise ValueError("devices.yaml must contain a devices mapping")
    definitions: dict[str, dict[str, Any]] = {}
    for device_id, raw in loaded["devices"].items():
        if not isinstance(device_id, str) or not isinstance(raw, dict):
            raise ValueError("device definitions must map identifiers to objects")
        normalized_id, definition = _normalize_device_definition({**raw, "device_id": device_id})
        definitions[normalized_id] = definition
    return definitions


def _normalize_device_definition(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    device_type = str(payload.get("type", "")).strip().lower()
    if device_type not in _DEVICE_TYPES:
        raise ValueError("device type must be light, curtain, climate, or switch")
    name = str(payload.get("name", "")).strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("device name must contain 1 to 40 characters")
    room = str(payload.get("room", "home")).strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,32}", room):
        raise ValueError("room must use 1 to 32 lowercase letters, numbers, or underscores")
    requested_id = str(payload.get("device_id", "")).strip().lower()
    device_id = requested_id or f"{room}_{device_type}_{uuid4().hex[:6]}"
    if not re.fullmatch(r"[a-z0-9_]{3,64}", device_id):
        raise ValueError("device_id must use 3 to 64 lowercase letters, numbers, or underscores")
    behavior = payload.get("behavior") or {}
    if not isinstance(behavior, dict):
        raise ValueError("behavior must be an object")
    initial_state = payload.get("initial_state")
    if initial_state is None:
        initial_state = _default_initial_state(device_type)
    if not isinstance(initial_state, dict):
        raise ValueError("initial_state must be an object")
    normalized = {
        "type": device_type,
        "name": name,
        "room": room,
        "initial_state": initial_state,
        "behavior": {
            "command_delay_ms": int(behavior.get("command_delay_ms", 100)),
            "failure_rate": float(behavior.get("failure_rate", 0)),
            **(
                {"move_duration_ms": int(behavior.get("move_duration_ms", 800))}
                if device_type == "curtain"
                else {}
            ),
        },
    }
    if not 0 <= normalized["behavior"]["command_delay_ms"] <= 10_000:
        raise ValueError("command_delay_ms must be between 0 and 10000")
    if not 0 <= normalized["behavior"]["failure_rate"] <= 1:
        raise ValueError("failure_rate must be between 0 and 1")
    if device_type == "curtain" and not 0 <= normalized["behavior"]["move_duration_ms"] <= 60_000:
        raise ValueError("move_duration_ms must be between 0 and 60000")
    return device_id, normalized


def _default_initial_state(device_type: str) -> dict[str, Any]:
    if device_type == "light":
        return {"power": "OFF", "brightness": 0}
    if device_type == "curtain":
        return {"position": 0, "target_position": 0, "status": "closed"}
    if device_type == "climate":
        return {"mode": "off", "temperature": 24, "current_temperature": 26}
    return {"power": "OFF", "power_w": 1.5}


def _device_from_definition(device_id: str, raw: dict[str, Any]) -> SimulatedDevice:
    initial_state = raw["initial_state"]
    behavior = raw["behavior"]
    if raw["type"] == "light":
        initial_command = dict(initial_state)
        if "power" in initial_command and "state" not in initial_command:
            initial_command["state"] = initial_command["power"]
        state = _light_state({"power": "OFF", "brightness": 0}, initial_command)
        return LightDevice(
            device_id,
            str(raw["name"]),
            str(raw["room"]),
            state,
            int(behavior["command_delay_ms"]),
            float(behavior["failure_rate"]),
        )
    if raw["type"] == "curtain":
        return CurtainDevice(
            device_id,
            str(raw["name"]),
            str(raw["room"]),
            _curtain_state(initial_state),
            int(behavior["command_delay_ms"]),
            float(behavior["failure_rate"]),
            int(behavior["move_duration_ms"]),
        )
    if raw["type"] == "climate":
        return ClimateDevice(
            device_id,
            str(raw["name"]),
            str(raw["room"]),
            _climate_state(initial_state),
            int(behavior["command_delay_ms"]),
            float(behavior["failure_rate"]),
        )
    return SwitchDevice(
        device_id,
        str(raw["name"]),
        str(raw["room"]),
        _switch_state(initial_state),
        int(behavior["command_delay_ms"]),
        float(behavior["failure_rate"]),
    )


def _load_devices(path: Path, store: DeviceStateStore) -> dict[str, SimulatedDevice]:
    existing = {item["device_id"]: item for item in store.definitions()}
    for device_id, definition in _load_device_definitions(path).items():
        current = existing.get(device_id)
        if current is None or current["source"] == "configured":
            store.save_definition(device_id, definition, source="configured", replace=True)
    return {
        item["device_id"]: _device_from_definition(item["device_id"], item["definition"])
        for item in store.definitions()
    }


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
            if parsed.path == "/admin/devices":
                try:
                    record = simulator.add_device(self._read_json())
                except (KeyError, ValueError) as error:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                self._write_json(HTTPStatus.CREATED, record)
                return
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

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            prefix = "/admin/devices/"
            if not parsed.path.startswith(prefix):
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            device_id = unquote(parsed.path.removeprefix(prefix))
            if not device_id or "/" in device_id:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                removed = simulator.remove_device(device_id)
            except KeyError as error:
                self._write_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            except ValueError as error:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._write_json(HTTPStatus.OK, {"removed": removed})

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
    store = DeviceStateStore(database_path)
    simulator = DeviceSimulator(
        _load_devices(config_path, store),
        store,
        os.environ.get("MQTT_HOST", "mqtt"),
        int(os.environ.get("MQTT_PORT", "1883")),
    )
    try:
        simulator.run(os.environ.get("ADMIN_HOST", "0.0.0.0"), int(os.environ.get("ADMIN_PORT", "8090")))
    finally:
        simulator.stop()


if __name__ == "__main__":
    main()
