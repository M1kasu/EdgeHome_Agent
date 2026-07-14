"""Black-box acceptance for the M4 Docker MQTT TV and power-meter chain.

The runner uses only the Home Assistant Conversation API plus the simulator's
test-only admin API.  It persists HA state, simulator events, and final
Conversation replies so a reviewer can independently inspect every result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
SIM_URL = os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090").rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
AGENT_ID = os.getenv("EDGEHOME_AGENT_ID", "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome")
ENTITY_ID = "switch.edgehome_living_room_tv"
POWER_ENTITY_ID = "sensor.edgehome_living_room_tv_power"
FEEDBACK_ENTITY_ID = "sensor.edgehome_living_room_tv_feedback"
DEVICE_ID = "living_room_tv"
REPORT = ROOT / "reports" / "acceptance" / "m4_mqtt_tv"
COMPOSE = ROOT / "deployment" / "docker-compose.yml"


def request(method: str, url: str, payload: dict[str, Any] | None = None, *, auth: bool = False, timeout: int = 90) -> Any:
    """Make an API request and retain useful HTTP errors in the acceptance log."""
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} {url}: {detail}") from error
    except (OSError, TimeoutError, URLError) as error:
        # A restarted Home Assistant can accept TCP and then close one request
        # before the API becomes ready.  The caller's bounded polling handles it.
        raise RuntimeError(f"HTTP transport unavailable for {url}: {error}") from error
    return json.loads(raw) if raw else None


def state(entity_id: str) -> dict[str, Any] | None:
    try:
        return request("GET", f"{HA_URL}/api/states/{entity_id}", auth=True)
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None
        raise


def device() -> dict[str, Any]:
    return request("GET", f"{SIM_URL}/devices/{DEVICE_ID}")


def events() -> list[dict[str, Any]]:
    return sorted(request("GET", f"{SIM_URL}/events?limit=500")["events"], key=lambda item: item["id"])


def reset() -> None:
    request("POST", f"{SIM_URL}/admin/devices/{DEVICE_ID}/reset", {})
    wait_for(lambda: state(ENTITY_ID) is not None and state(ENTITY_ID)["state"] != "unavailable", 20, "TV reset availability")


def fault(mode: str, delay_ms: int = 0) -> None:
    request("POST", f"{SIM_URL}/admin/devices/{DEVICE_ID}/fault", {"mode": mode, "delay_ms": delay_ms})


def assist(text: str, test_id: str) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    response = request(
        "POST",
        f"{HA_URL}/api/conversation/process",
        {"text": text, "language": "zh-Hans", "agent_id": AGENT_ID, "conversation_id": f"m4-{test_id}-{uuid4().hex[:12]}"},
        auth=True,
    )
    return response, round(time.monotonic() - started, 3)


def speech(response: dict[str, Any]) -> str:
    return str(response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", ""))


def wait_for(predicate: Any, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except RuntimeError:
            pass
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {label}")


def compose(*arguments: str) -> None:
    completed = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"docker compose {' '.join(arguments)} failed: {completed.stdout}\n{completed.stderr}")


def write_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def run_case(test_id: str, text: str, *, expected_state: str | None, expected_watts: float | None, should_succeed: bool, expected_feedback: str | None = None, wait_seconds: float = 1.0) -> dict[str, Any]:
    """Execute one isolated Conversation turn and collect the complete evidence chain."""
    event_floor = max((item["id"] for item in events()), default=0)
    before = {"switch": state(ENTITY_ID), "power": state(POWER_ENTITY_ID), "device": device()}
    response, elapsed = assist(text, test_id)
    time.sleep(wait_seconds)
    after = {
        "switch": state(ENTITY_ID),
        "power": state(POWER_ENTITY_ID),
        "feedback": state(FEEDBACK_ENTITY_ID),
        "trace": state("sensor.edgehome_last_execution"),
        "device": device(),
        "events": [item for item in events() if item["id"] > event_floor],
    }
    trace_state = after["trace"]["state"] if after["trace"] else None
    failures: list[str] = []
    if (trace_state == "success") != should_succeed:
        failures.append(f"trace={trace_state}, expected success={should_succeed}")
    if should_succeed:
        if after["switch"] is None or after["switch"]["state"] != expected_state:
            failures.append(f"HA TV state is not {expected_state}")
        watts = after["power"].get("state") if after["power"] else None
        if watts is None or abs(float(watts) - float(expected_watts)) > 0.01:
            failures.append(f"HA power is not {expected_watts} W")
        if after["device"]["state"] != {"power": expected_state.upper(), "power_w": expected_watts}:
            failures.append("SQLite-owned TV state differs from expected state")
        kinds = {item["event_type"] for item in after["events"]}
        if not {"command_received", "state_changed"}.issubset(kinds):
            failures.append("simulator event chain lacks command_received/state_changed")
    else:
        if trace_state == "success" or "已完成" in speech(response) or "已打开" in speech(response) or "已关闭" in speech(response):
            failures.append("false success in trace or agent reply")
        if expected_feedback is not None:
            feedback_state = after["feedback"].get("state") if after["feedback"] else None
            if feedback_state != expected_feedback:
                failures.append(f"feedback={feedback_state}, expected {expected_feedback}")
        if expected_feedback == "invalid_state" and (
            after["switch"] is None
            or after["power"] is None
            or after["switch"]["state"] != before["switch"]["state"]
            or after["power"]["state"] != before["power"]["state"]
        ):
            failures.append("invalid MQTT state changed a valid HA switch or power state")
        if test_id == "fault_delay_timeout" and elapsed < 2.8:
            failures.append("executor returned before its feedback timeout")
        if test_id == "fault_offline" and (after["switch"] is None or after["switch"]["state"] != "unavailable"):
            failures.append("offline TV did not remain unavailable in Home Assistant")
    record = {
        "test_id": test_id,
        "input": text,
        "elapsed_seconds": elapsed,
        "response": response,
        "agent_speech": speech(response),
        "before": before,
        "after": after,
        "passed": not failures,
        "failures": failures,
    }
    return record


def restart_records(results: list[dict[str, Any]]) -> None:
    """Verify that HA, simulator, and broker each recover device-owned state."""
    reset()
    initial = run_case("restart_seed", "打开客厅电视", expected_state="on", expected_watts=95.0, should_succeed=True)
    results.append(initial)
    if not initial["passed"]:
        return
    before = device()
    compose("restart", "home-assistant")
    wait_for(lambda: state(ENTITY_ID) is not None and state(ENTITY_ID)["state"] == "on" and float(state(POWER_ENTITY_ID)["state"]) == 95.0, 120, "Home Assistant MQTT TV recovery")
    ha_restart = {"test_id": "restart_home_assistant", "before_device": before, "after_device": device(), "after_switch": state(ENTITY_ID), "after_power": state(POWER_ENTITY_ID)}
    ha_restart["passed"] = ha_restart["before_device"]["state"] == ha_restart["after_device"]["state"]
    ha_restart["failures"] = [] if ha_restart["passed"] else ["HA restart changed simulator-owned state"]
    results.append(ha_restart)

    before = device()
    compose("stop", "device-simulator")
    wait_for(lambda: state(ENTITY_ID) is not None and state(ENTITY_ID)["state"] == "unavailable", 30, "simulator unavailable")
    compose("up", "-d", "device-simulator")
    wait_for(lambda: state(ENTITY_ID) is not None and state(ENTITY_ID)["state"] == "on" and float(state(POWER_ENTITY_ID)["state"]) == 95.0, 45, "simulator SQLite recovery")
    sim_restart = {"test_id": "restart_device_simulator", "before_device": before, "after_device": device(), "after_switch": state(ENTITY_ID), "after_power": state(POWER_ENTITY_ID)}
    sim_restart["passed"] = sim_restart["before_device"]["state"] == sim_restart["after_device"]["state"]
    sim_restart["failures"] = [] if sim_restart["passed"] else ["simulator restart did not recover SQLite state"]
    results.append(sim_restart)

    before = device()
    compose("stop", "mqtt")
    time.sleep(2)
    broker_failure = run_case("broker_control_failure", "关闭客厅电视", expected_state=None, expected_watts=None, should_succeed=False, wait_seconds=0.2)
    results.append(broker_failure)
    compose("up", "-d", "mqtt")
    wait_for(lambda: state(ENTITY_ID) is not None and state(ENTITY_ID)["state"] == "on" and float(state(POWER_ENTITY_ID)["state"]) == 95.0, 60, "broker MQTT recovery")
    broker_restart = {"test_id": "restart_mosquitto", "before_device": before, "after_device": device(), "after_switch": state(ENTITY_ID), "after_power": state(POWER_ENTITY_ID)}
    broker_restart["passed"] = broker_restart["before_device"]["state"] == broker_restart["after_device"]["state"]
    broker_restart["failures"] = [] if broker_restart["passed"] else ["broker restart changed simulator-owned state"]
    results.append(broker_restart)


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    result_path = REPORT / "results.jsonl"
    event_path = REPORT / "simulator_events.jsonl"
    ha_path = REPORT / "ha_states.jsonl"
    for path in (result_path, event_path, ha_path):
        path.write_text("", encoding="utf-8")
    results: list[dict[str, Any]] = []
    infrastructure_error: Exception | None = None
    cases = (
        ("normal_on", "打开客厅电视", "on", 95.0, "none", None, 1.0),
        ("normal_off", "关闭客厅电视", "off", 1.5, "none", None, 1.0),
        ("fault_reject", "打开客厅电视", None, None, "reject", "rejected", 1.0),
        ("fault_ack_without_change", "打开客厅电视", None, None, "ack_without_state_change", "ack_without_state_change", 1.0),
        ("fault_invalid_state", "打开客厅电视", None, None, "invalid_state", "invalid_state", 1.0),
        ("fault_delay_timeout", "打开客厅电视", None, None, "delay", None, 5.5),
    )
    try:
        for test_id, text, expected_state, expected_watts, mode, feedback, wait_seconds in cases:
            reset()
            fault(mode, 4_500 if mode == "delay" else 0)
            record = run_case(test_id, text, expected_state=expected_state, expected_watts=expected_watts, should_succeed=mode == "none", expected_feedback=feedback, wait_seconds=wait_seconds)
            results.append(record)
            if not record["passed"]:
                break
        if all(record["passed"] for record in results):
            reset()
            fault("offline")
            wait_for(lambda: state(ENTITY_ID) is not None and state(ENTITY_ID)["state"] == "unavailable", 20, "offline availability")
            results.append(run_case("fault_offline", "打开客厅电视", expected_state=None, expected_watts=None, should_succeed=False, wait_seconds=0.2))
        if all(record["passed"] for record in results):
            reset()
            fault("none")
            restart_records(results)
    except Exception as error:  # Save a reviewer-visible failure instead of losing earlier evidence.
        infrastructure_error = error
        results.append({"test_id": "acceptance_infrastructure", "passed": False, "failures": [str(error)]})
    finally:
        try:
            fault("none")
        except RuntimeError:
            pass
    for record in results:
        write_jsonl(result_path, record)
        after = record.get("after", {})
        for event in after.get("events", []):
            write_jsonl(event_path, {"test_id": record["test_id"], **event})
        write_jsonl(ha_path, {"test_id": record["test_id"], "before": record.get("before"), "after": after})
    passed = sum(bool(record.get("passed")) for record in results)
    summary = "\n".join(("# M4 MQTT 电视/智能插座验收", "", f"- 用例：{len(results)}", f"- 通过：{passed}", f"- 失败：{len(results) - passed}", "- 状态：`INTEGRATION_VERIFIED`" if results and passed == len(results) else "- 状态：`REJECTED`", ""))
    (REPORT / "summary.md").write_text(summary, encoding="utf-8")
    print(f"M4 MQTT TV: {passed}/{len(results)}")
    return 0 if infrastructure_error is None and results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
