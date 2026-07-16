"""Minimal execution and verification runtime for local acceptance tests."""

from __future__ import annotations

from dataclasses import replace

from .models import ActionResult, DeviceState, ExecutionReport, PlanAction, ServicePlan


class InMemoryHomeRuntime:
    """A small HA-like state store used by black-box acceptance scripts."""

    def __init__(self, devices: tuple[DeviceState, ...]) -> None:
        self._devices = {device.entity_id: device for device in devices}

    def read(self, entity_id: str) -> DeviceState | None:
        return self._devices.get(entity_id)

    def execute(self, action: PlanAction) -> ActionResult:
        before = self.read(action.entity_id)
        if before is None:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                success=False,
                before=None,
                after=None,
                message="entity not found",
            )
        if before.protected:
            return ActionResult(
                entity_id=action.entity_id,
                capability=action.capability,
                expected_value=action.value,
                success=False,
                before=before,
                after=before,
                message="protected entity requires external confirmation",
            )
        after = self._apply(before, action)
        self._devices[action.entity_id] = after
        return ActionResult(
            entity_id=action.entity_id,
            capability=action.capability,
            expected_value=action.value,
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


def execute_and_verify(runtime: InMemoryHomeRuntime, plan: ServicePlan) -> ExecutionReport:
    results = tuple(runtime.execute(action) for action in plan.actions)
    verified = bool(results) and all(_verify_result(result) for result in results)
    return ExecutionReport(
        plan_id=plan.plan_id,
        executed=all(result.success for result in results),
        verified=verified,
        results=results,
        verification_summary="all action targets reached expected state" if verified else "verification failed",
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
