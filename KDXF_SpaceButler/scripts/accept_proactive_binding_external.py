"""External acceptance for dynamic proactive-rule device binding."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = os.getenv("SPACEBUTLER_WORKBENCH_URL", "http://127.0.0.1:8766").rstrip("/")
DEVICES = {
    "presence": {
        "device_id": "acceptance_study_presence",
        "type": "presence",
        "name": "验收书房存在传感器",
        "room": "study",
        "initial_state": {"occupied": True},
    },
    "contact": {
        "device_id": "acceptance_study_window",
        "type": "contact",
        "name": "验收书房窗户传感器",
        "room": "study",
        "initial_state": {"open": False},
    },
    "climate": {
        "device_id": "acceptance_study_ac",
        "type": "climate",
        "name": "验收书房空调",
        "room": "study",
        "initial_state": {"mode": "off", "temperature": 24, "current_temperature": 27},
    },
}
LIVING_ROOM_CONFIG = {
    "enabled": True,
    "room": "living_room",
    "presence_device_id": "living_room_presence_sensor",
    "contact_device_id": "living_room_window_sensor",
    "climate_device_id": "living_room_ac",
}


def main() -> int:
    cases: list[dict[str, Any]] = []
    _cleanup()
    try:
        created = {
            role: _request("POST", "/api/devices", definition).get("created", {})
            for role, definition in DEVICES.items()
        }
        configured = _request(
            "POST",
            "/api/proactive/config",
            {
                "enabled": True,
                "room": "study",
                "presence_device_id": DEVICES["presence"]["device_id"],
                "contact_device_id": DEVICES["contact"]["device_id"],
                "climate_device_id": DEVICES["climate"]["device_id"],
            },
        )
        proactive = configured["proactive"]
        cases.append(
            {
                "test_id": "dynamic_rule_binding_uses_registered_study_devices",
                "passed": (
                    proactive["config"]["room"] == "study"
                    and proactive["sources"]["presence"]["device_id"] == DEVICES["presence"]["device_id"]
                    and proactive["sources"]["contact"]["device_id"] == DEVICES["contact"]["device_id"]
                    and proactive["sources"]["climate"]["device_id"] == DEVICES["climate"]["device_id"]
                    and all(
                        proactive["sources"][role]["discovered"] is True
                        for role in ("presence", "contact", "climate")
                    )
                    and proactive["ready"] is True
                ),
                "created": created,
                "config": proactive["config"],
            }
        )

        _request("POST", "/api/preferences/clear", {})
        reset = _request(
            "POST",
            "/api/scene/reset",
            {
                "occupied": False,
                "window_open": True,
                "climate_mode": "cool",
                "unoccupied_minutes": 24,
            },
        )
        reset_rule = reset["proactive"]
        cases.append(
            {
                "test_id": "device_side_sensor_events_drive_rule_conditions",
                "passed": (
                    reset_rule["room"]["room"] == "study"
                    and reset_rule["sources"]["presence"]["state"]["occupied"] is False
                    and reset_rule["sources"]["contact"]["state"]["open"] is True
                    and reset_rule["sources"]["climate"]["state"]["mode"] == "cool"
                    and reset_rule["unoccupied_minutes"] >= 24
                    and reset_rule["trigger_ready"] is True
                    and all(condition["met"] is True for condition in reset_rule["conditions"])
                ),
                "conditions": reset_rule["conditions"],
            }
        )

        observed = _request("POST", "/api/observe", {})
        confirmed = _request("POST", "/api/message", {"text": "确认"})
        target_entity = f"climate.spacebutler_{DEVICES['climate']['device_id']}"
        cases.append(
            {
                "test_id": "dynamic_rule_executes_bound_climate_with_readback",
                "passed": (
                    observed["response"]["status"] == "needs_confirmation"
                    and observed["response"]["plan"]["actions"][0]["entity_id"] == target_entity
                    and confirmed["response"]["status"] == "executed"
                    and confirmed["response"]["report"]["verified"] is True
                    and confirmed["status"]["proactive"]["sources"]["climate"]["state"]["mode"] == "off"
                ),
                "observed": observed["response"],
                "confirmed": confirmed["response"],
            }
        )
    except Exception as error:
        cases.append(
            {
                "test_id": "dynamic_proactive_binding_exception",
                "passed": False,
                "failure": str(error),
            }
        )
    finally:
        _cleanup()

    passed = len(cases) == 3 and all(bool(case.get("passed")) for case in cases)
    print(
        json.dumps(
            {
                "acceptance": "PASS" if passed else "FAIL",
                "boundary": "runtime sensors -> MQTT Discovery -> HA REST snapshot -> dynamic rule binding -> climate execution/readback",
                "cases_passed": sum(bool(case.get("passed")) for case in cases),
                "cases_total": len(cases),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def _cleanup() -> None:
    try:
        _request("POST", "/api/proactive/config", LIVING_ROOM_CONFIG)
        _request(
            "POST",
            "/api/scene/reset",
            {
                "occupied": False,
                "window_open": True,
                "climate_mode": "cool",
                "unoccupied_minutes": 23,
            },
        )
    except Exception:
        pass
    for definition in DEVICES.values():
        try:
            _request("DELETE", f"/api/devices/{quote(str(definition['device_id']))}")
        except Exception:
            pass


def _request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if data is not None else {}
    with urlopen(
        Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method),
        timeout=40,
    ) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("workbench returned a non-object response")
    return decoded


if __name__ == "__main__":
    raise SystemExit(main())
