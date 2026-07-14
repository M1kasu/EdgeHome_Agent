"""Tests for safe continuation of a pending clarification."""

from custom_components.llama_conversation.edgehome.context.models import PendingClarification
from custom_components.llama_conversation.edgehome.context.resolver import ContextResolver


def test_confirmation_without_missing_slots_resumes_only_the_saved_goal() -> None:
    pending = PendingClarification(
        original_text="\u628a\u706f\u8c03\u6697\u4e00\u70b9",
        question="\u4f60\u6307\u5ba2\u5385\u4e3b\u706f\u5417\uff1f",
        candidate_entity_id="light.edgehome_living_room_main",
    )

    resolution = ContextResolver().resolve_clarification(pending, "\u5bf9")

    assert resolution.text == pending.original_text
    assert resolution.repeat_question is None


def test_confirmation_cannot_fill_a_missing_numeric_slot() -> None:
    pending = PendingClarification(
        original_text="\u628a\u5ba2\u5385\u4e3b\u706f\u8c03\u5230",
        question="\u8bf7\u8865\u5145\u4eae\u5ea6\u3002",
        missing_slots=["value"],
    )

    resolution = ContextResolver().resolve_clarification(pending, "\u5bf9")

    assert resolution.text is None
    assert resolution.repeat_question == pending.question
