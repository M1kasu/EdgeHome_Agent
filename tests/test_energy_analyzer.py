"""确定性聚合、排行和对比测试。"""

from __future__ import annotations

from pathlib import Path

from custom_components.llama_conversation.edgehome.energy.analyzer import HomeEnergyAnalyzer
from custom_components.llama_conversation.edgehome.energy.loader import EnergyDatasetLoader


def test_energy_analyzer_returns_aggregate_rank_and_normal_week_delta() -> None:
    loader = EnergyDatasetLoader()
    abnormal = loader.load(Path("datasets/energy/abnormal_week.csv"))
    normal = loader.load(Path("datasets/energy/normal_week.csv"))

    analysis = HomeEnergyAnalyzer().analyze(abnormal.records, normal.records)

    assert analysis.record_count == 1176
    assert analysis.total_kwh > 0
    assert analysis.comparison_delta_kwh is not None and analysis.comparison_delta_kwh > 0
    assert analysis.high_consumption_rank[0] == "climate.edgehome_living_room_ac"
    assert analysis.temperature_ac_correlation is not None and analysis.temperature_ac_correlation > 0
