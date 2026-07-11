"""建议由异常证据驱动且自动排除保护设备。"""

from __future__ import annotations

from pathlib import Path

from custom_components.llama_conversation.edgehome.energy.analyzer import HomeEnergyAnalyzer
from custom_components.llama_conversation.edgehome.energy.loader import EnergyDatasetLoader
from custom_components.llama_conversation.edgehome.energy.recommendation_engine import EnergyRecommendationEngine


def test_energy_recommendations_only_target_virtual_nonprotected_devices() -> None:
    records = EnergyDatasetLoader().load(Path("datasets/energy/abnormal_week.csv")).records
    plan = EnergyRecommendationEngine().generate(HomeEnergyAnalyzer().analyze(records))

    assert [item.id for item in plan.recommendations] == ["unoccupied-ac", "tv-standby-socket", "night-light-off"]
    assert all(item.protected_excluded for item in plan.recommendations)
    assert all("refrigerator" not in item.target_entity_id and "router" not in item.target_entity_id for item in plan.recommendations)
