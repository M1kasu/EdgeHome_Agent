"""Small dependency-free resource monitor for bounded complex-planning concurrency."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter, process_time
import tracemalloc

from pydantic import BaseModel, ConfigDict, Field


class ResourceRequestRecord(BaseModel):
    """A redacted timing and process-resource record for one planning request."""

    model_config = ConfigDict(extra="forbid")

    route: str
    elapsed_ms: float = Field(ge=0.0)
    cpu_ms: float = Field(ge=0.0)
    peak_memory_delta_kib: float = Field(ge=0.0)
    concurrent_at_start: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class ResourceLease:
    """Opaque acquisition record; it does not contain user text or credentials."""

    route: str
    started_at: float
    cpu_started_at: float
    peak_started_at: int
    concurrent_at_start: int


class ResourceMonitor:
    """Reject excess model-planning concurrency and retain a bounded metric history."""

    def __init__(self, max_concurrent_plans: int = 1, max_records: int = 100) -> None:
        self._max_concurrent_plans = max_concurrent_plans
        self._max_records = max_records
        self._active = 0
        self._lock = asyncio.Lock()
        self._records: list[ResourceRequestRecord] = []
        if not tracemalloc.is_tracing():
            tracemalloc.start()

    async def async_try_acquire(self, route: str) -> ResourceLease | None:
        """Acquire a bounded planning slot or return None without waiting in the event loop."""
        async with self._lock:
            if self._active >= self._max_concurrent_plans:
                return None
            self._active += 1
            peak = tracemalloc.get_traced_memory()[1]
            return ResourceLease(route, perf_counter(), process_time(), peak, self._active)

    async def async_release(self, lease: ResourceLease) -> ResourceRequestRecord:
        """Release a planning slot and append only aggregate metrics."""
        elapsed_ms = max(0.0, (perf_counter() - lease.started_at) * 1000)
        cpu_ms = max(0.0, (process_time() - lease.cpu_started_at) * 1000)
        peak_now = tracemalloc.get_traced_memory()[1]
        record = ResourceRequestRecord(
            route=lease.route,
            elapsed_ms=elapsed_ms,
            cpu_ms=cpu_ms,
            peak_memory_delta_kib=max(0.0, (peak_now - lease.peak_started_at) / 1024),
            concurrent_at_start=lease.concurrent_at_start,
        )
        async with self._lock:
            self._active = max(0, self._active - 1)
            self._records.append(record)
            del self._records[: -self._max_records]
        return record

    @property
    def records(self) -> tuple[ResourceRequestRecord, ...]:
        """Return aggregate records only, in completion order."""
        return tuple(self._records)
