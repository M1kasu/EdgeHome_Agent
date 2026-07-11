"""本地确定性能耗汇总；计算过程不依赖语言模型。"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from math import sqrt

from .anomaly_detector import EnergyAnomalyDetector
from .schemas import EnergyAnalysis, EnergyIntervalFinding, EnergyRecord


class HomeEnergyAnalyzer:
    """按设备、房间、日期和时间段输出可复算的能耗分析。"""

    def __init__(self, detector: EnergyAnomalyDetector | None = None) -> None:
        self._detector = detector or EnergyAnomalyDetector()

    def analyze(self, records: Iterable[EnergyRecord], comparison: Iterable[EnergyRecord] | None = None) -> EnergyAnalysis:
        ordered = sorted(records, key=lambda item: (item.timestamp, item.entity_id))
        if not ordered:
            raise ValueError("没有可分析的能耗记录")
        device_kwh: defaultdict[str, float] = defaultdict(float)
        room_kwh: defaultdict[str, float] = defaultdict(float)
        daily_kwh: defaultdict[str, float] = defaultdict(float)
        for item in ordered:
            device_kwh[item.entity_id] += item.energy_kwh
            room_kwh[item.room] += item.energy_kwh
            daily_kwh[item.timestamp.date().isoformat()] += item.energy_kwh
        total = sum(item.energy_kwh for item in ordered)
        comparison_delta = None
        if comparison is not None:
            prior_total = sum(item.energy_kwh for item in comparison)
            comparison_delta = round(total - prior_total, 4)
        anomalies = self._detector.detect(ordered)
        unoccupied = [item for item in anomalies if item.kind == "unoccupied_ac"]
        standby = [item for item in anomalies if item.kind == "tv_standby"]
        return EnergyAnalysis(
            analysis_id=_analysis_id(ordered),
            period_start=ordered[0].timestamp,
            period_end=ordered[-1].timestamp,
            total_kwh=round(total, 4),
            device_kwh={key: round(value, 4) for key, value in sorted(device_kwh.items())},
            room_kwh={key: round(value, 4) for key, value in sorted(room_kwh.items())},
            daily_kwh={key: round(value, 4) for key, value in sorted(daily_kwh.items())},
            comparison_delta_kwh=comparison_delta,
            peak_power_w=round(max(item.power_w for item in ordered), 2),
            high_consumption_rank=[key for key, _ in sorted(device_kwh.items(), key=lambda item: (-item[1], item[0]))],
            unoccupied_intervals=unoccupied,
            standby_intervals=standby,
            temperature_ac_correlation=_temperature_ac_correlation(ordered),
            anomalies=anomalies,
            record_count=len(ordered),
        )


def _analysis_id(records: list[EnergyRecord]) -> str:
    material = "|".join(f"{item.timestamp.isoformat()}:{item.entity_id}:{item.energy_kwh}" for item in records)
    return f"energy-{hashlib.sha256(material.encode()).hexdigest()[:12]}"


def _temperature_ac_correlation(records: list[EnergyRecord]) -> float | None:
    values = [(item.temperature, item.power_w) for item in records if item.entity_id == "climate.edgehome_living_room_ac"]
    if len(values) < 2:
        return None
    temperatures, powers = zip(*values, strict=True)
    mean_temperature = sum(temperatures) / len(temperatures)
    mean_power = sum(powers) / len(powers)
    numerator = sum((temperature - mean_temperature) * (power - mean_power) for temperature, power in values)
    left = sqrt(sum((temperature - mean_temperature) ** 2 for temperature in temperatures))
    right = sqrt(sum((power - mean_power) ** 2 for power in powers))
    if not left or not right:
        return None
    return float(round(numerator / (left * right), 4))
