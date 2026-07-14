"""本地稳定性采样的模型健康检查不可达时必须如实记录失败。"""

from __future__ import annotations

from pathlib import Path

from benchmark.scripts.run_stability_test import _model_healthy


def test_offline_health_probe_reports_unreachable_local_endpoint() -> None:
    assert _model_healthy("http://127.0.0.1:1/health") is False
    assert Path("benchmark/scripts/run_stability_test.py").exists()
