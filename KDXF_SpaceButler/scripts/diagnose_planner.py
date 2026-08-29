"""Diagnose planner fallback long-tail: failure distribution + raw model outputs.

只跑 SpaceButler agent 链路（不跑 baseline），输出：
- planner_failure_distribution（按错误码）
- 每个失败样本的模型归一化输出与拒绝原因（不保存隐藏推理）
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
from spacebutler.agent.goal_compiler import GoalCompiler  # noqa: E402
from spacebutler.agent.planner import CounterfactualPlanner  # noqa: E402
from spacebutler.llm.client import LlmClient  # noqa: E402


def room_state(home: SimulatedHome):
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


def main() -> int:
    llm = LlmClient.from_env()
    print("llm:", llm.base_url if llm else None, "available:", llm.available(force_refresh=True))
    compiler = GoalCompiler()
    planner = CounterfactualPlanner()
    distribution: Counter[str] = Counter()
    records: list[dict[str, object]] = []
    compile_failures: list[str] = []

    for text in UTTERANCES:
        home = SimulatedHome()
        registry = build_capability_registry(home.devices())
        goal, mode, _sem = compiler.compile(text, llm)
        if goal is None:
            compile_failures.append(text)
            continue
        outcome = planner.plan(goal, room_state(home), registry, None, llm)
        if outcome.planner_mode == "llm":
            continue
        for rejection in outcome.llm_rejections:
            distribution[str(rejection.get("code"))] += 1
        records.append(
            {
                "text": text,
                "planner_mode": outcome.planner_mode,
                "rejections": outcome.llm_rejections,
            }
        )

    print("\n=== planner_failure_distribution ===")
    for code, count in distribution.most_common():
        print(f"  {code}: {count}")
    print(f"compile_failures: {len(compile_failures)}")
    for text in compile_failures:
        print(f"  - {text}")
    out = ROOT / "results" / "planner_failure_diagnosis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "planner_failure_distribution": dict(distribution),
                "compile_failures": compile_failures,
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
