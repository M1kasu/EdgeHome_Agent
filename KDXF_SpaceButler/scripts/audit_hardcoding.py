"""Conservative production-code hardcoding audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DIRS = [ROOT / "spacebutler"]
HIGH_RISK_PATTERNS = [
    "test_id",
    "expected_result",
    "expected_results",
    "benchmark_id",
    "from tests",
    "import tests",
    "accept-empty-room-open-window-001",
]
FULL_ACCEPTANCE_SENTENCES = [
    "客厅没人挺久了，空调是不是还开着？",
    "没人的房间别一直开空调。",
    "空调开着但窗户也开着时提醒我。",
    "以后客厅空置半小时就关空调。",
    "晚上电价高的时候优先提醒节能。",
]


def main() -> int:
    findings: list[dict[str, str]] = []
    for directory in PRODUCTION_DIRS:
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pattern in HIGH_RISK_PATTERNS + FULL_ACCEPTANCE_SENTENCES:
                if pattern in text:
                    findings.append(
                        {
                            "file": str(path.relative_to(ROOT)),
                            "pattern": pattern,
                            "severity": "P0",
                        }
                    )
    print(json.dumps({"findings": findings, "passed": not findings}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

