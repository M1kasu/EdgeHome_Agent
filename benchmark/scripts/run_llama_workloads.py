"""对真实 llama.cpp OpenAI 兼容端点执行版本控制工作负载并写入原始指标。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.llama_conversation.edgehome.benchmark import BenchmarkResultWriter, BenchmarkWorkload, LatencyRecorder
from custom_components.llama_conversation.edgehome.models import TaskPlan
from custom_components.llama_conversation.edgehome.understanding.intent_classifier import ChineseIntentClassifier
from custom_components.llama_conversation.edgehome.understanding.models import FastIntent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--max-tokens", default=24, type=int)
    parser.add_argument("--limit", default=0, type=int)
    parser.add_argument("--strategy", choices=("all_llm", "dual_path"), default="all_llm")
    parser.add_argument("--task-plan-schema", action="store_true")
    parser.add_argument("--results", default=Path("benchmark/results"), type=Path)
    parser.add_argument("--model-label", default="Home-Llama-3.2-3B.q4_k_m")
    arguments = parser.parse_args()
    raw = json.loads(arguments.workload.read_text(encoding="utf-8"))
    workloads = [BenchmarkWorkload.model_validate(item) for item in raw]
    if arguments.limit:
        workloads = workloads[: arguments.limit]
    metrics = []
    outcomes: list[dict[str, Any]] = []
    classifier = ChineseIntentClassifier()
    for workload in workloads:
        if arguments.strategy == "dual_path" and workload.task_type == "simple_control":
            recorder = LatencyRecorder(f"{arguments.name}-{workload.id}", workload.task_type, "fast_path")
            recorder.start_stage("routing")
            intent = classifier.classify(workload.text).intent
            recorder.end_stage("routing")
            success = intent != FastIntent.UNKNOWN
            metrics.append(recorder.finish(success))
            outcomes.append({"id": workload.id, "model_called": False, "schema_valid": None, "success": success})
            continue
        metric, outcome = _run_llm(workload, arguments.endpoint, arguments.max_tokens, arguments.name, arguments.task_plan_schema)
        metrics.append(metric)
        outcomes.append(outcome)
    writer = BenchmarkResultWriter(arguments.results)
    writer.write(
        arguments.name,
        metrics,
        {
            "workload": str(arguments.workload),
            "strategy": arguments.strategy,
            "model_label": arguments.model_label,
            "endpoint": arguments.endpoint,
            "max_tokens": arguments.max_tokens,
            "record_count": len(metrics),
        },
    )
    (arguments.results / f"{arguments.name}.outcomes.json").write_text(
        json.dumps(outcomes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(_summary(metrics, outcomes), ensure_ascii=False))


def _run_llm(workload: BenchmarkWorkload, endpoint: str, max_tokens: int, prefix: str, task_plan_schema: bool = False):
    recorder = LatencyRecorder(f"{prefix}-{workload.id}", workload.task_type, "llm_path")
    recorder.start_stage("routing")
    recorder.end_stage("routing")
    recorder.start_stage("context_build")
    payload = {
        "messages": [
            {"role": "system", "content": "你是本地 EdgeHome 基准模型。直接回答用户请求；不要声称执行了未调用的设备服务。"},
            {"role": "user", "content": workload.text},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "stream": False,
    }
    if task_plan_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "edgehome_task_plan", "schema": TaskPlan.model_json_schema(), "strict": True},
        }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    recorder.end_stage("context_build")
    recorder.start_stage("model_generation")
    started = perf_counter()
    try:
        request = Request(endpoint, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer local-no-auth"})
        with urlopen(request, timeout=90) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        recorder.end_stage("model_generation")
        return recorder.finish(False, error_code=type(error).__name__), {"id": workload.id, "model_called": True, "schema_valid": False, "success": False}
    elapsed = (perf_counter() - started) * 1000
    recorder.end_stage("model_generation")
    timings = decoded.get("timings") if isinstance(decoded, dict) else None
    usage = decoded.get("usage") if isinstance(decoded, dict) else None
    content = _content(decoded)
    # OpenAI 非流式端点无法观测首个网络 token；保持 null，而不是估计为 0。
    recorder.set_stage_duration("model_ttft", None)
    recorder.set_stage_duration("model_generation", elapsed)
    tokens_per_second = _number(timings, "predicted_per_second")
    metric = recorder.finish(
        bool(content),
        input_tokens=_integer(usage, "prompt_tokens"),
        output_tokens=_integer(usage, "completion_tokens"),
        tokens_per_second=tokens_per_second,
        error_code=None if content else "EMPTY_MODEL_CONTENT",
    )
    schema_valid = _is_task_plan(content) if task_plan_schema else None
    return metric, {"id": workload.id, "model_called": True, "schema_valid": schema_valid, "success": metric.success}


def _content(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    return message.get("content", "") if isinstance(message, dict) and isinstance(message.get("content"), str) else ""


def _integer(payload: Any, key: str) -> int | None:
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, int) and value >= 0 else None


def _number(payload: Any, key: str) -> float | None:
    value = payload.get(key) if isinstance(payload, dict) else None
    return float(value) if isinstance(value, (int, float)) and value >= 0 else None


def _is_json(content: str) -> bool:
    try:
        json.loads(content)
    except json.JSONDecodeError:
        return False
    return True


def _is_task_plan(content: str) -> bool:
    try:
        TaskPlan.model_validate_json(content)
    except ValueError:
        return False
    return True


def _summary(metrics: list[Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(metrics),
        "successes": sum(metric.success for metric in metrics),
        "model_calls": sum(item["model_called"] for item in outcomes),
        "schema_valid": sum(item["schema_valid"] is True for item in outcomes),
    }


if __name__ == "__main__":
    main()
