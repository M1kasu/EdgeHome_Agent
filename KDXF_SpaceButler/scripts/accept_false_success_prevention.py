"""Black-box acceptance for false-success prevention."""

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
    ExecutionStatus,
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
        DeviceState(
            "climate.living_room_ac",
            "climate",
            "living_room",
            "cool",
            {"temperature": 24, "power_w": 1050, "ack_without_state_change": True},
        ),
        DeviceState("window.living_room_window", "window", "living_room", "open"),
    )
    snapshot = SpatialSnapshot(
        scene="daily",
        time_of_day="afternoon",
        members=(HouseholdMember("member_01", "妈妈", MemberRole.ADULT, "bedroom", "resting"),),
        environment=EnvironmentState(27, 34, 62, 500, 18),
        devices=devices,
        rooms=(RoomState("living_room", occupied=False, unoccupied_minutes=25, window_state="open", power_w=1050),),
        snapshot_id="accept-false-success-001",
        source="black_box_acceptance",
    )
    session = SpaceButlerSession(SpaceButlerAgent(), InMemoryHomeRuntime(devices))
    suggestion = session.observe(snapshot)
    if suggestion.status != "needs_confirmation":
        return fail("scenario did not reach confirmable suggestion", response=to_jsonable(suggestion))
    confirmed = session.user_message("household", "确认")
    if confirmed.report is None:
        return fail("confirmation did not produce report", response=to_jsonable(confirmed))
    if confirmed.report.status != ExecutionStatus.VALIDATION_FAILED or confirmed.report.verified:
        return fail("false success was not blocked", response=to_jsonable(confirmed))
    print(
        json.dumps(
            {
                "acceptance": "PASS",
                "scenario": "command acknowledged but state unchanged",
                "response": to_jsonable(confirmed),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
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

