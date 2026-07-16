"""Run the current SpaceButler optimization loop checks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external", action="store_true", help="also run the Docker HA/MQTT/SQLite gate")
    args = parser.parse_args()
    commands = [
        ["python", "-m", "compileall", "-q", "spacebutler", "scripts", "tests"],
        ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        ["python", "scripts\\accept_empty_room_open_window_energy.py"],
        ["python", "scripts\\accept_false_success_prevention.py"],
        ["python", "scripts\\accept_local_fault_matrix.py"],
        ["python", "scripts\\audit_hardcoding.py"],
    ]
    if args.external:
        commands.append(["python", "scripts\\run_external_acceptance.py"])
    results = []
    failed = False
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        failed = failed or completed.returncode != 0
    report_dir = ROOT / "reports" / "iterations" / datetime.now(timezone.utc).strftime("iteration_%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "test_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [result for result in results if result["returncode"] != 0]
    (report_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    scorecard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": "LOCAL_REGRESSION",
        "passed": not failed,
        "checks_total": len(results),
        "checks_passed": len(results) - len(failures),
        "checks_failed": len(failures),
        "external_acceptance": "PASS" if args.external and not failed else "FAIL" if args.external else "NOT_EVALUATED",
    }
    (report_dir / "scorecard.json").write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = "\n".join(
        [
            "# Iteration Summary",
            "",
            f"- generated_at: {datetime.now(timezone.utc).isoformat()}",
            f"- passed: {not failed}",
            "",
            *[f"- `{ ' '.join(result['command']) }`: {result['returncode']}" for result in results],
        ]
    )
    (report_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(json.dumps({"passed": not failed, "report_dir": str(report_dir)}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
