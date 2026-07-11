"""从实际 CSV 生成第 13 阶段汇总 CSV 与可重复 SVG 图表。"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import fmean


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmark/results"
FIGURES = ROOT / "benchmark/figures"


def _rows(name: str) -> list[dict[str, str]]:
    with (RESULTS / name).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "")
    return float(value) if value not in {"", None} else None


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _summary(label: str, rows: list[dict[str, str]]) -> dict[str, object]:
    latency = [_float(row, "total_latency_ms") for row in rows]
    latency_values = [value for value in latency if value is not None]
    success = sum(row["success"].lower() == "true" for row in rows)
    model_calls = sum(row["route"] == "llm_path" for row in rows)
    tokens = [_float(row, "tokens_per_second") for row in rows]
    return {
        "label": label,
        "requests": len(rows),
        "successes": success,
        "success_rate": round(success / len(rows), 6) if rows else None,
        "model_calls": model_calls,
        "model_call_rate": round(model_calls / len(rows), 6) if rows else None,
        "average_latency_ms": round(fmean(latency_values), 6) if latency_values else None,
        "p50_latency_ms": round(_percentile(latency_values, 50) or 0, 6) if latency_values else None,
        "p95_latency_ms": round(_percentile(latency_values, 95) or 0, 6) if latency_values else None,
        "avg_tokens_per_second": round(fmean([item for item in tokens if item is not None]), 6) if any(item is not None for item in tokens) else None,
    }


def _write_csv(name: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with (RESULTS / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _svg_bar(name: str, title: str, rows: list[dict[str, object]], value_key: str, unit: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    width, height, baseline = 900, 420, 340
    values = [float(row[value_key] or 0) for row in rows]
    maximum = max(values, default=1.0) or 1.0
    bars = []
    for index, (row, value) in enumerate(zip(rows, values, strict=True)):
        bar_width = 110
        x = 80 + index * 150
        bar_height = value / maximum * 250
        y = baseline - bar_height
        label = str(row["label"])
        bars.append(
            f'<rect x="{x}" y="{y:.2f}" width="{bar_width}" height="{bar_height:.2f}" fill="#377eb8"/>'
            f'<text x="{x + 55}" y="{baseline + 20}" text-anchor="middle" font-size="12">{label}</text>'
            f'<text x="{x + 55}" y="{y - 8:.2f}" text-anchor="middle" font-size="12">{value:.2f} {unit}</text>'
        )
    svg = "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="40" y="40" font-size="20" font-family="sans-serif">{title}</text>',
        f'<line x1="60" y1="{baseline}" x2="860" y2="{baseline}" stroke="black"/>',
        *bars,
        '</svg>',
    ])
    (FIGURES / f"{name}.svg").write_text(svg, encoding="utf-8")


def _container_peak(name: str) -> float | None:
    path = RESULTS / f"{name}.container_stats.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    samples = payload if isinstance(payload, list) else [payload]
    values = []
    for sample in samples:
        value = str(sample.get("stats", "")).split("|")[0].split("/")[0].strip()
        if value.endswith("GiB"):
            values.append(float(value.removesuffix("GiB")) * 1024)
        elif value.endswith("MiB"):
            values.append(float(value.removesuffix("MiB")))
    return round(max(values), 3) if values else None


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    quant = [
        {**_summary("Q4_K_M ctx1024", _rows("quant_q4_ctx1024_sampled.csv")), "quantization": "Q4_K_M", "context_tokens": 1024, "container_peak_memory_mb": _container_peak("quant_q4_ctx1024_sampled"), "load_time_ms": 3330.731},
        {**_summary("Q4_K_M ctx2048", _rows("quant_q4_ctx2048_sampled.csv")), "quantization": "Q4_K_M", "context_tokens": 2048, "container_peak_memory_mb": _container_peak("quant_q4_ctx2048_sampled"), "load_time_ms": 20637.398},
    ]
    _write_csv("model_comparison.csv", quant)
    dual = [
        _summary("A 全量模型简单任务", _rows("dual_a_all_llm_simple_q4_ctx2048.csv")),
        _summary("B 双路径简单任务", _rows("dual_b_edgehome_simple_q4_ctx2048.csv")),
        _summary("B 双路径复杂任务", _rows("dual_b_edgehome_complex_q4_ctx2048.csv")),
    ]
    _write_csv("dual_path_results.csv", dual)
    concurrency = [_summary(f"并发 {level}", _rows(f"concurrency_{level}.csv")) for level in (1, 2, 4)]
    for row, level in zip(concurrency, (1, 2, 4), strict=True):
        average = float(row["average_latency_ms"] or 0)
        row["concurrency"] = level
        row["throughput_requests_per_second"] = round(level * 1000 / average, 6) if average else None
    _write_csv("concurrency_results.csv", concurrency)
    verification = _rows("execution_verification_comparison.csv")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in verification:
        grouped[row["mode"]].append(row)
    baseline = []
    for mode, group_rows in grouped.items():
        latency = [float(row["latency_ms"]) for row in group_rows]
        reported = sum(row["reported_success"].lower() == "true" for row in group_rows)
        final = sum(row["final_success"].lower() == "true" for row in group_rows)
        false = sum(row["false_success"].lower() == "true" for row in group_rows)
        baseline.append({"label": mode, "requests": len(group_rows), "reported_success_rate": reported / len(group_rows), "final_task_completion_rate": final / len(group_rows), "false_success_rate": false / len(group_rows), "average_latency_ms": fmean(latency), "p95_latency_ms": _percentile(latency, 95)})
    _write_csv("baseline_comparison.csv", baseline)
    _svg_bar("latency_comparison", "平均延迟 / Average latency", dual, "average_latency_ms", "ms")
    _svg_bar("p95_latency_comparison", "P95 延迟 / P95 latency", dual, "p95_latency_ms", "ms")
    _svg_bar("quant_response_rate", "模型响应率 / Model response rate", quant, "success_rate", "rate")
    _svg_bar("dual_model_call_rate", "模型调用率 / Model call rate", dual, "model_call_rate", "rate")
    _svg_bar("model_memory_comparison", "模型容器峰值内存 / Model container peak memory", quant, "container_peak_memory_mb", "MiB")
    _svg_bar("concurrency_throughput", "并发吞吐量 / Concurrent throughput", concurrency, "throughput_requests_per_second", "req/s")
    _svg_bar("verification_completion", "验证后实际完成率 / Verified completion", baseline, "final_task_completion_rate", "rate")
    ablation = [row for row in _rows("ablation_results.csv") if row.get("average_latency_ms")]
    for row in ablation:
        row["label"] = f"{row['module']}:{row['enabled']}"
    _svg_bar("ablation_latency", "消融探针延迟 / Ablation probe latency", ablation, "average_latency_ms", "ms")


if __name__ == "__main__":
    main()
