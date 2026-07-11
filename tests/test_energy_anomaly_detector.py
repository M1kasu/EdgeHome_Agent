"""三种演示异常及区间证据测试。"""

from __future__ import annotations

from pathlib import Path

from custom_components.llama_conversation.edgehome.energy.anomaly_detector import EnergyAnomalyDetector
from custom_components.llama_conversation.edgehome.energy.loader import EnergyDatasetLoader


def test_anomaly_detector_finds_unoccupied_ac_standby_and_night_light() -> None:
    records = EnergyDatasetLoader().load(Path("datasets/energy/abnormal_week.csv")).records
    findings = EnergyAnomalyDetector().detect(records)
    by_kind = {item.kind: item for item in findings}

    assert by_kind["unoccupied_ac"].duration_minutes >= 240
    assert by_kind["tv_standby"].energy_kwh > 0
    assert by_kind["night_light"].duration_minutes >= 300
