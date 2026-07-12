"""Persist M3 climate restart evidence without writing Home Assistant state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deployment" / "docker-compose.yml"
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
SIM_URL = os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090").rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
AGENT_ID = os.getenv("EDGEHOME_AGENT_ID", "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome")
ENTITY_ID, DEVICE_ID = "climate.edgehome_living_room_ac", "living_room_ac"
REPORT = ROOT / "reports" / "acceptance" / "m3_mqtt_climate" / "restart_matrix.jsonl"


def request(method: str, url: str, body: dict[str, Any] | None = None, *, auth: bool = False) -> Any:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode('utf-8', errors='replace')}") from error
    except (OSError, TimeoutError, URLError) as error:
        raise RuntimeError(f"transport unavailable: {error}") from error


def ha() -> dict[str, Any] | None:
    return request("GET", f"{HA_URL}/api/states/{ENTITY_ID}", auth=True)


def device() -> dict[str, Any]:
    return request("GET", f"{SIM_URL}/devices/{DEVICE_ID}")


def wait_for(predicate: Any, seconds: int, label: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (KeyError, RuntimeError, TypeError):
            pass
        time.sleep(1)
    raise TimeoutError(f"timed out waiting for {label}")


def compose(*args: str) -> None:
    completed = subprocess.run(["docker", "compose", "-f", str(COMPOSE), *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {completed.stderr}")


def seed() -> None:
    request("POST", f"{SIM_URL}/admin/devices/{DEVICE_ID}/reset", {})
    request("POST", f"{SIM_URL}/admin/devices/{DEVICE_ID}/fault", {"mode": "none", "delay_ms": 0})
    response = request(
        "POST",
        f"{HA_URL}/api/conversation/process",
        {"text": "把客厅空调调到26度", "language": "zh-Hans", "agent_id": AGENT_ID, "conversation_id": f"m3-restart-{uuid4().hex}"},
        auth=True,
    )
    if request("GET", f"{HA_URL}/api/states/sensor.edgehome_last_execution", auth=True)["state"] != "success":
        raise AssertionError(f"cannot seed climate restart matrix: {response}")
    wait_for(lambda: ha() is not None and float(ha()["attributes"]["temperature"]) == 26.0, 20, "climate target 26C")


def record(test_id: str, before: dict[str, Any], *, expect_unavailable: bool = False) -> dict[str, Any]:
    after_ha, after_device = ha(), device()
    passed = before["state"] == after_device["state"] and (after_ha is not None and (after_ha["state"] == "unavailable" if expect_unavailable else float(after_ha["attributes"]["temperature"]) == 26.0))
    return {"test_id": test_id, "before_device": before, "after_device": after_device, "after_ha": after_ha, "passed": passed}


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    try:
        seed()
        before = device()
        compose("restart", "home-assistant")
        wait_for(lambda: ha() is not None and float(ha()["attributes"]["temperature"]) == 26.0, 120, "HA climate recovery")
        records.append(record("restart_home_assistant", before))

        before = device()
        compose("stop", "device-simulator")
        wait_for(lambda: ha() is not None and ha()["state"] == "unavailable", 30, "simulator climate unavailable")
        unavailable = {"test_id": "stop_device_simulator", "before_device": before, "after_ha": ha(), "passed": True}
        records.append(unavailable)
        compose("up", "-d", "device-simulator")
        wait_for(lambda: ha() is not None and float(ha()["attributes"]["temperature"]) == 26.0, 60, "simulator climate recovery")
        records.append(record("restart_device_simulator", before))

        before = device()
        compose("stop", "mqtt")
        time.sleep(2)
        response = request(
            "POST",
            f"{HA_URL}/api/conversation/process",
            {"text": "把客厅空调调到25度", "language": "zh-Hans", "agent_id": AGENT_ID, "conversation_id": f"m3-broker-{uuid4().hex}"},
            auth=True,
        )
        trace = request("GET", f"{HA_URL}/api/states/sensor.edgehome_last_execution", auth=True)
        records.append({"test_id": "broker_control_failure", "response": response, "trace": trace, "before_device": before, "passed": trace["state"] != "success"})
        compose("up", "-d", "mqtt")
        wait_for(lambda: ha() is not None and float(ha()["attributes"]["temperature"]) == 26.0, 60, "broker climate recovery")
        records.append(record("restart_mosquitto", before))
    except Exception as error:
        records.append({"test_id": "restart_matrix_infrastructure", "passed": False, "failure": str(error)})
    finally:
        try:
            compose("up", "-d", "mqtt", "device-simulator")
        except RuntimeError:
            pass
    REPORT.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in records), encoding="utf-8")
    passed = sum(bool(item.get("passed")) for item in records)
    print(f"M3 restart matrix: {passed}/{len(records)}")
    return 0 if records and passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
