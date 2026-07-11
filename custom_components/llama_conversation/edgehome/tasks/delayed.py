"""Schedule, persist, query, and cancel only previously validated EdgeHome plans."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from pydantic import BaseModel, ConfigDict, Field

from ..execution.executor import FixedPlanExecutor
from ..models import TaskPlan, TaskStatus


class DelayedTaskStatus(StrEnum):
    """The lifecycle of a delayed fixed plan."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DelayedTask(BaseModel):
    """A persistent fixed plan that may be run once at a known UTC time."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1, max_length=256)
    plan: TaskPlan
    execute_at: datetime
    status: DelayedTaskStatus = DelayedTaskStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None


class DelayedCommandRequest(BaseModel):
    """Validated result of a deterministic Chinese delay command parse."""

    model_config = ConfigDict(extra="forbid")

    delay_seconds: int = Field(ge=1, le=7 * 24 * 60 * 60)
    command: str = Field(min_length=1, max_length=300)


class DelayedTaskManager:
    """Use HA Store and HA event scheduling; no background thread or raw database access."""

    _KEY = "edgehome_delayed_tasks"
    _VERSION = 1

    def __init__(self, hass: HomeAssistant, executor: FixedPlanExecutor) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, self._VERSION, self._KEY)
        self._executor = executor
        self._tasks: dict[str, DelayedTask] = {}
        self._unsubscribers: dict[str, Callable[[], None]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load persisted pending tasks and reschedule future executions after a restart."""
        if self._loaded:
            return
        payload = await self._store.async_load() or {}
        records = payload.get("tasks", []) if isinstance(payload, dict) else []
        now = datetime.now(timezone.utc)
        if isinstance(records, list):
            for raw in records:
                try:
                    task = DelayedTask.model_validate(raw)
                except ValueError:
                    continue
                self._tasks[task.id] = task
                if task.status == DelayedTaskStatus.PENDING and task.execute_at > now:
                    self._schedule(task)
                elif task.status == DelayedTaskStatus.PENDING:
                    self._tasks[task.id] = task.model_copy(
                        update={"status": DelayedTaskStatus.FAILED, "error": "任务错过执行时间", "updated_at": now}
                    )
        self._loaded = True
        await self._async_save()

    async def async_create(self, conversation_id: str, plan: TaskPlan, execute_at: datetime) -> DelayedTask:
        """Persist and register one future plan; validation occurs again when it runs."""
        await self.async_load()
        when = execute_at.astimezone(timezone.utc)
        if when <= datetime.now(timezone.utc):
            raise ValueError("延时任务必须安排在未来")
        task = DelayedTask(id=uuid4().hex, conversation_id=conversation_id, plan=plan, execute_at=when)
        self._tasks[task.id] = task
        self._schedule(task)
        await self._async_save()
        return task

    async def async_list(self, conversation_id: str) -> list[DelayedTask]:
        """Return one conversation's tasks in stable scheduled-time order."""
        await self.async_load()
        return sorted(
            (task for task in self._tasks.values() if task.conversation_id == conversation_id),
            key=lambda task: task.execute_at,
        )

    async def async_cancel(self, conversation_id: str, task_id: str) -> DelayedTask | None:
        """Cancel only the calling conversation's still-pending task."""
        await self.async_load()
        task = self._tasks.get(task_id)
        if task is None or task.conversation_id != conversation_id or task.status != DelayedTaskStatus.PENDING:
            return None
        unsubscribe = self._unsubscribers.pop(task_id, None)
        if unsubscribe:
            unsubscribe()
        cancelled = task.model_copy(update={"status": DelayedTaskStatus.CANCELLED, "updated_at": datetime.now(timezone.utc)})
        self._tasks[task_id] = cancelled
        await self._async_save()
        return cancelled

    async def async_shutdown(self) -> None:
        """Remove scheduled callbacks without deleting the persistent task records."""
        for unsubscribe in self._unsubscribers.values():
            unsubscribe()
        self._unsubscribers.clear()

    def _schedule(self, task: DelayedTask) -> None:
        unsubscribe = self._unsubscribers.pop(task.id, None)
        if unsubscribe:
            unsubscribe()

        async def run(_now: datetime) -> None:
            await self._async_run(task.id)

        self._unsubscribers[task.id] = async_track_point_in_utc_time(self._executor.hass, run, task.execute_at)

    async def _async_run(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.status != DelayedTaskStatus.PENDING:
            return
        unsubscribe = self._unsubscribers.pop(task_id, None)
        if unsubscribe:
            unsubscribe()
        self._tasks[task_id] = task.model_copy(update={"status": DelayedTaskStatus.RUNNING, "updated_at": datetime.now(timezone.utc)})
        await self._async_save()
        report = await self._executor.async_execute(task.plan)
        now = datetime.now(timezone.utc)
        if report.status == TaskStatus.SUCCESS:
            completed = task.model_copy(update={"status": DelayedTaskStatus.COMPLETED, "updated_at": now})
        else:
            result = report.step_results[-1] if report.step_results else None
            completed = task.model_copy(
                update={
                    "status": DelayedTaskStatus.FAILED,
                    "updated_at": now,
                    "error": result.error_message if result and result.error_message else "延时任务未完成",
                }
            )
        self._tasks[task_id] = completed
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save({"tasks": [task.model_dump(mode="json") for task in self._tasks.values()]})
