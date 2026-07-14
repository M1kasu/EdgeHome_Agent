"""单调时钟、百分位数和可选资源采样的基础函数。"""

from __future__ import annotations

from collections.abc import Iterable
from time import perf_counter_ns


def monotonic_ms() -> float:
    """所有延迟均来自单调时钟，避免系统时间跳变污染结果。"""
    return perf_counter_ns() / 1_000_000


def elapsed_ms(started_ms: float, finished_ms: float | None = None) -> float:
    """返回非负的单调耗时。"""
    return max(0.0, (finished_ms if finished_ms is not None else monotonic_ms()) - started_ms)


def percentile(values: Iterable[float], percent: float) -> float | None:
    """使用线性插值计算 P50/P90/P95；空样本不伪造 0。"""
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0.0 <= percent <= 100.0:
        raise ValueError("percent 必须在 0 到 100 之间")
    position = (len(ordered) - 1) * percent / 100.0
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
