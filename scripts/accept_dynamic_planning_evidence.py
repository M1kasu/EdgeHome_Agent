"""Generate evidence that EdgeHome planning is goal/state/constraint driven.

This is a dry-run planning acceptance test. It does not claim live MQTT execution;
it proves the planner input, structured constraints, plan differences, safety
decisions, and fair traditional-scene comparison that should be shown in demos.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.llama_conversation.edgehome.models import EntitySnapshot, TaskPlan  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.goal_scenes import GoalDrivenScenePlanner  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.goal_spec import GoalSpecBuilder  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.scoring import PlanEvaluator  # noqa: E402
from custom_components.llama_conversation.edgehome.planner.templates import SceneName  # noqa: E402


REPORT_DIR = ROOT / "reports" / "goal_agent_acceptance"
DYNAMIC_REPORT = ROOT / "reports" / "DYNAMIC_PLANNING_EVIDENCE.md"
COMPARISON_REPORT = ROOT / "reports" / "TRADITIONAL_VS_EDGEHOME.md"

CONTROL_TOOLS = {
    "turn_on_device",
    "turn_off_device",
    "set_light_brightness",
    "set_temperature",
    "set_hvac_mode",
    "control_curtain",
    "activate_security_mode",
}
LIVING_ENTITIES = {
    "light.edgehome_living_room_main",
    "cover.edgehome_living_room_curtain",
    "switch.edgehome_living_room_tv",
    "climate.edgehome_living_room_ac",
}
TRADITIONAL_MOVIE_STEPS = [
    {"tool": "control_curtain", "entity_id": "cover.edgehome_living_room_curtain", "position": 0},
    {"tool": "set_light_brightness", "entity_id": "light.edgehome_living_room_main", "brightness": 20},
    {"tool": "turn_on_device", "entity_id": "switch.edgehome_living_room_tv"},
    {"tool": "set_hvac_mode", "entity_id": "climate.edgehome_living_room_ac", "hvac_mode": "cool"},
    {"tool": "set_temperature", "entity_id": "climate.edgehome_living_room_ac", "temperature": 25.0},
]


@dataclass(frozen=True)
class ShowcaseCase:
    test_id: str
    title: str
    user_input: str
    snapshots: list[EntitySnapshot]
    expected_point: str
    prohibit_climate: bool = False
    min_brightness: int | None = None
    min_temperature: float | None = None
    target_room: str = "living_room"
    forbid_living_room: bool = False
    cool_enough: bool = False


def run(report_dir: Path = REPORT_DIR) -> int:
    report_dir.mkdir(parents=True, exist_ok=True)
    cases = _cases()
    spec_builder = GoalSpecBuilder()
    planner = GoalDrivenScenePlanner()
    evaluator = PlanEvaluator()

    results: list[dict[str, Any]] = []
    goal_specs: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    mqtt_commands: list[dict[str, Any]] = []
    mqtt_states: list[dict[str, Any]] = []
    execution_results: list[dict[str, Any]] = []

    for case in cases:
        goal_spec = spec_builder.build(case.user_input, SceneName.MOVIE.value, case.snapshots)
        draft = planner.build(case.user_input, goal_spec, SceneName.MOVIE, case.snapshots)
        if draft is None:
            raise RuntimeError(f"planner did not support {case.test_id}")
        plan = evaluator.evaluate(draft.plan, case.snapshots)
        edgehome_metrics = _evaluate_plan(case, plan)
        traditional_metrics = _evaluate_traditional(case)
        signature = _plan_signature(plan)
        plan_hash = _hash(signature)
        passed = (
            edgehome_metrics["constraint_violations"] == 0
            and edgehome_metrics["wrong_room_controls"] == 0
            and edgehome_metrics["unnecessary_controls"] == 0
        )

        goal_specs.append(
            {
                "test_id": case.test_id,
                "conversation_id": f"dryrun-{case.test_id}",
                "user_input": case.user_input,
                "goal_spec": goal_spec.model_dump(mode="json"),
            }
        )
        snapshots.append(
            {
                "test_id": case.test_id,
                "conversation_id": f"dryrun-{case.test_id}",
                "state_snapshot": {
                    entity.entity_id: {
                        "domain": entity.domain,
                        "state": entity.state,
                        "attributes": entity.attributes,
                        "friendly_name": entity.friendly_name,
                    }
                    for entity in case.snapshots
                },
            }
        )
        plans.append(
            {
                "test_id": case.test_id,
                "conversation_id": f"dryrun-{case.test_id}",
                "plan_hash": plan_hash,
                "plan_signature": signature,
                "plan": plan.model_dump(mode="json"),
            }
        )
        comparisons.append(
            {
                "test_id": case.test_id,
                "edgehome": edgehome_metrics,
                "traditional_movie_scene": traditional_metrics,
            }
        )
        mqtt_commands.append(
            {
                "test_id": case.test_id,
                "dry_run": True,
                "note": "This showcase checks planning evidence only. Run scripts/accept_m5_mqtt_movie.py for live MQTT command evidence.",
                "would_require_mqtt_for_steps": [step for step in signature if step["tool"] in CONTROL_TOOLS],
            }
        )
        mqtt_states.append(
            {
                "test_id": case.test_id,
                "dry_run": True,
                "note": "No live MQTT state is produced by this dry-run showcase.",
            }
        )
        execution_results.append(
            {
                "test_id": case.test_id,
                "dry_run": True,
                "planning_result": "safe_clarification" if plan.need_clarification else "plan_ready",
                "passed": passed,
                "reason": edgehome_metrics["reasons"],
            }
        )
        results.append(
            {
                "test_id": case.test_id,
                "title": case.title,
                "conversation_id": f"dryrun-{case.test_id}",
                "user_input": case.user_input,
                "state_summary": _state_summary(case.snapshots),
                "constraint_summary": _constraint_summary(goal_spec),
                "action_summary": _action_summary(plan),
                "expected_point": case.expected_point,
                "plan_hash": plan_hash,
                "status": "PASS" if passed else "FAIL",
                "passed": passed,
                "edgehome_metrics": edgehome_metrics,
                "traditional_metrics": traditional_metrics,
                "risk_assessment": plan.risk_assessment.model_dump(mode="json") if plan.risk_assessment else None,
                "plan_score": plan.plan_score.model_dump(mode="json") if plan.plan_score else None,
            }
        )

    _write_jsonl(report_dir / "results.jsonl", results)
    _write_jsonl(report_dir / "goal_specs.jsonl", goal_specs)
    _write_jsonl(report_dir / "state_snapshots.jsonl", snapshots)
    _write_jsonl(report_dir / "plans.jsonl", plans)
    _write_jsonl(report_dir / "traditional_comparison.jsonl", comparisons)
    _write_jsonl(report_dir / "mqtt_commands.jsonl", mqtt_commands)
    _write_jsonl(report_dir / "mqtt_states.jsonl", mqtt_states)
    _write_jsonl(report_dir / "execution_results.jsonl", execution_results)

    dynamic_summary = _dynamic_summary(results, plans)
    comparison_summary = _comparison_summary(results)
    DYNAMIC_REPORT.write_text(dynamic_summary, encoding="utf-8")
    COMPARISON_REPORT.write_text(comparison_summary, encoding="utf-8")
    return 0 if all(result["passed"] for result in results) and _unique_hashes(plans) >= 4 else 1


def _cases() -> list[ShowcaseCase]:
    return [
        ShowcaseCase(
            test_id="movie_a_day_hot",
            title="A：白天高光照高温",
            user_input="我要看电影",
            snapshots=_movie_snapshots(illuminance=800, temperature=29, curtain_position=100, tv_state="off", ac_state="off"),
            expected_point="高光照/高温应关闭窗帘、调暗主灯、打开电视并开启制冷。",
        ),
        ShowcaseCase(
            test_id="movie_b_night_cool",
            title="B：夜晚低温",
            user_input="我要看电影",
            snapshots=_movie_snapshots(illuminance=30, temperature=22, curtain_position=0, tv_state="off", ac_state="off"),
            expected_point="低光照/低温且窗帘已关闭时，不应重复关窗帘，也不应启动空调。",
            cool_enough=True,
        ),
        ShowcaseCase(
            test_id="movie_c_user_forbids_ac",
            title="C：用户明确不要空调",
            user_input="我要看电影，但不要打开空调。",
            snapshots=_movie_snapshots(illuminance=800, temperature=29, curtain_position=100, tv_state="off", ac_state="off"),
            expected_point="用户禁止空调时，计划中不得出现空调控制。",
            prohibit_climate=True,
        ),
        ShowcaseCase(
            test_id="movie_d_min_light",
            title="D：主灯不低于 40%",
            user_input="我要看电影，主灯不要低于40%。",
            snapshots=_movie_snapshots(illuminance=800, temperature=29, curtain_position=100, tv_state="off", ac_state="off"),
            expected_point="亮度下限必须进入结构化约束，最终主灯目标不得低于 40%。",
            min_brightness=40,
        ),
        ShowcaseCase(
            test_id="movie_e_bedroom_no_living",
            title="E：卧室观影且客厅不要动",
            user_input="我想在卧室看电影，客厅设备不要动。",
            snapshots=_movie_snapshots(illuminance=500, temperature=28, curtain_position=100, tv_state="off", ac_state="off")
            + [
                _entity("light.edgehome_bedroom_main", "light", "off", friendly_name="卧室主灯", brightness=0),
                _entity("climate.edgehome_bedroom_ac", "climate", "off", friendly_name="卧室空调", temperature=26.0),
                _entity("sensor.edgehome_bedroom_temperature", "sensor", "27", friendly_name="卧室温度", device_class="temperature"),
                _entity("binary_sensor.edgehome_bedroom_presence", "binary_sensor", "on", friendly_name="卧室有人", device_class="presence"),
            ],
            expected_point="目标房间为卧室且用户排除客厅时，不得控制任何客厅实体；设备不足时应澄清。",
            target_room="bedroom",
            forbid_living_room=True,
        ),
        ShowcaseCase(
            test_id="movie_f_min_temperature",
            title="F：空调不低于 25℃",
            user_input="我要看电影了，把客厅弄舒服一点，空调不要低于25度。",
            snapshots=_movie_snapshots(illuminance=780, temperature=30, curtain_position=100, tv_state="off", ac_state="off"),
            expected_point="温度下限必须进入结构化约束，最终空调目标不得低于 25℃。",
            min_temperature=25.0,
        ),
    ]


def _movie_snapshots(
    *,
    illuminance: float,
    temperature: float,
    curtain_position: int,
    tv_state: str,
    ac_state: str,
) -> list[EntitySnapshot]:
    return [
        _entity("binary_sensor.edgehome_living_room_presence", "binary_sensor", "on", friendly_name="客厅有人", device_class="presence"),
        _entity("sensor.edgehome_living_room_illuminance", "sensor", str(illuminance), friendly_name="客厅光照", device_class="illuminance"),
        _entity("sensor.edgehome_living_room_temperature", "sensor", str(temperature), friendly_name="客厅温度", device_class="temperature"),
        _entity("sensor.edgehome_living_room_humidity", "sensor", "60", friendly_name="客厅湿度", device_class="humidity"),
        _entity("binary_sensor.edgehome_window", "binary_sensor", "off", friendly_name="窗户", device_class="window"),
        _entity("light.edgehome_living_room_main", "light", "off", friendly_name="客厅主灯", brightness=0),
        _entity("cover.edgehome_living_room_curtain", "cover", "open" if curtain_position > 0 else "closed", friendly_name="客厅窗帘", current_position=curtain_position),
        _entity("switch.edgehome_living_room_tv", "switch", tv_state, friendly_name="客厅电视"),
        _entity("climate.edgehome_living_room_ac", "climate", ac_state, friendly_name="客厅空调", temperature=26.0 if ac_state != "off" else None),
    ]


def _entity(entity_id: str, domain: str, state: str, *, friendly_name: str, **attributes: Any) -> EntitySnapshot:
    clean_attributes = {key: value for key, value in attributes.items() if value is not None}
    return EntitySnapshot(entity_id=entity_id, domain=domain, state=state, friendly_name=friendly_name, attributes=clean_attributes)


def _evaluate_plan(case: ShowcaseCase, plan: TaskPlan) -> dict[str, Any]:
    controls = _control_steps(plan)
    reasons: list[str] = []
    constraint_violations = 0
    wrong_room_controls = 0
    unnecessary_controls = 0

    if case.prohibit_climate and any(step["entity_id"] == "climate.edgehome_living_room_ac" for step in controls):
        constraint_violations += 1
        reasons.append("违反用户禁止空调约束")
    if case.min_brightness is not None:
        brightness_values = [int(step["arguments"].get("brightness", 0)) for step in controls if step["tool"] == "set_light_brightness"]
        if brightness_values and min(brightness_values) < case.min_brightness:
            constraint_violations += 1
            reasons.append("主灯亮度低于用户下限")
    if case.min_temperature is not None:
        temp_values = [float(step["arguments"].get("temperature", 0)) for step in controls if step["tool"] == "set_temperature"]
        if temp_values and min(temp_values) < case.min_temperature:
            constraint_violations += 1
            reasons.append("空调温度低于用户下限")
    if case.forbid_living_room:
        living_controls = [step for step in controls if step["entity_id"] in LIVING_ENTITIES]
        if living_controls:
            wrong_room_controls += len(living_controls)
            reasons.append("卧室目标错误控制了客厅设备")
    if case.cool_enough:
        climate_controls = [step for step in controls if step["entity_id"] == "climate.edgehome_living_room_ac"]
        if climate_controls:
            unnecessary_controls += len(climate_controls)
            reasons.append("低温环境不应启动空调")
    if _curtain_position(case.snapshots) == 0 and any(step["tool"] == "control_curtain" for step in controls):
        unnecessary_controls += 1
        reasons.append("窗帘已关闭时重复关闭")

    if not reasons:
        reasons.append("满足本用例的目标、状态与约束要求")
    return {
        "control_steps": len(controls),
        "constraint_violations": constraint_violations,
        "wrong_room_controls": wrong_room_controls,
        "unnecessary_controls": unnecessary_controls,
        "need_clarification": plan.need_clarification,
        "reasons": reasons,
    }


def _evaluate_traditional(case: ShowcaseCase) -> dict[str, Any]:
    violations = 0
    wrong_room = 0
    unnecessary = 0
    reasons: list[str] = []
    if case.prohibit_climate:
        violations += 1
        reasons.append("固定观影场景仍会打开/设置空调")
    if case.min_brightness is not None and 20 < case.min_brightness:
        violations += 1
        reasons.append("固定观影场景主灯 20% 低于用户亮度下限")
    if case.min_temperature is not None and 25.0 < case.min_temperature:
        violations += 1
        reasons.append("固定观影场景温度低于用户下限")
    if case.forbid_living_room:
        wrong_room += len(TRADITIONAL_MOVIE_STEPS)
        violations += 1
        reasons.append("固定观影场景只控制客厅设备")
    if case.cool_enough:
        unnecessary += 2
        reasons.append("低温环境仍会启动并设置空调")
    if _curtain_position(case.snapshots) == 0:
        unnecessary += 1
        reasons.append("窗帘已关闭时仍重复关闭")
    if not reasons:
        reasons.append("固定场景在该环境下没有明显冲突")
    return {
        "control_steps": len(TRADITIONAL_MOVIE_STEPS),
        "constraint_violations": violations,
        "wrong_room_controls": wrong_room,
        "unnecessary_controls": unnecessary,
        "reasons": reasons,
    }


def _control_steps(plan: TaskPlan) -> list[dict[str, Any]]:
    return [
        {
            "id": step.id,
            "tool": step.tool,
            "entity_id": step.arguments.get("entity_id"),
            "arguments": step.arguments,
            "reason": step.reason,
            "expected_outcome": step.expected_outcome,
        }
        for step in plan.steps
        if step.tool in CONTROL_TOOLS
    ]


def _plan_signature(plan: TaskPlan) -> list[dict[str, Any]]:
    return [
        {
            "tool": step.tool,
            "entity_id": step.arguments.get("entity_id"),
            "arguments": {key: step.arguments[key] for key in sorted(step.arguments)},
            "reason": step.reason,
            "verify": step.verify.model_dump(mode="json") if step.verify else None,
        }
        for step in plan.steps
    ]


def _hash(signature: list[dict[str, Any]]) -> str:
    raw = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _curtain_position(snapshots: list[EntitySnapshot]) -> int | None:
    for snapshot in snapshots:
        if snapshot.entity_id == "cover.edgehome_living_room_curtain":
            value = snapshot.attributes.get("current_position")
            return int(value) if isinstance(value, (int, float)) else None
    return None


def _unique_hashes(plans: list[dict[str, Any]]) -> int:
    return len({plan["plan_hash"] for plan in plans})


def _dynamic_summary(results: list[dict[str, Any]], plans: list[dict[str, Any]]) -> str:
    passed = sum(1 for row in results if row["passed"])
    unique_hashes = _unique_hashes(plans)
    lines = [
        "# 动态规划有效性证据",
        "",
        "状态：READY_FOR_EXTERNAL_ACCEPTANCE",
        "",
        "本报告由 `scripts/accept_dynamic_planning_evidence.py` 自动生成。它是规划层 dry-run 证据，证明目标、状态和约束会改变计划；实时 MQTT 执行证据仍以 M1–M8 验收脚本为准。",
        "",
        f"- 用例数：{len(results)}",
        f"- 通过：{passed}/{len(results)}",
        f"- 不同计划哈希数：{unique_hashes}",
        f"- EdgeHome 约束违反：{sum(row['edgehome_metrics']['constraint_violations'] for row in results)}",
        f"- EdgeHome 错误房间控制：{sum(row['edgehome_metrics']['wrong_room_controls'] for row in results)}",
        f"- EdgeHome 不必要控制：{sum(row['edgehome_metrics']['unnecessary_controls'] for row in results)}",
        "",
        "| 用例 | 状态输入 | 结构化约束 | EdgeHome 动作摘要 | 规划哈希 | 证明点 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        lines.append(
            f"| {row['title']}<br>`{row['user_input']}` | {row['state_summary']} | {row['constraint_summary']} | "
            f"{row['action_summary']} | `{row['plan_hash']}` | {row['expected_point']} |"
        )
    lines.extend(
        [
            "",
            "原始证据文件：",
            "",
            "- `reports/goal_agent_acceptance/results.jsonl`",
            "- `reports/goal_agent_acceptance/goal_specs.jsonl`",
            "- `reports/goal_agent_acceptance/state_snapshots.jsonl`",
            "- `reports/goal_agent_acceptance/plans.jsonl`",
            "- `reports/goal_agent_acceptance/execution_results.jsonl`",
            "",
            "判定：这些用例覆盖同一句目标在不同状态下的差异计划、用户禁止项、参数下限和房间排除。若所有计划哈希完全相同，或出现约束/房间违反，本报告会失败。",
        ]
    )
    return "\n".join(lines) + "\n"


def _comparison_summary(results: list[dict[str, Any]]) -> str:
    edgehome_violations = sum(row["edgehome_metrics"]["constraint_violations"] for row in results)
    traditional_violations = sum(row["traditional_metrics"]["constraint_violations"] for row in results)
    edgehome_unnecessary = sum(row["edgehome_metrics"]["unnecessary_controls"] for row in results)
    traditional_unnecessary = sum(row["traditional_metrics"]["unnecessary_controls"] for row in results)
    edgehome_wrong_room = sum(row["edgehome_metrics"]["wrong_room_controls"] for row in results)
    traditional_wrong_room = sum(row["traditional_metrics"]["wrong_room_controls"] for row in results)
    lines = [
        "# 传统固定场景与 EdgeHome 动态规划对比",
        "",
        "本报告公平采用一个传统固定观影场景作为基线：关闭窗帘、主灯 20%、打开电视、空调制冷并设为 25℃。该基线适合固定需求，但不会读取本轮状态与临时约束。",
        "",
        "| 指标 | EdgeHome | 传统固定观影场景 |",
        "| --- | ---: | ---: |",
        f"| 用户约束违反次数 | {edgehome_violations} | {traditional_violations} |",
        f"| 错误房间控制次数 | {edgehome_wrong_room} | {traditional_wrong_room} |",
        f"| 不必要控制次数 | {edgehome_unnecessary} | {traditional_unnecessary} |",
        "",
        "| 用例 | EdgeHome 结论 | 传统固定场景结论 |",
        "| --- | --- | --- |",
    ]
    for row in results:
        edgehome_reason = "；".join(row["edgehome_metrics"]["reasons"])
        traditional_reason = "；".join(row["traditional_metrics"]["reasons"])
        lines.append(f"| {row['title']} | {edgehome_reason} | {traditional_reason} |")
    lines.extend(
        [
            "",
            "结论：传统固定场景执行速度和行为稳定，适合固定需求；EdgeHome 规划成本更高，但可以处理家庭状态、临时约束和房间边界。报告不宣称传统智能家居无用，也不把 dry-run 规划证据替代实时 MQTT 验收。",
        ]
    )
    return "\n".join(lines) + "\n"


def _state_summary(snapshots: list[EntitySnapshot]) -> str:
    values: dict[str, str] = {}
    for snapshot in snapshots:
        if snapshot.entity_id == "sensor.edgehome_living_room_illuminance":
            values["光照"] = f"{snapshot.state} lux"
        elif snapshot.entity_id == "sensor.edgehome_living_room_temperature":
            values["温度"] = f"{snapshot.state}℃"
        elif snapshot.entity_id == "cover.edgehome_living_room_curtain":
            values["窗帘"] = f"{snapshot.attributes.get('current_position', '?')}%"
        elif snapshot.entity_id == "switch.edgehome_living_room_tv":
            values["电视"] = "开" if snapshot.state == "on" else "关"
        elif snapshot.entity_id == "climate.edgehome_living_room_ac":
            values["空调"] = snapshot.state
        elif snapshot.entity_id == "binary_sensor.edgehome_bedroom_presence":
            values["卧室有人"] = "是" if snapshot.state == "on" else "否"
    return "；".join(f"{key}={value}" for key, value in values.items()) or "无"


def _constraint_summary(goal_spec: Any) -> str:
    constraints = [constraint.statement for constraint in goal_spec.constraints]
    if goal_spec.target_rooms:
        constraints.insert(0, "目标房间=" + ",".join(goal_spec.target_rooms))
    return "；".join(constraints) if constraints else "无显式约束"


def _action_summary(plan: TaskPlan) -> str:
    if plan.need_clarification:
        return "澄清/阻断：" + (plan.clarification_question or "需要用户确认")
    controls = _control_steps(plan)
    if not controls:
        return "无控制动作"
    actions: list[str] = []
    for step in controls:
        entity_id = step["entity_id"] or ""
        arguments = step["arguments"]
        if step["tool"] == "control_curtain":
            actions.append(f"窗帘→{arguments.get('position')}%")
        elif step["tool"] == "set_light_brightness":
            actions.append(f"主灯→{arguments.get('brightness')}%")
        elif step["tool"] == "turn_on_device" and "tv" in entity_id:
            actions.append("电视→开")
        elif step["tool"] == "set_hvac_mode":
            actions.append(f"空调模式→{arguments.get('hvac_mode')}")
        elif step["tool"] == "set_temperature":
            actions.append(f"空调温度→{arguments.get('temperature')}℃")
        else:
            actions.append(f"{step['tool']}({entity_id})")
    return "；".join(actions)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()
    return run(args.report_dir)


if __name__ == "__main__":
    raise SystemExit(main())
