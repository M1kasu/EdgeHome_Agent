"""Tests for deterministic Recorder-statistics conversion, anomaly detection, and recommendations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.llama_conversation.edgehome.energy.conversation import EnergyConversationHandler
from custom_components.llama_conversation.edgehome.energy.statistics import (
    EnergyAnalyzer,
    EnergySample,
    HomeAssistantEnergyStatistics,
)


@pytest.mark.asyncio
async def test_energy_conversation_accepts_natural_weekly_increase_question() -> None:
    class Service:
        async def analyse_home_energy(self):
            return type(
                "Analysis",
                (),
                {
                    "total_kwh": 42.5,
                    "comparison_delta_kwh": 8.2,
                    "high_consumption_rank": ["客厅空调"],
                    "anomalies": [object()],
                },
            )()

    handler = EnergyConversationHandler(None, Service())

    result = await handler.async_handle("最近一周为什么用电量增加？", "conversation-1")

    assert result is not None
    assert result.startswith("最近一周累计用电 42.50 kWh")


@pytest.mark.asyncio
async def test_recorder_statistics_are_converted_to_valid_energy_samples(hass) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def reader(_hass, _start, _end, statistic_ids):
        assert statistic_ids == {"sensor.energy"}
        return {
            "sensor.energy": [
                {"start": start, "sum": 10.0},
                {"start": start + timedelta(hours=1), "sum": 11.0},
                {"start": start + timedelta(hours=2), "sum": "invalid"},
            ]
        }

    samples = await HomeAssistantEnergyStatistics(hass, reader=reader).async_samples(
        "sensor.energy",
        start,
        start + timedelta(hours=3),
    )

    assert [sample.sum_kwh for sample in samples] == [10.0, 11.0]


def test_energy_analyzer_detects_anomaly_and_returns_evidence_backed_recommendation() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [
        EnergySample(statistic_id="sensor.energy", timestamp=start + timedelta(hours=index), sum_kwh=value)
        for index, value in enumerate((0.0, 1.0, 2.0, 6.0))
    ]

    report = EnergyAnalyzer().build_report("sensor.energy", samples)

    assert report.total_kwh == 6.0
    assert len(report.anomalies) == 1
    assert report.anomalies[0].interval.energy_kwh == 4.0
    assert "4.00 kWh" in report.recommendations[0].message


def test_energy_analyzer_does_not_guess_when_history_is_insufficient() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [
        EnergySample(statistic_id="sensor.energy", timestamp=start + timedelta(hours=index), sum_kwh=value)
        for index, value in enumerate((0.0, 1.0))
    ]

    report = EnergyAnalyzer().build_report("sensor.energy", samples)

    assert report.anomalies == []
    assert report.recommendations == []
