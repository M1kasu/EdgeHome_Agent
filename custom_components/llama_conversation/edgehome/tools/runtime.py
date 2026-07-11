"""Uniform, verified tool results for the virtual EdgeHome home."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from pydantic import BaseModel, ConfigDict, Field

from ..entity_resolver import EntityNameResolver
from ..errors import EdgeHomeError
from ..execution.executor import FixedPlanExecutor
from ..models import PlanStep, TaskPlan, TaskStatus, VerificationRule
from ..tasks.delayed import DelayedTaskManager


class ToolResult(BaseModel):
    """JSON-safe response returned by every supported EdgeHome tool."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    tool: str
    entity_id: str | None = None
    requested: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str


class EdgeHomeToolRuntime:
    """Call only registered tools and always re-read state through FixedPlanExecutor."""

    def __init__(
        self,
        hass: HomeAssistant,
        resolver: EntityNameResolver,
        executor: FixedPlanExecutor | None = None,
        delayed_tasks: DelayedTaskManager | None = None,
    ) -> None:
        self._resolver = resolver
        self._executor = executor or FixedPlanExecutor(hass, resolver=resolver)
        self._delayed_tasks = delayed_tasks or DelayedTaskManager(hass, self._executor)

    async def get_home_state(self) -> ToolResult:
        """Return all registered virtual entities without exposing helper or real entities."""
        return self._state_collection("get_home_state", None)

    async def get_room_state(self, room: str) -> ToolResult:
        """Return registered entities belonging to one named virtual room."""
        if room not in {"living_room", "bedroom", "home"}:
            return self._failure("get_room_state", None, {"room": room}, "INVALID_ROOM", "不支持的演示房间")
        return self._state_collection("get_room_state", room)

    async def get_device_state(self, entity_id: str) -> ToolResult:
        """Return one current registered and exposed entity snapshot."""
        try:
            snapshot = self._resolver.snapshot(entity_id)
        except EdgeHomeError as error:
            return self._edgehome_failure("get_device_state", entity_id, {}, error)
        return ToolResult(
            success=True,
            tool="get_device_state",
            entity_id=entity_id,
            actual=_snapshot_data(snapshot),
            message=f"{snapshot.friendly_name or entity_id} 当前状态已读取",
        )

    async def turn_on_device(self, entity_id: str) -> ToolResult:
        return await self._execute_entity_tool(
            "turn_on_device",
            entity_id,
            {},
            VerificationRule(entity_id=entity_id, state="on"),
            f"{entity_id} 已打开",
        )

    async def turn_off_device(self, entity_id: str) -> ToolResult:
        return await self._execute_entity_tool(
            "turn_off_device",
            entity_id,
            {},
            VerificationRule(entity_id=entity_id, state="off"),
            f"{entity_id} 已关闭",
        )

    async def set_light_brightness(self, entity_id: str, brightness: int) -> ToolResult:
        expected = round(brightness * 255 / 100)
        return await self._execute_entity_tool(
            "set_light_brightness",
            entity_id,
            {"brightness": brightness},
            VerificationRule(entity_id=entity_id, state="on", attributes={"brightness": expected}, tolerance=2.0),
            f"{entity_id} 亮度已设置为 {brightness}%",
        )

    async def set_temperature(self, entity_id: str, temperature: float) -> ToolResult:
        return await self._execute_entity_tool(
            "set_temperature",
            entity_id,
            {"temperature": temperature},
            VerificationRule(entity_id=entity_id, attributes={"temperature": temperature}, tolerance=0.5),
            f"{entity_id} 目标温度已设置为 {temperature:g}°C",
        )

    async def control_curtain(self, entity_id: str, position: int) -> ToolResult:
        return await self._execute_entity_tool(
            "control_curtain",
            entity_id,
            {"position": position},
            VerificationRule(entity_id=entity_id, attributes={"current_position": position}, tolerance=1.0),
            f"{entity_id} 窗帘位置已设置为 {position}%",
        )

    async def activate_security_mode(self, entity_id: str = "input_boolean.edgehome_security_mode") -> ToolResult:
        return await self._execute_entity_tool(
            "activate_security_mode",
            entity_id,
            {},
            VerificationRule(entity_id=entity_id, state="on"),
            "虚拟安防模式已开启",
        )

    async def create_delayed_task(
        self,
        conversation_id: str,
        plan: TaskPlan,
        delay_seconds: int,
    ) -> ToolResult:
        """Persist a validated fixed plan and schedule it through Home Assistant's event loop."""
        try:
            self._executor.validator.validate(plan)
            task = await self._delayed_tasks.async_create(
                conversation_id,
                plan,
                datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
            )
        except (EdgeHomeError, ValueError) as error:
            return self._failure(
                "create_delayed_task",
                None,
                {"delay_seconds": delay_seconds},
                "INVALID_DELAYED_TASK",
                str(error),
            )
        return ToolResult(
            success=True,
            tool="create_delayed_task",
            requested={"delay_seconds": delay_seconds, "plan": plan.model_dump(mode="json")},
            actual={"task_id": task.id, "execute_at": task.execute_at.isoformat(), "status": task.status.value},
            message="延迟任务已创建",
        )

    def _state_collection(self, tool: str, room: str | None) -> ToolResult:
        try:
            snapshots = [self._resolver.snapshot(entity_id) for entity_id in self._resolver.registered_entities(room)]
        except EdgeHomeError as error:
            return self._edgehome_failure(tool, None, {"room": room} if room else {}, error)
        return ToolResult(
            success=True,
            tool=tool,
            requested={"room": room} if room else {},
            actual={"entities": [_snapshot_data(snapshot) for snapshot in snapshots]},
            message="家庭状态已读取" if room is None else "房间状态已读取",
        )

    async def _execute_entity_tool(
        self,
        tool: str,
        entity_id: str,
        requested: dict[str, Any],
        verify: VerificationRule,
        success_message: str,
    ) -> ToolResult:
        step = PlanStep(
            id="tool_1",
            tool=tool,
            arguments={"entity_id": entity_id, **requested},
            verify=verify,
        )
        report = await self._executor.async_execute(TaskPlan(goal=success_message, scene="tool", steps=[step]))
        result = report.step_results[0] if report.step_results else None
        if report.status == TaskStatus.SUCCESS and result is not None:
            snapshot = result.state_after or result.state_before
            return ToolResult(
                success=True,
                tool=tool,
                entity_id=entity_id,
                requested=requested,
                actual=_snapshot_data(snapshot) if snapshot else {},
                message=success_message,
            )
        error_code = _external_code(result.error_code if result and result.error_code else "execution_failed")
        return self._failure(
            tool,
            entity_id,
            requested,
            error_code,
            result.error_message if result and result.error_message else "服务调用后状态未达到预期",
            _snapshot_data(result.state_after) if result and result.state_after else {},
        )

    def _edgehome_failure(self, tool: str, entity_id: str | None, requested: dict[str, Any], error: EdgeHomeError) -> ToolResult:
        return self._failure(tool, entity_id, requested, _external_code(error.code), error.message)

    @staticmethod
    def _failure(
        tool: str,
        entity_id: str | None,
        requested: dict[str, Any],
        error_code: str,
        message: str,
        actual: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(
            success=False,
            tool=tool,
            entity_id=entity_id,
            requested=requested,
            actual=actual or {},
            error_code=error_code,
            message=message,
        )


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    return {"state": snapshot.state, "attributes": dict(snapshot.attributes), "friendly_name": snapshot.friendly_name}


def _external_code(code: str) -> str:
    return {
        "entity_not_found": "ENTITY_NOT_FOUND",
        "entity_not_exposed": "ENTITY_NOT_EXPOSED",
        "entity_not_registered": "ENTITY_NOT_REGISTERED",
        "entity_domain_not_allowed": "ENTITY_DOMAIN_NOT_ALLOWED",
        "verification_failed": "VERIFICATION_FAILED",
        "service_timeout": "SERVICE_TIMEOUT",
        "service_call_failed": "SERVICE_CALL_FAILED",
    }.get(code, code.upper())
