"""Black-box M2 acceptance through Home Assistant Conversation API."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
HA = os.getenv("HA_URL", "http://127.0.0.1:8123")
SIM = os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090")
TOKEN = os.environ["HA_TOKEN"]
AGENT = os.getenv("EDGEHOME_AGENT_ID", "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome")
ENTITY = "cover.edgehome_living_room_curtain"
DEVICE = "living_room_curtain"
REPORT = ROOT / "reports" / "acceptance" / "m2_mqtt_curtain"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request(method: str, url: str, payload: dict | None = None, auth: bool = False) -> dict:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode()
    with urlopen(Request(url, data=data, method=method, headers=headers), timeout=30) as response:
        return json.loads(response.read().decode())


def ha_state() -> dict:
    return request("GET", f"{HA}/api/states/{ENTITY}", auth=True)


def trace() -> dict:
    return request("GET", f"{HA}/api/states/sensor.edgehome_last_execution", auth=True)


def device() -> dict:
    return request("GET", f"{SIM}/devices/{DEVICE}")


def reset() -> None:
    request("POST", f"{SIM}/admin/devices/{DEVICE}/reset", {})
    time.sleep(1)


def fault(mode: str, delay_ms: int = 0) -> None:
    request("POST", f"{SIM}/admin/devices/{DEVICE}/fault", {"mode": mode, "delay_ms": delay_ms})
    time.sleep(0.4)


def speak(text: str, conversation_id: str) -> dict:
    return request("POST", f"{HA}/api/conversation/process", {"text": text, "language": "zh-Hans", "agent_id": AGENT, "conversation_id": conversation_id}, auth=True)


def speech(response: dict) -> str:
    return str(response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", ""))


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    cases = [
        ("close", "关闭客厅窗帘", 0, "none", True),
        ("open", "打开客厅窗帘", 100, "none", True),
        ("position_35", "把客厅窗帘调到35%", 35, "none", True),
        ("stuck", "关闭客厅窗帘", 0, "stuck", False),
        ("reject", "关闭客厅窗帘", 0, "reject", False),
        ("ack_without_change", "关闭客厅窗帘", 0, "ack_without_state_change", False),
        ("delay", "关闭客厅窗帘", 0, "delay", False),
        ("offline", "关闭客厅窗帘", 0, "offline", False),
    ]
    for case_id, text, target, mode, should_succeed in cases:
        reset()
        fault(mode)
        conversation_id = f"m2-{case_id}-{uuid4().hex[:10]}"
        before = {"ha": ha_state(), "device": device()}
        response = speak(text, conversation_id)
        time.sleep(2.2 if mode != "delay" else 6)
        after = {"ha": ha_state(), "device": device(), "trace": trace()}
        success = after["trace"]["state"] == "success"
        position = after["ha"].get("attributes", {}).get("current_position")
        moving_seen = any(event["event_type"] == "state_changed" and event["payload"].get("state", {}).get("status") == "moving" for event in request("GET", f"{SIM}/events?limit=100")["events"] if event["device_id"] == DEVICE)
        failures: list[str] = []
        if success != should_succeed:
            failures.append(f"trace success={success}, expected {should_succeed}")
        if should_succeed and position != target:
            failures.append(f"HA position={position}, expected {target}")
        if should_succeed and after["device"]["state"]["position"] != target:
            failures.append("SQLite-owned device state did not reach target")
        if case_id in {"close", "open", "position_35"} and not moving_seen:
            failures.append("no moving state recorded")
        if case_id == "stuck" and after["device"]["state"]["status"] != "stuck":
            failures.append("stuck fault did not preserve partial state")
        if not should_succeed and success:
            failures.append("false success")
        record = {"test_id": case_id, "timestamp": now(), "input": text, "speech": speech(response), "before": before, "after": after, "passed": not failures, "failures": failures}
        results.append(record)
        if failures:
            break
    fault("none")
    (REPORT / "results.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in results), encoding="utf-8")
    passed = sum(item["passed"] for item in results)
    (REPORT / "summary.md").write_text(f"# M2 MQTT 窗帘验收\n\n- 用例：{len(results)}\n- 通过：{passed}\n- 失败：{len(results) - passed}\n- 状态：`{'INTEGRATION_VERIFIED' if passed == len(cases) else 'REJECTED'}`\n", encoding="utf-8")
    print(f"M2 curtain: {passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
