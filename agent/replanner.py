from __future__ import annotations

from agent.plan_schema import PlanStep


class Replanner:
    """Small deterministic fallback set. It never retries an offline or protected device."""

    def alternative(self, step: PlanStep, error: str | None) -> PlanStep | None:
        if error == "device_offline" and step.arguments.get("entity_id") == "light.living_room_main":
            return PlanStep(
                id=f"{step.id}_fallback",
                tool="set_light_brightness",
                arguments={"entity_id": "light.living_room_ambient", "brightness": 20},
                verify={"brightness": 20},
                optional=True,
                on_failure="report",
            )
        return None
