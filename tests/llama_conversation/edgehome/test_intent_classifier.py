"""Tests for conservative Chinese fast-path intent classification."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.understanding.intent_classifier import ChineseIntentClassifier
from custom_components.llama_conversation.edgehome.understanding.models import FastIntent


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("打开客厅主灯", FastIntent.TURN_ON),
        ("关闭卧室风扇", FastIntent.TURN_OFF),
        ("把客厅灯亮度调到百分之五十", FastIntent.SET_BRIGHTNESS),
        ("空调温度调到二十六度", FastIntent.SET_TEMPERATURE),
        ("把窗帘拉开", FastIntent.CONTROL_CURTAIN),
        ("客厅灯现在是什么状态？", FastIntent.QUERY_STATE),
    ],
)
def test_classifier_recognizes_supported_single_commands(text: str, intent: FastIntent) -> None:
    result = ChineseIntentClassifier().classify(text)

    assert result.intent == intent
    assert result.confidence >= 0.95


@pytest.mark.parametrize("text", ["打开客厅灯然后关闭卧室灯", "十分钟后打开客厅灯", "如果天黑就打开客厅灯"])
def test_classifier_rejects_complex_requests(text: str) -> None:
    result = ChineseIntentClassifier().classify(text)

    assert result.intent == FastIntent.UNKNOWN
    assert result.confidence == 0.0
