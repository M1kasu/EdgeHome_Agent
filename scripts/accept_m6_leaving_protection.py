"""Formal Conversation API acceptance for M6 MQTT-aware leaving protection."""

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
REPORT = ROOT / "reports" / "acceptance" / "m6_leaving_protection"
DEVICES = ("living_room_main_light", "living_room_curtain", "living_room_ac", "living_room_tv")
CHECKED = ("light.edgehome_living_room_main", "cover.edgehome_living_room_curtain", "climate.edgehome_living_room_ac", "switch.edgehome_living_room_tv", "switch.edgehome_router", "switch.edgehome_refrigerator", "input_boolean.edgehome_security_mode")


def request(method: str, url: str, body: dict[str, Any] | None = None, *, auth: bool = False) -> Any:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode('utf-8', errors='replace')}") from error


def state(entity_id: str) -> dict[str, Any]:
    return request("GET", f"{HA_URL}/api/states/{entity_id}", auth=True)


def states() -> dict[str, Any]:
    return {entity_id: state(entity_id) for entity_id in CHECKED}


def helper(entity_id: str, turn_on: bool) -> None:
    request("POST", f"{HA_URL}/api/services/input_boolean/{'turn_on' if turn_on else 'turn_off'}", {"entity_id": entity_id}, auth=True)


def reset() -> None:
    for device_id in DEVICES:
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/reset", {})
        request("POST", f"{SIM_URL}/admin/devices/{device_id}/fault", {"mode": "none", "delay_ms": 0})
    for entity_id in ("input_boolean.edgehome_router_power", "input_boolean.edgehome_refrigerator_power"):
        helper(entity_id, True)
    for entity_id in ("input_boolean.edgehome_window_open_state", "input_boolean.edgehome_living_room_presence_state", "input_boolean.edgehome_security_mode"):
        helper(entity_id, False)
    time.sleep(2)


def assist(text: str, test_id: str) -> dict[str, Any]:
    return request("POST", f"{HA_URL}/api/conversation/process", {"text": text, "language": "zh-Hans", "agent_id": AGENT_ID, "conversation_id": f"m6-{test_id}-{uuid4().hex[:12]}"}, auth=True)


def run_case(test_id: str, text: str) -> dict[str, Any]:
    before = states()
    response = assist(text, test_id)
    time.sleep(3)
    after = states()
    trace = state("sensor.edgehome_last_execution")
    return {"test_id": test_id, "input": text, "response": response, "speech": response["response"]["speech"]["plain"]["speech"], "before": before, "after": after, "trace": trace, "failures": []}


def assert_protected(record: dict[str, Any]) -> None:
    if record["after"]["switch.edgehome_router"]["state"] != "on" or record["after"]["switch.edgehome_refrigerator"]["state"] != "on":
        record["failures"].append("router or refrigerator was changed by leaving protection")


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    try:
        reset()
        assist("打开客厅电视", "seed-tv")
        normal = run_case("normal", "我要出门了")
        assert_protected(normal)
        if normal["trace"]["state"] != "success" or normal["after"]["light.edgehome_living_room_main"]["state"] != "off" or normal["after"]["switch.edgehome_living_room_tv"]["state"] != "off" or normal["after"]["climate.edgehome_living_room_ac"]["state"] != "off" or normal["after"]["cover.edgehome_living_room_curtain"]["attributes"].get("current_position") != 0 or normal["after"]["input_boolean.edgehome_security_mode"]["state"] != "on":
            normal["failures"].append("normal leaving state was not fully verified")
        results.append(normal)

        reset()
        helper("input_boolean.edgehome_window_open_state", True)
        window = run_case("window_open_blocks", "我要出门了")
        assert_protected(window)
        if "已阻断" not in window["speech"] or window["after"]["light.edgehome_living_room_main"]["state"] != "on":
            window["failures"].append("open window did not block all leaving actions")
        results.append(window)

        reset()
        helper("input_boolean.edgehome_living_room_presence_state", True)
        presence = run_case("presence_blocks", "我要出门了")
        assert_protected(presence)
        if "已阻断" not in presence["speech"] or presence["after"]["light.edgehome_living_room_main"]["state"] != "on":
            presence["failures"].append("presence did not block all leaving actions")
        results.append(presence)

        reset()
        assist("打开客厅电视", "seed-tv-reject")
        request("POST", f"{SIM_URL}/admin/devices/living_room_tv/fault", {"mode": "reject", "delay_ms": 0})
        rejected = run_case("tv_reject", "我要出门了")
        assert_protected(rejected)
        if rejected["trace"]["state"] == "success" or "已完成离家保护" in rejected["speech"]:
            rejected["failures"].append("TV rejection produced a false leaving success")
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
    (REPORT / "ha_states.jsonl").write_text("".join(json.dumps({"test_id": item["test_id"], "before": item.get("before"), "after": item.get("after"), "trace": item.get("trace")}, ensure_ascii=False, separators=(",", ":")) + "\n" for item in results), encoding="utf-8")
    passed = sum(item["passed"] for item in results)
    (REPORT / "summary.md").write_text(f"# M6 离家保护验收\n\n- 用例：{len(results)}\n- 通过：{passed}\n- 失败：{len(results) - passed}\n", encoding="utf-8")
    print(f"M6 leaving: {passed}/{len(results)}")
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
