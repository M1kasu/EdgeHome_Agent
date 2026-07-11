"""自动化计划 schema 白名单测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from custom_components.llama_conversation.edgehome.energy.schemas import EnergyIntervalFinding, EnergyRecommendation


def test_energy_plan_schema_rejects_protected_or_arbitrary_target() -> None:
    finding = EnergyIntervalFinding(
        kind="unoccupied_ac", entity_id="climate.edgehome_living_room_ac", start="2026-01-01T00:00:00Z",
        end="2026-01-01T01:00:00Z", duration_minutes=60, energy_kwh=1, detail="test"
    )
    with pytest.raises(ValidationError):
        EnergyRecommendation(
            id="bad-target", title="bad", rationale="bad", target_entity_id="switch.edgehome_refrigerator",
            rule_type="unoccupied_ac", evidence=[finding]
        )
