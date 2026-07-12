"""Safe continuation rules for structured EdgeHome clarification state."""

from __future__ import annotations

from dataclasses import dataclass

from .models import PendingClarification


@dataclass(frozen=True, slots=True)
class ClarificationResolution:
    """A bounded result for resuming a clarification on the next turn."""

    text: str | None = None
    repeat_question: str | None = None


class ContextResolver:
    """Resume only explicit answers; do not turn a vague confirmation into a guess."""

    _AFFIRMATIVE = frozenset({"对", "是", "好的", "好", "没错", "确认"})

    def resolve_clarification(self, pending: PendingClarification, reply: str) -> ClarificationResolution:
        """Merge a value answer, or retain a pending question when confirmation is insufficient."""
        normalized = reply.strip().lower().rstrip("。！!？?")
        if normalized in self._AFFIRMATIVE:
            if pending.candidate_entity_id and not pending.missing_slots:
                return ClarificationResolution(text=pending.original_text)
            return ClarificationResolution(repeat_question=pending.question)
        if not normalized:
            return ClarificationResolution(repeat_question=pending.question)
        return ClarificationResolution(text=f"{pending.original_text} {reply.strip()}")
