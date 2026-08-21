"""External acceptance for validated edge-LLM preference routing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler import (  # noqa: E402
    DeviceState,
    EdgeLanguageRouter,
    EdgeLlmClient,
    EnvironmentState,
    HouseholdMemory,
    InMemoryHomeRuntime,
    RoomState,
    SpaceButlerAgent,
    SpaceButlerSession,
    SpatialSnapshot,
)


EDGE_LLM_URL = os.getenv("EDGE_LLM_URL", "http://127.0.0.1:12881")
MEMORY_DB = Path(
    os.getenv(
        "SPACEBUTLER_EDGE_MEMORY_DB",
        str(ROOT / "deployment" / "runtime" / "agent" / "edge_llm_memory.db"),
    )
)


def main() -> int:
    memory = HouseholdMemory(MEMORY_DB)
    memory.clear()
    router = EdgeLanguageRouter(EdgeLlmClient(EDGE_LLM_URL, timeout_seconds=60))
    agent = SpaceButlerAgent(memory)
    response = SpaceButlerSession(agent, InMemoryHomeRuntime(()), router).user_message(
        "household",
        "等客厅没人待够四十分钟再帮我处理空调。",
    )

    reconstructed = SpaceButlerAgent(HouseholdMemory(MEMORY_DB))
    below_threshold = reconstructed.observe_and_plan(_snapshot(39))
    at_threshold = reconstructed.observe_and_plan(_snapshot(40))
    preferences = reconstructed.memory.export_preferences()
    passed = all(
        (
            response.status == "learned",
            response.route == "edge_llm",
            response.learned == "learned_unoccupied_40",
            not any(plan.plan_id == "empty_room_open_window_energy_guard" for plan in below_threshold),
            any(plan.plan_id == "empty_room_open_window_energy_guard" for plan in at_threshold),
            len(preferences) == 1,
            bool(preferences and preferences[0].source == "edge_llm_validated"),
            bool(preferences and preferences[0].value == 40),
        )
    )
    print(
        json.dumps(
            {
                "acceptance": "PASS" if passed else "FAIL",
                "boundary": "local_llama_cpp -> validated_intent_allowlist -> sqlite_memory -> deterministic_service_engine",
                "response": {
                    "status": response.status,
                    "route": response.route,
                    "learned": response.learned,
                },
                "preference": {
                    "value": preferences[0].value if preferences else None,
                    "source": preferences[0].source if preferences else None,
                    "confidence": preferences[0].confidence if preferences else None,
                },
                "below_threshold_triggered": any(
                    plan.plan_id == "empty_room_open_window_energy_guard" for plan in below_threshold
                ),
                "at_threshold_triggered": any(
                    plan.plan_id == "empty_room_open_window_energy_guard" for plan in at_threshold
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 1


def _snapshot(unoccupied_minutes: int) -> SpatialSnapshot:
    return SpatialSnapshot(
        scene="daily",
        time_of_day="afternoon",
        members=(),
        environment=EnvironmentState(27, 34, 62, 500, 18),
        devices=(
            DeviceState("climate.living_room_ac", "climate", "living_room", "cool", {"power_w": 1050}),
            DeviceState("window.living_room_window", "window", "living_room", "open"),
        ),
        rooms=(
            RoomState(
                "living_room",
                occupied=False,
                unoccupied_minutes=unoccupied_minutes,
                window_state="open",
                power_w=1050,
            ),
        ),
    )

if __name__ == "__main__":
    raise SystemExit(main())
