"""Tests for bounded Chinese numeric slot extraction."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.understanding.models import FastIntent
from custom_components.llama_conversation.edgehome.understanding.slot_extractor import ChineseSlotExtractor


@pytest.mark.parametrize(
    ("text", "intent", "expected"),
    [
        ("把客厅灯亮度调到百分之五十", FastIntent.SET_BRIGHTNESS, 50.0),
        ("空调调到26.5度", FastIntent.SET_TEMPERATURE, 26.5),
        ("窗帘拉开", FastIntent.CONTROL_CURTAIN, 100.0),
        ("窗帘关上", FastIntent.CONTROL_CURTAIN, 0.0),
        ("窗帘开到三十", FastIntent.CONTROL_CURTAIN, 30.0),
    ],
)
def test_extractor_parses_supported_values(text: str, intent: FastIntent, expected: float) -> None:
    slots = ChineseSlotExtractor().extract(text, intent)

    assert slots.value == expected
    assert slots.errors == []


@pytest.mark.parametrize(
    ("text", "intent", "error"),
    [
        ("把灯调亮", FastIntent.SET_BRIGHTNESS, "缺少目标数值"),
        ("亮度调到101%", FastIntent.SET_BRIGHTNESS, "百分比必须在 0 到 100 之间"),
        ("空调温度调到31度", FastIntent.SET_TEMPERATURE, "温度必须在 16 到 30 摄氏度之间"),
    ],
)
def test_extractor_marks_invalid_values_non_executable(text: str, intent: FastIntent, error: str) -> None:
    slots = ChineseSlotExtractor().extract(text, intent)

    assert error in slots.errors
