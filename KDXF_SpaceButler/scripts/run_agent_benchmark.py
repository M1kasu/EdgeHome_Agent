"""Agent benchmark: Scene Baseline vs LLM Direct Tool vs SpaceButler.

全部结果由真实运行产生（进程内模拟家庭环境 + 可配置的真实 LLM）。
Scene Baseline 仅作为实验对照，不属于产品主流程。
ablation：full | no_model_planner | no_counterfactual | no_preference | no_replanning
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler.agent.capabilities import build_capability_registry  # noqa: E402
from spacebutler.agent.counterfactual import CounterfactualPredictor  # noqa: E402
from spacebutler.agent.goal_compiler import GoalCompiler  # noqa: E402
from spacebutler.agent.models import CompiledGoal  # noqa: E402
from spacebutler.agent.planner import CounterfactualPlanner  # noqa: E402
from spacebutler.agent.policy_guard import PolicyGuard  # noqa: E402
from spacebutler.llm.client import LlmClient  # noqa: E402

# 34 条未在代码中定义过的自然语言目标（泛化验收语料）。
UTTERANCES = [
    "我准备在客厅看会电影，有点热，但是别弄得完全黑。",
    "我想窝在沙发上看点东西，屋里有点闷，但是别搞得太黑。",
    "我准备休息一会，但不睡觉，凉快一点，给我留点光。",
    "客厅现在有点刺眼，我想安静坐会。",
    "我就待半小时，舒服点就行，但别太耗电。",
    "我有点困，不过现在还不想睡。",
    "今天想放松一会，不想开太亮的灯。",
    "下午想在小客厅看看书，光线柔一点最好。",
    "屋里闷得慌，帮我弄凉快些，但风别对着吹。",
    "想瘫在沙发刷剧，灯太亮了眼睛累。",
    "朋友一会来，先让客厅看起来舒服点，别太暗。",
    "我一个人想静静，灯光暗一些，别太冷。",
    "看完电影了，帮我把客厅恢复成日常状态。",
    "想小憩二十分钟，安静、微凉、灯光柔和。",
    "加班处理点事情，需要亮一点的灯，别太热。",
    "晚饭后在客厅发会儿呆，室温舒服最重要。",
    "开会前把客厅弄得亮堂一点，别热。",
    "想在客厅做做拉伸，通风凉快点，灯别晃眼。",
    "宝宝在客厅睡着啦，灯光弄暗，室温保持舒服。",
    "打游戏呢，屋里有点热，灯别在屏幕上反光。",
    "我就想凉快点，别的无所谓。",
    "灯光调暗点，我想眯一会儿。",
    "客厅太闷了，待着不舒服。",
    "想看部电影，帮我布置一下客厅。",
    "要在这个屋子里练瑜伽，亮但别刺眼，凉快些。",
    "爸妈要来坐坐，客厅弄得舒适大方，亮度适中。",
    "我想在沙发上躺躺，屋里凉一点，灯光留一点。",
    "晚上想安静地听音乐，光线氛围一点，别太亮。",
    "有点燥热，把客厅弄凉快，窗帘该关就关。",
    "想在客厅视频通话，背景亮一点，我别出汗。",
    "夜里起来看了看，别把灯全关了，也别太凉。",
    "客厅现在又亮又热，帮我调到能舒服待着的状态。",
    "准备睡午觉，窗帘拉上，灯关小，温度适宜。",
    "想赖在沙发上一下午，环境越舒服越好，别费电。",
]

# Scene Baseline：固定场景映射，仅用于对照实验（禁止进入产品主流程）。
_SCENE_TABLE = [
    (re.compile(r"电影|观影|刷剧|看部|看会|看片|看点"), [
        {"capability": "set_position", "domain": "cover", "value": 0},
        {"capability": "set_brightness", "domain": "light", "value": 30},
        {"capability": "set_temperature", "domain": "climate", "value": 26, "mode": "cool"},
    ]),
    (re.compile(r"睡觉|午觉|小憩|眯一"), [
        {"capability": "set_position", "domain": "cover", "value": 0},
        {"capability": "set_brightness", "domain": "light", "value": 5},
        {"capability": "set_temperature", "domain": "climate", "value": 26, "mode": "cool"},
    ]),
    (re.compile(r"热|闷|凉快|降温|燥热"), [
        {"capability": "set_temperature", "domain": "climate", "value": 26, "mode": "cool"},
    ]),
    (re.compile(r"亮堂|亮一点|亮一点就行|刺眼|太亮|调暗|暗"), [
        {"capability": "set_brightness", "domain": "light", "value": 45},
    ]),
]


class SimulatedHome:
    """进程内模拟家庭：真实物理速率 + 可编程初始状态。"""

    def __init__(self) -> None:
        self.temperature = 30.2
        self.light_brightness = 204  # 80%
        self.light_on = True
        self.curtain = 100.0
        self.ac_mode = "off"
        self.ac_target: float | None = None

    def devices(self) -> list[dict[str, object]]:
        return [
            {"device_id": "living_room_main_light", "entity_id": "light.spacebutler_living_room_main_2", "type": "light",
             "name": "客厅主灯", "room": "living_room", "online": True,
             "state": {"power": "ON" if self.light_on else "OFF", "brightness": self.light_brightness}},
            {"device_id": "living_room_curtain", "entity_id": "cover.spacebutler_living_room_curtain_2", "type": "curtain",
             "name": "客厅窗帘", "room": "living_room", "online": True, "state": {"position": self.curtain}},
            {"device_id": "living_room_ac", "entity_id": "climate.spacebutler_living_room_ac_2", "type": "climate",
             "name": "客厅空调", "room": "living_room", "online": True,
             "state": {"mode": self.ac_mode, "temperature": self.ac_target or 26, "current_temperature": round(self.temperature, 2)}},
            {"device_id": "living_room_presence_sensor", "entity_id": "binary_sensor.spacebutler_living_room_presence",
             "type": "presence", "name": "客厅人体", "room": "living_room", "online": True, "state": {"occupied": True}},
            {"device_id": "living_room_temperature_sensor", "entity_id": "sensor.spacebutler_living_room_temperature",
             "type": "temperature", "name": "客厅温度", "room": "living_room", "online": True,
             "state": {"temperature": round(self.temperature, 2)}},
        ]

    def control(self, device_id: str, payload: dict[str, object]) -> dict[str, object]:
        if device_id == "living_room_main_light":
            if "power" in payload:
                self.light_on = str(payload["power"]).upper() == "ON"
            if "brightness" in payload:
                self.light_brightness = round(int(payload["brightness"]) * 255 / 100)
        elif device_id == "living_room_curtain" and "position" in payload:
            self.curtain = float(payload["position"])
        elif device_id == "living_room_ac":
            if "mode" in payload:
                self.ac_mode = str(payload["mode"])
            if "temperature" in payload:
                self.ac_target = float(payload["temperature"])
        return {"devices": self.devices()}

    def tick(self, seconds: float = 5.0) -> None:
        if self.ac_mode == "cool":
            self.temperature = max(self.temperature - 0.2 * seconds, (self.ac_target or 26) - 1.0)
        else:
            self.temperature = min(self.temperature + 0.05 * seconds, 30.2)


def goal_satisfied(state: dict[str, object], constraints: list) -> float:
    values = {
        "brightness": state.get("brightness_pct"),
        "temperature": state.get("temperature"),
        "curtain_position": state.get("curtain_position"),
    }
    if not constraints:
        return 0.0
    total = 0.0
    for constraint in constraints:
        metric = constraint["metric"] if isinstance(constraint, dict) else constraint.metric
        value = values.get(metric)
        distance = constraint.distance(value)
        total += max(0.0, 1.0 - distance / 25.0)
    return total / len(constraints)


def run_scene_baseline(text: str, home: SimulatedHome) -> dict[str, object]:
    domain_to_type = {"cover": "curtain", "light": "light", "climate": "climate"}
    actions = []
    for pattern, template in _SCENE_TABLE:
        if not pattern.search(text):
            continue
        for entry in template:
            wanted_type = domain_to_type.get(str(entry["domain"]))
            device = next((d for d in home.devices() if d["type"] == wanted_type), None)
            if device is None:
                continue
            payload: dict[str, object] = {}
            if entry["capability"] == "set_position":
                payload = {"position": entry["value"]}
            elif entry["capability"] == "set_brightness":
                payload = {"power": "on", "brightness": entry["value"]}
            else:
                payload = {"mode": entry.get("mode", "cool"), "temperature": entry["value"]}
            home.control(str(device["device_id"]), payload)
            actions.append(str(device["device_id"]))
    return {"actions": actions, "valid": bool(actions)}


def run_llm_direct_tool(llm: LlmClient, text: str, home: SimulatedHome, guard: PolicyGuard, registry: dict) -> dict[str, object]:
    raw = llm.chat_json(
        "把用户的家居请求转成动作 JSON：{\"actions\": [{\"device_id\": \"...\", \"capability\": \"...\", \"value\": 数字, \"mode\": \"cool|null\"}]}。"
        "只能用给定设备："
        + json.dumps({k: v.to_dict() for k, v in registry.items() if not v.readonly}, ensure_ascii=False),
        text,
    )
    actions = raw.get("actions", []) if isinstance(raw, dict) else []
    if guard.issues(actions):
        return {"actions": [], "valid": False, "unsafe": True}
    executed = []
    for entry in actions:
        from spacebutler.agent.executor import payload_for

        try:
            home.control(str(entry["device_id"]), payload_for(str(entry["capability"]), entry.get("value"), entry.get("mode")))
            executed.append(str(entry["device_id"]))
        except (RuntimeError, ValueError):
            return {"actions": executed, "valid": False, "unsafe": True}
    return {"actions": executed, "valid": True, "unsafe": False}


def build_goal_constraints(goal: CompiledGoal | None) -> list:
    return list(goal.constraints) if goal else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=len(UTTERANCES))
    parser.add_argument("--ablation", choices=["full", "no_model_planner", "no_counterfactual", "no_preference", "no_replanning"], default="full")
    parser.add_argument("--ticks", type=int, default=6, help="每个样本的环境演化 tick 数（每 tick 60s 模拟）")
    parser.add_argument("--out", default="results/agent_benchmark")
    parser.add_argument("--split", choices=["all", "dev", "test"], default="all", help="dev=前24条（调优用），test=后10条（held-out）")
    parser.add_argument("--temperature", type=float, default=None, help="覆盖 SPACEBUTLER_LLM_TEMPERATURE")
    parser.add_argument("--systems", default="scene_baseline,llm_direct_tool,spacebutler_agent", help="逗号分隔要跑的系统")
    args = parser.parse_args()

    llm = LlmClient.from_env()
    if args.temperature is not None and llm is not None:
        llm = LlmClient(llm.base_url, llm._model, llm._api_key, llm._timeout, args.temperature)
    llm_on = bool(llm and llm.available(force_refresh=True))
    print(f"LLM available: {llm_on} ({llm.base_url if llm else '-'}) temperature={getattr(llm, '_temperature', '-')}")

    if args.split == "dev":
        utterances = UTTERANCES[:24]
    elif args.split == "test":
        utterances = UTTERANCES[24:]
    else:
        utterances = UTTERANCES[: max(1, args.limit)]
    selected_systems = [item.strip() for item in args.systems.split(",") if item.strip()]
    systems: dict[str, list[dict[str, object]]] = {
        "scene_baseline": [],
        "llm_direct_tool": [],
        "spacebutler_agent": [],
    }

    def new_home() -> SimulatedHome:
        home = SimulatedHome()
        home.temperature = 30.2
        return home

    for index, text in enumerate(utterances, start=1):
        # ---- scene baseline ----
        home = new_home()
        result = run_scene_baseline(text, home)
        for _ in range(args.ticks):
            home.tick(60)
        systems["scene_baseline"].append(
            {
                "text": text,
                "valid": bool(result["valid"]),
                "unsafe": bool(result.get("unsafe", False)),
                "action_count": len(result["actions"]),
                "goal_satisfaction": 0.0,  # scene baseline 无语义约束可评（对照）。
            }
        )

        # ---- llm direct tool ----
        home = new_home()
        registry = build_capability_registry(home.devices())
        guard = PolicyGuard(registry)
        result = run_llm_direct_tool(llm, text, home, guard, registry) if llm_on else {"actions": [], "valid": False, "unsafe": False}
        for _ in range(args.ticks):
            home.tick(60)
        constraints, _mode, _sem = GoalCompiler().compile(text, llm)
        snapshot = _snapshot_state(home)
        systems["llm_direct_tool"].append(
            {
                "text": text,
                "valid": bool(result["valid"]),
                "unsafe": bool(result.get("unsafe", False)),
                "action_count": len(result["actions"]),
                "goal_satisfaction": round(goal_satisfied(snapshot, build_goal_constraints(constraints)), 3) if constraints else 0.0,
                "llm_involved": llm_on,
            }
        )

        # ---- spacebutler agent（同步驱动，无后台线程） ----
        home = new_home()
        registry = build_capability_registry(home.devices())
        compiler = GoalCompiler()
        planner = CounterfactualPlanner(
            predictor=None if args.ablation != "no_counterfactual" else _NullPredictor()
        )
        from spacebutler.agent.executor import AgentExecutor  # noqa: E402
        from spacebutler.agent.verifier import Verifier  # noqa: E402

        executor = AgentExecutor(home.control)
        verifier = Verifier()
        llm_for_run = None if args.ablation == "no_model_planner" else llm
        goal, compile_mode, _sem = compiler.compile(text, llm_for_run)
        goal_parse_valid = goal is not None
        goal_compiled_by_llm = compile_mode == "llm"
        action_count = 0
        valid = True
        plan_generated_by_llm = False
        plan_schema_valid = False
        execution_valid = False
        verification_success = False
        error_stage = None if goal is not None else "goal_compile"
        if goal is None:
            valid = False
        else:
            # 持续自治语义（与 AgentRuntime 一致）：规划 -> 执行 -> 环境演化 ->
            # 评估 -> 未满足则重规划，直到 no_action（收敛）或轮次上限。
            plan_generated_by_llm = False
            plan_schema_valid = False
            execution_valid = True
            verification_success = True
            llm_candidate_seen = False
            for _round in range(4):
                outcome = planner.plan(goal, _snapshot_room(home), registry, None, llm_for_run)
                plan_generated_by_llm = plan_generated_by_llm or outcome.planner_mode == "llm"
                llm_candidate_seen = llm_candidate_seen or any(
                    item.source == "llm" and item.actions for item in outcome.candidates
                )
                if outcome.no_action or outcome.selected is None:
                    if outcome.no_action:
                        plan_schema_valid = True
                    else:
                        error_stage = error_stage or "planner"
                    break
                records = executor.execute(outcome.selected, _WorldStub())
                failures = verifier.verify_actions(records, lambda entity_id: _ha_state(home, entity_id))
                verifier_success = failures == 0
                action_count += len(records)
                valid = valid and verifier_success
                execution_valid = execution_valid and verifier_success
                verification_success = verification_success and verifier_success
                if failures:
                    error_stage = error_stage or "verification"
                home.tick(60)
            plan_schema_valid = plan_schema_valid or action_count > 0
            for _ in range(args.ticks):
                home.tick(60)
        snapshot = _snapshot_state(home)
        satisfaction = round(goal_satisfied(snapshot, build_goal_constraints(goal)), 3) if goal else 0.0
        used_fallback = (not goal_compiled_by_llm) or (not plan_generated_by_llm)
        full_llm_chain = bool(
            goal_parse_valid
            and goal_compiled_by_llm
            and plan_generated_by_llm
            and llm_candidate_seen
            and execution_valid
            and not used_fallback
        )
        systems["spacebutler_agent"].append(
            {
                "text": text,
                "valid": valid,
                "unsafe": False,
                "action_count": action_count,
                "goal_satisfaction": satisfaction,
                "llm_involved": goal_compiled_by_llm or plan_generated_by_llm,
                "goal_parse_valid": goal_parse_valid,
                "goal_compiled_by_llm": goal_compiled_by_llm,
                "plan_generated_by_llm": plan_generated_by_llm,
                "plan_schema_valid": plan_schema_valid,
                "used_fallback": used_fallback,
                "execution_valid": execution_valid,
                "verification_success": verification_success,
                "full_llm_chain_success": full_llm_chain,
                "error_stage": error_stage,
            }
        )
        print(f"[{index}/{len(utterances)}] {text[:24]}... done")

    # ---- 汇总 ----
    summary: dict[str, object] = {"ablation": args.ablation, "llm_available": llm_on, "samples": len(utterances), "split": args.split, "temperature": getattr(llm, "_temperature", None), "systems": {}}
    for name, rows in systems.items():
        if name not in selected_systems:
            continue
        count = max(1, len(rows))
        summary["systems"][name] = {
            "plan_validity_rate": round(sum(1 for r in rows if r["valid"]) / count, 3),
            "unsafe_plan_rate": round(sum(1 for r in rows if r.get("unsafe")) / count, 3),
            "avg_action_count": round(sum(int(r["action_count"]) for r in rows) / count, 2),
            "goal_satisfaction_rate": round(sum(float(r.get("goal_satisfaction", 0)) for r in rows) / count, 3),
            "llm_contribution_rate": round(sum(1 for r in rows if r.get("llm_involved")) / count, 3),
            "generalization_success_rate": round(
                sum(1 for r in rows if r["valid"] and float(r.get("goal_satisfaction", 0)) >= 0.75) / count, 3
            ),
        }
        if name == "spacebutler_agent":
            summary["systems"][name].update(
                {
                    "goal_parse_valid_rate": round(sum(1 for r in rows if r.get("goal_parse_valid")) / count, 3),
                    "goal_compiled_by_llm_rate": round(sum(1 for r in rows if r.get("goal_compiled_by_llm")) / count, 3),
                    "plan_generated_by_llm_rate": round(sum(1 for r in rows if r.get("plan_generated_by_llm")) / count, 3),
                    "planner_fallback_rate": round(sum(1 for r in rows if r.get("used_fallback")) / count, 3),
                    "execution_valid_rate": round(sum(1 for r in rows if r.get("execution_valid")) / count, 3),
                    "full_llm_chain_success_rate": round(sum(1 for r in rows if r.get("full_llm_chain_success")) / count, 3),
                    "goal_success_rate": round(
                        sum(1 for r in rows if r["valid"] and float(r.get("goal_satisfaction", 0)) >= 0.75) / count, 3
                    ),
                    "error_stage_distribution": _count_stages(rows),
                }
            )

    out_dir = ROOT / args.out
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    (Path(f"{args.out}.json")).write_text(json.dumps({"summary": summary, "rows": systems}, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(f"{args.out}.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["system", "text", "valid", "unsafe", "action_count", "goal_satisfaction", "llm_involved"])
        for name, rows in systems.items():
            for row in rows:
                writer.writerow([name, row["text"], row["valid"], row.get("unsafe", False), row["action_count"], row.get("goal_satisfaction", 0), row.get("llm_involved", False)])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


class _WorldStub:
    def record_action(self, record: object) -> None:
        return

    def update_action(self, *args: object, **kwargs: object) -> None:
        return


class _NullPredictor(CounterfactualPredictor):
    """ablation: w/o Counterfactual —— 预测退化为当前状态（无前瞻）。"""

    def predict(self, actions: object, state: object, horizon_minutes: float = 5.0):  # type: ignore[override]
        from spacebutler.agent.models import PredictedState

        return PredictedState(temperature=state.temperature, temperature_trend="stable")


def _count_stages(rows: list[dict[str, object]]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for row in rows:
        stage = str(row.get("error_stage") or "ok")
        distribution[stage] = distribution.get(stage, 0) + 1
    return distribution


def _snapshot_state(home: SimulatedHome) -> dict[str, object]:
    brightness = round(home.light_brightness * 100 / 255, 1) if home.light_on else 0.0
    return {"temperature": round(home.temperature, 2), "brightness_pct": brightness, "curtain_position": home.curtain}


def _snapshot_room(home: SimulatedHome):
    from spacebutler.agent.models import RoomState

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


def _ha_state(home: SimulatedHome, entity_id: str) -> dict[str, object]:
    """按实体类型返回正确的 HA 状态视图（verifier 回读依据）。"""
    for device in home.devices():
        if str(device.get("entity_id")) != entity_id:
            continue
        state = device["state"] if isinstance(device["state"], dict) else {}
        attributes: dict[str, object] = {}
        if device["type"] == "light":
            attributes = {"brightness": state.get("brightness")}
            return {"state": str(state.get("power", "off")).lower(), "attributes": attributes}
        if device["type"] == "curtain":
            attributes = {"current_position": state.get("position")}
            return {"state": "open" if float(state.get("position", 0) or 0) > 0 else "closed", "attributes": attributes}
        if device["type"] == "climate":
            attributes = {"temperature": state.get("temperature")}
            return {"state": str(state.get("mode", "off")), "attributes": attributes}
    return {"state": "unknown", "attributes": {}}


if __name__ == "__main__":
    raise SystemExit(main())
