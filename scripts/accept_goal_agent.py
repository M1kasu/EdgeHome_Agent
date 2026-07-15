"""Goal-driven EdgeHome acceptance harness with traceable evidence files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.llama_conversation.edgehome.models import EntitySnapshot, PlanStep, TaskPlan, VerificationRule  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.goal_scenes import GoalDrivenScenePlanner  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.goal_spec import GoalSpecBuilder  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.scoring import PlanEvaluator  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.templates import SceneName  # noqa: E402


DEFAULT_REPORT = ROOT / "reports" / "goal_agent_acceptance"


def _entity(entity_id: str, domain: str, state: str, **attributes: Any) -> EntitySnapshot:
    friendly_name = attributes.pop("friendly_name", None)
    return EntitySnapshot(entity_id=entity_id, domain=domain, state=state, attributes=attributes, friendly_name=friendly_name)


def _base_living_snapshots(*, illuminance: float = 650, temperature: float = 29, curtain_position: int = 100) -> list[EntitySnapshot]:
    return [
        _entity("light.edgehome_living_room_main", "light", "off", brightness=0, friendly_name="客厅主灯"),
        _entity("cover.edgehome_living_room_curtain", "cover", "open", current_position=curtain_position, friendly_name="客厅窗帘"),
        _entity("switch.edgehome_living_room_tv", "switch", "off", friendly_name="客厅电视"),
        _entity("climate.edgehome_living_room_ac", "climate", "off", temperature=26.0, friendly_name="客厅空调"),
        _entity("sensor.edgehome_living_room_illuminance", "sensor", str(illuminance), device_class="illuminance", friendly_name="客厅光照"),
        _entity("sensor.edgehome_living_room_temperature", "sensor", str(temperature), device_class="temperature", friendly_name="客厅温度"),
    ]


def _leaving_snapshots(*, window_state: str = "off", presence_state: str = "off") -> list[EntitySnapshot]:
    return _base_living_snapshots(illuminance=300, temperature=25, curtain_position=80) + [
        _entity("binary_sensor.edgehome_window", "binary_sensor", window_state, device_class="window", friendly_name="窗户"),
        _entity("binary_sensor.edgehome_living_room_presence", "binary_sensor", presence_state, device_class="presence", friendly_name="客厅有人"),
        _entity("input_boolean.edgehome_security_mode", "input_boolean", "off", friendly_name="安防模式"),
        _entity("switch.edgehome_router", "switch", "on", friendly_name="路由器"),
        _entity("switch.edgehome_refrigerator", "switch", "on", friendly_name="冰箱"),
    ]


SCENARIOS = [
    {
        "id": "movie_high_light_dynamic",
        "input": "帮我准备客厅观影场景",
        "scene": SceneName.MOVIE,
        "snapshots": _base_living_snapshots(illuminance=850, temperature=29, curtain_position=100),
        "expect": lambda plan: _has_tool(plan, "control_curtain") and _brightness(plan) == 20,
        "requirement": "高光照观影应关闭窗帘并把主灯压暗。",
    },
    {
        "id": "movie_low_light_no_climate",
        "input": "帮我准备观影，但不要打开空调，主灯不低于40%",
        "scene": SceneName.MOVIE,
        "snapshots": _base_living_snapshots(illuminance=80, temperature=23, curtain_position=0),
        "expect": lambda plan: _brightness(plan) >= 40 and not _controls_entity(plan, "climate.edgehome_living_room_ac"),
        "requirement": "用户禁止空调时不得控制空调，低光照仍要满足最低亮度。",
    },
    {
        "id": "movie_bedroom_clarify",
        "input": "帮我准备卧室观影",
        "scene": SceneName.MOVIE,
        "snapshots": _base_living_snapshots(),
        "expect": lambda plan: plan.need_clarification is True and not any(step.verify for step in plan.steps),
        "requirement": "卧室未接入独立观影设备时必须澄清。",
    },
    {
        "id": "leaving_safe_no_protected_shutdown",
        "input": "我要出门了，关闭所有不必要设备，包括冰箱和路由器也不要乱动",
        "scene": SceneName.LEAVING,
        "snapshots": _leaving_snapshots(window_state="off", presence_state="off"),
        "expect": lambda plan: not _controls_entity(plan, "switch.edgehome_router") and not _controls_entity(plan, "switch.edgehome_refrigerator"),
        "requirement": "离家计划不得控制路由器或冰箱。",
    },
    {
        "id": "leaving_window_open_blocks",
        "input": "我要出门了，进入离家保护",
        "scene": SceneName.LEAVING,
        "snapshots": _leaving_snapshots(window_state="on", presence_state="off"),
        "expect": lambda plan: plan.need_clarification is True and plan.risk_assessment is not None and plan.risk_assessment.blocked,
        "requirement": "窗户打开时离家保护必须阻断。",
    },
    {
        "id": "protected_shutdown_risk_scoring",
        "input": "把路由器关掉",
        "scene": None,
        "snapshots": [_entity("switch.edgehome_router", "switch", "on", friendly_name="路由器")],
        "expect": lambda plan: plan.risk_assessment is not None and plan.risk_assessment.blocked,
        "requirement": "受保护设备关机必须被风险评估标记为阻断。",
    },
]


def run(report_dir: Path = DEFAULT_REPORT) -> int:
    report_dir.mkdir(parents=True, exist_ok=True)
    spec_builder = GoalSpecBuilder()
    scene_planner = GoalDrivenScenePlanner()
    evaluator = PlanEvaluator()
    results: list[dict[str, Any]] = []
    specs: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        goal_spec = spec_builder.build(scenario["input"], scenario["scene"].value if scenario["scene"] else None, scenario["snapshots"])
        if scenario["scene"] is None:
            plan = evaluator.evaluate(
                TaskPlan(
                    goal=scenario["input"],
                    goal_spec=goal_spec,
                    steps=[
                        PlanStep(
                            id="protected_shutdown_probe",
                            tool="turn_off_device",
                            arguments={"entity_id": "switch.edgehome_router"},
                            verify=VerificationRule(entity_id="switch.edgehome_router", state="off"),
                        )
                    ],
                ),
                scenario["snapshots"],
            )
        else:
            draft = scene_planner.build(scenario["input"], goal_spec, scenario["scene"], scenario["snapshots"])
            if draft is None:
                raise RuntimeError(f"unsupported scenario {scenario['id']}")
            plan = evaluator.evaluate(draft.plan, scenario["snapshots"])
        passed = bool(scenario["expect"](plan))
        specs.append({"test_id": scenario["id"], "input": scenario["input"], "goal_spec": goal_spec.model_dump(mode="json")})
        plans.append({"test_id": scenario["id"], "plan": plan.model_dump(mode="json")})
        results.append(
            {
                "test_id": scenario["id"],
                "input": scenario["input"],
                "requirement": scenario["requirement"],
                "passed": passed,
                "status": "PASS" if passed else "FAIL",
                "risk": plan.risk_assessment.model_dump(mode="json") if plan.risk_assessment else None,
                "score": plan.plan_score.model_dump(mode="json") if plan.plan_score else None,
            }
        )

    _write_jsonl(report_dir / "goal_specs.jsonl", specs)
    _write_jsonl(report_dir / "plans.jsonl", plans)
    _write_jsonl(report_dir / "test_results.jsonl", results)
    _write_jsonl(report_dir / "mqtt_messages.jsonl", [])
    _write_jsonl(report_dir / "simulator_events.jsonl", [])
    _write_jsonl(report_dir / "ha_states.jsonl", [])
    passed = sum(1 for result in results if result["passed"])
    summary = [
        "# EdgeHome Goal Agent Acceptance Harness",
        "",
        f"Status: {'READY_FOR_EXTERNAL_ACCEPTANCE' if passed == len(results) else 'NEEDS_REMEDIATION'}",
        "",
        f"Result: {passed}/{len(results)} local goal-policy checks passed.",
        "",
        "This harness records structured goal specs, plans, risk scores, and placeholders for live MQTT/HA evidence.",
        "It is not a substitute for independent external black-box acceptance.",
    ]
    (report_dir / "acceptance_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return 0 if passed == len(results) else 1


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _has_tool(plan: TaskPlan, tool: str) -> bool:
    return any(step.tool == tool for step in plan.steps)


def _brightness(plan: TaskPlan) -> int:
    for step in plan.steps:
        if step.tool == "set_light_brightness":
            return int(step.arguments.get("brightness", -1))
    return -1


def _controls_entity(plan: TaskPlan, entity_id: str) -> bool:
    return any(
        step.arguments.get("entity_id") == entity_id
        and step.tool in {"turn_on_device", "turn_off_device", "set_hvac_mode", "set_temperature", "set_light_brightness", "control_curtain"}
        for step in plan.steps
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    return run(args.report_dir)


if __name__ == "__main__":
    raise SystemExit(main())
