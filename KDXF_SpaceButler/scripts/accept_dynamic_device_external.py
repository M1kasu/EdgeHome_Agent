"""External acceptance for runtime device registration, control and persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("SPACEBUTLER_WORKBENCH_URL", "http://127.0.0.1:8766").rstrip("/")
HA_URL = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:12900").rstrip("/")
HA_TOKEN = os.environ["HA_TOKEN"]
COMPOSE = ROOT / "deployment" / "docker-compose.yml"
DEVICE_ID = "acceptance_study_light"
ENTITY_ID = f"light.spacebutler_{DEVICE_ID}"


def main() -> int:
    cases: list[dict[str, Any]] = []
    _cleanup()
    try:
        created = _request(
            "POST",
            "/api/devices",
            {
                "device_id": DEVICE_ID,
                "type": "light",
                "name": "验收书房灯",
                "room": "study",
                "initial_state": {"power": "OFF", "brightness": 0},
            },
        )
        created_device = created.get("created", {})
        cases.append(
            {
                "test_id": "runtime_registration_and_ha_discovery",
                "passed": (
                    created_device.get("entity_id") == ENTITY_ID
                    and created_device.get("definition_source") == "runtime"
                    and _ha_state(ENTITY_ID).get("state") == "off"
                ),
                "created": created_device,
            }
        )

        controlled = _request(
            "POST",
            f"/api/devices/{quote(DEVICE_ID)}/control",
            {"power": "on", "brightness": 67},
        )
        controlled_device = _find_device(controlled)
        ha_after = _ha_state(ENTITY_ID)
        brightness = ha_after.get("attributes", {}).get("brightness")
        cases.append(
            {
                "test_id": "manual_control_through_ha_mqtt_readback",
                "passed": (
                    controlled_device.get("state", {}).get("power") == "ON"
                    and ha_after.get("state") == "on"
                    and isinstance(brightness, (int, float))
                    and abs(round(float(brightness) * 100 / 255) - 67) <= 1
                    and bool(controlled_device.get("last_command_id"))
                ),
                "device": controlled_device,
                "ha_state": ha_after,
            }
        )

        runtime_container = str(created_device.get("runtime", {}).get("container", ""))
        if not runtime_container:
            raise RuntimeError("created device did not report its dedicated runtime container")
        before_started_at = _container_started_at(runtime_container)
        _docker("restart", runtime_container)
        after_started_at = _wait_for_restart(runtime_container, before_started_at)
        recovered = _wait_for_device()
        cases.append(
            {
                "test_id": "runtime_device_survives_own_container_restart",
                "passed": (
                    before_started_at != after_started_at
                    and recovered.get("state", {}).get("power") == "ON"
                    and recovered.get("definition_source") == "runtime"
                    and recovered.get("discovered") is True
                ),
                "before_started_at": before_started_at,
                "after_started_at": after_started_at,
                "device": recovered,
            }
        )

        removed = _request("DELETE", f"/api/devices/{quote(DEVICE_ID)}")
        _wait_for(lambda: not _ha_entity_exists(ENTITY_ID), 30, "Home Assistant discovery removal")
        devices_after = _request("GET", "/api/devices").get("devices", [])
        cases.append(
            {
                "test_id": "runtime_device_removal_clears_discovery",
                "passed": (
                    removed.get("removed", {}).get("device_id") == DEVICE_ID
                    and not any(item.get("device_id") == DEVICE_ID for item in devices_after)
                    and not _ha_entity_exists(ENTITY_ID)
                ),
                "removed": removed.get("removed"),
            }
        )
    except Exception as error:
        cases.append({"test_id": "dynamic_device_external_exception", "passed": False, "failure": str(error)})
    finally:
        _cleanup()

    passed = len(cases) == 4 and all(bool(case.get("passed")) for case in cases)
    print(
        json.dumps(
            {
                "acceptance": "PASS" if passed else "FAIL",
                "boundary": "workbench -> Fleet Gateway -> dynamic device container -> HA/MQTT/SQLite -> restart/removal",
                "cases_passed": sum(bool(case.get("passed")) for case in cases),
                "cases_total": len(cases),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def _find_device(payload: dict[str, Any]) -> dict[str, Any]:
    devices = payload.get("devices", [])
    for device in devices:
        if isinstance(device, dict) and device.get("device_id") == DEVICE_ID:
            return device
    raise RuntimeError(f"dynamic device is missing: {DEVICE_ID}")


def _wait_for_device() -> dict[str, Any]:
    latest: dict[str, Any] = {}

    def available() -> bool:
        nonlocal latest
        try:
            latest = _find_device(_request("GET", "/api/devices"))
            return latest.get("discovered") is True and latest.get("online") is True
        except (RuntimeError, HTTPError, URLError):
            return False

    _wait_for(available, 90, "dynamic device recovery")
    return latest


def _cleanup() -> None:
    try:
        _request("DELETE", f"/api/devices/{quote(DEVICE_ID)}")
    except Exception:
        pass


def _request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data is not None else {}
    with urlopen(Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method), timeout=35) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("workbench returned a non-object response")
    return decoded


def _ha_state(entity_id: str) -> dict[str, Any]:
    request = Request(
        f"{HA_URL}/api/states/{entity_id}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Accept": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("Home Assistant returned a non-object state")
    return decoded


def _ha_entity_exists(entity_id: str) -> bool:
    try:
        _ha_state(entity_id)
        return True
    except HTTPError as error:
        if error.code == 404:
            return False
        raise


def _compose(*arguments: str) -> None:
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
        raise RuntimeError(completed.stderr.strip())


def _docker(*arguments: str) -> None:
    completed = subprocess.run(
        ["docker", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())


def _container_started_at(container: str) -> str:
    completed = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.StartedAt}}",
            container,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return completed.stdout.strip()


def _wait_for_restart(container: str, before_started_at: str) -> str:
    latest = before_started_at

    def restarted() -> bool:
        nonlocal latest
        latest = _container_started_at(container)
        return bool(latest and latest != before_started_at)

    _wait_for(restarted, 30, "simulator restart")
    return latest


def _wait_for(predicate: Any, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, RuntimeError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {label}")


if __name__ == "__main__":
    raise SystemExit(main())
