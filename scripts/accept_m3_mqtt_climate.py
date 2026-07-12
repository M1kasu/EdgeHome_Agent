"""Black-box M3 MQTT Climate fault acceptance through Conversation API."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
HA, SIM = os.getenv("HA_URL", "http://127.0.0.1:8123"), os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090")
TOKEN = os.environ["HA_TOKEN"]
AGENT = "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome"
ENTITY, DEVICE = "climate.edgehome_living_room_ac", "living_room_ac"
REPORT = ROOT / "reports" / "acceptance" / "m3_mqtt_climate"


def call(method: str, url: str, body: dict | None = None, auth: bool = False) -> dict:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    with urlopen(Request(url, method=method, data=data, headers=headers), timeout=30) as response:
        return json.loads(response.read().decode())


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    results = []
    cases = (
        ("set_25", "none", True),
        ("reject", "reject", False),
        ("ack_without_change", "ack_without_state_change", False),
        ("invalid_state", "invalid_state", False),
        ("delay", "delay", False),
        ("offline", "offline", False),
    )
    for test_id, mode, expected_success in cases:
        call("POST", f"{SIM}/admin/devices/{DEVICE}/reset", {})
        call("POST", f"{SIM}/admin/devices/{DEVICE}/fault", {"mode": mode, "delay_ms": 0})
        response = call("POST", f"{HA}/api/conversation/process", {"text": "把客厅空调调到25度", "language": "zh-Hans", "agent_id": AGENT, "conversation_id": f"m3-{uuid4().hex}"}, True)
        time.sleep(6 if mode == "delay" else 2)
        ha = call("GET", f"{HA}/api/states/{ENTITY}", auth=True)
        trace = call("GET", f"{HA}/api/states/sensor.edgehome_last_execution", auth=True)
        device = call("GET", f"{SIM}/devices/{DEVICE}")
        success = trace["state"] == "success"
        failures = []
        if success != expected_success:
            failures.append(f"success={success}, expected={expected_success}")
        if expected_success and (ha.get("attributes", {}).get("temperature") != 25 or device["state"]["temperature"] != 25):
            failures.append("HA or SQLite target temperature mismatch")
        if not expected_success and success:
            failures.append("false success")
        results.append({"test_id": test_id, "input": "把客厅空调调到25度", "response": response, "ha": ha, "trace": trace, "device": device, "passed": not failures, "failures": failures})
        if failures:
            break
    call("POST", f"{SIM}/admin/devices/{DEVICE}/fault", {"mode": "none", "delay_ms": 0})
    (REPORT / "results.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in results), encoding="utf-8")
    passed = sum(item["passed"] for item in results)
    (REPORT / "summary.md").write_text(f"# M3 MQTT 空调验收\n\n- 用例：{len(results)}\n- 通过：{passed}\n- 失败：{len(results)-passed}\n", encoding="utf-8")
    print(f"M3 climate: {passed}/{len(cases)}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
