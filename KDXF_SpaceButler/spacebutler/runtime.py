"""Minimal execution and verification runtime for local acceptance tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from .models import ActionResult, DeviceState, ExecutionReport, ExecutionStatus, PlanAction, ServicePlan


class HomeRuntime(Protocol):
    def execute(self, action: PlanAction) -> ActionResult: ...


class InMemoryHomeRuntime:
    """A small HA-like state store used by black-box acceptance scripts."""

    def __init__(self, devices: tuple[DeviceState, ...], command_timeout_ms: int = 1_000) -> None:
        self._devices = {device.entity_id: device for device in devices}
        self._command_timeout_ms = command_timeout_ms

    def read(self, entity_id: str) -> DeviceState | None:
        return self._devices.get(entity_id)

    def execute(self, action: PlanAction) -> ActionResult:
        before = self.read(action.entity_id)
        if before is None:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.DEVICE_UNAVAILABLE,
                success=False,
                before=None,
                after=None,
                message="entity not found",
            )
        if before.attributes.get("available") is False:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.DEVICE_UNAVAILABLE,
                success=False,
                before=before,
                after=before,
                message="device unavailable",
            )
        response_delay_ms = int(before.attributes.get("response_delay_ms", 0))
        if response_delay_ms > self._command_timeout_ms:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.TIMEOUT,
                success=False,
                before=before,
                after=before,
                message=f"device response exceeded {self._command_timeout_ms} ms timeout",
            )
        if before.protected:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.CANCELLED,
                success=False,
                before=before,
                after=before,
                message="protected entity requires external confirmation",
            )
        if before.attributes.get("reject_commands") is True:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.EXECUTION_FAILED,
                success=False,
                before=before,
                after=before,
                message="device rejected command",
            )
        if before.attributes.get("ack_without_state_change") is True:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                status=ExecutionStatus.SUCCESS,
                success=True,
                before=before,
                after=before,
                message="command acknowledged but state did not change",
            )
        after = self._apply(before, action)
        self._devices[action.entity_id] = after
        return ActionResult(
            entity_id=action.entity_id,
            capability=action.capability,
            expected_value=action.value,
            status=ExecutionStatus.SUCCESS,
            success=True,
            before=before,
            after=after,
            message="executed",
        )

    @staticmethod
    def _apply(device: DeviceState, action: PlanAction) -> DeviceState:
        if action.capability == "turn_off":
            return replace(device, state="off")
        if action.capability == "turn_on":
            return replace(device, state="on")
        if action.capability in {"set_temperature", "set_brightness"}:
            attributes = dict(device.attributes)
            key = "temperature" if action.capability == "set_temperature" else "brightness"
            attributes[key] = action.value
            state = device.state
            if action.capability == "set_brightness" and state == "off":
                state = "on"
            return replace(device, state=state, attributes=attributes)
        return device


def execute_and_verify(runtime: HomeRuntime, plan: ServicePlan) -> ExecutionReport:
    results = tuple(runtime.execute(action) for action in plan.actions)
    verified_results = tuple(_verify_result(result) for result in results)
    verified = bool(results) and all(verified_results)
    status = _report_status(results, verified_results)
    return ExecutionReport(
        plan_id=plan.plan_id,
        status=status,
        executed=all(result.success for result in results),
        verified=verified,
        results=results,
        verification_summary=(
            "all action targets reached expected state"
            if verified
            else f"{sum(verified_results)}/{len(verified_results)} action targets verified"
        ),
    )


def _verify_result(result: ActionResult) -> bool:
    if not result.success or result.after is None:
        return False
    if result.capability == "turn_off":
        return result.after.state == "off"
    if result.capability == "turn_on":
        return result.after.state == "on"
    if result.capability == "set_temperature":
        return result.after.attributes.get("temperature") == result.expected_value
    if result.capability == "set_brightness":
        return result.after.state == "on" and result.after.attributes.get("brightness") == result.expected_value
    return result.after == result.before


def _report_status(results: tuple[ActionResult, ...], verified_results: tuple[bool, ...]) -> ExecutionStatus:
    if not results:
        return ExecutionStatus.CANCELLED
    if all(verified_results):
        return ExecutionStatus.SUCCESS
    if any(verified_results):
        return ExecutionStatus.PARTIAL_SUCCESS
    if any(result.status == ExecutionStatus.TIMEOUT for result in results):
        return ExecutionStatus.TIMEOUT
    if any(result.status == ExecutionStatus.DEVICE_UNAVAILABLE for result in results):
        return ExecutionStatus.DEVICE_UNAVAILABLE
    if any(result.status == ExecutionStatus.EXECUTION_FAILED for result in results):
        return ExecutionStatus.EXECUTION_FAILED
    if any(result.success for result in results):
        return ExecutionStatus.VALIDATION_FAILED
    return ExecutionStatus.EXECUTION_FAILED
