"""External HTTP acceptance for the SpaceButler control workbench."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.request import Request, urlopen


BASE_URL = os.getenv("SPACEBUTLER_WORKBENCH_URL", "http://127.0.0.1:8766")


def main() -> int:
    clear = request("POST", "/api/preferences/clear", {})
    reset = request("POST", "/api/scene/reset", {"unoccupied_minutes": 23})
    observed = request("POST", "/api/observe", {"unoccupied_minutes": 23})
    confirmed = request("POST", "/api/message", {"text": "确认"})
    learned = request("POST", "/api/message", {"text": "以后这种情况直接执行"})
    request("POST", "/api/scene/reset", {"unoccupied_minutes": 23})
    second = request("POST", "/api/observe", {"unoccupied_minutes": 23})
    events = request("GET", "/api/events")
    passed = all(
        (
            clear["preferences"] == [],
            reset["climate"]["state"] == "cool",
            observed["response"]["status"] == "needs_confirmation",
            confirmed["response"]["status"] == "executed",
            confirmed["response"]["report"]["verified"] is True,
            learned["response"]["learned"] == "learned_auto_execute",
            second["response"]["status"] == "executed",
            second["response"]["report"]["verified"] is True,
            isinstance(events.get("events"), list),
            len(events.get("events", [])) > 0,
        )
    )
    print(
        json.dumps(
            {
                "acceptance": "PASS" if passed else "FAIL",
                "boundary": "external_http_client -> workbench_api -> agent -> HA_MQTT_SQLite",
                "first_status": observed["response"]["status"],
                "confirmed_status": confirmed["response"]["status"],
                "learned": learned["response"]["learned"],
                "second_status": second["response"]["status"],
                "event_count": len(events.get("events", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    with urlopen(Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method), timeout=30) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("workbench returned a non-object response")
    return decoded


if __name__ == "__main__":
    raise SystemExit(main())
