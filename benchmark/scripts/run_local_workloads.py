"""执行真实本地知识或能耗工作负载，不把 CSV 或资料交给模型。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.core import HomeAssistant

from custom_components.llama_conversation.edgehome.benchmark import BenchmarkResultWriter, BenchmarkWorkload, LatencyRecorder
from custom_components.llama_conversation.edgehome.energy.service import EnergyService
from custom_components.llama_conversation.edgehome.knowledge.indexer import LocalKnowledgeIndex
from custom_components.llama_conversation.edgehome.knowledge.service import KnowledgeService


async def run(arguments: argparse.Namespace) -> None:
    raw = json.loads(arguments.workload.read_text(encoding="utf-8"))
    workloads = [BenchmarkWorkload.model_validate(item) for item in raw]
    hass = HomeAssistant(str(ROOT / "benchmark/runtime/local-workloads"))
    knowledge = KnowledgeService(hass, LocalKnowledgeIndex(hass, ROOT / "benchmark/runtime/local-workloads/knowledge.sqlite"), ROOT / "knowledge")
    energy = EnergyService(hass, ROOT / "datasets/energy/abnormal_week.csv", ROOT / "datasets/energy/normal_week.csv")
    metrics = []
    for workload in workloads:
        route = "retrieval_path" if workload.task_type == "knowledge_retrieval" else "energy_path"
        recorder = LatencyRecorder(f"{arguments.name}-{workload.id}", workload.task_type, route)
        if route == "retrieval_path":
            recorder.start_stage("retrieval")
            result = await knowledge.search_knowledge(workload.text, top_k=3)
            recorder.end_stage("retrieval")
            metrics.append(recorder.finish(result.success, error_code=result.error_code))
        else:
            recorder.start_stage("retrieval")
            result = await energy.analyse_home_energy()
            recorder.end_stage("retrieval")
            metrics.append(recorder.finish(result.record_count > 0))
    BenchmarkResultWriter(arguments.results).write(arguments.name, metrics, {"workload": str(arguments.workload), "model_used": False})
    await hass.async_stop(force=True)
    print(json.dumps({"requests": len(metrics), "successes": sum(metric.success for metric in metrics)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--results", type=Path, default=ROOT / "benchmark/results")
    arguments = parser.parse_args()
    asyncio.run(run(arguments))


if __name__ == "__main__":
    main()
