"""Diagnose goal_satisfaction < 0.75 samples: ABCDEF classification.

对每个低满意度样本记录：约束 / 选中动作 / 预测 / 观测终态 / 未满足维度 / 误差，
并按规则归类：
  A goal_interval     模型约束区间与物理可达终态冲突
  B planner_actions   未满足维度没有对应动作（规划动作不足）
  C scorer_choice     有动作且生效但约束仍未满足（选值问题）
  D predictor         预测与观测偏差大（>2）
  E convergence       温度类渐进维度：空调已按目标制冷但模拟时间不足
  F evaluation        其他
温度维度允许"进入区间或明确收敛"计数（用户语义允许渐进满足）；
亮度/窗帘为即时期量，仍按终态验证。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))

from run_agent_benchmark import UTTERANCES, SimulatedHome  # noqa: E402

from spacebutler.agent.capabilities import build_capability_registry  # noqa: E402
from spacebutler.agent.executor import AgentExecutor  # noqa: E402
from spacebutler.agent.goal_compiler import GoalCompiler  # noqa: E402
from spacebutler.agent.models import RoomState  # noqa: E402
from spacebutler.agent.planner import CounterfactualPlanner  # noqa: E402
from spacebutler.agent.verifier import Verifier  # noqa: E402
from spacebutler.llm.client import LlmClient  # noqa: E402

DIM_METRIC = {"thermal": "temperature", "lighting": "brightness", "glare": "curtain_position"}


def room_state(home: SimulatedHome) -> RoomState:
    brightness = round(home.light_brightness * 100 / 255, 1) if home.light_on else 0.0
    return RoomState(
        temperature=round(home.temperature, 2),
        presence=True,
        light_on=home.light_on,
        brightness_pct=brightness,
        curtain_position=home.curtain,
        ac_mode=home.ac_mode,
        ac_target=home.ac_target,
    )


def snapshot(home: SimulatedHome) -> dict[str, object]:
    brightness = round(home.light_brightness * 100 / 255, 1) if home.light_on else 0.0
    return {"temperature": round(home.temperature, 2), "brightness_pct": brightness, "curtain_position": home.curtain}


def classify(constraint, value: float | None, has_action: bool, predicted, home: SimulatedHome) -> str:
    distance = constraint.distance(value)
    if distance <= 0:
        return "satisfied"
    metric = constraint.metric
    if metric == "temperature":
        # 渐进维度：空调已按目标制冷 = 明确收敛趋势（用户语义允许渐进满足）。
        if home.ac_mode == "cool" and home.ac_target is not None and constraint.lower is not None:
            if constraint.lower <= home.ac_target <= constraint.upper:
                return "E_convergence"
        return "B_planner_actions" if not has_action else "A_goal_interval"
    if not has_action:
        return "B_planner_actions"
    if predicted is not None:
        predicted_value = {
            "brightness": predicted.brightness_pct,
            "curtain_position": predicted.curtain_position,
            "temperature": predicted.temperature,
        }.get(metric)
        if predicted_value is not None and constraint.distance(predicted_value) <= 0:
            # 预测可达但实际没到：环境收敛问题（即时期量通常由验证保证，少见）。
            return "E_convergence"
    return "C_scorer_choice"


def main() -> int:
    llm = LlmClient.from_env()
    compiler = GoalCompiler()
    planner = CounterfactualPlanner()
    executor = AgentExecutor(home_control := (lambda device_id, payload: (_ for _ in ()).throw(RuntimeError("unused"))))
    verifier = Verifier()

    homes: dict[int, SimulatedHome] = {}
    controls: dict[int, object] = {}

    def make_control(index: int, home: SimulatedHome):
        def control(device_id: str, payload: dict[str, object]) -> dict[str, object]:
            result = homes[index].control(device_id, payload)
            controls[index] = result
            return result
        return control

    distribution: Counter[str] = Counter()
    records: list[dict[str, object]] = []
    compile_raw_failures: list[dict[str, object]] = []

    for index, text in enumerate(UTTERANCES, start=1):
        home = SimulatedHome()
        homes[index] = home
        registry = build_capability_registry(home.devices())
        goal, mode, _sem = compiler.compile(text, llm)
        if goal is None:
            compile_raw_failures.append(
                {
                    "text": text,
                    "raw_model_json": compiler.last_raw,
                    "is_home_goal": (compiler.last_raw or {}).get("is_home_goal"),
                    "extracted_constraints": (compiler.last_raw or {}).get("constraints"),
                    "failure_reason": "model returned no usable constraints after retry",
                }
            )
            continue
        registry = build_capability_registry(home.devices())
        executor = AgentExecutor(make_control(index, home))
        satisfaction_rounds = []
        selected_last = None
        predicted_last = None
        for _round in range(4):
            outcome = planner.plan(goal, room_state(home), registry, None, llm)
            if outcome.no_action or outcome.selected is None:
                break
            selected_last = outcome.selected
            predicted_last = outcome.selected.predicted
            records_round = executor.execute(outcome.selected, _NoWorld())
            verifier.verify_actions(records_round, lambda entity_id: _ha(home, entity_id))
            home.tick(90)
        final = snapshot(home)
        values = {
            "brightness": final.get("brightness_pct"),
            "temperature": final.get("temperature"),
            "curtain_position": final.get("curtain_position"),
        }
        dims = []
        for constraint in goal.constraints:
            value = values.get(constraint.metric)
            distance = constraint.distance(value)
            satisfied = constraint.satisfied_by(value)
            if metric_is_progressive(constraint.metric):
                satisfied = satisfied or progressive_satisfied(constraint, home)
            if not satisfied:
                has_action = any(
                    action.device_id
                    == _metric_device(constraint.metric, registry, goal.room)
                    for action in (selected_last.actions if selected_last else [])
                )
                category = classify(constraint, value, has_action, predicted_last, home)
                distribution[category] += 1
                dims.append(
                    {
                        "metric": constraint.metric,
                        "range": [constraint.lower, constraint.upper],
                        "observed": value,
                        "error": round(distance, 2),
                        "category": category,
                    }
                )
            satisfaction_rounds.append(1.0 if satisfied else max(0.0, 1.0 - distance / 25.0))
        score = sum(satisfaction_rounds) / len(satisfaction_rounds) if satisfaction_rounds else 0.0
        if score < 0.75:
            records.append(
                {
                    "text": text,
                    "goal_satisfaction": round(score, 3),
                    "constraints": [item.to_dict() for item in goal.constraints],
                    "selected_actions": [
                        {"device_id": a.device_id, "capability": a.capability, "value": a.value}
                        for a in (selected_last.actions if selected_last else [])
                    ],
                    "predicted": predicted_last.to_dict() if predicted_last else None,
                    "observed_final": final,
                    "unsatisfied": dims,
                    "compile_mode": mode,
                }
            )

    print("=== unsatisfied dimension category distribution ===")
    for code, count in distribution.most_common():
        print(f"  {code}: {count}")
    print(f"compile long-tail failures: {len(compile_raw_failures)}")
    for item in compile_raw_failures:
        print(f"  - {item['text'][:24]} | is_home_goal={item['is_home_goal']}")
    out = ROOT / "results" / "satisfaction_diagnosis.json"
    out.write_text(
        json.dumps(
            {"category_distribution": dict(distribution), "compile_raw_failures": compile_raw_failures, "records": records},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved: {out}")
    return 0


class _NoWorld:
    def record_action(self, record: object) -> None:
        return

    def update_action(self, *args: object, **kwargs: object) -> None:
        return


def metric_is_progressive(metric: str) -> bool:
    return metric == "temperature"


def progressive_satisfied(constraint, home: SimulatedHome) -> bool:
    """温度渐进语义：已进入区间，或空调设定在区间内且明确收敛。"""
    current = home.temperature
    if constraint.satisfied_by(current):
        return True
    if home.ac_mode == "cool" and home.ac_target is not None:
        if constraint.lower is not None and constraint.lower <= home.ac_target <= (constraint.upper or 30):
            return True
    return False


def _metric_device(metric: str, registry: dict, room: str) -> str | None:
    wanted = {"brightness": ("light", "set_brightness"), "curtain_position": ("cover", "set_position"), "temperature": ("climate", "set_temperature")}.get(metric)
    if wanted is None:
        return None
    domain, capability = wanted
    matches = [d for d in registry.values() if d.domain == domain and capability in d.actions and not d.readonly]
    for d in matches:
        if d.room == room:
            return d.device_id
    return matches[0].device_id if matches else None


def _ha(home: SimulatedHome, entity_id: str) -> dict[str, object]:
    from run_agent_benchmark import _ha_state

    return _ha_state(home, entity_id)


if __name__ == "__main__":
    raise SystemExit(main())
