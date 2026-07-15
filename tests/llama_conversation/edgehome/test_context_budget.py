"""Tests for deterministic edge-model planning context limits."""

from __future__ import annotations

from datetime import datetime, timezone

from custom_components.llama_conversation.edgehome.context.environment import EnvironmentContextSnapshot
from custom_components.llama_conversation.edgehome.context.home_state import build_home_state_snapshot
from custom_components.llama_conversation.edgehome.models import EntitySnapshot
from custom_components.llama_conversation.edgehome.planner.context_budget import PlanningContextBudgeter


def test_context_budget_keeps_a_bounded_entity_prefix_and_safe_attributes() -> None:
    entities = [
        EntitySnapshot(
            entity_id=f"light.demo_{index}",
            domain="light",
            state="on",
            attributes={"brightness": 80, "secret_like_value": "x" * 500},
        )
        for index in range(3)
    ]
    home_state = build_home_state_snapshot("配置客厅", None, entities)
    snapshot = EnvironmentContextSnapshot(
        goal="配置客厅",
        captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stale_after=home_state.stale_after,
        snapshot_id=home_state.snapshot_id,
        home_state=home_state,
        entities=entities,
    )

    result = PlanningContextBudgeter(max_entities=2, max_characters=2000).trim(snapshot)

    assert result.entity_count == 2
    assert result.dropped_entities == 1
    assert result.truncated is True
    assert result.payload["snapshot_id"] is not None
    assert "secret_like_value" not in result.payload["entities"][0]["attributes"]
