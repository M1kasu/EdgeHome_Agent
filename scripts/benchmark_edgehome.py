"""Run a small deterministic EdgeHome planning-schema benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.llama_conversation.edgehome.models import TaskPlan


_PLAN = {
    "goal": "打开客厅灯",
    "steps": [
        {
            "id": "step_1",
            "tool": "turn_on_device",
            "arguments": {"entity_id": "light.living"},
            "verify": {"entity_id": "light.living", "state": "on"},
        }
    ],
}


def main() -> None:
    """Measure schema validation only; real model inference must be profiled on target hardware."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    arguments = parser.parse_args()
    if arguments.iterations < 1:
        raise SystemExit("--iterations must be positive")

    tracemalloc.start()
    started = perf_counter()
    for _ in range(arguments.iterations):
        TaskPlan.model_validate(_PLAN)
    elapsed_ms = (perf_counter() - started) * 1000
    _, peak_bytes = tracemalloc.get_traced_memory()
    print(
        json.dumps(
            {
                "iterations": arguments.iterations,
                "schema_validation_total_ms": round(elapsed_ms, 3),
                "schema_validation_mean_ms": round(elapsed_ms / arguments.iterations, 5),
                "python_tracemalloc_peak_kib": round(peak_bytes / 1024, 3),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
