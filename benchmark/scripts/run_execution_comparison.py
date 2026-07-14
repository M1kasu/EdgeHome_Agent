"""在隔离的真实 Home Assistant Core 中比较无验证与回读验证执行。"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry

from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.execution.executor import FixedPlanExecutor
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, TaskStatus, VerificationRule


async def run() -> list[dict[str, object]]:
    hass = HomeAssistant(str(ROOT / "benchmark/runtime/execution"))
    device_registry.async_setup(hass)
    await area_registry.async_load(hass, load_empty=True)
    await device_registry.async_load(hass, load_empty=True)
    await entity_registry.async_load(hass, load_empty=True)
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    executor = FixedPlanExecutor(hass, resolver=resolver)
    calls = 0

    async def turn_on(call) -> None:
        nonlocal calls
        calls += 1
        # 每 10 次故意不更新状态，模拟“服务成功但设备未生效”的真实验证场景。
        if calls % 10:
            hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    rows: list[dict[str, object]] = []
    for mode in ("edgehome_without_verification", "edgehome_full_verification"):
        for index in range(50):
            hass.states.async_set("light.benchmark", "off", {"friendly_name": "基准灯"})
            started = perf_counter()
            if mode == "edgehome_without_verification":
                await hass.services.async_call("light", "turn_on", {"entity_id": "light.benchmark"}, blocking=True)
                reported_success = True
                final_success = hass.states.get("light.benchmark").state == "on"
            else:
                plan = TaskPlan(
                    goal="打开基准灯",
                    steps=[PlanStep(
                        id=f"step-{index}",
                        tool="turn_on_device",
                        arguments={"entity_id": "light.benchmark"},
                        verify=VerificationRule(entity_id="light.benchmark", state="on"),
                    )],
                )
                report = await executor.async_execute(plan)
                reported_success = report.status == TaskStatus.SUCCESS
                final_success = hass.states.get("light.benchmark").state == "on"
            rows.append({
                "mode": mode,
                "request_id": f"{mode}-{index:03d}",
                "reported_success": reported_success,
                "final_success": final_success,
                "false_success": reported_success and not final_success,
                "latency_ms": round((perf_counter() - started) * 1000, 6),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
    await hass.async_stop(force=True)
    return rows


def main() -> None:
    rows = asyncio.run(run())
    output = ROOT / "benchmark/results/execution_verification_comparison.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (ROOT / "benchmark/results/execution_verification_comparison.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
