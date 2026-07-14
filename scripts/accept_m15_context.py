"""Black-box acceptance for persistent, structured EdgeHome conversation context."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from paho.mqtt import client as mqtt


ROOT = Path(__file__).resolve().parents[1]
LIGHT = "light.edgehome_living_room_main"
DEVICE_ID = "living_room_main_light"
AGENT = "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome"
TOPICS = (f"edgehome/devices/{DEVICE_ID}/command", f"edgehome/devices/{DEVICE_ID}/state")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evidence:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.paths = {name: directory / name for name in (
            "results.jsonl", "mqtt_commands.jsonl", "mqtt_states.jsonl", "simulator_events.jsonl", "ha_states.jsonl", "conversation_states.jsonl"
        )}
        for path in self.paths.values():
            path.write_text("", encoding="utf-8")

    def write(self, name: str, value: dict[str, Any]) -> None:
        with self.paths[name].open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


class Observer:
    def __init__(self, host: str, port: int) -> None:
        self.messages: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edgehome-m15-observer-{uuid4().hex}")
        self.client.on_connect = self._connect
        self.client.on_message = self._message
        self.host, self.port = host, port

    def start(self) -> None:
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        if not self.ready.wait(10):
            raise RuntimeError("MQTT observer did not subscribe")

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def clear(self) -> None:
        with self.lock:
            self.messages.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return json.loads(json.dumps(self.messages))

    def _connect(self, client: mqtt.Client, _userdata: Any, _flags: mqtt.ConnectFlags, reason: mqtt.ReasonCode, _properties: mqtt.Properties | None) -> None:
        if reason.is_failure:
            return
        for topic in TOPICS:
            client.subscribe(topic, qos=1)
        self.ready.set()

    def _message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        raw = message.payload.decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        with self.lock:
            self.messages.append({"timestamp": now(), "topic": message.topic, "payload": payload, "retain": bool(message.retain)})


class Acceptance:
    def __init__(self, args: argparse.Namespace, evidence: Evidence, observer: Observer) -> None:
        self.ha = args.home_assistant_url.rstrip("/")
        self.sim = args.simulator_url.rstrip("/")
        self.token = os.environ["HA_TOKEN"]
        self.agent = args.agent_id
        self.evidence, self.observer = evidence, observer
        self.context_db, self.compose_file = args.context_db, args.compose_file
        self.records: list[dict[str, Any]] = []
        self.aborted: str | None = None

    def http(self, method: str, url: str, payload: dict[str, Any] | None = None, *, auth: bool = False, timeout: int = 90) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            with urlopen(Request(url, data=data, method=method, headers=headers), timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(f"HTTP {error.code}: {error.read().decode('utf-8', errors='replace')}") from error
        return json.loads(raw) if raw else None

    def reset(self) -> None:
        self.http("POST", f"{self.sim}/admin/devices/{DEVICE_ID}/reset", {})
        time.sleep(0.5)

    def fault(self, mode: str) -> None:
        self.http("POST", f"{self.sim}/admin/devices/{DEVICE_ID}/fault", {"mode": mode, "delay_ms": 0})
        time.sleep(0.3)

    def ha_state(self, entity: str = LIGHT) -> dict[str, Any] | None:
        try:
            return self.http("GET", f"{self.ha}/api/states/{entity}", auth=True, timeout=15)
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                return None
            raise

    def device(self) -> dict[str, Any]:
        return self.http("GET", f"{self.sim}/devices/{DEVICE_ID}", timeout=15)

    def events(self) -> list[dict[str, Any]]:
        return sorted(self.http("GET", f"{self.sim}/events?limit=500", timeout=15)["events"], key=lambda item: item["id"])

    def assist(self, text: str, conversation_id: str) -> dict[str, Any]:
        return self.http("POST", f"{self.ha}/api/conversation/process", {"text": text, "language": "zh-Hans", "agent_id": self.agent, "conversation_id": conversation_id}, auth=True)

    def turn(self, test_id: str, text: str, conversation_id: str, checks) -> dict[str, Any]:
        floor = max((item["id"] for item in self.events()), default=0)
        before = self.ha_state()
        device_before = self.device()
        self.observer.clear()
        started = time.monotonic()
        response = self.assist(text, conversation_id)
        elapsed = round(time.monotonic() - started, 3)
        time.sleep(0.5)
        after = self.ha_state()
        record = {
            "test_id": test_id,
            "timestamp": now(),
            "conversation_id": conversation_id,
            "input": text,
            "response": response,
            "speech": self.speech(response),
            "elapsed_seconds": elapsed,
            "ha_before": before,
            "ha_after": after,
            "trace": self.ha_state("sensor.edgehome_last_execution"),
            "device_before": device_before,
            "device_after": self.device(),
            "events": [item for item in self.events() if item["id"] > floor],
            "mqtt": self.observer.snapshot(),
            "conversation_states": self.conversation_states(conversation_id),
        }
        failures = checks(record)
        record["passed"], record["failures"] = not failures, failures
        self.persist(record)
        self.records.append(record)
        if failures:
            raise AssertionError(f"{test_id}: {'; '.join(failures)}")
        return record

    @staticmethod
    def speech(response: dict[str, Any]) -> str:
        return str(response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", ""))

    @staticmethod
    def brightness(state: dict[str, Any] | None) -> int | None:
        value = (state or {}).get("attributes", {}).get("brightness")
        return int(value) if isinstance(value, (int, float)) else None

    @classmethod
    def percent(cls, state: dict[str, Any] | None) -> int | None:
        brightness = cls.brightness(state)
        return round(brightness * 100 / 255) if brightness is not None else None

    @staticmethod
    def command_count(record: dict[str, Any]) -> int:
        return sum(message["topic"].endswith("/command") for message in record["mqtt"])

    def conversation_states(self, conversation_id: str) -> list[dict[str, Any]]:
        if not self.context_db.exists():
            return []
        with sqlite3.connect(self.context_db) as connection:
            rows = connection.execute("SELECT state_json, updated_at, expires_at FROM conversation_state WHERE conversation_id = ?", (conversation_id,)).fetchall()
        output = []
        for state_json, updated_at, expires_at in rows:
            state = json.loads(state_json)
            output.append({
                "conversation_id": conversation_id, "updated_at": updated_at, "expires_at": expires_at,
                "last_entity_id": state.get("last_entity_id"), "last_successful_action": state.get("last_successful_action"),
                "last_requested_parameters": state.get("last_requested_parameters"), "last_actual_state": state.get("last_actual_state"),
                "recent_results": state.get("recent_results"), "pending_clarification": state.get("pending_clarification"),
            })
        return output

    def persist(self, record: dict[str, Any]) -> None:
        self.evidence.write("results.jsonl", {key: value for key, value in record.items() if key not in {"mqtt", "events", "conversation_states"}})
        for message in record["mqtt"]:
            self.evidence.write("mqtt_commands.jsonl" if message["topic"].endswith("/command") else "mqtt_states.jsonl", {"test_id": record["test_id"], "conversation_id": record["conversation_id"], **message})
        for event in record["events"]:
            self.evidence.write("simulator_events.jsonl", {"test_id": record["test_id"], "conversation_id": record["conversation_id"], **event})
        self.evidence.write("ha_states.jsonl", {"test_id": record["test_id"], "conversation_id": record["conversation_id"], "before": record["ha_before"], "after": record["ha_after"], "trace": record["trace"]})
        for state in record["conversation_states"]:
            self.evidence.write("conversation_states.jsonl", {"test_id": record["test_id"], **state})

    def expect_control(self, percent: int | None = None, state: str = "on"):
        def check(record: dict[str, Any]) -> list[str]:
            failures = []
            if record["trace"] is None or record["trace"].get("state") != "success":
                failures.append("trace is not verified success")
            if self.command_count(record) != 1:
                failures.append("expected exactly one MQTT command")
            if record["ha_after"] is None or record["ha_after"].get("state") != state:
                failures.append(f"HA state is not {state}")
            if percent is not None and self.percent(record["ha_after"]) != percent:
                failures.append(f"HA brightness is not {percent}%")
            return failures
        return check

    def expect_query(self, contains: str | None = None):
        def check(record: dict[str, Any]) -> list[str]:
            failures = []
            if self.command_count(record) != 0:
                failures.append("query sent an MQTT command")
            if record["trace"] is None or record["trace"].get("state") == "success":
                failures.append("query was marked as device success")
            if contains is not None and contains not in record["speech"]:
                failures.append(f"query speech misses {contains!r}")
            return failures
        return check

    def expect_failure_without_context(self):
        def check(record: dict[str, Any]) -> list[str]:
            failures = []
            if self.command_count(record) != 0:
                failures.append("new session unexpectedly sent MQTT command")
            if "设备" not in record["speech"]:
                failures.append("new session did not ask for an entity")
            return failures
        return check

    def compose(self, *arguments: str, ttl_seconds: int | None = None) -> None:
        environment = dict(os.environ)
        if ttl_seconds is None:
            environment.pop("EDGEHOME_CONTEXT_TTL_SECONDS", None)
        else:
            environment["EDGEHOME_CONTEXT_TTL_SECONDS"] = str(ttl_seconds)
        command = ["docker", "compose", "-f", str(self.compose_file), *arguments]
        result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"docker compose failed: {' '.join(command)}\n{result.stdout}\n{result.stderr}")

    def wait_agent(self, timeout: int = 90) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                agent_state = self.ha_state(self.agent)
                if (
                    agent_state is not None
                    and agent_state.get("state") not in {"unavailable", "unknown"}
                    and self.ha_state() is not None
                ):
                    return
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError("Home Assistant conversation agent did not recover")

    def run(self) -> None:
        self.reset()
        first = f"m15-chain-{uuid4().hex[:12]}"
        self.turn("chain_set_35", "把客厅主灯调到35%", first, self.expect_control(35))
        self.turn("chain_last_setting", "刚才设置的是多少？", first, self.expect_query("35%"))
        self.turn("chain_current", "现在亮度是多少？", first, self.expect_query("35%"))
        self.turn("chain_last_entity", "刚才控制的是哪个设备？", first, self.expect_query("客厅主灯"))
        self.turn("chain_last_success", "上一次操作成功了吗？", first, self.expect_query("成功"))
        self.turn("chain_raise_10", "再调高10%", first, self.expect_control(45))
        self.turn("chain_turn_off", "关掉吧", first, self.expect_control(state="off"))

        self.reset()
        second = f"m15-decrease-{uuid4().hex[:12]}"
        self.turn("decrease_set_62", "把客厅主灯调到62%", second, self.expect_control(62))
        self.turn("decrease_history", "刚才调成多少了？", second, self.expect_query("62%"))
        self.turn("decrease_by_7", "再降低7%", second, self.expect_control(55))

        self.reset()
        self.turn("new_session_history", "刚才设置的是多少？", f"m15-isolated-{uuid4().hex[:12]}", self.expect_failure_without_context())

        self.reset()
        failed = f"m15-failure-{uuid4().hex[:12]}"
        self.turn("failure_seed_70", "把客厅主灯调到70%", failed, self.expect_control(70))
        self.fault("ack_without_state_change")
        rejected = self.turn("failure_rejected_30", "把客厅主灯调到30%", failed, lambda record: ["fault path reported success"] if record["trace"] and record["trace"].get("state") == "success" else [])
        if self.percent(rejected["ha_after"]) != 70:
            raise AssertionError("failed target changed the real HA state")
        self.fault("none")
        self.turn("failure_relative_uses_actual", "再暗一点", failed, self.expect_control(60))

        self.reset()
        restart = f"m15-restart-{uuid4().hex[:12]}"
        self.turn("restart_seed_50", "把客厅主灯调到50%", restart, self.expect_control(50))
        self.compose("restart", "home-assistant")
        self.wait_agent()
        self.turn("restart_relative", "再暗一点", restart, self.expect_control(40))

        self.reset()
        session_a, session_b = f"m15-a-{uuid4().hex[:12]}", f"m15-b-{uuid4().hex[:12]}"
        self.turn("isolation_seed_a", "把客厅主灯调到60%", session_a, self.expect_control(60))
        isolated = self.turn("isolation_relative_b", "再暗一点", session_b, self.expect_failure_without_context())
        if self.percent(isolated["ha_after"]) != 60:
            raise AssertionError("session B changed session A device state")

        self.compose("up", "-d", "--force-recreate", "home-assistant", ttl_seconds=2)
        self.wait_agent()
        self.reset()
        ttl = f"m15-ttl-{uuid4().hex[:12]}"
        self.turn("ttl_seed", "把客厅主灯调到48%", ttl, self.expect_control(48))
        time.sleep(3)
        self.turn("ttl_expired_query", "刚才设置的是多少？", ttl, self.expect_failure_without_context())
        self.compose("up", "-d", "--force-recreate", "home-assistant")
        self.wait_agent()

    def summary(self) -> str:
        passed = sum(record["passed"] for record in self.records)
        failed = len(self.records) - passed
        status = "INTEGRATION_VERIFIED" if failed == 0 and self.aborted is None else "REJECTED"
        return "\n".join((
            "# M1.5 结构化会话黑盒验收汇总", "",
            f"- 生成时间：{now()}", f"- 总用例：{len(self.records)}", f"- 通过：{passed}", f"- 失败：{failed}",
            f"- 中止原因：{self.aborted or '无'}", "- 入口：Home Assistant `/api/conversation/process`；MQTT 仅作只读订阅。",
            "- 证据：`results.jsonl`、`mqtt_commands.jsonl`、`mqtt_states.jsonl`、`simulator_events.jsonl`、`ha_states.jsonl`、`conversation_states.jsonl`。",
            "", f"状态：`{status}`。", "",
        ))


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home-assistant-url", default=os.getenv("HA_URL", "http://127.0.0.1:8123"))
    parser.add_argument("--simulator-url", default=os.getenv("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--agent-id", default=os.getenv("EDGEHOME_AGENT_ID", AGENT))
    parser.add_argument("--mqtt-host", default=os.getenv("EDGEHOME_MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.getenv("EDGEHOME_MQTT_PORT", "1883")))
    parser.add_argument("--report-directory", type=Path, default=ROOT / "reports" / "acceptance" / "m15_context")
    parser.add_argument("--context-db", type=Path, default=ROOT / "deployment" / "runtime" / "edgehome_context.db")
    parser.add_argument("--compose-file", type=Path, default=ROOT / "deployment" / "docker-compose.yml")
    return parser.parse_args()


def main() -> int:
    if not os.getenv("HA_TOKEN"):
        print("HA_TOKEN must be set.", file=sys.stderr)
        return 2
    parsed = args()
    evidence, observer = Evidence(parsed.report_directory), Observer(parsed.mqtt_host, parsed.mqtt_port)
    acceptance = Acceptance(parsed, evidence, observer)
    observer.start()
    error: Exception | None = None
    try:
        acceptance.run()
    except Exception as exc:  # noqa: BLE001 - acceptance failures must remain visible.
        error = exc
        acceptance.aborted = f"{type(exc).__name__}: {exc}"
        evidence.write("results.jsonl", {"test_id": "acceptance_aborted", "timestamp": now(), "passed": False, "failures": [acceptance.aborted]})
    finally:
        try:
            acceptance.compose("up", "-d", "mqtt", "device-simulator", "home-assistant")
            acceptance.reset()
        except Exception as cleanup:  # noqa: BLE001
            print(f"cleanup warning: {cleanup}", file=sys.stderr)
        observer.stop()
        (parsed.report_directory / "summary.md").write_text(acceptance.summary(), encoding="utf-8", newline="\n")
    if error is not None:
        print(f"M1.5 acceptance failed: {error}", file=sys.stderr)
        return 1
    print(f"M1.5 acceptance passed. Evidence: {parsed.report_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
