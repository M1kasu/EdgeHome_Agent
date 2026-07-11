"""长期稳定性测试：循环执行只读核心任务并记录真实持续时间。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.core import HomeAssistant

from custom_components.llama_conversation.edgehome.benchmark.resource_monitor import SystemResourceMonitor
from custom_components.llama_conversation.edgehome.energy.service import EnergyService
from custom_components.llama_conversation.edgehome.knowledge.indexer import LocalKnowledgeIndex
from custom_components.llama_conversation.edgehome.knowledge.service import KnowledgeService
from custom_components.llama_conversation.edgehome.understanding.intent_classifier import ChineseIntentClassifier


async def _run(arguments: argparse.Namespace) -> None:
    started = time.monotonic()
    monitor = SystemResourceMonitor()
    classifier = ChineseIntentClassifier()
    hass = HomeAssistant(str(arguments.runtime_dir))
    knowledge = KnowledgeService(hass, LocalKnowledgeIndex(hass, arguments.runtime_dir / "knowledge.sqlite"), ROOT / "knowledge")
    energy = EnergyService(hass, ROOT / "datasets/energy/abnormal_week.csv", ROOT / "datasets/energy/normal_week.csv")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("w", encoding="utf-8") as handle:
        iteration = 0
        while time.monotonic() - started < arguments.duration_seconds:
            iteration += 1
            cycle_started = time.monotonic()
            classification = classifier.classify("请查询客厅温度").intent.value
            knowledge_result = await knowledge.query_device_manual("climate.edgehome_living_room_ac", "E3")
            energy_result = await energy.analyse_home_energy()
            model_healthy = _model_healthy(arguments.model_health_url)
            payload = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "cycle_latency_ms": round((time.monotonic() - cycle_started) * 1000, 3),
                "simple_route": classification,
                "knowledge_success": knowledge_result.success,
                "energy_record_count": energy_result.record_count,
                "model_healthy": model_healthy,
                "resource": monitor.snapshot().model_dump(mode="json"),
            }
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            remaining = arguments.interval_seconds - (time.monotonic() - cycle_started)
            if remaining > 0:
                await _sleep(remaining)
    await hass.async_stop(force=True)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _model_healthy(url: str) -> bool:
    try:
        with urlopen(url, timeout=5) as response:
            return 200 <= response.status < 300
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=3600)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark/results/stability_1h.jsonl")
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / "benchmark/runtime/stability")
    parser.add_argument("--model-health-url", default="http://127.0.0.1:8080/health")
    arguments = parser.parse_args()
    import asyncio

    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
