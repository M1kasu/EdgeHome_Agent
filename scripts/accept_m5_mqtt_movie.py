"""Formal Conversation API acceptance for the dynamic M5 MQTT movie scene."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
SIM_URL = os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090").rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
AGENT_ID = os.getenv("EDGEHOME_AGENT_ID", "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome")
REPORT = ROOT / "reports" / "acceptance" / "m5_mqtt_movie"
DEVICES = ("living_room_main_light", "living_room_curtain", "living_room_ac", "living_room_tv")
ENTITIES = {
    "light": "light.edgehome_living_room_main",
    "curtain": "cover.edgehome_living_room_curtain",
    "climate": "climate.edgehome_living_room_ac",
    "tv": "switch.edgehome_living_room_tv",
    "power": "sensor.edgehome_living_room_tv_power",
}


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
    return json.loads(raw) if raw else None


def state(entity_id: str) -> dict[str, Any]:
    return request("GET", f"{HA_URL}/api/states/{entity_id}", auth=True)


def states() -> dict[str, Any]:
    return {name: state(entity_id) for name, entity_id in ENTITIES.items()}


def simulator() -> dict[str, Any]:
    return {device_id: request("GET", f"{SIM_URL}/devices/{device_id}") for device_id in DEVICES}


def events() -> list[dict[str, Any]]:
    return sorted(request("GET", f"{SIM_URL}/events?limit=500")["events"], key=lambda event: event["id"])


def reset() -> None:
    for device_id in DEVICES:
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/reset", {})
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/fault", {"mode": "none", "delay_ms": 0})
    time.sleep(2)


def assist(text: str, test_id: str) -> dict[str, Any]:
    return request(
        "POST",
        f"{HA_URL}/api/conversation/process",
        {"text": text, "language": "zh-Hans", "agent_id": AGENT_ID, "conversation_id": f"m5-{test_id}-{uuid4().hex[:12]}"},
        auth=True,
    )


def speech(response: dict[str, Any]) -> str:
    return str(response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", ""))


def run_case(test_id: str, text: str) -> dict[str, Any]:
    floor = max((event["id"] for event in events()), default=0)
    before = {"ha": states(), "simulator": simulator()}
    response = assist(text, test_id)
    time.sleep(3)
    after = {"ha": states(), "simulator": simulator(), "trace": state("sensor.edgehome_last_execution"), "events": [event for event in events() if event["id"] > floor]}
    return {"test_id": test_id, "input": text, "response": response, "speech": speech(response), "before": before, "after": after, "failures": []}


def assert_success(record: dict[str, Any], *, expect_hvac_mode: bool = False) -> None:
    after = record["after"]
    trace = after["trace"]
    failures = record["failures"]
    if trace["state"] != "success" or trace["attributes"].get("route") != "mqtt_movie_scene":
        failures.append("scene did not report verified MQTT movie success")
    plan_targets = {step["entity_id"] for step in trace["attributes"].get("plan_steps", []) if step.get("entity_id")}
    expected = {ENTITIES["light"], ENTITIES["curtain"], ENTITIES["climate"], ENTITIES["tv"]}
    if not plan_targets.issubset(expected) or "light.edgehome_living_room_ambient" in plan_targets:
        failures.append("scene included a non-MQTT or template entity")
    ha, sim = after["ha"], after["simulator"]
    if ha["light"]["state"] != "on" or int(ha["light"]["attributes"].get("brightness", -1)) not in {50, 51}:
        failures.append("main light was not verified at 20%")
    if ha["curtain"]["attributes"].get("current_position") != 0 or sim["living_room_curtain"]["state"]["position"] != 0:
        failures.append("curtain was not closed through the simulator")
    if ha["tv"]["state"] != "on" or float(ha["power"]["state"]) != 95.0 or sim["living_room_tv"]["state"]["power_w"] != 95.0:
        failures.append("TV state or power did not come from the MQTT simulator")
    if ha["climate"]["state"] != "cool" or float(ha["climate"]["attributes"].get("temperature", -1)) != 26.0:
        failures.append("AC target or mode was not verified")
    if expect_hvac_mode and "set_hvac_mode" not in {step["tool"] for step in trace["attributes"].get("plan_steps", [])}:
        failures.append("scene did not restore the MQTT AC mode")


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        reset()
        normal = run_case("dynamic_success", "启动观影模式，主灯调到20%，空调设为26度")
        assert_success(normal)
        results.append(normal)

        idempotent = run_case("already_at_target", "启动观影模式，主灯调到20%，空调设为26度")
        trace_steps = idempotent["after"]["trace"]["attributes"].get("plan_steps", [])
        if idempotent["after"]["trace"]["state"] != "success" or [step["tool"] for step in trace_steps] != ["get_room_state"]:
            idempotent["failures"].append("scene did not remove already satisfied MQTT actions")
        if any(event["event_type"] == "command_received" for event in idempotent["after"]["events"]):
            idempotent["failures"].append("idempotent scene still published a device command")
        results.append(idempotent)

        reset()
        off = run_case("turn_ac_off", "关闭客厅空调")
        if off["after"]["trace"]["state"] != "success":
            off["failures"].append("could not prepare AC-off recovery case")
        results.append(off)
        recovery = run_case("restore_ac_mode", "启动观影模式，主灯调到20%，空调设为26度")
        assert_success(recovery, expect_hvac_mode=True)
        results.append(recovery)

        reset()
        request("POST", f"{SIM_URL}/admin/devices/living_room_curtain/fault", {"mode": "stuck", "delay_ms": 0})
        stuck = run_case("curtain_stuck_partial", "启动观影模式，主灯调到20%，空调设为26度")
        if stuck["after"]["trace"]["state"] != "partial_success" or "部分完成" not in stuck["speech"]:
            stuck["failures"].append("curtain stuck was not reported as partial completion")
        if stuck["after"]["ha"]["tv"]["state"] != "on" or float(stuck["after"]["ha"]["power"]["state"]) != 95.0:
            stuck["failures"].append("scene did not continue independently verified TV actions after a skippable curtain failure")
        results.append(stuck)

        reset()
        request("POST", f"{SIM_URL}/admin/devices/living_room_tv/fault", {"mode": "reject", "delay_ms": 0})
        rejected = run_case("tv_rejected", "启动观影模式，主灯调到20%，空调设为26度")
        if rejected["after"]["trace"]["state"] == "success" or "已完成" in rejected["speech"]:
            rejected["failures"].append("TV rejection produced a false scene success")
        results.append(rejected)
    except Exception as error:
        results.append({"test_id": "acceptance_infrastructure", "failures": [str(error)]})
    finally:
        try:
            reset()
        except Exception:
            pass
    for record in results:
        record["passed"] = not record["failures"]
    (REPORT / "results.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in results), encoding="utf-8")
    (REPORT / "simulator_events.jsonl").write_text("".join(json.dumps({"test_id": record["test_id"], **event}, ensure_ascii=False, separators=(",", ":")) + "\n" for record in results for event in record.get("after", {}).get("events", [])), encoding="utf-8")
    (REPORT / "ha_states.jsonl").write_text("".join(json.dumps({"test_id": record["test_id"], "before": record.get("before", {}).get("ha"), "after": record.get("after", {}).get("ha"), "trace": record.get("after", {}).get("trace")}, ensure_ascii=False, separators=(",", ":")) + "\n" for record in results), encoding="utf-8")
    passed = sum(record["passed"] for record in results)
    (REPORT / "summary.md").write_text(f"# M5 动态 MQTT 观影验收\n\n- 用例：{len(results)}\n- 通过：{passed}\n- 失败：{len(results) - passed}\n", encoding="utf-8")
    print(f"M5 MQTT movie: {passed}/{len(results)}")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
