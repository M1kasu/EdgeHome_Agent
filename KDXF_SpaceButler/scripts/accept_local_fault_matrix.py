"""Black-box acceptance for deterministic local execution failures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler import (  # noqa: E402
    DeviceState,
    ExecutionStatus,
    InMemoryHomeRuntime,
    PlanAction,
    PlanPriority,
    ServicePlan,
    execute_and_verify,
)


def plan(*actions: PlanAction) -> ServicePlan:
    return ServicePlan(
        plan_id="local_fault_matrix",
        title="local fault matrix",
        priority=PlanPriority.ENERGY,
        proactive=True,
        target_members=("household",),
        actions=actions,
        explanation="local execution fault acceptance",
    )


def main() -> int:
    cases: list[dict[str, object]] = []

    scenarios = (
        (
            "reject",
            (DeviceState("climate.ac", "climate", "living_room", "cool", {"reject_commands": True}),),
            plan(PlanAction("climate.ac", "turn_off", True, "energy")),
            ExecutionStatus.EXECUTION_FAILED,
            1_000,
        ),
        (
            "timeout",
            (DeviceState("climate.ac", "climate", "living_room", "cool", {"response_delay_ms": 2_000}),),
            plan(PlanAction("climate.ac", "turn_off", True, "energy")),
            ExecutionStatus.TIMEOUT,
            500,
        ),
        (
            "partial_success",
            (
                DeviceState("climate.ac", "climate", "living_room", "cool"),
                DeviceState("light.main", "light", "living_room", "on", {"available": False}),
            ),
            plan(
                PlanAction("climate.ac", "turn_off", True, "energy"),
                PlanAction("light.main", "turn_off", True, "energy"),
            ),
            ExecutionStatus.PARTIAL_SUCCESS,
            1_000,
        ),
    )

    for test_id, devices, service_plan, expected, timeout_ms in scenarios:
        report = execute_and_verify(
            InMemoryHomeRuntime(devices, command_timeout_ms=timeout_ms),
            service_plan,
        )
        passed = report.status == expected and not report.verified
        cases.append(
            {
                "test_id": test_id,
                "expected_status": expected.value,
                "actual_status": report.status.value,
                "verified": report.verified,
                "passed": passed,
            }
        )

    accepted = all(bool(case["passed"]) for case in cases)
    print(json.dumps({"acceptance": "PASS" if accepted else "FAIL", "cases": cases}, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
