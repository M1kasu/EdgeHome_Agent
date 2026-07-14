"""Fail a build when production code depends on acceptance data or fakes device state.

The scanner is intentionally conservative: natural-language parsing rules and
configured aliases are reported as warnings for review, while direct acceptance
sentence matches, test-data imports, benchmark/test-ID branches, and non-
diagnostic Home Assistant state writes are build errors.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (
    ROOT / "custom_components" / "llama_conversation" / "edgehome",
    ROOT / "device_simulator" / "edgehome_simulator",
)
ACCEPTANCE_SOURCE = ROOT / "scripts" / "accept_m1_mqtt_light.py"
TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]")
FORBIDDEN_IMPORT = re.compile(r"\b(?:tests?|benchmark|acceptance|fixtures|expected_results)\b", re.IGNORECASE)
FORBIDDEN_ID_BRANCH = re.compile(r"\b(?:test_id|task_id|benchmark_id)\b", re.IGNORECASE)
DIRECT_STATE_WRITE = re.compile(r"\b(?:hass\.)?states\.(?:async_)?set\(")


@dataclass(frozen=True)
class Finding:
    severity: str
    kind: str
    file: str
    line: int
    detail: str


def normalize(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum() or TEXT_PATTERN.fullmatch(character))


def python_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        yield from root.rglob("*.py")


def string_literals(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as error:
        return [(error.lineno or 1, f"<syntax-error:{error.msg}>")]
    return [(node.lineno, node.value) for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def acceptance_inputs() -> set[str]:
    values: set[str] = set()
    if ACCEPTANCE_SOURCE.exists():
        values.update(value for _, value in string_literals(ACCEPTANCE_SOURCE) if len(normalize(value)) >= 8 and TEXT_PATTERN.search(value))
    for path in (ROOT / "reports" / "acceptance").glob("**/results.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                input_text = json.loads(line).get("input")
            except json.JSONDecodeError:
                continue
            if isinstance(input_text, str) and len(normalize(input_text)) >= 8:
                values.add(input_text)
    return values


def scan() -> list[Finding]:
    findings: list[Finding] = []
    acceptance = acceptance_inputs()
    for path in python_files(PRODUCTION_ROOTS):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), start=1):
            if FORBIDDEN_IMPORT.search(line) and re.search(r"^\s*(?:from|import)\s+", line):
                findings.append(Finding("error", "forbidden_test_import", relative, number, line.strip()))
            if FORBIDDEN_ID_BRANCH.search(line) and re.search(r"\b(?:if|elif|case)\b", line):
                findings.append(Finding("error", "test_identifier_branch", relative, number, line.strip()))
            if DIRECT_STATE_WRITE.search(line):
                allowed = relative.endswith("edgehome/coordinator.py") and "sensor.edgehome_last_execution" in line
                findings.append(
                    Finding("warning" if allowed else "error", "direct_state_write", relative, number, "diagnostic trace" if allowed else line.strip())
                )

        for line, value in string_literals(path):
            normalized = normalize(value)
            if not normalized or len(normalized) < 8:
                continue
            best = max((SequenceMatcher(None, normalized, normalize(candidate)).ratio() for candidate in acceptance), default=0.0)
            if best >= 0.88:
                findings.append(Finding("error", "acceptance_sentence_similarity", relative, line, f"similarity={best:.2f}; literal={value!r}"))

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and any(
                isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and TEXT_PATTERN.search(item.value)
                and len(normalize(item.value)) >= 8
                for item in ast.walk(node.test)
            ):
                findings.append(Finding("warning", "exact_text_branch", relative, node.lineno, ast.unparse(node.test)[:240]))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "acceptance" / "m1_mqtt_light" / "hardcoding_audit.json")
    args = parser.parse_args()
    findings = scan()
    payload = {
        "scanner": "scripts/audit_hardcoding.py",
        "production_roots": [path.relative_to(ROOT).as_posix() for path in PRODUCTION_ROOTS],
        "acceptance_input_count": len(acceptance_inputs()),
        "errors": sum(item.severity == "error" for item in findings),
        "warnings": sum(item.severity == "warning" for item in findings),
        "findings": [asdict(item) for item in findings],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("acceptance_input_count", "errors", "warnings")}, ensure_ascii=False))
    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
