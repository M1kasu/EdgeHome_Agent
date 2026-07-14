"""Optional production resource sampling without fabricating unavailable metrics."""

from __future__ import annotations

from .snapshot import ResourceSnapshot


class SystemResourceMonitor:
    """Sample current process and system values when psutil is available."""

    def __init__(self) -> None:
        try:
            import psutil
        except ImportError:
            self._psutil = None
        else:
            self._psutil = psutil
            self._process = psutil.Process()

    def snapshot(self) -> ResourceSnapshot:
        if self._psutil is None:
            return ResourceSnapshot()
        memory = self._psutil.virtual_memory()
        return ResourceSnapshot(
            process_memory_mb=round(self._process.memory_info().rss / 1024 / 1024, 3),
            system_memory_mb=round(memory.available / 1024 / 1024, 3),
            system_memory_percent=round(memory.percent, 3),
            cpu_percent=round(self._psutil.cpu_percent(interval=None), 3),
        )
