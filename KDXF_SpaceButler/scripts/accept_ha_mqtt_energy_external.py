"""External black-box acceptance against HA, MQTT and one device container."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from paho.mqtt import client as mqtt

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler import (  # noqa: E402
    ExecutionStatus,
    HomeAssistantClient,
    HomeAssistantRuntime,
    HomeAssistantSpaceAdapter,
    HouseholdMemory,
    SpaceButlerAgent,
    SpaceButlerSession,
)


HA_URL = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:12900")
SIMULATOR_URL = os.getenv("SPACEBUTLER_SIMULATOR_URL", "http://127.0.0.1:12891")
MQTT_HOST = os.getenv("SPACEBUTLER_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("SPACEBUTLER_MQTT_PORT", "18884"))
MEMORY_DB = Path(
    os.getenv(
        "SPACEBUTLER_MEMORY_DB",
        str(ROOT / "deployment" / "runtime" / "agent" / "household_memory.db"),
    )
)
TOKEN = os.environ["HA_TOKEN"]
CLIMATE = "climate.spacebutler_living_room_ac"
FEEDBACK = "sensor.spacebutler_living_room_ac_feedback"
DEVICE_ID = "living_room_ac"
DEVICE_SERVICE = "spacebutler-device-living-room-ac"
STATE_TOPIC = "spacebutler/devices/living_room_ac/state"


class MqttCapture:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"spacebutler-external-{uuid4().hex[:10]}",
        )
        self.client.on_message = self._on_message

    def __enter__(self) -> MqttCapture:
        self.client.connect(MQTT_HOST, MQTT_PORT, 30)
        self.client.subscribe(STATE_TOPIC, qos=1)
        self.client.loop_start()
        return self

    def __exit__(self, *_: object) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_message(self, _client: mqtt.Client, _userdata: object, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            self.messages.append(payload)


def main() -> int:
    client = HomeAssistantClient(HA_URL, TOKEN, request_timeout_seconds=10)
    adapter = HomeAssistantSpaceAdapter(client)
    cases: list[dict[str, Any]] = []

    try:
        _prepare_healthy_scene(client)
        with MqttCapture() as mqtt_capture:
            memory = HouseholdMemory(MEMORY_DB)
            memory.clear()
            agent = SpaceButlerAgent(memory)
            session = SpaceButlerSession(
                agent,
                HomeAssistantRuntime(client, verification_timeout_seconds=5),
            )
            snapshot = adapter.capture_energy_snapshot(unoccupied_minutes=23)
            suggestion = session.observe(snapshot)
            confirmed = session.user_message("household", "确认")
            _wait_for(lambda: _simulator_device()["state"]["mode"] == "off", 10, "simulator AC off")
            ha_after = client.state(CLIMATE)
            simulator_after = _simulator_device()
            sqlite_after = _sqlite_device()
            mqtt_off = next((item for item in reversed(mqtt_capture.messages) if item.get("mode") == "off"), None)
            report = confirmed.report
            normal_passed = all(
                (
                    suggestion.status == "needs_confirmation",
                    report is not None,
                    bool(report and report.status == ExecutionStatus.SUCCESS),
                    bool(report and report.verified),
                    ha_after.get("state") == "off",
                    simulator_after["state"]["mode"] == "off",
                    sqlite_after["state"]["mode"] == "off",
                    mqtt_off is not None,
                    bool(mqtt_off and mqtt_off.get("request_id") == sqlite_after.get("last_command_id")),
                )
            )
            cases.append(
                {
                    "test_id": "normal_confirm_execute_readback",
                    "passed": normal_passed,
                    "suggestion": to_jsonable(suggestion),
                    "confirmation": to_jsonable(confirmed),
                    "ha_after": ha_after,
                    "simulator_after": simulator_after,
                    "sqlite_after": sqlite_after,
                    "mqtt_off": mqtt_off,
                }
            )

            learned = session.user_message("household", "以后这种情况直接执行")
            _prepare_healthy_scene(client)
            reconstructed_agent = SpaceButlerAgent(HouseholdMemory(MEMORY_DB))
            second = SpaceButlerSession(
                reconstructed_agent,
                HomeAssistantRuntime(client, verification_timeout_seconds=5),
            ).observe(adapter.capture_energy_snapshot(unoccupied_minutes=23))
            _wait_for(lambda: _simulator_device()["state"]["mode"] == "off", 10, "learned auto execution")
            cases.append(
                {
                    "test_id": "feedback_changes_next_behavior",
                    "passed": (
                        learned.learned == "learned_auto_execute"
                        and second.status == "executed"
                        and second.report is not None
                        and second.report.verified
                        and len(reconstructed_agent.memory.export_preferences()) == 1
                    ),
                    "reconstructed_agent": True,
                    "memory_database_exists": MEMORY_DB.exists(),
                    "learned": to_jsonable(learned),
                    "second": to_jsonable(second),
                }
            )

        fault_expectations = {
            "ack_without_state_change": ExecutionStatus.VALIDATION_FAILED,
            "reject": ExecutionStatus.EXECUTION_FAILED,
            "delay": ExecutionStatus.TIMEOUT,
            "offline": ExecutionStatus.DEVICE_UNAVAILABLE,
        }
        for mode, expected in fault_expectations.items():
            _prepare_healthy_scene(client)
            agent = SpaceButlerAgent()
            session = SpaceButlerSession(
                agent,
                HomeAssistantRuntime(client, verification_timeout_seconds=1.2),
            )
            suggestion = session.observe(adapter.capture_energy_snapshot(unoccupied_minutes=23))
            _set_fault(mode, delay_ms=3_000 if mode == "delay" else 0)
            if mode == "offline":
                _wait_for(lambda: client.state(CLIMATE).get("state") == "unavailable", 10, "HA unavailable")
            confirmed = session.user_message("household", "确认")
            report = confirmed.report
            simulator_after = _simulator_device()
            feedback = client.state(FEEDBACK)
            cases.append(
                {
                    "test_id": f"fault_{mode}",
                    "passed": (
                        suggestion.status == "needs_confirmation"
                        and report is not None
                        and report.status == expected
                        and not report.verified
                        and simulator_after["state"]["mode"] != "off"
                    ),
                    "expected_status": expected.value,
                    "report": to_jsonable(report),
                    "simulator_after": simulator_after,
                    "feedback": feedback,
                }
            )
            if mode == "delay":
                time.sleep(2.2)
            _set_fault("none")
    except Exception as error:
        cases.append({"test_id": "external_infrastructure", "passed": False, "failure": str(error)})
    finally:
        try:
            _set_fault("none")
        except Exception:
            pass

    passed = bool(cases) and all(bool(case.get("passed")) for case in cases)
    print(
        json.dumps(
            {
                "acceptance": "PASS" if passed else "FAIL",
                "boundary": "external_process_to_ha_mqtt_device_container_sqlite",
                "cases_passed": sum(bool(case.get("passed")) for case in cases),
                "cases_total": len(cases),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def _prepare_healthy_scene(client: HomeAssistantClient) -> None:
    _simulator_request("POST", f"/admin/devices/{DEVICE_ID}/fault", {"mode": "none", "delay_ms": 0})
    _simulator_request("POST", f"/admin/devices/{DEVICE_ID}/reset", {})
    client.call_service("input_boolean", "turn_off", {"entity_id": "input_boolean.spacebutler_living_room_presence"})
    client.call_service("input_boolean", "turn_on", {"entity_id": "input_boolean.spacebutler_living_room_window_open"})
    _wait_for(lambda: client.state(CLIMATE).get("state") == "cool", 15, "HA climate cool")


def _set_fault(mode: str, delay_ms: int = 0) -> dict[str, Any]:
    return _simulator_request(
        "POST",
        f"/admin/devices/{DEVICE_ID}/fault",
        {"mode": mode, "delay_ms": delay_ms},
    )


def _simulator_device() -> dict[str, Any]:
    return _simulator_request("GET", f"/devices/{DEVICE_ID}")


def _simulator_request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"{SIMULATOR_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"simulator returned HTTP {error.code}: {error.read().decode()}") from error
    if not isinstance(decoded, dict):
        raise RuntimeError("simulator returned a non-object response")
    return decoded


def _sqlite_device() -> dict[str, Any]:
    compose = ROOT / "deployment" / "docker-compose.yml"
    container_result = subprocess.run(
        ["docker", "compose", "-f", str(compose), "ps", "-q", DEVICE_SERVICE],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    container = container_result.stdout.strip()
    if container_result.returncode or not container:
        raise RuntimeError(container_result.stderr.strip() or f"missing container for {DEVICE_SERVICE}")
    query = (
        "import json,sqlite3;"
        "c=sqlite3.connect('/app/data/device_state.db');"
        "r=c.execute(\"SELECT state_json,updated_at,last_command_id,online,fault_mode "
        f"FROM device_state WHERE device_id='{DEVICE_ID}'\").fetchone();"
        "print(json.dumps({'state':json.loads(r[0]),'updated_at':r[1],"
        "'last_command_id':r[2],'online':bool(r[3]),'fault_mode':r[4]}))"
    )
    result = subprocess.run(
        ["docker", "exec", container, "python", "-c", query],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    decoded = json.loads(result.stdout)
    if not isinstance(decoded, dict):
        raise RuntimeError("device container returned invalid SQLite state")
    return decoded


def _wait_for(predicate: Any, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (KeyError, RuntimeError):
            pass
        time.sleep(0.25)
    raise TimeoutError(f"timed out waiting for {label}")


def to_jsonable(value: object) -> object:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
