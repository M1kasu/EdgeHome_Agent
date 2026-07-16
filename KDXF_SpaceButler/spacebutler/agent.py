"""SpaceButler orchestration entry point."""

from __future__ import annotations

from .memory import HouseholdMemory
from .models import ServicePlan, SpatialSnapshot
from .services import ProactiveServiceEngine


class SpaceButlerAgent:
    """A compact Agent kernel for the KDXF SpaceMind challenge."""

    def __init__(self, memory: HouseholdMemory | None = None) -> None:
        self.memory = memory or HouseholdMemory()
        self._services = ProactiveServiceEngine(self.memory)

    def observe_and_plan(self, snapshot: SpatialSnapshot) -> list[ServicePlan]:
        """Return proactive or semi-proactive plans for the current home-space state."""
        return self._services.propose(snapshot)

