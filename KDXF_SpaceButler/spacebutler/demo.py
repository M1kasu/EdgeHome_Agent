"""Command-line demo for the SpaceButler core Agent."""

from __future__ import annotations

from .agent import SpaceButlerAgent
from .models import DeviceState, EnvironmentState, HouseholdMember, MemberRole, SpatialSnapshot


def build_demo_agent() -> SpaceButlerAgent:
    agent = SpaceButlerAgent()
    agent.memory.learn_preference("mom", "return_home", "target_temperature", 25)
    agent.memory.learn_preference("dad", "movie", "target_temperature", 24)
    agent.memory.learn_preference("child", "movie", "target_temperature", 27)
    return agent


def build_demo_snapshot() -> SpatialSnapshot:
    return SpatialSnapshot(
        scene="return_home",
        time_of_day="evening",
        members=(
            HouseholdMember("mom", "妈妈", MemberRole.ADULT, "living_room", "returning_home"),
        ),
        environment=EnvironmentState(
            indoor_temperature=29,
            outdoor_temperature=34,
            humidity=65,
            illuminance=80,
            pm25=18,
        ),
        devices=(
            DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"temperature": 28}),
            DeviceState("light.living_room_main", "light", "living_room", "off", {"brightness": 0}),
        ),
    )


def main() -> None:
    agent = build_demo_agent()
    plans = agent.observe_and_plan(build_demo_snapshot())
    for plan in plans:
        print(f"[{plan.priority.value}] {plan.title}: {plan.explanation}")
        for action in plan.actions:
            print(f"  - {action.entity_id}.{action.capability} -> {action.value} ({action.reason})")


if __name__ == "__main__":
    main()

