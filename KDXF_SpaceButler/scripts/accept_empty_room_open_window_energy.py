"""External acceptance for the empty-room open-window AC energy loop.

This script intentionally lives outside tests. It exercises the public package
entry points as a black-box demo: build spatial snapshot, ask the Agent for a
proactive plan, execute it through a runtime, then verify by state readback.
"""

from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler import (  # noqa: E402
    DeviceState,
    EnvironmentState,
    HouseholdMember,
    InMemoryHomeRuntime,
    MemberRole,
    RoomState,
    SpaceButlerSession,
    SpaceButlerAgent,
    SpatialSnapshot,
)


def main() -> int:
    devices = (
        DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"temperature": 24, "power_w": 1050}),
        DeviceState("window.living_room_window", "window", "living_room", "open"),
        DeviceState("light.living_room_main", "light", "living_room", "off"),
    )
    snapshot = SpatialSnapshot(
        scene="daily",
        time_of_day="afternoon",
        members=(
            HouseholdMember("member_01", "妈妈", MemberRole.ADULT, "bedroom", "resting"),
            HouseholdMember("member_02", "爸爸", MemberRole.ADULT, "study", "working"),
        ),
        environment=EnvironmentState(27, 34, 62, 500, 18, electricity_price_level="normal"),
        devices=devices,
        rooms=(
            RoomState(
                "living_room",
                occupied=False,
                unoccupied_minutes=23,
                temperature=27,
                humidity=62,
                illuminance=500,
                window_state="open",
                power_w=1050,
                source="acceptance_simulator",
            ),
        ),
        snapshot_id="accept-empty-room-open-window-001",
        source="black_box_acceptance",
    )
    agent = SpaceButlerAgent()
    runtime = InMemoryHomeRuntime(devices)
    session = SpaceButlerSession(agent, runtime)
    suggestion = session.observe(snapshot)
    if suggestion.status != "needs_confirmation" or suggestion.plan is None:
        return fail("first trigger should create a confirmable proactive suggestion", response=to_jsonable(suggestion))
    confirmed = session.user_message("household", "确认")
    report = confirmed.report
    if report is None:
        return fail("confirmation did not produce execution report", response=to_jsonable(confirmed))
    final_ac = runtime.read("climate.living_room_ac")
    if not report.executed or not report.verified:
        return fail("execution report was not verified", report=to_jsonable(report))
    if final_ac is None or final_ac.state != "off":
        return fail("AC state readback did not reach off", final_state=to_jsonable(final_ac))
    learned = session.user_message("household", "以后这种情况直接执行")
    if learned.learned != "learned_auto_execute":
        return fail("feedback did not update auto-execute preference", response=to_jsonable(learned))
    second_devices = (
        DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"temperature": 24, "power_w": 1050}),
        DeviceState("window.living_room_window", "window", "living_room", "open"),
    )
    second = SpaceButlerSession(agent, InMemoryHomeRuntime(second_devices)).observe(snapshot)
    if second.status != "executed" or second.report is None or not second.report.verified:
        return fail("learned auto-execute did not change next behavior", response=to_jsonable(second))
    evidence = {
        "acceptance": "PASS",
        "scenario": "客厅无人、空调运行、窗户打开",
        "first_response": to_jsonable(suggestion),
        "confirmed_response": to_jsonable(confirmed),
        "learned_response": to_jsonable(learned),
        "second_response_after_learning": to_jsonable(second),
        "report": to_jsonable(report),
        "final_ac_state": to_jsonable(final_ac),
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


def fail(message: str, **payload: object) -> int:
    print(json.dumps({"acceptance": "FAIL", "message": message, **payload}, ensure_ascii=False, indent=2))
    return 1


def to_jsonable(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Enum):
        return value.value
    return value


if __name__ == "__main__":
    raise SystemExit(main())
