from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    slots: dict[str, Any]


class IntentClassifier:
    """Explainable rule classifier for the offline fast path.

    It is intentionally conservative: unknown/compound input becomes ``complex_task``
    instead of issuing a potentially unsafe device command.
    """

    def classify(self, text: str, slots: dict[str, Any], memory: dict[str, Any] | None = None) -> IntentResult:
        normalized = text.lower().strip()
        if not normalized:
            return IntentResult("unknown", 0.0, slots)
        if any(word in normalized for word in ("用电", "能耗", "耗电", "电量")):
            return IntentResult("query_energy", 0.98, slots)
        if any(word in normalized for word in ("说明书", "故障", "错误代码", "e1", "e2")):
            return IntentResult("query_manual", 0.94, slots)
        if slots.get("scene"):
            return IntentResult("activate_scene", 0.98, slots)
        if slots.get("referenced_previous") and memory and memory.get("last_intent"):
            return IntentResult(memory["last_intent"], 0.90, slots)
        compound_markers = ("，", "、", "但", "不要", "如果", "同时", "然后")
        if slots.get("delay_minutes") is not None and len([item for item in (slots.get("temperature"), slots.get("brightness"), slots.get("position")) if item is not None]) > 0:
            return IntentResult("complex_task", 0.88, slots)
        if slots.get("delay_minutes") is not None:
            return IntentResult("create_delayed_task", 0.96, slots)
        if "状态" in normalized or any(word in normalized for word in ("温度多少", "开着吗", "打开吗")):
            return IntentResult("query_state", 0.93, slots)
        if "brightness" in slots:
            return IntentResult("set_brightness", 0.96, slots)
        if "temperature" in slots:
            return IntentResult("set_temperature", 0.96, slots)
        if slots.get("device") == "curtain" and "position" in slots:
            return IntentResult("control_curtain", 0.96, slots)
        if any(word in normalized for word in ("关闭", "关掉", "关上", "断开")):
            return IntentResult("turn_off", 0.91 if slots.get("device") else 0.45, slots)
        if any(word in normalized for word in ("打开", "开启", "开一下")):
            return IntentResult("turn_on", 0.91 if slots.get("device") else 0.45, slots)
        if any(marker in normalized for marker in compound_markers):
            return IntentResult("complex_task", 0.75, slots)
        return IntentResult("complex_task", 0.30, slots)
