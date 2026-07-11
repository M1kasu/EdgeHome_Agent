"""Tests for deterministic edge-model planning context limits."""

from __future__ import annotations

from datetime import datetime, timezone

from custom_components.llama_conversation.edgehome.context.environment import EnvironmentContextSnapshot
from custom_components.llama_conversation.edgehome.models import EntitySnapshot
from custom_components.llama_conversation.edgehome.planner.context_budget import PlanningContextBudgeter


def test_context_budget_keeps_a_bounded_entity_prefix_and_safe_attributes() -> None:
    snapshot = EnvironmentContextSnapshot(
        goal="配置客厅",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entities=[
            EntitySnapshot(
                entity_id=f"light.demo_{index}",
                domain="light",
                state="on",
                attributes={"brightness": 80, "secret_like_value": "x" * 500},
            )
            for index in range(3)
        ],
    )

    result = PlanningContextBudgeter(max_entities=2, max_characters=1000).trim(snapshot)

    assert result.entity_count == 2
    assert result.dropped_entities == 1
    assert result.truncated is True
    assert "secret_like_value" not in result.payload["entities"][0]["attributes"]
