"""Regression tests for the dynamic-planning showcase evidence script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "accept_dynamic_planning_evidence.py"
SPEC = importlib.util.spec_from_file_location("accept_dynamic_planning_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_dynamic_planning_evidence_shows_state_and_constraint_differences(tmp_path: Path) -> None:
    result = MODULE.run(tmp_path)

    assert result == 0
    results = _jsonl(tmp_path / "results.jsonl")
    specs = _jsonl(tmp_path / "goal_specs.jsonl")
    plans = _jsonl(tmp_path / "plans.jsonl")

    assert len(results) >= 5
    assert all(row["passed"] for row in results)
    assert len({row["plan_hash"] for row in plans}) >= 4
    assert sum(row["edgehome_metrics"]["constraint_violations"] for row in results) == 0
    assert sum(row["edgehome_metrics"]["wrong_room_controls"] for row in results) == 0
    assert sum(row["edgehome_metrics"]["unnecessary_controls"] for row in results) == 0

    no_ac_plan = next(row for row in plans if row["test_id"] == "movie_c_user_forbids_ac")
    no_ac_controls = [
        step for step in no_ac_plan["plan_signature"] if step["entity_id"] == "climate.edgehome_living_room_ac"
    ]
    assert no_ac_controls == []

    min_light = next(row for row in plans if row["test_id"] == "movie_d_min_light")
    light_steps = [step for step in min_light["plan_signature"] if step["tool"] == "set_light_brightness"]
    assert light_steps and light_steps[0]["arguments"]["brightness"] >= 40

    bedroom = next(row for row in plans if row["test_id"] == "movie_e_bedroom_no_living")
    assert all(step["entity_id"] not in MODULE.LIVING_ENTITIES for step in bedroom["plan_signature"])

    min_temp_spec = next(row for row in specs if row["test_id"] == "movie_f_min_temperature")
    constraints = min_temp_spec["goal_spec"]["constraints"]
    assert any(item["target"] == "climate" and item["constraint_type"] == "min_value" for item in constraints)
