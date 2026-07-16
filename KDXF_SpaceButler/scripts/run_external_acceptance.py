"""Start the independent Docker stack and run external black-box acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
import time
from urllib.request import urlopen

from ha_bootstrap import configure_mqtt, obtain_token, wait_until_ready

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
COMPOSE = ROOT / "deployment" / "docker-compose.yml"
HA_URL = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:8900")
SIMULATOR_URL = os.getenv("SPACEBUTLER_SIMULATOR_URL", "http://127.0.0.1:8091")


def main() -> int:
    generated_at = datetime.now(timezone.utc)
    report_dir = ROOT / "reports" / "external" / generated_at.strftime("external_%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []

    docker = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    results.append(docker)
    if docker["returncode"] == 0:
        compose = _run(["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--build"], timeout=180)
        results.append(compose)
    else:
        compose = {"returncode": 1}

    token: str | None = None
    bootstrap_error: str | None = None
    if compose["returncode"] == 0:
        try:
            wait_until_ready(HA_URL, timeout_seconds=150)
            _wait_simulator(timeout_seconds=60)
            token = _obtain_token_with_retry(timeout_seconds=60)
            mqtt_result = configure_mqtt(HA_URL, token)
            results.append({"command": ["bootstrap_home_assistant", "configure_mqtt"], "returncode": 0, "result": mqtt_result})
            _wait_for_climate(token, timeout_seconds=60)
        except Exception as error:
            bootstrap_error = str(error)
            results.append(
                {
                    "command": ["bootstrap_home_assistant", "configure_mqtt"],
                    "returncode": 1,
                    "stderr": bootstrap_error,
                }
            )

    if token and bootstrap_error is None:
        environment = os.environ.copy()
        environment["HA_TOKEN"] = token
        acceptance = _run(
            [sys.executable, str(ROOT / "scripts" / "accept_ha_mqtt_energy_external.py")],
            timeout=120,
            environment=environment,
        )
        results.append(acceptance)

    failures = [item for item in results if item.get("returncode") != 0]
    passed = bool(results) and not failures and any(
        "accept_ha_mqtt_energy_external.py" in " ".join(map(str, item.get("command", []))) for item in results
    )
    (report_dir / "test_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    scorecard = {
        "generated_at": generated_at.isoformat(),
        "gate": "EXTERNAL_HA_MQTT_SQLITE_ACCEPTANCE",
        "passed": passed,
        "checks_total": len(results),
        "checks_passed": len(results) - len(failures),
        "checks_failed": len(failures),
        "external_acceptance": "PASS" if passed else "FAIL",
        "boundary": "separate_process -> Home Assistant REST -> MQTT -> device simulator -> SQLite -> MQTT/HA readback",
    }
    (report_dir / "scorecard.json").write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"passed": passed, "report_dir": str(report_dir), "failures": len(failures)}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


def _run(command: list[str], timeout: int, environment: dict[str, str] | None = None) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _wait_simulator(timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{SIMULATOR_URL}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError("device simulator did not become healthy")


def _wait_for_climate(token: str, timeout_seconds: float) -> None:
    from spacebutler import HomeAssistantClient

    client = HomeAssistantClient(HA_URL, token)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if client.state("climate.spacebutler_living_room_ac").get("state") in {"off", "cool", "heat"}:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError("MQTT climate entity was not discovered by Home Assistant")


def _obtain_token_with_retry(timeout_seconds: float) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    auth_store = ROOT / "deployment" / "homeassistant" / ".storage" / "auth"
    while time.monotonic() < deadline:
        try:
            return obtain_token(HA_URL, auth_store)
        except (OSError, RuntimeError) as error:
            last_error = error
            time.sleep(2)
    raise RuntimeError(f"Home Assistant authentication bootstrap did not stabilize: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
