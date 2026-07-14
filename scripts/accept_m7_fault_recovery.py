"""Formal Conversation API acceptance for M7 cross-device fault recovery.

The test intentionally keeps one conversation ID across a movie request and its
restore request.  It therefore proves that a stored recovery point survives a
real device rejection, is never reported as successful prematurely, and can be
retried after the device returns to service.
"""

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
REPORT = ROOT / "reports" / "acceptance" / "m7_fault_recovery"
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
    return sorted(request("GET", f"{SIM_URL}/events?limit=500")["events"], key=lambda item: item["id"])


def reset() -> None:
    for device_id in DEVICES:
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/reset", {})
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/fault", {"mode": "none", "delay_ms": 0})
    time.sleep(3)


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
    time.sleep(3)
    after = {
        "ha": states(),
        "simulator": simulator(),
        "trace": state("sensor.edgehome_last_execution"),
        "events": [event for event in events() if event["id"] > floor],
    }
    return {"input": text, "response": response, "speech": speech(response), "before": before, "after": after}


def assert_restored(turn_record: dict[str, Any], failures: list[str]) -> None:
    trace = turn_record["after"]["trace"]
    ha = turn_record["after"]["ha"]
    sim = turn_record["after"]["simulator"]
    expected_entities = set(ENTITIES.values()) - {ENTITIES["power"]}
    targets = {step["entity_id"] for step in trace["attributes"].get("plan_steps", []) if step.get("entity_id")}
    if trace["state"] != "success" or trace["attributes"].get("route") != "scenario_restore":
        failures.append("restore did not publish a verified scenario_restore success trace")
    if not targets or not targets.issubset(expected_entities):
        failures.append("restore included an entity outside the captured MQTT movie devices")
    if ha["light"]["state"] != "on" or int(ha["light"]["attributes"].get("brightness", -1)) != 204:
        failures.append("main light was not restored to its captured brightness")
    if ha["curtain"]["attributes"].get("current_position") != 100 or sim["living_room_curtain"]["state"]["position"] != 100:
        failures.append("curtain was not restored to its captured position")
    if ha["climate"]["state"] != "cool" or float(ha["climate"]["attributes"].get("temperature", -1)) != 24.0:
        failures.append("AC mode or target temperature was not restored")
    if ha["tv"]["state"] != "off" or float(ha["power"]["state"]) != 1.5 or sim["living_room_tv"]["state"]["power_w"] != 1.5:
        failures.append("TV or simulator-owned power state was not restored")


def assert_movie_success(turn_record: dict[str, Any], failures: list[str]) -> None:
    trace = turn_record["after"]["trace"]
    if trace["state"] != "success" or trace["attributes"].get("route") != "mqtt_movie_scene":
        failures.append("movie scene did not create a verified recovery point")


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        reset()
        conversation = f"m7-restore-{uuid4().hex[:12]}"
        normal_scene = turn("启动观影模式，主灯调到20%，空调设为26度", conversation)
        normal_restore = turn("恢复观影前状态", conversation)
        normal = {"test_id": "full_cross_device_restore", "turns": [normal_scene, normal_restore], "failures": []}
        assert_movie_success(normal_scene, normal["failures"])
        assert_restored(normal_restore, normal["failures"])
        results.append(normal)

        reset()
        conversation = f"m7-retry-{uuid4().hex[:12]}"
        retry_scene = turn("启动观影模式，主灯调到20%，空调设为26度", conversation)
        request("POST", f"{SIM_URL}/admin/devices/living_room_main_light/fault", {"mode": "reject", "delay_ms": 0})
        rejected_restore = turn("恢复观影", conversation)
        request("POST", f"{SIM_URL}/admin/devices/living_room_main_light/fault", {"mode": "none", "delay_ms": 0})
        recovered_restore = turn("恢复观影场景", conversation)
        retry = {"test_id": "rejection_keeps_recovery_point_for_retry", "turns": [retry_scene, rejected_restore, recovered_restore], "failures": []}
        assert_movie_success(retry_scene, retry["failures"])
        rejected_trace = rejected_restore["after"]["trace"]
        if rejected_trace["state"] == "success" or "失败" not in rejected_restore["speech"] or "恢复点已保留" not in rejected_restore["speech"]:
            retry["failures"].append("rejected restoration produced a false success or discarded its recovery point")
        if not any(event["event_type"] == "command_rejected" for event in rejected_restore["after"]["events"]):
            retry["failures"].append("rejected restoration has no simulator command_rejected evidence")
        assert_restored(recovered_restore, retry["failures"])
        results.append(retry)

        reset()
        conversation = f"m7-failed-scene-{uuid4().hex[:12]}"
        request("POST", f"{SIM_URL}/admin/devices/living_room_tv/fault", {"mode": "reject", "delay_ms": 0})
        failed_scene = turn("启动观影模式，主灯调到20%，空调设为26度", conversation)
        request("POST", f"{SIM_URL}/admin/devices/living_room_tv/fault", {"mode": "none", "delay_ms": 0})
        absent_restore = turn("恢复观影前状态", conversation)
        cleared = {"test_id": "failed_scene_has_no_stale_restore_point", "turns": [failed_scene, absent_restore], "failures": []}
        if failed_scene["after"]["trace"]["state"] == "success" or "已完成" in failed_scene["speech"]:
            cleared["failures"].append("rejected movie scene produced a false success")
        if "没有可恢复" not in absent_restore["speech"] or absent_restore["after"]["trace"]["attributes"].get("route") != "scenario_restore":
            cleared["failures"].append("failed movie scene left a stale recovery point")
        results.append(cleared)
    except Exception as error:
        results.append({"test_id": "acceptance_infrastructure", "turns": [], "failures": [str(error)]})
    finally:
        try:
            reset()
        except Exception:
            pass

    for record in results:
        record["passed"] = not record["failures"]
    (REPORT / "results.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in results), encoding="utf-8"
    )
    (REPORT / "simulator_events.jsonl").write_text(
        "".join(
            json.dumps({"test_id": record["test_id"], "input": turn_record["input"], **event}, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in results
            for turn_record in record.get("turns", [])
            for event in turn_record.get("after", {}).get("events", [])
        ),
        encoding="utf-8",
    )
    (REPORT / "ha_states.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "test_id": record["test_id"],
                    "input": turn_record["input"],
                    "before": turn_record.get("before", {}).get("ha"),
                    "after": turn_record.get("after", {}).get("ha"),
                    "trace": turn_record.get("after", {}).get("trace"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for record in results
            for turn_record in record.get("turns", [])
        ),
        encoding="utf-8",
    )
    passed = sum(record["passed"] for record in results)
    (REPORT / "summary.md").write_text(
        "# M7 跨设备故障恢复验收\n\n"
        f"- 用例：{len(results)}\n- 通过：{passed}\n- 失败：{len(results) - passed}\n"
        "- 恢复点：仅在观影场景完整回读成功后保留；恢复失败保留，场景失败则清除。\n",
        encoding="utf-8",
    )
    print(f"M7 fault recovery: {passed}/{len(results)}")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
