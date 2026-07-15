"""Tests for the goal-agent acceptance evidence harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "accept_goal_agent.py"
SPEC = importlib.util.spec_from_file_location("accept_goal_agent", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_goal_agent_acceptance_harness_writes_required_evidence(tmp_path: Path) -> None:
    result = MODULE.run(tmp_path)

    assert result == 0
    for name in (
        "goal_specs.jsonl",
        "plans.jsonl",
        "test_results.jsonl",
        "mqtt_messages.jsonl",
        "simulator_events.jsonl",
        "ha_states.jsonl",
        "acceptance_summary.md",
    ):
        assert (tmp_path / name).is_file()
    assert "READY_FOR_EXTERNAL_ACCEPTANCE" in (tmp_path / "acceptance_summary.md").read_text(encoding="utf-8")
