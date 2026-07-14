"""第 14 阶段结构化规划失败证据留存测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _runner_module():
    path = Path(__file__).parents[1] / "benchmark/scripts/run_llama_workloads.py"
    spec = importlib.util.spec_from_file_location("edgehome_workload_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_schema_validation_returns_auditable_error_for_truncated_output() -> None:
    runner = _runner_module()
    valid, error = runner._task_plan_validation('{"goal":"观影","steps":[')

    assert valid is False
    assert error is not None
    assert "json" in error.lower()


def test_schema_validation_accepts_minimal_task_plan() -> None:
    runner = _runner_module()
    valid, error = runner._task_plan_validation('{"goal":"观影","steps":[{"id":"s1","tool":"scene"}]}')

    assert valid is True
    assert error is None
