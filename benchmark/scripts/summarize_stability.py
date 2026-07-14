"""从稳定性 JSONL 生成真实持续时间和资源汇总。"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    source = ROOT / "benchmark/results/stability_1h.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("没有稳定性记录")
    process_memory = [row["resource"]["process_memory_mb"] for row in rows if row["resource"]["process_memory_mb"] is not None]
    cpu = [row["resource"]["cpu_percent"] for row in rows if row["resource"]["cpu_percent"] is not None]
    summary = {
        "samples": len(rows),
        "first_elapsed_seconds": rows[0]["elapsed_seconds"],
        "last_elapsed_seconds": rows[-1]["elapsed_seconds"],
        "sample_span_seconds": rows[-1]["elapsed_seconds"] - rows[0]["elapsed_seconds"],
        "process_memory_min_mb": min(process_memory) if process_memory else None,
        "process_memory_max_mb": max(process_memory) if process_memory else None,
        "process_memory_delta_mb": process_memory[-1] - process_memory[0] if len(process_memory) > 1 else None,
        "cpu_percent_max": max(cpu) if cpu else None,
        "model_unhealthy_count": sum(not row["model_healthy"] for row in rows),
        "knowledge_failure_count": sum(not row["knowledge_success"] for row in rows),
        "energy_failure_count": sum(row["energy_record_count"] <= 0 for row in rows),
    }
    output = ROOT / "benchmark/results/stability_1h_summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
