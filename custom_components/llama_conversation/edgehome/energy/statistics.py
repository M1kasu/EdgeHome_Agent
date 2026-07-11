"""Read Recorder statistics and compute energy deltas, anomalies, and recommendations deterministically."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from statistics import median
from typing import Any

from homeassistant.components.recorder import statistics as recorder_statistics
from homeassistant.core import HomeAssistant
from pydantic import BaseModel, ConfigDict, Field


class EnergySample(BaseModel):
    """A cumulative energy-statistic sample in kWh from Home Assistant Recorder."""

    model_config = ConfigDict(extra="forbid")

    statistic_id: str = Field(min_length=1, max_length=255)
    timestamp: datetime
    sum_kwh: float = Field(ge=0.0)


class EnergyInterval(BaseModel):
    """One positive cumulative-sum delta over a Recorder statistics interval."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    energy_kwh: float = Field(ge=0.0)


class EnergyAnomaly(BaseModel):
    """A deterministic comparison of the latest interval with prior interval consumption."""

    model_config = ConfigDict(extra="forbid")

    statistic_id: str
    interval: EnergyInterval
    baseline_kwh: float = Field(gt=0.0)
    multiplier: float = Field(gt=1.0)


class EnergyRecommendation(BaseModel):
    """Actionable but non-controlling suggestion derived from a measured anomaly."""

    model_config = ConfigDict(extra="forbid")

    statistic_id: str
    message: str


class EnergyReport(BaseModel):
    """Structured deterministic energy result suitable for a future UI or model explanation."""

    model_config = ConfigDict(extra="forbid")

    statistic_id: str
    intervals: list[EnergyInterval] = Field(default_factory=list)
    total_kwh: float = Field(ge=0.0)
    anomalies: list[EnergyAnomaly] = Field(default_factory=list)
    recommendations: list[EnergyRecommendation] = Field(default_factory=list)


StatisticsReader = Callable[[HomeAssistant, datetime, datetime, set[str]], dict[str, list[dict[str, Any]]]]


class HomeAssistantEnergyStatistics:
    """Fetch cumulative-sum statistics using Recorder in an executor job."""

    def __init__(self, hass: HomeAssistant, reader: StatisticsReader | None = None) -> None:
        self._hass = hass
        self._reader = reader or _read_statistics

    async def async_samples(self, statistic_id: str, start: datetime, end: datetime) -> list[EnergySample]:
        """Convert Recorder rows into validated kWh samples; unsupported rows are ignored."""
        rows_by_id = await self._hass.async_add_executor_job(self._reader, self._hass, start, end, {statistic_id})
        samples: list[EnergySample] = []
        for row in rows_by_id.get(statistic_id, []):
            timestamp = row.get("start")
            value = row.get("sum")
            if not isinstance(timestamp, datetime) or not isinstance(value, (int, float)):
                continue
            samples.append(
                EnergySample(
                    statistic_id=statistic_id,
                    timestamp=timestamp.astimezone(timezone.utc),
                    sum_kwh=float(value),
                )
            )
        return sorted(samples, key=lambda sample: sample.timestamp)


class EnergyAnalyzer:
    """Calculate energy deltas and anomalies without model inference or mutable global state."""

    def build_report(self, statistic_id: str, samples: list[EnergySample], anomaly_multiplier: float = 2.0) -> EnergyReport:
        """Analyze cumulative kWh samples and make only evidence-backed non-controlling suggestions."""
        intervals = self.intervals(samples)
        anomalies = self.detect_anomalies(statistic_id, intervals, anomaly_multiplier)
        return EnergyReport(
            statistic_id=statistic_id,
            intervals=intervals,
            total_kwh=sum(interval.energy_kwh for interval in intervals),
            anomalies=anomalies,
            recommendations=[
                EnergyRecommendation(
                    statistic_id=anomaly.statistic_id,
                    message=(
                        f"最近能耗 {anomaly.interval.energy_kwh:.2f} kWh，约为基线 "
                        f"{anomaly.baseline_kwh:.2f} kWh 的 {anomaly.interval.energy_kwh / anomaly.baseline_kwh:.1f} 倍；"
                        "请检查设备运行时段和待机负载。"
                    ),
                )
                for anomaly in anomalies
            ],
        )

    @staticmethod
    def intervals(samples: list[EnergySample]) -> list[EnergyInterval]:
        """Turn sorted cumulative samples into non-negative consumption intervals."""
        ordered = sorted(samples, key=lambda sample: sample.timestamp)
        intervals: list[EnergyInterval] = []
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.statistic_id != current.statistic_id or current.sum_kwh < previous.sum_kwh:
                continue
            intervals.append(EnergyInterval(start=previous.timestamp, end=current.timestamp, energy_kwh=current.sum_kwh - previous.sum_kwh))
        return intervals

    @staticmethod
    def detect_anomalies(statistic_id: str, intervals: list[EnergyInterval], multiplier: float) -> list[EnergyAnomaly]:
        """Flag only a final interval that materially exceeds the median of prior positive intervals."""
        if len(intervals) < 3:
            return []
        baseline_values = [interval.energy_kwh for interval in intervals[:-1] if interval.energy_kwh > 0]
        if not baseline_values:
            return []
        baseline = median(baseline_values)
        latest = intervals[-1]
        if latest.energy_kwh < baseline * multiplier:
            return []
        return [EnergyAnomaly(statistic_id=statistic_id, interval=latest, baseline_kwh=baseline, multiplier=multiplier)]


def _read_statistics(hass: HomeAssistant, start: datetime, end: datetime, statistic_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    rows = recorder_statistics.statistics_during_period(
        hass,
        start,
        end,
        statistic_ids,
        "hour",
        None,
        {"sum"},
    )
    return {statistic_id: [dict(row) for row in values] for statistic_id, values in rows.items()}
