from __future__ import annotations

from understanding.intent_classifier import IntentResult


class ComplexityRouter:
    COMPLEX_INTENTS = {"complex_task", "activate_scene", "query_energy", "query_manual", "create_delayed_task"}

    def is_complex(self, text: str, result: IntentResult) -> bool:
        if result.intent in self.COMPLEX_INTENTS or result.confidence < 0.8:
            return True
        return any(token in text for token in ("但", "不要", "如果", "同时", "然后", "也一样"))
