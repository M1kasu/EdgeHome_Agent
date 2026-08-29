"""Simulate a human override: REALLY change the light through Home Assistant.

智能体会从真实 HA 状态变化中检测到 HUMAN_OVERRIDE，记录偏好观测——
脚本不接触任何 Agent 内部接口。
"""

from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brightness", type=int, default=45)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    request = Request(
        f"{args.url}/api/agent/demo-override",
        data=json.dumps({"brightness": args.brightness}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    print(f"human override sent: light -> {args.brightness}% (through Home Assistant)")
    state = payload.get("devices", [])
    for device in state:
        if device.get("device_id") == "living_room_main_light":
            print(f"light now: {device.get('state')}")
    print("watch the workbench Agent Activity panel / scripts\\demo_status.py for detection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
