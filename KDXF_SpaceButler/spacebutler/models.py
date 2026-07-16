"""Typed domain models for proactive home-space services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum


class MemberRole(str, Enum):
    ADULT = "adult"
    ELDER = "elder"
    CHILD = "child"


class PlanPriority(str, Enum):
    SAFETY = "safety"
    COMFORT = "comfort"
    ENERGY = "energy"
    ENTERTAINMENT = "entertainment"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    EXECUTION_FAILED = "execution_failed"
    VALIDATION_FAILED = "validation_failed"
    DEVICE_UNAVAILABLE = "device_unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class HouseholdMember:
    member_id: str
    name: str
    role: MemberRole
    location: str
    activity: str
    is_home: bool = True


@dataclass(frozen=True)
class EnvironmentState:
    indoor_temperature: float
    outdoor_temperature: float
    humidity: float
    illuminance: int
    pm25: int
    electricity_price_level: str = "normal"


@dataclass(frozen=True)
class DeviceState:
    entity_id: str
    domain: str
    room: str
    state: str
    attributes: dict[str, object] = field(default_factory=dict)
    protected: bool = False


@dataclass(frozen=True)
class RoomState:
    room_id: str
    occupied: bool
    unoccupied_minutes: int = 0
    temperature: float | None = None
    humidity: float | None = None
    illuminance: int | None = None
    window_state: str | None = None
    power_w: float | None = None
    source: str = "configured"
    confidence: float = 1.0


@dataclass(frozen=True)
class SpatialSnapshot:
    scene: str
    time_of_day: str
    members: tuple[HouseholdMember, ...]
    environment: EnvironmentState
    devices: tuple[DeviceState, ...]
    rooms: tuple[RoomState, ...] = ()
    snapshot_id: str = "snapshot-local"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "local_runtime"
    ttl_seconds: int = 60
    missing_fields: tuple[str, ...] = ()
    confidence: float = 1.0

    def members_in_room(self, room: str) -> tuple[HouseholdMember, ...]:
        return tuple(member for member in self.members if member.is_home and member.location == room)

    def devices_in_room(self, room: str, domain: str | None = None) -> tuple[DeviceState, ...]:
        return tuple(
            device
            for device in self.devices
            if device.room == room and (domain is None or device.domain == domain)
        )

    def room_state(self, room: str) -> RoomState | None:
        return next((state for state in self.rooms if state.room_id == room), None)

    def is_fresh(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return current - self.created_at <= timedelta(seconds=self.ttl_seconds)


@dataclass(frozen=True)
class PlanAction:
    entity_id: str
    capability: str
    value: object
    reason: str


@dataclass(frozen=True)
class ServicePlan:
    plan_id: str
    title: str
    priority: PlanPriority
    proactive: bool
    target_members: tuple[str, ...]
    actions: tuple[PlanAction, ...]
    explanation: str
    requires_confirmation: bool = False


@dataclass(frozen=True)
class ActionResult:
    entity_id: str
    capability: str
    expected_value: object
    status: ExecutionStatus
    success: bool
    before: DeviceState | None
    after: DeviceState | None
    message: str


@dataclass(frozen=True)
class ExecutionReport:
    plan_id: str
    status: ExecutionStatus
    executed: bool
    verified: bool
    results: tuple[ActionResult, ...]
    verification_summary: str
