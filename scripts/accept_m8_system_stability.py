"""System-level M8 acceptance for MQTT availability, restart, and stability.

This script is deliberately serial: it owns the four MQTT demo devices while
it stops and starts compose services, so no other acceptance script may run at
the same time.  It records both the expected temporary outages and the state
read back after each service recovers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deployment" / "docker-compose.yml"
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
SIM_URL = os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090").rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
AGENT_ID = os.getenv("EDGEHOME_AGENT_ID", "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome")
REPORT = ROOT / "reports" / "acceptance" / "m8_system_stability"
DEVICES = ("living_room_main_light", "living_room_curtain", "living_room_ac", "living_room_tv")
ENTITIES = {
    "light": "light.edgehome_living_room_main",
    "curtain": "cover.edgehome_living_room_curtain",
    "climate": "climate.edgehome_living_room_ac",
    "tv": "switch.edgehome_living_room_tv",
    "power": "sensor.edgehome_living_room_tv_power",
    "router": "switch.edgehome_router",
    "refrigerator": "switch.edgehome_refrigerator",
    "security": "input_boolean.edgehome_security_mode",
}
OPERATIONS: list[dict[str, Any]] = []


def request(method: str, url: str, body: dict[str, Any] | None = None, *, auth: bool = False, timeout: int = 90) -> Any:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode('utf-8', errors='replace')}") from error
    except URLError as error:
        raise RuntimeError(f"connection error: {error.reason}") from error
    return json.loads(raw) if raw else None


def state(entity_id: str) -> dict[str, Any]:
    return request("GET", f"{HA_URL}/api/states/{entity_id}", auth=True)


def states() -> dict[str, Any]:
    return {name: state(entity_id) for name, entity_id in ENTITIES.items()}


def simulator() -> dict[str, Any]:
    return {device_id: request("GET", f"{SIM_URL}/devices/{device_id}") for device_id in DEVICES}


def events() -> list[dict[str, Any]]:
    return sorted(request("GET", f"{SIM_URL}/events?limit=800")["events"], key=lambda item: item["id"])


def compose(*arguments: str) -> None:
    command = ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    operation = {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    OPERATIONS.append(operation)
    if completed.returncode:
        raise RuntimeError(f"compose command failed: {' '.join(arguments)}\n{completed.stderr}")


def wait_for(label: str, predicate: Callable[[], bool], timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as error:  # Service restarts are expected to be briefly unreachable.
            last_error = error
        time.sleep(1)
    raise RuntimeError(f"timed out waiting for {label}: {last_error}")


def available(snapshot: dict[str, Any]) -> bool:
    return all(snapshot[name]["state"] not in {"unavailable", "unknown"} for name in ("light", "curtain", "climate", "tv"))


def reset() -> None:
    for device_id in DEVICES:
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/reset", {})
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/fault", {"mode": "none", "delay_ms": 0})
    for entity_id in ("input_boolean.edgehome_router_power", "input_boolean.edgehome_refrigerator_power"):
        request("POST", f"{HA_URL}/api/services/input_boolean/turn_on", {"entity_id": entity_id}, auth=True)
    for entity_id in (
        "input_boolean.edgehome_window_open_state",
        "input_boolean.edgehome_living_room_presence_state",
        "input_boolean.edgehome_security_mode",
    ):
        request("POST", f"{HA_URL}/api/services/input_boolean/turn_off", {"entity_id": entity_id}, auth=True)
    wait_for("MQTT entities after reset", lambda: available(states()))
    time.sleep(2)


def assist(text: str, conversation_id: str) -> dict[str, Any]:
    return request(
        "POST",
        f"{HA_URL}/api/conversation/process",
        {"text": text, "language": "zh-Hans", "agent_id": AGENT_ID, "conversation_id": conversation_id},
        auth=True,
    )


def speech(response: dict[str, Any]) -> str:
    return str(response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", ""))


def turn(text: str, conversation_id: str) -> dict[str, Any]:
    floor = max((event["id"] for event in events()), default=0)
    before = {"ha": states(), "simulator": simulator()}
    response = assist(text, conversation_id)
    time.sleep(2)
    after = {
        "ha": states(),
        "simulator": simulator(),
        "trace": state("sensor.edgehome_last_execution"),
        "events": [event for event in events() if event["id"] > floor],
    }
    return {"input": text, "response": response, "speech": speech(response), "before": before, "after": after}


def assert_coherent(ha: dict[str, Any], sim: dict[str, Any], failures: list[str]) -> None:
    if ha["light"]["state"] != ("on" if sim["living_room_main_light"]["state"]["power"] == "ON" else "off"):
        failures.append("HA light state differs from simulator state")
    if ha["light"]["state"] == "on" and int(ha["light"]["attributes"].get("brightness", -1)) != sim["living_room_main_light"]["state"]["brightness"]:
        failures.append("HA light brightness differs from simulator state")
    if ha["curtain"]["attributes"].get("current_position") != sim["living_room_curtain"]["state"]["position"]:
        failures.append("HA curtain position differs from simulator state")
    if ha["climate"]["state"] != sim["living_room_ac"]["state"]["mode"]:
        failures.append("HA AC mode differs from simulator state")
    if float(ha["climate"]["attributes"].get("temperature", -1)) != float(sim["living_room_ac"]["state"]["temperature"]):
        failures.append("HA AC target temperature differs from simulator state")
    expected_tv = "on" if sim["living_room_tv"]["state"]["power"] == "ON" else "off"
    if ha["tv"]["state"] != expected_tv or float(ha["power"]["state"]) != float(sim["living_room_tv"]["state"]["power_w"]):
        failures.append("HA TV state or power differs from simulator state")


def assert_movie(turn_record: dict[str, Any], failures: list[str]) -> None:
    trace = turn_record["after"]["trace"]
    if trace["state"] != "success" or trace["attributes"].get("route") != "mqtt_movie_scene":
        failures.append("movie scene did not finish with verified MQTT success")


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        cycles: list[dict[str, Any]] = []
        cycle_failures: list[str] = []
        for index in range(1, 4):
            reset()
            conversation = f"m8-cycle-{index}-{uuid4().hex[:10]}"
            movie = turn("启动观影模式，主灯调到20%，空调设为26度", conversation)
            leaving = turn("我要出门了", conversation)
            assert_movie(movie, cycle_failures)
            after = leaving["after"]
            if after["trace"]["state"] != "success" or after["trace"]["attributes"].get("route") != "deterministic_leaving_scene":
                cycle_failures.append(f"cycle {index}: leaving protection did not complete")
            if after["ha"]["router"]["state"] != "on" or after["ha"]["refrigerator"]["state"] != "on":
                cycle_failures.append(f"cycle {index}: protected router or refrigerator changed state")
            if after["ha"]["light"]["state"] != "off" or after["ha"]["curtain"]["attributes"].get("current_position") != 0 or after["ha"]["climate"]["state"] != "off" or after["ha"]["tv"]["state"] != "off":
                cycle_failures.append(f"cycle {index}: leaving target state was not reached")
            assert_coherent(after["ha"], after["simulator"], cycle_failures)
            cycles.append({"round": index, "movie": movie, "leaving": leaving})
        results.append({"test_id": "three_round_movie_leaving_stability", "rounds": cycles, "failures": cycle_failures})

        reset()
        conversation = f"m8-ha-restart-{uuid4().hex[:10]}"
        movie = turn("启动观影模式，主灯调到20%，空调设为26度", conversation)
        before_restart = {"ha": states(), "simulator": simulator()}
        compose("restart", "home-assistant")
        wait_for("Home Assistant API", lambda: bool(states()))
        wait_for("HA MQTT entities after restart", lambda: available(states()))
        time.sleep(3)
        after_restart = {"ha": states(), "simulator": simulator()}
        ha_restart = {"test_id": "home_assistant_restart_rehydrates_mqtt_state", "turns": [movie], "before_restart": before_restart, "after_restart": after_restart, "failures": []}
        assert_movie(movie, ha_restart["failures"])
        assert_coherent(after_restart["ha"], after_restart["simulator"], ha_restart["failures"])
        results.append(ha_restart)

        reset()
        conversation = f"m8-simulator-restart-{uuid4().hex[:10]}"
        movie = turn("启动观影模式，主灯调到20%，空调设为26度", conversation)
        before_stop = {"ha": states(), "simulator": simulator()}
        compose("stop", "device-simulator")
        time.sleep(3)
        offline_states = states()
        compose("start", "device-simulator")
        wait_for("device simulator API", lambda: bool(simulator()))
        wait_for("HA MQTT entities after simulator restart", lambda: available(states()))
        time.sleep(3)
        after_start = {"ha": states(), "simulator": simulator()}
        simulator_restart = {
            "test_id": "simulator_restart_preserves_sqlite_state",
            "turns": [movie],
            "before_stop": before_stop,
            "offline_states": offline_states,
            "after_start": after_start,
            "failures": [],
        }
        assert_movie(movie, simulator_restart["failures"])
        if not any(offline_states[name]["state"] == "unavailable" for name in ("light", "curtain", "climate", "tv")):
            simulator_restart["failures"].append("stopped simulator did not make any MQTT entity unavailable")
        if before_stop["simulator"] != after_start["simulator"]:
            simulator_restart["failures"].append("simulator state changed across restart instead of restoring SQLite state")
        assert_coherent(after_start["ha"], after_start["simulator"], simulator_restart["failures"])
        results.append(simulator_restart)

        reset()
        conversation = f"m8-broker-{uuid4().hex[:10]}"
        compose("stop", "mqtt")
        time.sleep(3)
        during_outage = turn("打开客厅电视", conversation)
        compose("start", "mqtt")
        wait_for("MQTT broker recovery", lambda: available(states()), timeout=60)
        time.sleep(3)
        after_recovery = turn("打开客厅电视", conversation)
        broker = {"test_id": "broker_outage_never_reports_false_success", "turns": [during_outage, after_recovery], "failures": []}
        outage_trace = during_outage["after"]["trace"]
        if outage_trace["state"] == "success" or "已打开" in during_outage["speech"]:
            broker["failures"].append("broker outage produced a false successful TV response")
        recovery_trace = after_recovery["after"]["trace"]
        if recovery_trace["state"] != "success" or after_recovery["after"]["ha"]["tv"]["state"] != "on":
            broker["failures"].append("TV did not recover automatically after broker restart")
        assert_coherent(after_recovery["after"]["ha"], after_recovery["after"]["simulator"], broker["failures"])
        results.append(broker)
    except Exception as error:
        results.append({"test_id": "acceptance_infrastructure", "failures": [str(error)]})
    finally:
        # Always leave the demo runnable even when a restart assertion fails.
        try:
            compose("start", "mqtt", "device-simulator", "home-assistant")
            wait_for("services after cleanup", lambda: bool(simulator()) and bool(states()), timeout=60)
            reset()
        except Exception as error:
            OPERATIONS.append({"cleanup_error": str(error)})

    for record in results:
        record["passed"] = not record["failures"]
    (REPORT / "results.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in results), encoding="utf-8"
    )
    (REPORT / "docker_operations.jsonl").write_text(
        "".join(json.dumps(operation, ensure_ascii=False, separators=(",", ":")) + "\n" for operation in OPERATIONS), encoding="utf-8"
    )
    turns = [
        turn_record
        for record in results
        for turn_record in [*record.get("turns", []), *(round_record.get("movie") for round_record in record.get("rounds", [])), *(round_record.get("leaving") for round_record in record.get("rounds", []))]
        if isinstance(turn_record, dict)
    ]
    (REPORT / "simulator_events.jsonl").write_text(
        "".join(
            json.dumps({"input": turn_record["input"], **event}, ensure_ascii=False, separators=(",", ":")) + "\n"
            for turn_record in turns
            for event in turn_record.get("after", {}).get("events", [])
        ),
        encoding="utf-8",
    )
    (REPORT / "ha_states.jsonl").write_text(
        "".join(
            json.dumps(
                {"input": turn_record["input"], "before": turn_record.get("before", {}).get("ha"), "after": turn_record.get("after", {}).get("ha"), "trace": turn_record.get("after", {}).get("trace")},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for turn_record in turns
        ),
        encoding="utf-8",
    )
    passed = sum(record["passed"] for record in results)
    (REPORT / "summary.md").write_text(
        "# M8 MQTT 系统稳定性验收\n\n"
        f"- 用例：{len(results)}\n- 通过：{passed}\n- 失败：{len(results) - passed}\n"
        "- 覆盖：三轮观影/离家循环、Home Assistant 重启、模拟器停启与 SQLite 恢复、Broker 中断与自动恢复。\n",
        encoding="utf-8",
    )
    print(f"M8 system stability: {passed}/{len(results)}")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
