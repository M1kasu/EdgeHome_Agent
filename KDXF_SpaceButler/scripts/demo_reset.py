"""Reset the living-room demo to the delivery-plan initial state.

初始状态（慧家中枢 9 月 1 日交付版）：
- 客厅主灯：开启，80%
- 电动窗帘：完全打开（100%）
- 客厅空调：关闭（目标温度 26°C）
- 环境温度：回到 30.2°C（重启 ac 节点，环境模型 initial_value=30.2）
- 人体存在：客厅有人（固件恒定上报）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_bootstrap import obtain_token, wait_until_ready  # noqa: E402
from urllib.request import Request, urlopen  # noqa: E402

ESPHOME_SERVICES = (
    "spacebutler-esphome-light",
    "spacebutler-esphome-curtain",
    "spacebutler-esphome-ac",
    "spacebutler-esphome-presence",
)


def call_service(base_url: str, token: str, domain: str, service: str, data: dict[str, object]) -> None:
    request = Request(
        f"{base_url.rstrip('/')}/api/services/{domain}/{service}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15):
        pass


def wait_entity(base_url: str, token: str, entity_id: str, predicate, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(
                Request(
                    f"{base_url.rstrip('/')}/api/states/{entity_id}",
                    headers={"Authorization": f"Bearer {token}"},
                ),
                timeout=10,
            ) as response:
                state = json.loads(response.read().decode("utf-8"))
        except OSError:
            state = {}
        if predicate(state):
            return
        time.sleep(1)
    raise TimeoutError(f"{entity_id} did not reach the expected state")


def resolve_entity(base_url: str, token: str, friendly_name: str, domain: str) -> str:
    """按 friendly_name + domain 解析实体 ID（注册表残留旧条目时可能带 _2 后缀）。"""
    request = Request(
        f"{base_url.rstrip('/')}/api/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=15) as response:
        states = json.loads(response.read().decode("utf-8"))
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        attributes = item.get("attributes") or {}
        if (
            entity_id.startswith(f"{domain}.")
            and str(attributes.get("friendly_name", "")) == friendly_name
        ):
            return entity_id
    raise RuntimeError(f"entity not found for {domain} '{friendly_name}'")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://127.0.0.1:12900")
    parser.add_argument("--skip-container-restart", action="store_true")
    args = parser.parse_args()
    auth_store = ROOT / "deployment" / "homeassistant" / ".storage" / "auth"

    wait_until_ready(args.ha_url, timeout_seconds=120)
    token = obtain_token(args.ha_url, auth_store)

    if not args.skip_container_restart:
        subprocess.run(
            ["docker", "compose", "-f", str(ROOT / "deployment" / "docker-compose.yml"), "restart", *ESPHOME_SERVICES],
            check=False,
            capture_output=True,
        )
        time.sleep(20)

    light = resolve_entity(args.ha_url, token, "SpaceButler Living Room Main", "light")
    curtain = resolve_entity(args.ha_url, token, "SpaceButler Living Room Curtain", "cover")
    climate = resolve_entity(args.ha_url, token, "SpaceButler Living Room AC", "climate")

    call_service(args.ha_url, token, "light", "turn_on", {"entity_id": light, "brightness_pct": 80})
    call_service(args.ha_url, token, "cover", "set_cover_position", {"entity_id": curtain, "position": 100})

    wait_entity(
        args.ha_url,
        token,
        light,
        lambda state: state.get("state") == "on",
        30,
    )
    wait_entity(
        args.ha_url,
        token,
        curtain,
        lambda state: abs(float(state.get("attributes", {}).get("current_position", -1)) - 100) < 1,
        40,
    )

    # ac 节点重启后固件会恢复 Demo preset（cool），关机命令可能被启动过程覆盖，重试直至稳定。
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        call_service(args.ha_url, token, "climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "off"})
        try:
            wait_entity(args.ha_url, token, climate, lambda state: state.get("state") == "off", 12)
            break
        except TimeoutError:
            continue
    else:
        raise TimeoutError(f"{climate} did not stay off")
    print("demo reset complete: light 80% on, curtain 100% open, climate off, presence on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
