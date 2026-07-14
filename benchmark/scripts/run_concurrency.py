"""对互不控制实体的只读模型请求执行真实并发压力测试。"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.llama_conversation.edgehome.benchmark.result_writer import BenchmarkResultWriter
from custom_components.llama_conversation.edgehome.benchmark.schemas import BenchmarkWorkload
from benchmark.scripts.run_llama_workloads import _run_llm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--results", type=Path, default=ROOT / "benchmark/results")
    arguments = parser.parse_args()
    workloads = [
        BenchmarkWorkload(
            id=f"concurrent-{index:03d}",
            task_type="simple_query",
            text=f"只回复客厅温度状态，独立查询编号 {index}。",
            expected_route="llm_path",
        )
        for index in range(arguments.requests)
    ]
    name = f"concurrency_{arguments.concurrency}"
    metrics = []
    outcomes = []
    with ThreadPoolExecutor(max_workers=arguments.concurrency) as executor:
        futures = [executor.submit(_run_llm, workload, arguments.endpoint, 12, name) for workload in workloads]
        for future in as_completed(futures):
            metric, outcome = future.result()
            metrics.append(metric)
            outcomes.append(outcome)
    BenchmarkResultWriter(arguments.results).write(
        name,
        metrics,
        {"concurrency": arguments.concurrency, "requests": arguments.requests, "workload": "independent_read_only_model_queries"},
    )
    (arguments.results / f"{name}.outcomes.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"requests": len(metrics), "successes": sum(item.success for item in metrics)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
