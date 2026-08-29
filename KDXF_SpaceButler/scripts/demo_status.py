"""Print a compact view of the agent runtime (goal, planner mode, scores, timeline)."""

from __future__ import annotations

import argparse
import json
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeline", type=int, default=15)
    args = parser.parse_args()
    data = json.loads(urlopen(f"{args.url}/api/agent", timeout=15).read().decode())
    state = data.get("current_state", {})
    goal = data.get("active_goal")
    print(f"planner_mode = {data.get('planner_mode')} (llm_available={data.get('llm_available')})")
    print(f"goal_status  = {data.get('goal_status')}")
    if goal:
        print(f"active_goal  = {goal['title']} [{goal['activity']}]")
        for constraint in goal["constraints"]:
            print(f"  constraint {constraint['metric']}: [{constraint['lower']}, {constraint['upper']}] target={constraint['target']}")
    print(
        f"state        = {state.get('temperature')}°C, light {state.get('brightness_pct')}%, "
        f"curtain {state.get('curtain_position')}%, ac {state.get('ac_mode')}, presence={state.get('presence')}"
    )
    preference = data.get("preference")
    if preference:
        print(f"preference   = {preference['metric']} mean {preference['mean']}% (n={preference['count']}, conf={preference['confidence']})")
    outcome = data.get("last_outcome")
    if outcome and outcome.get("candidates"):
        print("candidates:")
        for candidate in outcome["candidates"]:
            marker = " <-" if candidate.get("selected") else ""
            print(f"  {candidate['label']}: total={candidate.get('total_score')}{marker}")
            print(f"    predicted: {candidate['predicted']}")
    if outcome and outcome.get("reason"):
        print(f"selection    = {outcome['reason']}")
    print("timeline:")
    for event in data.get("timeline", [])[-args.timeline :]:
        print(f"  [{event['time']}] {event['kind']}: {event['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
