"""可选 psutil 资源采样；依赖不存在时显式返回 null。"""

from __future__ import annotations

from .schemas import ResourceSnapshot


class SystemResourceMonitor:
    """不安装额外系统探针也能运行，绝不虚构 GPU、功耗或温度。"""

    def __init__(self) -> None:
        try:
            import psutil
        except ImportError:
            self._psutil = None
        else:
            self._psutil = psutil
            self._process = psutil.Process()

    def snapshot(self) -> ResourceSnapshot:
        """采样本进程 RSS、可用系统内存和即时 CPU 使用率。"""
        if self._psutil is None:
            return ResourceSnapshot()
        memory = self._psutil.virtual_memory()
        return ResourceSnapshot(
            process_memory_mb=round(self._process.memory_info().rss / 1024 / 1024, 3),
            system_memory_mb=round(memory.available / 1024 / 1024, 3),
            system_memory_percent=round(memory.percent, 3),
            cpu_percent=round(self._psutil.cpu_percent(interval=None), 3),
        )
