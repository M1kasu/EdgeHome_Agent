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
HA_URL = os.getenv("SPACEBUTLER_HA_URL", "http://127.0.0.1:12900")
SIMULATOR_URL = os.getenv("SPACEBUTLER_SIMULATOR_URL", "http://127.0.0.1:12891")
EDGE_LLM_URL = os.getenv("EDGE_LLM_URL", "http://127.0.0.1:12881")
MODEL_FILENAME = "Home-Llama-3.2-3B.q4_k_m.gguf"


def main() -> int:
    generated_at = datetime.now(timezone.utc)
    report_dir = ROOT / "reports" / "external" / generated_at.strftime("external_%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []

    docker = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    results.append(docker)
    if docker["returncode"] == 0:
        try:
            models_dir = _resolve_models_dir()
            compose_environment = os.environ.copy()
            compose_environment["SPACEBUTLER_MODELS_DIR"] = str(models_dir)
            results.append(
                {
                    "command": ["resolve_edge_llm_model", str(models_dir / MODEL_FILENAME)],
                    "returncode": 0,
                }
            )
            compose = _run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE),
                    "--profile",
                    "edge-llm",
                    "up",
                    "-d",
                    "--build",
                ],
                timeout=240,
                environment=compose_environment,
            )
            results.append(compose)
        except RuntimeError as error:
            compose = {"returncode": 1}
            results.append(
                {
                    "command": ["resolve_edge_llm_model", MODEL_FILENAME],
                    "returncode": 1,
                    "stderr": str(error),
                }
            )
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
        if acceptance["returncode"] == 0:
            restart_acceptance = _run(
                [sys.executable, str(ROOT / "scripts" / "accept_restart_recovery_external.py")],
                timeout=300,
                environment=environment,
            )
            results.append(restart_acceptance)
            if restart_acceptance["returncode"] == 0:
                edge_llm_health = _edge_llm_health()
                results.append(edge_llm_health)
                if edge_llm_health["returncode"] == 0:
                    environment["EDGE_LLM_URL"] = EDGE_LLM_URL
                    edge_llm_acceptance = _run(
                        [sys.executable, str(ROOT / "scripts" / "accept_edge_llm_routing_external.py")],
                        timeout=120,
                        environment=environment,
                    )
                    results.append(edge_llm_acceptance)
                    if edge_llm_acceptance["returncode"] == 0:
                        results.extend(_run_workbench_gate(environment))

    failures = [item for item in results if item.get("returncode") != 0]
    command_lines = [" ".join(map(str, item.get("command", []))) for item in results]
    passed = (
        bool(results)
        and not failures
        and any("accept_ha_mqtt_energy_external.py" in command for command in command_lines)
        and any("accept_restart_recovery_external.py" in command for command in command_lines)
        and any("accept_edge_llm_routing_external.py" in command for command in command_lines)
        and any("accept_workbench_external.py" in command for command in command_lines)
        and any("accept_proactive_binding_external.py" in command for command in command_lines)
        and any("accept_dynamic_device_external.py" in command for command in command_lines)
        and any("accept_conversation_agent_external.py" in command for command in command_lines)
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
        "boundary": "separate process -> HA REST -> MQTT -> simulator -> SQLite -> readback, plus container restarts",
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


def _edge_llm_health(timeout_seconds: float = 120) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_error = "edge LLM did not become healthy"
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{EDGE_LLM_URL.rstrip('/')}/health", timeout=5) as response:
                payload = response.read().decode("utf-8", errors="replace")
            if response.status == 200:
                return {
                    "command": ["edge_llm_health", EDGE_LLM_URL],
                    "returncode": 0,
                    "stdout": payload,
                    "stderr": "",
                }
            last_error = f"edge LLM returned HTTP {response.status}"
        except OSError as error:
            last_error = str(error)
        time.sleep(1)
    return {
        "command": ["edge_llm_health", EDGE_LLM_URL],
        "returncode": 1,
        "stdout": "",
        "stderr": last_error,
    }


def _resolve_models_dir() -> Path:
    configured = os.getenv("SPACEBUTLER_MODELS_DIR")
    candidates = (
        [Path(configured).expanduser()]
        if configured
        else [
            ROOT / "deployment" / "models",
            ROOT.parent / "EdgeHome_Agent" / "deployment" / "models",
        ]
    )
    for candidate in candidates:
        model = candidate / MODEL_FILENAME
        if model.is_file():
            return candidate.resolve()
    searched = ", ".join(str(candidate / MODEL_FILENAME) for candidate in candidates)
    raise RuntimeError(f"edge LLM model not found; searched: {searched}")


def _run_workbench_gate(environment: dict[str, str]) -> list[dict[str, object]]:
    workbench_environment = environment.copy()
    workbench_environment["SPACEBUTLER_WORKBENCH_URL"] = "http://127.0.0.1:8766"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_workbench.py"), "--port", "8766"],
        cwd=ROOT,
        env=workbench_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    startup_result: dict[str, object]
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urlopen("http://127.0.0.1:8766/api/status", timeout=2) as response:
                    if response.status == 200:
                        startup_result = {
                            "command": ["run_workbench.py", "--port", "8766"],
                            "returncode": 0,
                            "stdout": "workbench ready",
                            "stderr": "",
                        }
                        acceptance = _run(
                            [sys.executable, str(ROOT / "scripts" / "accept_workbench_external.py")],
                            timeout=120,
                            environment=workbench_environment,
                        )
                        results = [startup_result, acceptance]
                        if acceptance["returncode"] == 0:
                            proactive_acceptance = _run(
                                [sys.executable, str(ROOT / "scripts" / "accept_proactive_binding_external.py")],
                                timeout=240,
                                environment=workbench_environment,
                            )
                            results.append(proactive_acceptance)
                            if proactive_acceptance["returncode"] == 0:
                                dynamic_acceptance = _run(
                                    [sys.executable, str(ROOT / "scripts" / "accept_dynamic_device_external.py")],
                                    timeout=240,
                                    environment=workbench_environment,
                                )
                                results.append(dynamic_acceptance)
                                if dynamic_acceptance["returncode"] == 0:
                                    results.append(
                                        _run(
                                            [
                                                sys.executable,
                                                str(ROOT / "scripts" / "accept_conversation_agent_external.py"),
                                            ],
                                            timeout=360,
                                            environment=workbench_environment,
                                        )
                                    )
                        return results
            except OSError:
                pass
            time.sleep(0.5)
        if process.poll() is None:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        startup_result = {
            "command": ["run_workbench.py", "--port", "8766"],
            "returncode": process.returncode if process.returncode is not None else 1,
            "stdout": stdout,
            "stderr": stderr or "workbench did not become ready",
        }
        return [startup_result]
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


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
