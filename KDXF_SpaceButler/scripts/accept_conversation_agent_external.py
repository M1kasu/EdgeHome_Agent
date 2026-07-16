"""External acceptance for conversational planning and verified task execution."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.getenv("SPACEBUTLER_WORKBENCH_URL", "http://127.0.0.1:8765").rstrip("/")


def main() -> int:
    checks: list[dict[str, object]] = []
    try:
        _request("POST", "/api/chat/clear", {})
        _fault("bedroom_reading_light", "none")
        _fault("living_room_tv", "none")
        _fault("living_room_curtain", "none")
        _fault("living_room_ac", "none")
        _control("bedroom_reading_light", {"power": "off"})
        _control("living_room_tv", {"power": "on"})
        _control("living_room_curtain", {"position": 100})
        _control("living_room_ac", {"mode": "cool", "temperature": 24})

        planned = _request(
            "POST",
            "/api/chat",
            {
                "text": "把卧室阅读灯打开到55%，关闭客厅电视，把客厅窗帘调到30%，空调设为25度制冷",
            },
        )
        plan = planned["active_plan"]
        step_ids = {step.get("device_id") for step in plan["steps"]}
        checks.append(
            _check(
                "natural_language_is_decomposed_against_live_registry",
                plan["intent"] == "device_control"
                and plan["status"] == "awaiting_confirmation"
                and len(plan["steps"]) == 4
                and step_ids
                == {
                    "bedroom_reading_light",
                    "living_room_tv",
                    "living_room_curtain",
                    "living_room_ac",
                }
                and plan["route"]
                in {"edge_llm", "edge_llm_grounded", "deterministic_repair", "deterministic_fallback"},
                {
                    "intent": plan["intent"],
                    "status": plan["status"],
                    "route": plan["route"],
                    "steps": [
                        {
                            "device_id": step.get("device_id"),
                            "action": step.get("action"),
                            "value": step.get("value"),
                        }
                        for step in plan["steps"]
                    ],
                },
            )
        )

        confirmed = _request("POST", "/api/chat/confirm", {})
        completed = confirmed["active_plan"]
        devices = _device_map(_request("GET", "/api/devices"))
        checks.append(
            _check(
                "confirmed_plan_executes_through_ha_mqtt_and_reads_back",
                completed["status"] == "completed"
                and all(step["status"] == "success" and step["verified"] is True for step in completed["steps"])
                and devices["bedroom_reading_light"]["state"]["power"] == "ON"
                and abs(round(devices["bedroom_reading_light"]["state"]["brightness"] * 100 / 255) - 55) <= 1
                and devices["living_room_tv"]["state"]["power"] == "OFF"
                and devices["living_room_curtain"]["state"]["position"] == 30
                and devices["living_room_ac"]["state"]["mode"] == "cool"
                and float(devices["living_room_ac"]["state"]["temperature"]) == 25,
                {
                    "plan_status": completed["status"],
                    "step_statuses": [step["status"] for step in completed["steps"]],
                    "states": {
                        device_id: devices[device_id]["state"]
                        for device_id in (
                            "bedroom_reading_light",
                            "living_room_tv",
                            "living_room_curtain",
                            "living_room_ac",
                        )
                    },
                },
            )
        )

        _control("living_room_main_light", {"power": "on", "brightness": 80})
        _fault("living_room_tv", "reject")
        _request(
            "POST",
            "/api/chat",
            {"text": "打开客厅电视，然后关闭客厅主灯"},
        )
        failed = _request("POST", "/api/chat/confirm", {})["active_plan"]
        devices = _device_map(_request("GET", "/api/devices"))
        checks.append(
            _check(
                "device_rejection_is_not_reported_as_success",
                failed["status"] == "failed"
                and failed["steps"][0]["status"] == "failed"
                and failed["steps"][0]["verified"] is False
                and failed["steps"][1]["status"] == "skipped"
                and devices["living_room_main_light"]["state"]["power"] == "ON",
                {
                    "plan_status": failed["status"],
                    "steps": [
                        {
                            "device_id": step["device_id"],
                            "status": step["status"],
                            "error": step["error"],
                        }
                        for step in failed["steps"]
                    ],
                    "main_light": devices["living_room_main_light"]["state"],
                },
            )
        )
    except Exception as error:
        checks.append(_check("conversation_agent_exception", False, {"error": str(error)}))
    finally:
        try:
            _fault("living_room_tv", "none")
        except Exception:
            pass

    report = {
        "suite": "conversation_agent_external",
        "workbench_url": BASE_URL,
        "passed": all(bool(item["passed"]) for item in checks),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def _request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    try:
        with urlopen(request, timeout=90) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned HTTP {error.code}: {detail}") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{method} {path} returned a non-object")
    return decoded


def _control(device_id: str, payload: dict[str, object]) -> None:
    _request("POST", f"/api/devices/{device_id}/control", payload)


def _fault(device_id: str, mode: str) -> None:
    _request("POST", f"/api/devices/{device_id}/fault", {"mode": mode, "delay_ms": 0})


def _device_map(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(item["device_id"]): item
        for item in payload.get("devices", [])
        if isinstance(item, dict)
    }


def _check(name: str, passed: bool, evidence: dict[str, object]) -> dict[str, object]:
    return {"name": name, "passed": passed, "evidence": evidence}


if __name__ == "__main__":
    raise SystemExit(main())
