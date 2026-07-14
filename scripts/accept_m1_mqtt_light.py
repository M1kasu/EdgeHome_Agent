"""Formal Home Assistant API acceptance for the M1 MQTT light protocol chain.

This runner never imports EdgeHome production code and never writes Home Assistant
state. It observes the public HA API, subscribes to MQTT read-only, and uses the
simulator's explicitly test-only reset/fault endpoints.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
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
DEVICE_ID = "living_room_main_light"
LIGHT_ENTITY_ID = "light.edgehome_living_room_main"
FEEDBACK_ENTITY_ID = "sensor.edgehome_living_room_main_feedback"
AGENT_ID = "conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome"
MQTT_TOPICS = (
    f"edgehome/devices/{DEVICE_ID}/command",
    f"edgehome/devices/{DEVICE_ID}/state",
    f"edgehome/devices/{DEVICE_ID}/availability",
    f"edgehome/devices/{DEVICE_ID}/feedback",
)
EXPECTED_CASE_COUNT = 31


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


class JsonlEvidence:
    """Write raw, one-record-per-line evidence without secrets."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.results = directory / "results.jsonl"
        self.commands = directory / "mqtt_commands.jsonl"
        self.states = directory / "mqtt_states.jsonl"
        self.events = directory / "simulator_events.jsonl"
        self.ha_states = directory / "ha_states.jsonl"
        self.conversations = directory / "conversation_states.jsonl"
        for path in (self.results, self.commands, self.states, self.events, self.ha_states, self.conversations):
            path.write_text("", encoding="utf-8")

    @staticmethod
    def append(path: Path, value: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


class MqttObserver:
    """Collect MQTT protocol messages without publishing anything."""

    def __init__(self, host: str, port: int) -> None:
        self._messages: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edgehome-m1-observer-{uuid4().hex}")
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._host = host
        self._port = port

    def start(self) -> None:
        self._client.connect(self._host, self._port, keepalive=30)
        self._client.loop_start()
        if not self._ready.wait(10):
            raise RuntimeError("MQTT observer did not subscribe within 10 seconds")

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def clear(self) -> None:
        with self._lock:
            self._messages.clear()

    def messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return json_clone(self._messages)

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: mqtt.ConnectFlags, reason_code: mqtt.ReasonCode, _properties: mqtt.Properties | None) -> None:
        if reason_code.is_failure:
            return
        for topic in MQTT_TOPICS:
            client.subscribe(topic, qos=1)
        self._ready.set()

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        raw = message.payload.decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        with self._lock:
            self._messages.append({"timestamp": utc_now(), "topic": message.topic, "payload": payload, "retain": bool(message.retain)})


class M1Acceptance:
    """Execute all M1 black-box cases and persist evidence before asserting a gate."""

    def __init__(self, args: argparse.Namespace, evidence: JsonlEvidence, observer: MqttObserver) -> None:
        self.ha_url = args.home_assistant_url.rstrip("/")
        self.simulator_url = args.simulator_url.rstrip("/")
        self.agent_id = args.agent_id
        self.token = os.environ["HA_TOKEN"]
        self.evidence = evidence
        self.observer = observer
        self.context_db = args.context_db
        self.compose_file = args.compose_file
        self._result_records: list[dict[str, Any]] = []
        self.aborted_reason: str | None = None

    def request(self, method: str, url: str, *, payload: dict[str, Any] | None = None, authorized: bool = False, timeout: int = 30) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if authorized:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code} from {url}: {raw}") from error
        return json.loads(raw) if raw else None

    def reset(self) -> dict[str, Any]:
        return self.request("POST", f"{self.simulator_url}/admin/devices/{DEVICE_ID}/reset", payload={})

    def set_fault(self, mode: str, delay_ms: int = 0) -> dict[str, Any]:
        return self.request(
            "POST",
            f"{self.simulator_url}/admin/devices/{DEVICE_ID}/fault",
            payload={"mode": mode, "delay_ms": delay_ms},
        )

    def device(self) -> dict[str, Any]:
        return self.request("GET", f"{self.simulator_url}/devices/{DEVICE_ID}")

    def events(self) -> list[dict[str, Any]]:
        response = self.request("GET", f"{self.simulator_url}/events?limit=500")
        return sorted(response["events"], key=lambda item: item["id"])

    def ha_state(self, entity_id: str = LIGHT_ENTITY_ID) -> dict[str, Any] | None:
        try:
            return self.request("GET", f"{self.ha_url}/api/states/{entity_id}", authorized=True)
        except RuntimeError as error:
            if "HTTP 404" in str(error):
                return None
            raise

    def assist(self, text: str, conversation_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"{self.ha_url}/api/conversation/process",
            authorized=True,
            payload={"text": text, "language": "zh-Hans", "agent_id": self.agent_id, "conversation_id": conversation_id},
            timeout=90,
        )

    @staticmethod
    def _brightness(state: dict[str, Any] | None) -> int | None:
        if state is None:
            return None
        value = state.get("attributes", {}).get("brightness")
        return int(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _speech(response: dict[str, Any]) -> str:
        return str(response.get("response", {}).get("speech", {}).get("plain", {}).get("speech", ""))

    @staticmethod
    def _trace_status(trace: dict[str, Any] | None) -> str | None:
        return trace.get("state") if trace is not None else None

    def _conversation_state(self, conversation_id: str) -> list[dict[str, Any]]:
        if not self.context_db.exists():
            return []
        with sqlite3.connect(self.context_db) as connection:
            rows = connection.execute(
                "SELECT state_json, updated_at, expires_at FROM conversation_state WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for state_json, updated_at, expires_at in rows:
            state = json.loads(state_json)
            records.append(
                {
                    "conversation_id": conversation_id,
                    "updated_at": updated_at,
                    "expires_at": expires_at,
                    "last_entity_id": state.get("last_entity_id"),
                    "last_action": state.get("last_action"),
                    "last_parameters": state.get("last_parameters"),
                    "recent_results": state.get("recent_results"),
                    "pending_clarification": state.get("pending_clarification"),
                }
            )
        return records

    def _run(self, test_id: str, text: str, evaluate: Callable[[dict[str, Any]], list[str]]) -> dict[str, Any]:
        conversation_id = f"m1-{test_id}-{uuid4().hex[:12]}"
        event_floor = max((event["id"] for event in self.events()), default=0)
        ha_before = self.ha_state()
        device_before = self.device()
        self.observer.clear()
        started = time.monotonic()
        response = self.assist(text, conversation_id)
        elapsed_seconds = round(time.monotonic() - started, 3)
        time.sleep(0.5)
        trace = self.ha_state("sensor.edgehome_last_execution")
        ha_after = self.ha_state()
        feedback_after = self.ha_state(FEEDBACK_ENTITY_ID)
        device_after = self.device()
        new_events = [event for event in self.events() if event["id"] > event_floor]
        mqtt_messages = self.observer.messages()
        conversation_states = self._conversation_state(conversation_id)
        record = {
            "test_id": test_id,
            "timestamp": utc_now(),
            "conversation_id": conversation_id,
            "input": text,
            "response": response,
            "agent_speech": self._speech(response),
            "elapsed_seconds": elapsed_seconds,
            "trace": trace,
            "ha_before": ha_before,
            "ha_after": ha_after,
            "feedback_after": feedback_after,
            "device_before": device_before,
            "device_after": device_after,
            "simulator_events": new_events,
            "mqtt_messages": mqtt_messages,
            "conversation_states": conversation_states,
        }
        failures = evaluate(record)
        record["passed"] = not failures
        record["failures"] = failures
        self._write_evidence(record)
        self._result_records.append(record)
        if failures:
            raise AssertionError(f"{test_id} failed: {'; '.join(failures)}")
        return record

    def _write_evidence(self, record: dict[str, Any]) -> None:
        self.evidence.append(self.evidence.results, {key: value for key, value in record.items() if key not in {"mqtt_messages", "simulator_events", "conversation_states"}})
        for message in record["mqtt_messages"]:
            target = self.evidence.commands if message["topic"].endswith("/command") else self.evidence.states
            self.evidence.append(target, {"test_id": record["test_id"], "conversation_id": record["conversation_id"], **message})
        for event in record["simulator_events"]:
            self.evidence.append(self.evidence.events, {"test_id": record["test_id"], "conversation_id": record["conversation_id"], **event})
        self.evidence.append(
            self.evidence.ha_states,
            {
                "test_id": record["test_id"],
                "conversation_id": record["conversation_id"],
                "before": record["ha_before"],
                "after": record["ha_after"],
                "feedback_after": record["feedback_after"],
                "trace": record["trace"],
            },
        )
        for state in record["conversation_states"]:
            self.evidence.append(self.evidence.conversations, {"test_id": record["test_id"], **state})

    @staticmethod
    def _has_topic(record: dict[str, Any], suffix: str) -> bool:
        return any(message["topic"].endswith(suffix) for message in record["mqtt_messages"])

    @staticmethod
    def _has_event(record: dict[str, Any], event_type: str) -> bool:
        return any(event["event_type"] == event_type for event in record["simulator_events"])

    def _normal_checks(self, expected_state: str, expected_brightness: int | None) -> Callable[[dict[str, Any]], list[str]]:
        def evaluate(record: dict[str, Any]) -> list[str]:
            failures: list[str] = []
            ha_after = record["ha_after"]
            device_after = record["device_after"]
            if self._trace_status(record["trace"]) != "success":
                failures.append("EdgeHome trace is not verified success")
            if not self._has_topic(record, "/command") or not self._has_topic(record, "/state"):
                failures.append("missing MQTT command or state evidence")
            if not self._has_event(record, "command_received") or not self._has_event(record, "state_changed"):
                failures.append("simulator did not record command_received/state_changed")
            if ha_after is None or ha_after.get("state") != expected_state:
                failures.append(f"HA state is not {expected_state}")
            if device_after["state"]["power"].lower() != expected_state:
                failures.append(f"SQLite power is not {expected_state}")
            if expected_brightness is not None:
                if self._brightness(ha_after) is None or abs(self._brightness(ha_after) - expected_brightness) > 1:
                    failures.append(f"HA brightness is not {expected_brightness}")
                if abs(int(device_after["state"]["brightness"]) - expected_brightness) > 1:
                    failures.append(f"SQLite brightness is not {expected_brightness}")
            return failures

        return evaluate

    def _failure_checks(
        self,
        *,
        expected_feedback: str | None = None,
        expected_error_fragment: str | None = None,
        require_command: bool = True,
        minimum_elapsed_seconds: float | None = None,
        expect_unavailable: bool = False,
    ) -> Callable[[dict[str, Any]], list[str]]:
        def evaluate(record: dict[str, Any]) -> list[str]:
            failures: list[str] = []
            if self._trace_status(record["trace"]) == "success":
                failures.append("false success: trace reports success")
            if "已完成" in record["agent_speech"] or "已设置" in record["agent_speech"]:
                failures.append("false success: agent speech claims completion")
            if require_command != self._has_topic(record, "/command"):
                failures.append("unexpected MQTT command presence")
            if expected_feedback is not None:
                feedback = record["feedback_after"]
                if feedback is None or feedback.get("state") != expected_feedback:
                    failures.append(f"feedback sensor is not {expected_feedback}")
            if expected_error_fragment is not None and expected_error_fragment not in record["agent_speech"]:
                failures.append(f"agent speech does not contain {expected_error_fragment!r}")
            if minimum_elapsed_seconds is not None and record["elapsed_seconds"] < minimum_elapsed_seconds:
                failures.append("executor returned before the feedback timeout")
            if expect_unavailable and (record["ha_after"] is None or record["ha_after"].get("state") != "unavailable"):
                failures.append("HA entity is not unavailable")
            return failures

        return evaluate

    def normal_controls(self) -> None:
        self.reset()
        time.sleep(1)
        self._run("normal_turn_off", "请关闭大厅主灯", self._normal_checks("off", None))
        self._run("normal_turn_on", "把客厅主照明开一下", self._normal_checks("on", 204))
        cases = (
            ("brightness_5", "将客厅主灯调到5%", 13),
            ("brightness_17", "大厅那盏主灯设为17%", 43),
            ("brightness_26", "我想让客厅大灯保持26%的亮度", 66),
            ("brightness_33", "把客厅主照明设置到33%", 84),
            ("brightness_35", "请将客厅主灯亮度改成35%", 89),
            ("brightness_48", "客厅灯调至48%", 122),
            ("brightness_50", "把大厅主灯设为50%", 128),
            ("brightness_59", "把客厅大灯调到59%", 150),
            ("brightness_67", "客厅主灯亮度设置67%", 171),
            ("brightness_74", "请把大厅灯改成74%", 189),
            ("brightness_86", "客厅主灯调亮到86%", 219),
            ("brightness_93", "把客厅灯设置为93%", 237),
            ("brightness_0", "请将客厅主灯设为0%", None),
            ("brightness_100", "把客厅主灯调到100%", 255),
            ("brightness_half", "客厅主灯调到一半", 128),
            ("brightness_tenths", "把大厅主灯调成三成", 76),
        )
        for test_id, text, brightness in cases:
            self.reset()
            time.sleep(0.3)
            expected_state = "off" if brightness is None else "on"
            self._run(test_id, text, self._normal_checks(expected_state, brightness))

    def invalid_brightness(self) -> None:
        for test_id, text in (("invalid_negative", "把客厅主灯调到-10%"), ("invalid_above_100", "把客厅主灯调到150%")):
            self.reset()
            time.sleep(0.5)
            self._run(test_id, text, self._failure_checks(require_command=False))

    def injected_faults(self) -> None:
        cases = (
            ("fault_reject", "reject", 0, self._failure_checks(expected_feedback="rejected", expected_error_fragment="拒绝")),
            ("fault_ack_without_state_change", "ack_without_state_change", 0, self._failure_checks(expected_feedback="ack_without_state_change", expected_error_fragment="状态没有变化")),
            ("fault_invalid_state", "invalid_state", 0, self._failure_checks(expected_feedback="invalid_state", expected_error_fragment="无效状态")),
            ("fault_delay_timeout", "delay", 4_500, self._failure_checks(minimum_elapsed_seconds=2.8)),
        )
        for test_id, mode, delay_ms, checks in cases:
            self.reset()
            time.sleep(0.5)
            self.set_fault(mode, delay_ms)
            time.sleep(0.5)
            before = self.ha_state()
            record = self._run(test_id, "把客厅主灯调到35%", checks)
            if mode in {"ack_without_state_change", "invalid_state", "delay"}:
                if self._brightness(record["ha_after"]) != self._brightness(before):
                    raise AssertionError(f"{test_id} changed HA state despite the injected failure")
            if mode == "delay":
                time.sleep(5)
            self.reset()
            time.sleep(0.5)

    def offline_case(self) -> None:
        self.reset()
        self.set_fault("offline")
        self._wait_for(lambda: self.ha_state() is not None and self.ha_state()["state"] == "unavailable", 20, "MQTT availability offline")
        self._run("fault_offline", "把客厅主灯调到35%", self._failure_checks(require_command=False, expected_error_fragment="不可用", expect_unavailable=True))
        self.reset()
        self._wait_for(lambda: self.ha_state() is not None and self.ha_state()["state"] != "unavailable", 20, "MQTT availability recovery")

    def restart_cases(self) -> None:
        self.reset()
        self._run("restart_seed_ha", "把客厅主灯调到59%", self._normal_checks("on", 150))
        device_before = self.device()
        self._compose("restart", "home-assistant")
        self._wait_for(
            lambda: self.ha_state(self.agent_id) is not None
            and self.ha_state() is not None
            and self._brightness(self.ha_state()) == 150,
            90,
            "HA conversation agent and MQTT light recovery",
        )
        self._record_restart("restart_home_assistant", device_before, self.device(), self.ha_state())

        self.reset()
        self._run("restart_seed_simulator", "把客厅主灯调到67%", self._normal_checks("on", 171))
        device_before = self.device()
        self._compose("stop", "device-simulator")
        self._wait_for(lambda: self.ha_state() is not None and self.ha_state()["state"] == "unavailable", 30, "simulator offline availability")
        self._compose("up", "-d", "device-simulator")
        self._wait_for(lambda: self.ha_state() is not None and self._brightness(self.ha_state()) == 171, 45, "simulator restart recovery")
        self._record_restart("restart_device_simulator", device_before, self.device(), self.ha_state())

    def broker_case(self) -> None:
        self.reset()
        self._compose("stop", "mqtt")
        time.sleep(2)
        record = self._run("restart_mosquitto_control", "把客厅主灯调到35%", self._failure_checks(require_command=False))
        self._compose("up", "-d", "mqtt")
        self._wait_for(lambda: self.ha_state() is not None and self.ha_state()["state"] != "unavailable", 45, "broker recovery")
        self._record_restart("restart_mosquitto", record["device_before"], self.device(), self.ha_state())

    def _record_restart(self, test_id: str, device_before: dict[str, Any], device_after: dict[str, Any], ha_after: dict[str, Any] | None) -> None:
        record = {
            "test_id": test_id,
            "timestamp": utc_now(),
            "conversation_id": None,
            "input": None,
            "passed": device_before["state"] == device_after["state"] and self._brightness(ha_after) == int(device_after["state"]["brightness"]),
            "failures": [],
            "device_before": device_before,
            "device_after": device_after,
            "ha_after": ha_after,
            "agent_speech": None,
            "elapsed_seconds": None,
        }
        if not record["passed"]:
            record["failures"].append("device SQLite state or HA state did not recover")
        self.evidence.append(self.evidence.results, record)
        self.evidence.append(self.evidence.ha_states, {"test_id": test_id, "before": None, "after": ha_after})
        self._result_records.append(record)
        if not record["passed"]:
            raise AssertionError(f"{test_id} failed: {record['failures'][0]}")

    def _compose(self, *arguments: str) -> None:
        command = ["docker", "compose", "-f", str(self.compose_file), *arguments]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"docker compose failed: {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")

    @staticmethod
    def _wait_for(predicate: Callable[[], bool], timeout_seconds: float, label: str) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError(f"Timed out waiting for {label}")

    def summary(self) -> str:
        passed = sum(1 for record in self._result_records if record["passed"])
        failed = len(self._result_records) - passed
        false_successes = sum(
            1
            for record in self._result_records
            if not record["passed"] and (self._trace_status(record.get("trace")) == "success" or "已完成" in str(record.get("agent_speech")))
        )
        complete = len(self._result_records) == EXPECTED_CASE_COUNT and self.aborted_reason is None
        status = "INTEGRATION_VERIFIED" if failed == 0 and complete else "REJECTED"
        return "\n".join(
            (
                "# M1 MQTT 主灯黑盒验收汇总",
                "",
                f"- 生成时间：{utc_now()}",
                f"- 总用例：{len(self._result_records)}",
                f"- 计划用例：{EXPECTED_CASE_COUNT}",
                f"- 通过：{passed}",
                f"- 失败：{failed}",
                f"- 假成功：{false_successes}",
                f"- 中止原因：{self.aborted_reason or '无'}",
                "- 入口：Home Assistant `/api/conversation/process`；MQTT 仅作只读订阅。",
                "- 证据：`results.jsonl`、`mqtt_commands.jsonl`、`mqtt_states.jsonl`、`simulator_events.jsonl`、`ha_states.jsonl`、`conversation_states.jsonl`。",
                "",
                f"状态：`{status}`（只有通过后才可等待外部独立验收）。",
                "",
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home-assistant-url", default=os.environ.get("HA_URL", "http://127.0.0.1:8123"))
    parser.add_argument("--simulator-url", default=os.environ.get("EDGEHOME_SIMULATOR_URL", "http://127.0.0.1:8090"))
    parser.add_argument("--agent-id", default=os.environ.get("EDGEHOME_AGENT_ID", AGENT_ID))
    parser.add_argument("--mqtt-host", default=os.environ.get("EDGEHOME_MQTT_HOST", "127.0.0.1"))
    parser.add_argument("--mqtt-port", type=int, default=int(os.environ.get("EDGEHOME_MQTT_PORT", "1883")))
    parser.add_argument("--report-directory", type=Path, default=ROOT / "reports" / "acceptance" / "m1_mqtt_light")
    parser.add_argument("--context-db", type=Path, default=ROOT / "deployment" / "runtime" / "edgehome_context.db")
    parser.add_argument("--compose-file", type=Path, default=ROOT / "deployment" / "docker-compose.yml")
    return parser.parse_args()


def main() -> int:
    if not os.environ.get("HA_TOKEN"):
        print("HA_TOKEN must be set for M1 acceptance.", file=sys.stderr)
        return 2
    args = parse_args()
    evidence = JsonlEvidence(args.report_directory)
    observer = MqttObserver(args.mqtt_host, args.mqtt_port)
    acceptance = M1Acceptance(args, evidence, observer)
    observer.start()
    failure: Exception | None = None
    try:
        acceptance.normal_controls()
        acceptance.invalid_brightness()
        acceptance.injected_faults()
        acceptance.offline_case()
        acceptance.restart_cases()
        acceptance.broker_case()
    except Exception as error:  # noqa: BLE001 - acceptance failures must become evidence, not be hidden.
        failure = error
        acceptance.aborted_reason = f"{type(error).__name__}: {error}"
        JsonlEvidence.append(
            evidence.results,
            {
                "test_id": "acceptance_aborted",
                "timestamp": utc_now(),
                "conversation_id": None,
                "input": None,
                "passed": False,
                "failures": [acceptance.aborted_reason],
            },
        )
    finally:
        try:
            acceptance._compose("up", "-d", "mqtt", "device-simulator", "home-assistant")
            acceptance.reset()
        except Exception as error:  # noqa: BLE001 - cleanup failure belongs in the terminal output.
            print(f"cleanup warning: {error}", file=sys.stderr)
        observer.stop()
        (args.report_directory / "summary.md").write_text(acceptance.summary(), encoding="utf-8", newline="\n")
    if failure is not None:
        print(f"M1 acceptance failed: {failure}", file=sys.stderr)
        return 1
    print(f"M1 acceptance passed. Evidence: {args.report_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
