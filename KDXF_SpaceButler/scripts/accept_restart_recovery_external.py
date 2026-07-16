"""External recovery acceptance for simulator, MQTT and Home Assistant restarts."""

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
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler import (  # noqa: E402
    HomeAssistantClient,
    HomeAssistantRuntime,
    HomeAssistantSpaceAdapter,
    SpaceButlerAgent,
    SpaceButlerSession,
)


HA_URL = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:8900")
SIMULATOR_URL = os.getenv("SPACEBUTLER_SIMULATOR_URL", "http://127.0.0.1:8091")
TOKEN = os.environ["HA_TOKEN"]
COMPOSE = ROOT / "deployment" / "docker-compose.yml"
CLIMATE = "climate.spacebutler_living_room_ac"
DEVICE_ID = "living_room_ac"


def main() -> int:
    cases: list[dict[str, Any]] = []
    services = (
        "spacebutler-device-simulator",
        "spacebutler-mqtt",
        "spacebutler-home-assistant",
    )
    for service in services:
        try:
            client = HomeAssistantClient(HA_URL, TOKEN)
            _prepare_cool(client)
            before_started_at = _container_started_at(service)
            _compose("restart", service)
            after_started_at = _wait_for_restart(service, before_started_at, 30)
            _wait_for_infrastructure(client, service, 90)
            response = _execute_energy_loop(client)
            simulator = _simulator_device()
            cases.append(
                {
                    "test_id": f"restart_{service}",
                    "passed": (
                        before_started_at != after_started_at
                        and response.status == "executed"
                        and response.report is not None
                        and response.report.verified
                        and simulator["state"]["mode"] == "off"
                    ),
                    "before_started_at": before_started_at,
                    "after_started_at": after_started_at,
                    "response": to_jsonable(response),
                    "simulator_after": simulator,
                }
            )
        except Exception as error:
            cases.append(
                {
                    "test_id": f"restart_{service}",
                    "passed": False,
                    "failure": str(error),
                }
            )
    passed = len(cases) == len(services) and all(bool(case.get("passed")) for case in cases)
    print(
        json.dumps(
            {
                "acceptance": "PASS" if passed else "FAIL",
                "boundary": "container_restart_then_full_agent_ha_mqtt_sqlite_execution",
                "cases_passed": sum(bool(case.get("passed")) for case in cases),
                "cases_total": len(cases),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def _prepare_cool(client: HomeAssistantClient) -> None:
    _simulator_request("POST", f"/admin/devices/{DEVICE_ID}/fault", {"mode": "none", "delay_ms": 0})
    _simulator_request("POST", f"/admin/devices/{DEVICE_ID}/reset", {})
    client.call_service("input_boolean", "turn_off", {"entity_id": "input_boolean.spacebutler_living_room_presence"})
    client.call_service("input_boolean", "turn_on", {"entity_id": "input_boolean.spacebutler_living_room_window_open"})
    _wait_for(lambda: client.state(CLIMATE).get("state") == "cool", 20, "HA climate cool")


def _wait_for_infrastructure(client: HomeAssistantClient, service: str, timeout_seconds: float) -> None:
    if service == "spacebutler-device-simulator":
        _wait_for(lambda: _simulator_device().get("online") is True, timeout_seconds, "simulator recovery")
    if service == "spacebutler-home-assistant":
        _wait_for(_ha_http_available, timeout_seconds, "Home Assistant HTTP recovery")
    _wait_for(lambda: client.state(CLIMATE).get("state") == "cool", timeout_seconds, "HA MQTT climate recovery")


def _execute_energy_loop(client: HomeAssistantClient):
    snapshot = HomeAssistantSpaceAdapter(client).capture_energy_snapshot(unoccupied_minutes=23)
    session = SpaceButlerSession(
        SpaceButlerAgent(),
        HomeAssistantRuntime(client, verification_timeout_seconds=8),
    )
    suggestion = session.observe(snapshot)
    if suggestion.status != "needs_confirmation":
        raise AssertionError(f"restart recovery did not produce a confirmable plan: {suggestion.status}")
    response = session.user_message("household", "确认")
    _wait_for(lambda: _simulator_device()["state"]["mode"] == "off", 15, "simulator AC off after restart")
    return response


def _container_started_at(service: str) -> str:
    container = _compose("ps", "-q", service).strip()
    if not container:
        raise RuntimeError(f"compose service has no container: {service}")
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.StartedAt}}", container],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return completed.stdout.strip()


def _wait_for_restart(service: str, before_started_at: str, timeout_seconds: float) -> str:
    latest = before_started_at
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        latest = _container_started_at(service)
        if latest and latest != before_started_at:
            return latest
        time.sleep(0.5)
    raise TimeoutError(f"container StartedAt did not change for {service}")


def _compose(*arguments: str) -> str:
    completed = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"docker compose {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout


def _ha_http_available() -> bool:
    try:
        with urlopen(f"{HA_URL}/", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def _simulator_device() -> dict[str, Any]:
    return _simulator_request("GET", f"/devices/{DEVICE_ID}")


def _simulator_request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    with urlopen(Request(f"{SIMULATOR_URL}{path}", data=data, headers=headers, method=method), timeout=10) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("simulator returned a non-object response")
    return decoded


def _wait_for(predicate: Any, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(0.5)
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
