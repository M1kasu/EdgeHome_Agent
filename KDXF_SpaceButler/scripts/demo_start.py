"""One-command demo readiness check: HA / ESPHome / LLM / Agent / devices.

现场演示前运行：全部 OK 才开始演示；任何一项失败给出明确修复提示。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ha_bootstrap import obtain_token  # noqa: E402
from spacebutler.esphome_registry import resolve_entity_id  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, hint: str = "") -> bool:
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f" — {hint}" if hint and not ok else ""))
    RESULTS.append((name, status, hint))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ha-url", default="http://127.0.0.1:12900")
    parser.add_argument("--workbench-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    auth_store = ROOT / "deployment" / "homeassistant" / ".storage" / "auth"

    print("SpaceButler Demo Readiness")
    print("==========================")

    # 1. Workbench
    workbench_ok = False
    agent_ready = False
    agent_mode = "unknown"
    llm_ok = False
    try:
        payload = json.loads(urlopen(f"{args.workbench_url}/api/agent", timeout=10).read().decode())
        health = json.loads(urlopen(f"{args.workbench_url}/api/health", timeout=10).read().decode())
        workbench_ok = health.get("workbench") == "ok"
        runtime = health.get("agent") if isinstance(health.get("agent"), dict) else {}
        agent_ready = bool(
            runtime.get("thread_alive")
            and runtime.get("recent_tick")
            and not runtime.get("fatal_error")
        )
        agent_mode = str(runtime.get("mode", "unknown"))
        llm_ok = bool(
            health.get("llm") == "ok"
            and payload.get("llm_available") is True
            and agent_mode == "llm"
        )
    except OSError:
        pass
    check(
        f"Workbench ({args.workbench_url})",
        workbench_ok,
        f"启动对应实例：python scripts\\run_workbench.py --port {args.workbench_url.rsplit(':', 1)[-1]}",
    )

    # 2. Home Assistant + token
    ha_ok = False
    token = None
    try:
        wait_hint = "启动 docker compose 并等待 HA 就绪"
        token = obtain_token(args.ha_url, auth_store)
        ha_ok = bool(token)
    except (RuntimeError, TimeoutError, OSError):
        pass
    check("Home Assistant", ha_ok, f"{wait_hint}；Token 缺失时删除 deployment/homeassistant/.storage 后重启")

    # 3. ESPHome devices via HA states（按 friendly_name 匹配，实体 ID 可能带 _2 后缀）
    esphome_ok = False
    devices_ok = {}
    if ha_ok and token:
        try:
            request = Request(f"{args.ha_url.rstrip('/')}/api/states", headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=15) as response:
                states = json.loads(response.read().decode())
            expected = {
                "light": "living_room_main_light",
                "curtain": "living_room_curtain",
                "climate": "living_room_ac",
                "temperature": "living_room_temperature_sensor",
                "presence": "living_room_presence_sensor",
            }
            all_ok = True
            by_entity = {str(item.get("entity_id", "")): item for item in states if isinstance(item, dict)}
            for name, device_id in expected.items():
                entity_id = resolve_entity_id(states, device_id)
                value = str(by_entity.get(entity_id, {}).get("state", "missing"))
                ok = value not in {"missing", "unknown", "unavailable"}
                devices_ok[name] = "ok" if ok else value
                all_ok = all_ok and ok
                check(f"ESPHome {name}", ok, f"{entity_id} = {value}；检查 Ubuntu 上对应的 *-node 容器")
            esphome_ok = all_ok
        except (OSError, RuntimeError) as error:
            print(f"  [FAIL] ESPHome states — {error}")
    else:
        print("  [SKIP] ESPHome devices（HA 不可用）")

    # 4. LLM
    check(
        "LLM model chain",
        llm_ok,
        "当前 Workbench 未连接可用模型——自然语言 Agent 会安全拒绝执行，请先启动本地模型",
    )

    # 5. Agent
    agent_ok = workbench_ok and agent_ready
    check("Agent runtime", agent_ok, "后台线程未存活、最近无 tick，或存在 fatal error")

    print("==========================")
    critical_ok = workbench_ok and ha_ok and esphome_ok and agent_ok
    if critical_ok and llm_ok and agent_mode == "llm":
        print("SpaceButler Demo Ready (Full AI Mode)")
    elif critical_ok:
        print("SpaceButler Demo Ready (Fallback Mode — LLM unavailable)")
    else:
        print("Demo NOT ready — fix FAIL items above")
    print(f"Workbench: {args.workbench_url}")
    return 0 if critical_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
