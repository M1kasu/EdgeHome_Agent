"""第 13 阶段请求指标、百分位和结果落盘测试。"""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.benchmark import BenchmarkResultWriter, LatencyRecorder
from custom_components.llama_conversation.edgehome.benchmark.metrics import percentile


def test_latency_metric_uses_null_for_unobserved_hardware_and_writer_redacts_config(tmp_path) -> None:
    recorder = LatencyRecorder("req-1", "simple_control", "fast_path")
    recorder.start_stage("routing")
    recorder.end_stage("routing")
    metric = recorder.finish(True)
    paths = BenchmarkResultWriter(tmp_path).write("run", [metric], {"api_key": "never-write", "context": 2048})

    assert metric.total_latency_ms >= 0
    assert metric.gpu_percent is None
    assert paths[0].exists() and paths[1].exists()
    assert "never-write" not in paths[2].read_text(encoding="utf-8")
    assert percentile([1.0, 2.0, 3.0], 95) == 2.9
    assert percentile([], 50) is None
