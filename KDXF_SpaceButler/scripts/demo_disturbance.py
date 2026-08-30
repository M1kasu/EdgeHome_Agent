"""Simulate an environment disturbance: inject heat through the ESPHome firmware.

调用 ESPHome 暴露给 Home Assistant 的真实固件服务（demo_heat_injection），
室温环境模型被外部热源抬升 -> Agent 从状态轮询中检测到目标偏离 -> 自动重规划。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_bootstrap import obtain_token  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temperature", type=float, default=31.0)
    parser.add_argument("--ha-url", default="http://127.0.0.1:12900")
    args = parser.parse_args()
    token = obtain_token(args.ha_url, ROOT / "deployment" / "homeassistant" / ".storage" / "auth")
    request = Request(
        f"{args.ha_url.rstrip('/')}/api/services/esphome/"
        f"{os.environ.get('ESPHOME_HEAT_INJECTION_SERVICE', 'living_room_ac_demo_heat_injection')}",
        data=json.dumps({"target_temperature": args.temperature}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30):
        pass
    print(f"heat injected through ESPHome firmware service: room temperature -> {args.temperature}°C")
    print("the agent should detect goal deviation and replan automatically (no chat needed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
