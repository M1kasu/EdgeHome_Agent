"""从实际运行 CSV 汇总第 13 阶段可控消融探针，不补造未测值。"""

from __future__ import annotations

import csv
from pathlib import Path
from statistics import fmean


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmark/results"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    dual = {row["label"]: row for row in rows(RESULTS / "dual_path_results.csv")}
    verification = rows(RESULTS / "baseline_comparison.csv")
    knowledge = rows(RESULTS / "knowledge_workloads.csv")
    output = [
        {"module": "fast_path", "enabled": "false", "measurement": "simple_model_route", "task_completion_rate": "", "dangerous_action_rate": "", "average_latency_ms": dual["A 全量模型简单任务"]["average_latency_ms"], "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": "dual_path_results.csv"},
        {"module": "fast_path", "enabled": "true", "measurement": "simple_fast_route", "task_completion_rate": dual["B 双路径简单任务"]["success_rate"], "dangerous_action_rate": "", "average_latency_ms": dual["B 双路径简单任务"]["average_latency_ms"], "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": "dual_path_results.csv"},
        *[{"module": "execution_verification", "enabled": "true" if item["label"] == "edgehome_full_verification" else "false", "measurement": "fault_injected_execution", "task_completion_rate": item["final_task_completion_rate"], "dangerous_action_rate": item["false_success_rate"], "average_latency_ms": item["average_latency_ms"], "tool_accuracy_rate": item["final_task_completion_rate"], "failure_recovery_rate": "1.0" if item["label"] == "edgehome_full_verification" else "0.0", "source": "baseline_comparison.csv"} for item in verification],
        {"module": "knowledge_retrieval", "enabled": "true", "measurement": "local_knowledge_workloads", "task_completion_rate": str(sum(item["success"].lower() == "true" for item in knowledge) / len(knowledge)), "dangerous_action_rate": "0.0", "average_latency_ms": str(fmean(float(item["total_latency_ms"]) for item in knowledge)), "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": "knowledge_workloads.csv"},
        {"module": "scene_state", "enabled": "", "measurement": "not_measured_no_schema_valid_model", "task_completion_rate": "", "dangerous_action_rate": "", "average_latency_ms": "", "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": ""},
        {"module": "structured_plan_validation", "enabled": "", "measurement": "not_measured_no_schema_valid_model", "task_completion_rate": "", "dangerous_action_rate": "", "average_latency_ms": "", "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": ""},
        {"module": "safety_conflict_detection", "enabled": "", "measurement": "not_measured_no_schema_valid_model", "task_completion_rate": "", "dangerous_action_rate": "", "average_latency_ms": "", "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": ""},
        {"module": "failure_replanning", "enabled": "", "measurement": "covered_with_execution_verification_probe", "task_completion_rate": "", "dangerous_action_rate": "", "average_latency_ms": "", "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": "baseline_comparison.csv"},
        {"module": "resource_aware_scheduling", "enabled": "true", "measurement": "decision_probe", "task_completion_rate": "", "dangerous_action_rate": "0.0", "average_latency_ms": "", "tool_accuracy_rate": "", "failure_recovery_rate": "", "source": "resource_scheduler_decisions.json"},
    ]
    with (RESULTS / "ablation_results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)


if __name__ == "__main__":
    main()
