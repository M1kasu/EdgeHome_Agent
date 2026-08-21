"""Conversational intent planning and verified execution for the workbench."""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Callable
from uuid import uuid4

from .edge_language import EdgeLlmClient


InventoryProvider = Callable[[], dict[str, object]]
DeviceExecutor = Callable[[str, dict[str, object]], dict[str, object]]
ProactiveAnalyzer = Callable[[], dict[str, object]]

_WRITE_ACTIONS = {
    "turn_on",
    "turn_off",
    "set_brightness",
    "set_temperature",
    "set_hvac_mode",
    "set_position",
}
_CONFIRM_WORDS = {"确认", "确认执行", "同意", "执行计划", "开始执行", "可以执行"}
_CANCEL_WORDS = {"取消", "取消执行", "不用了", "先别执行", "放弃计划"}
_DIRECT_EXECUTION = re.compile(r"立即执行|直接执行|马上执行|现在执行|不用确认")
_QUERY_WORDS = re.compile(r"状态|怎么样|开着吗|关着吗|多少|几度|查询|查看|有没有")
_PROACTIVE_WORDS = re.compile(r"主动服务|节能机会|节能分析|能耗分析|空调.*(?:窗户|门窗|开窗)|(?:窗户|门窗|开窗).*空调")
_NUMBER = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)")
_ROOM_NAMES = {
    "living_room": "客厅",
    "bedroom": "卧室",
    "primary_bedroom": "主卧",
    "study": "书房",
    "kitchen": "厨房",
    "balcony": "阳台",
    "bathroom": "卫生间",
    "home": "全屋",
}
_TYPE_WORDS = {
    "light": ("灯", "照明"),
    "switch": ("电视", "开关", "插座"),
    "climate": ("空调",),
    "curtain": ("窗帘",),
    "presence": ("人体", "存在", "有人", "无人"),
    "contact": ("窗户", "门窗", "窗"),
}
_ACTION_LABELS = {
    "turn_on": "开启",
    "turn_off": "关闭",
    "set_brightness": "设置亮度",
    "set_temperature": "设置温度",
    "set_hvac_mode": "设置空调模式",
    "set_position": "设置窗帘位置",
    "read_state": "读取状态",
    "analyze_proactive": "运行主动节能分析",
}


class ConversationAgent:
    """Plan against the live registry, then execute only validated task steps."""

    def __init__(
        self,
        inventory_provider: InventoryProvider,
        device_executor: DeviceExecutor,
        proactive_analyzer: ProactiveAnalyzer,
        llm_client: EdgeLlmClient | None = None,
        database_path: str | Path | None = None,
    ) -> None:
        self._inventory_provider = inventory_provider
        self._device_executor = device_executor
        self._proactive_analyzer = proactive_analyzer
        self._llm_client = llm_client
        self._database_path = Path(database_path) if database_path is not None else None
        self._messages: list[dict[str, object]] = []
        self._plans: list[dict[str, object]] = []
        self._active_plan_id: str | None = None
        self._busy = False
        self._lock = threading.RLock()
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
            self._load()
        if not self._messages:
            self._append_message(
                "assistant",
                "你好，我是 SpaceButler。你可以直接说出家居目标，我会先理解意图、拆解任务，再通过真实设备链路执行并回读。",
            )

    def history(self) -> dict[str, object]:
        with self._lock:
            active = self._plan_by_id(self._active_plan_id)
            return {
                "conversation_id": "household",
                "messages": deepcopy(self._messages[-80:]),
                "plans": deepcopy(self._plans[-20:]),
                "active_plan": deepcopy(active),
                "busy": self._busy,
                "llm_available": self._llm_client is not None,
            }

    def submit(self, text: str) -> dict[str, object]:
        normalized = text.strip()
        if not normalized or len(normalized) > 500:
            raise ValueError("message must contain 1 to 500 characters")
        compact = "".join(normalized.split())
        if compact in _CONFIRM_WORDS:
            return self.confirm()
        if compact in _CANCEL_WORDS:
            return self.cancel()

        with self._lock:
            if self._busy:
                raise ValueError("Agent is processing another request")
            self._busy = True
            context = self._planning_context()
            self._append_message("user", normalized)
        try:
            inventory = self._inventory_provider()
            devices = _inventory_devices(inventory)
            candidate, route, model_error = self._plan_candidate(normalized, devices, context)
            plan = self._validate_plan(normalized, candidate, devices, route, model_error)
            with self._lock:
                self._plans.append(plan)
                self._active_plan_id = str(plan["plan_id"])
                self._persist_plan(plan)

            if plan["status"] == "clarification":
                self._append_message("assistant", str(plan["reply"]), str(plan["plan_id"]))
            elif plan["intent"] == "general_chat":
                self._append_message("assistant", str(plan["reply"]), str(plan["plan_id"]))
                plan["status"] = "completed"
                self._persist_plan(plan)
            elif plan["intent"] in {"query_state", "proactive_service"}:
                self._execute_plan(plan)
            elif not bool(plan["requires_confirmation"]):
                self._execute_plan(plan)
            else:
                self._append_message(
                    "assistant",
                    f"我理解为“{plan['summary']}”。已拆成 {len(plan['steps'])} 个步骤，确认后将按顺序执行。",
                    str(plan["plan_id"]),
                )
        finally:
            with self._lock:
                self._busy = False
        return self.history()

    def confirm(self) -> dict[str, object]:
        with self._lock:
            plan = self._pending_plan()
            if plan is None:
                self._append_message("assistant", "当前没有等待确认的任务计划。")
                return self.history()
            if self._busy:
                raise ValueError("Agent is processing another request")
            self._busy = True
        try:
            self._append_message("user", "确认执行", str(plan["plan_id"]))
            self._execute_plan(plan)
        finally:
            with self._lock:
                self._busy = False
        return self.history()

    def cancel(self) -> dict[str, object]:
        with self._lock:
            if self._busy:
                raise ValueError("Agent is processing another request")
            plan = self._pending_plan()
            if plan is None:
                self._append_message("assistant", "当前没有等待取消的任务计划。")
                return self.history()
            plan["status"] = "cancelled"
            plan["updated_at"] = _now()
            for step in plan["steps"]:
                if step["status"] == "pending":
                    step["status"] = "skipped"
            self._persist_plan(plan)
            self._append_message("user", "取消执行", str(plan["plan_id"]))
            self._append_message("assistant", "已取消本次任务，没有发送设备控制命令。", str(plan["plan_id"]))
            return self.history()

    def clear(self) -> dict[str, object]:
        with self._lock:
            if self._busy:
                raise ValueError("Agent is processing another request")
            self._messages.clear()
            self._plans.clear()
            self._active_plan_id = None
            if self._database_path is not None:
                with closing(sqlite3.connect(self._database_path)) as connection:
                    with connection:
                        connection.execute("DELETE FROM conversation_message")
                        connection.execute("DELETE FROM conversation_plan")
            self._append_message(
                "assistant",
                "新会话已开始。设备仍来自当前注册表，后续任务会继续经过计划校验和状态回读。",
            )
            return self.history()

    def _plan_candidate(
        self,
        text: str,
        devices: list[dict[str, object]],
        context: list[dict[str, object]],
    ) -> tuple[dict[str, object], str, str | None]:
        model_error = None
        if self._llm_client is not None:
            try:
                candidate = self._llm_client.plan_home_task(text, devices, context)
                if isinstance(candidate, dict):
                    return candidate, "edge_llm", None
            except (RuntimeError, TimeoutError, ValueError, OSError) as error:
                model_error = type(error).__name__
        return self._deterministic_candidate(text, devices, context), "deterministic_fallback", model_error

    def _validate_plan(
        self,
        text: str,
        candidate: dict[str, object],
        devices: list[dict[str, object]],
        route: str,
        model_error: str | None,
    ) -> dict[str, object]:
        device_map = {str(item.get("device_id")): item for item in devices}
        intent = str(candidate.get("intent", "clarification"))
        effective_route = route
        if intent not in {
            "device_control",
            "query_state",
            "proactive_service",
            "general_chat",
            "clarification",
        }:
            intent = "clarification"
        raw_steps = candidate.get("steps")
        if not isinstance(raw_steps, list):
            raw_steps = []
        grounding_steps: list[dict[str, object]] = []
        if intent == "device_control" and route == "edge_llm":
            grounding = self._deterministic_candidate(text, devices, self._planning_context())
            grounding_raw = grounding.get("steps")
            if isinstance(grounding_raw, list):
                grounding_steps = [item for item in grounding_raw if isinstance(item, dict)]
        steps: list[dict[str, object]] = []
        issues: list[str] = []
        for index, raw in enumerate(raw_steps[:12], start=1):
            if not isinstance(raw, dict):
                issues.append(f"步骤 {index} 不是对象")
                continue
            action = _normalize_action(str(raw.get("action", "")))
            device_id = str(raw.get("device_id") or "")
            grounded_device_id = _ground_device_id(
                text,
                device_id,
                action,
                raw.get("value"),
                devices,
                grounding_steps,
            )
            if grounded_device_id != device_id:
                device_id = grounded_device_id
                effective_route = "edge_llm_grounded"
            if action == "analyze_proactive":
                if intent != "proactive_service":
                    issues.append("主动分析步骤的意图不匹配")
                    continue
                device = None
            else:
                device = device_map.get(device_id)
                if device is None:
                    issues.append(f"步骤 {index} 引用了未注册设备")
                    continue
                raw_value = _coerce_value(action, raw.get("value"))
                raw_mode = _normalized_mode(raw.get("mode"))
                issue = _validate_action(action, raw_value, raw_mode, device)
                if issue:
                    issues.append(issue)
                    continue
                if _target_is_ambiguous(text, device, devices):
                    issues.append(f"{device.get('room_name', '')}{_type_label(str(device.get('type', '')))}目标不明确")
                    continue
            raw_value = _coerce_value(action, raw.get("value"))
            raw_mode = _normalized_mode(raw.get("mode"))
            step = {
                "step_id": f"step_{index}",
                "device_id": device_id or None,
                "entity_id": device.get("entity_id") if device else None,
                "device_name": device.get("name") if device else "主动节能服务",
                "room": device.get("room") if device else None,
                "room_name": device.get("room_name") if device else None,
                "device_type": device.get("type") if device else "service",
                "action": action,
                "value": _normalized_value(action, raw_value),
                "mode": raw_mode,
                "reason": _optional_text(raw.get("reason")) or _step_reason(action, device),
                "status": "pending",
                "error": None,
                "before": None,
                "after": None,
                "verified": None,
            }
            steps.append(step)

        if intent == "device_control" and not steps:
            fallback = self._deterministic_candidate(text, devices, self._planning_context())
            if effective_route.startswith("edge_llm") and fallback.get("steps"):
                return self._validate_plan(text, fallback, devices, "deterministic_repair", "; ".join(issues) or model_error)
            intent = "clarification"
        if intent == "device_control" and effective_route.startswith("edge_llm"):
            planned_ids = {str(step.get("device_id")) for step in steps if step.get("device_id")}
            explicit_ids = {
                str(item.get("device_id"))
                for item in devices
                if str(item.get("name", "")) and str(item.get("name")) in text
            }
            if explicit_ids - planned_ids:
                fallback = self._deterministic_candidate(text, devices, self._planning_context())
                if fallback.get("steps"):
                    return self._validate_plan(
                        text,
                        fallback,
                        devices,
                        "deterministic_repair",
                        "模型计划漏掉了用户明确提及的设备",
                    )
                intent = "clarification"
                steps = []
        if intent == "general_chat" and effective_route == "edge_llm" and _looks_like_device_request(text, devices):
            fallback = self._deterministic_candidate(text, devices, self._planning_context())
            return self._validate_plan(
                text,
                fallback,
                devices,
                "deterministic_repair",
                "模型未识别出明确的家居任务",
            )
        if intent == "query_state" and not steps:
            matched = _match_devices(text, devices)
            steps = [
                {
                    "step_id": f"step_{index}",
                    "device_id": item.get("device_id"),
                    "entity_id": item.get("entity_id"),
                    "device_name": item.get("name"),
                    "room": item.get("room"),
                    "room_name": item.get("room_name"),
                    "device_type": item.get("type"),
                    "action": "read_state",
                    "value": None,
                    "mode": None,
                    "reason": "读取当前设备状态",
                    "status": "pending",
                    "error": None,
                    "before": None,
                    "after": None,
                    "verified": None,
                }
                for index, item in enumerate(matched[:12], start=1)
            ]
            if not steps:
                intent = "clarification"
        if intent == "proactive_service" and not steps:
            steps = [
                {
                    "step_id": "step_1",
                    "device_id": None,
                    "entity_id": None,
                    "device_name": "主动节能服务",
                    "room": None,
                    "room_name": None,
                    "device_type": "service",
                    "action": "analyze_proactive",
                    "value": None,
                    "mode": None,
                    "reason": "读取当前空间信号并运行主动服务分析",
                    "status": "pending",
                    "error": None,
                    "before": None,
                    "after": None,
                    "verified": None,
                }
            ]

        has_writes = any(step["action"] in _WRITE_ACTIONS for step in steps)
        requires_confirmation = has_writes and len(steps) > 1 and not _DIRECT_EXECUTION.search(text)
        status = "awaiting_confirmation" if requires_confirmation else "ready"
        if intent == "clarification":
            status = "clarification"
        if intent == "general_chat":
            status = "ready"
        summary = str(candidate.get("summary") or _summary(intent, steps))
        reply = str(candidate.get("reply") or "")
        if intent == "clarification":
            reply = reply or (
                "我还不能唯一确定要操作的设备或参数。请说出空间、设备名称和目标状态，例如具体到“客厅主灯”。"
            )
        elif intent == "general_chat":
            reply = reply or "我在。你可以继续告诉我想完成的家居目标。"
        now = _now()
        return {
            "plan_id": f"plan_{uuid4().hex[:12]}",
            "user_text": text,
            "intent": intent,
            "summary": summary,
            "reasoning": str(candidate.get("reasoning") or _reasoning(intent, steps)),
            "reply": reply,
            "route": effective_route,
            "model_error": model_error,
            "requires_confirmation": requires_confirmation,
            "status": status,
            "steps": steps,
            "created_at": now,
            "updated_at": now,
        }

    def _execute_plan(self, plan: dict[str, object]) -> None:
        plan["status"] = "executing"
        plan["updated_at"] = _now()
        self._persist_plan(plan)
        failed = False
        for step in plan["steps"]:
            if failed:
                step["status"] = "skipped"
                continue
            step["status"] = "running"
            self._persist_plan(plan)
            try:
                if step["action"] == "read_state":
                    device = _device_by_id(
                        _inventory_devices(self._inventory_provider()),
                        str(step["device_id"]),
                    )
                    if device is None:
                        raise RuntimeError("设备已从注册表移除")
                    step["before"] = _state_evidence(device)
                    step["after"] = _state_evidence(device)
                    step["verified"] = True
                elif step["action"] == "analyze_proactive":
                    result = self._proactive_analyzer()
                    step["after"] = _proactive_evidence(result)
                    step["verified"] = True
                else:
                    inventory = _inventory_devices(self._inventory_provider())
                    before = _device_by_id(inventory, str(step["device_id"]))
                    if before is None:
                        raise RuntimeError("设备已从注册表移除")
                    step["before"] = _state_evidence(before)
                    payload = _control_payload(step, before)
                    result = self._device_executor(str(step["device_id"]), payload)
                    after = _device_by_id(_inventory_devices(result), str(step["device_id"]))
                    if after is None:
                        after = _device_by_id(
                            _inventory_devices(self._inventory_provider()),
                            str(step["device_id"]),
                        )
                    step["after"] = _state_evidence(after) if after else None
                    step["verified"] = after is not None and _step_matches(step, after)
                    if not step["verified"]:
                        raise RuntimeError("Home Assistant 回读未达到计划目标")
                step["status"] = "success"
            except (RuntimeError, TimeoutError, ValueError) as error:
                step["status"] = "failed"
                step["error"] = str(error)
                step["verified"] = False
                failed = True
            finally:
                plan["updated_at"] = _now()
                self._persist_plan(plan)

        statuses = [str(step["status"]) for step in plan["steps"]]
        successes = statuses.count("success")
        if statuses and successes == len(statuses):
            plan["status"] = "completed"
        elif successes:
            plan["status"] = "partial_success"
        else:
            plan["status"] = "failed"
        plan["updated_at"] = _now()
        self._persist_plan(plan)
        self._append_message("assistant", _execution_reply(plan), str(plan["plan_id"]))

    def _deterministic_candidate(
        self,
        text: str,
        devices: list[dict[str, object]],
        context: list[dict[str, object]],
    ) -> dict[str, object]:
        if _PROACTIVE_WORDS.search(text):
            return {
                "intent": "proactive_service",
                "summary": "分析当前空间是否存在主动节能机会",
                "reasoning": "请求涉及空间状态、空调与门窗信号，需要调用主动服务分析。",
                "steps": [{"device_id": None, "action": "analyze_proactive", "value": None}],
            }
        query = bool(_QUERY_WORDS.search(text))
        matched = _match_devices(text, devices)
        if not matched and re.search(r"它|这个设备|刚才那个", text):
            context_ids = [
                str(item.get("device_id"))
                for item in context
                if item.get("device_id")
            ]
            matched = [item for item in devices if str(item.get("device_id")) in context_ids[-1:]]
        if query:
            if not matched and re.search(r"全屋|家里|所有设备|设备状态", text):
                matched = devices
            return {
                "intent": "query_state" if matched else "clarification",
                "summary": "查询设备当前状态",
                "reasoning": "识别到状态查询意图。",
                "steps": [
                    {"device_id": item.get("device_id"), "action": "read_state", "value": None}
                    for item in matched[:12]
                ],
            }

        steps: list[dict[str, object]] = []
        segments = [item for item in re.split(r"[，,。；;、]|然后|再把|并且|同时|以及", text) if item.strip()]
        last_room: str | None = None
        for segment in segments or [text]:
            segment_devices = _match_devices(segment, devices)
            if not segment_devices and last_room is not None:
                segment_types = {
                    device_type
                    for device_type, words in _TYPE_WORDS.items()
                    if any(word in segment for word in words)
                }
                inferred = [
                    item
                    for item in devices
                    if item.get("room") == last_room and item.get("type") in segment_types
                ]
                if len(inferred) == 1:
                    segment_devices = inferred
            if not segment_devices and len(matched) == 1:
                segment_devices = matched
            if segment_devices:
                rooms = {str(item.get("room")) for item in segment_devices if item.get("room")}
                if len(rooms) == 1:
                    last_room = next(iter(rooms))
            for device in segment_devices:
                action = _action_from_text(segment, device)
                if action is not None:
                    steps.append({"device_id": device.get("device_id"), **action})
        unique: list[dict[str, object]] = []
        seen: set[tuple[object, object, object]] = set()
        for step in steps:
            key = (step.get("device_id"), step.get("action"), step.get("value"))
            if key not in seen:
                seen.add(key)
                unique.append(step)
        if unique:
            return {
                "intent": "device_control",
                "summary": f"执行 {len(unique)} 个家居控制步骤",
                "reasoning": "从设备名称、空间、动作词和目标数值中抽取了可执行任务。",
                "steps": unique,
            }
        return {
            "intent": "general_chat",
            "summary": "普通对话",
            "reasoning": "没有识别到可安全执行的设备任务。",
            "reply": "我在。请继续告诉我你的家居目标或想查询的设备状态。",
            "steps": [],
        }

    def _planning_context(self) -> list[dict[str, object]]:
        context: list[dict[str, object]] = []
        for message in self._messages[-6:]:
            context.append(
                {
                    "role": message.get("role"),
                    "content": message.get("content"),
                }
            )
        active = self._plan_by_id(self._active_plan_id)
        if active:
            for step in active.get("steps", []):
                if step.get("device_id"):
                    context.append(
                        {
                            "device_id": step.get("device_id"),
                            "device_name": step.get("device_name"),
                            "last_action": step.get("action"),
                        }
                    )
        return context[-12:]

    def _pending_plan(self) -> dict[str, object] | None:
        for plan in reversed(self._plans):
            if plan.get("status") == "awaiting_confirmation":
                return plan
        return None

    def _plan_by_id(self, plan_id: str | None) -> dict[str, object] | None:
        if not plan_id:
            return None
        return next((item for item in reversed(self._plans) if item.get("plan_id") == plan_id), None)

    def _append_message(self, role: str, content: str, plan_id: str | None = None) -> None:
        message = {
            "message_id": f"msg_{uuid4().hex[:12]}",
            "role": role,
            "content": content,
            "plan_id": plan_id,
            "created_at": _now(),
        }
        self._messages.append(message)
        self._persist_message(message)

    def _initialize_database(self) -> None:
        assert self._database_path is not None
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_message (
                        message_id TEXT PRIMARY KEY,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        plan_id TEXT,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_plan (
                        plan_id TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

    def _load(self) -> None:
        assert self._database_path is not None
        with closing(sqlite3.connect(self._database_path)) as connection:
            message_rows = connection.execute(
                """
                SELECT message_id, role, content, plan_id, created_at
                FROM conversation_message ORDER BY rowid DESC LIMIT 80
                """
            ).fetchall()
            plan_rows = connection.execute(
                """
                SELECT payload_json FROM conversation_plan
                ORDER BY updated_at DESC LIMIT 20
                """
            ).fetchall()
        self._messages = [
            {
                "message_id": row[0],
                "role": row[1],
                "content": row[2],
                "plan_id": row[3],
                "created_at": row[4],
            }
            for row in reversed(message_rows)
        ]
        self._plans = [json.loads(row[0]) for row in reversed(plan_rows)]
        if self._plans:
            self._active_plan_id = str(self._plans[-1].get("plan_id"))
            for plan in self._plans:
                if plan.get("status") == "executing":
                    plan["status"] = "interrupted"
                    self._persist_plan(plan)

    def _persist_message(self, message: dict[str, object]) -> None:
        if self._database_path is None:
            return
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO conversation_message (
                        message_id, role, content, plan_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        message["message_id"],
                        message["role"],
                        message["content"],
                        message.get("plan_id"),
                        message["created_at"],
                    ),
                )

    def _persist_plan(self, plan: dict[str, object]) -> None:
        if self._database_path is None:
            return
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO conversation_plan (plan_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        plan["plan_id"],
                        json.dumps(plan, ensure_ascii=False, separators=(",", ":")),
                        plan["created_at"],
                        plan["updated_at"],
                    ),
                )


def _inventory_devices(payload: dict[str, object]) -> list[dict[str, object]]:
    devices = payload.get("devices")
    return [item for item in devices if isinstance(item, dict)] if isinstance(devices, list) else []


def _match_devices(text: str, devices: list[dict[str, object]]) -> list[dict[str, object]]:
    direct = [
        item
        for item in devices
        if str(item.get("name", "")) in text or str(item.get("device_id", "")) in text
    ]
    if direct:
        return direct
    rooms = {
        room
        for room, name in _ROOM_NAMES.items()
        if name in text or room in text
    }
    types = {
        device_type
        for device_type, words in _TYPE_WORDS.items()
        if any(word in text for word in words)
    }
    if not rooms and not types:
        return []
    matched = [
        item
        for item in devices
        if (not rooms or item.get("room") in rooms)
        and (not types or item.get("type") in types)
    ]
    if not rooms and types and len(matched) > 1 and not re.search(r"全部|所有|都", text):
        return []
    return matched


def _target_is_ambiguous(
    text: str,
    selected: dict[str, object],
    devices: list[dict[str, object]],
) -> bool:
    name = str(selected.get("name", ""))
    device_id = str(selected.get("device_id", ""))
    if name and name in text or device_id and device_id in text:
        return False
    same_kind = [
        item
        for item in devices
        if item.get("room") == selected.get("room")
        and item.get("type") == selected.get("type")
    ]
    room_name = str(selected.get("room_name", ""))
    type_words = _TYPE_WORDS.get(str(selected.get("type", "")), ())
    generic_mention = (not room_name or room_name in text) and any(word in text for word in type_words)
    return generic_mention and len(same_kind) > 1


def _ground_device_id(
    text: str,
    selected_id: str,
    action: str,
    value: object,
    devices: list[dict[str, object]],
    grounding_steps: list[dict[str, object]],
) -> str:
    selected = _device_by_id(devices, selected_id)
    if selected is None:
        return selected_id
    name = str(selected.get("name", ""))
    if name and name in text:
        return selected_id
    device_type = str(selected.get("type", ""))
    same_type = [item for item in devices if item.get("type") == device_type]
    if len(same_type) <= 1:
        return selected_id
    normalized_value = _coerce_value(action, value)
    candidates: list[str] = []
    for step in grounding_steps:
        candidate_id = str(step.get("device_id") or "")
        candidate_device = _device_by_id(devices, candidate_id)
        if candidate_device is None or candidate_device.get("type") != device_type:
            continue
        if _normalize_action(str(step.get("action", ""))) != action:
            continue
        candidate_value = _coerce_value(action, step.get("value"))
        if normalized_value is not None and candidate_value != normalized_value:
            continue
        candidates.append(candidate_id)
    return candidates[0] if len(set(candidates)) == 1 else selected_id


def _looks_like_device_request(text: str, devices: list[dict[str, object]]) -> bool:
    has_target = bool(_match_devices(text, devices)) or any(
        word in text
        for words in _TYPE_WORDS.values()
        for word in words
    )
    has_action = bool(
        re.search(r"打开|开启|关闭|关掉|设置|调到|调成|查询|查看|状态|几度|多少|分析", text)
    )
    return has_target and has_action


def _action_from_text(text: str, device: dict[str, object]) -> dict[str, object] | None:
    device_type = str(device.get("type", ""))
    numbers = [float(item) for item in _NUMBER.findall(text)]
    value = numbers[-1] if numbers else None
    off = bool(re.search(r"关闭|关掉|关上|熄灭|停止", text))
    on = bool(re.search(r"打开|开启|开灯|点亮|启动", text))
    if device_type == "light":
        if value is not None and any(word in text for word in ("亮度", "%", "百分之", "调到", "调成")):
            return {"action": "set_brightness", "value": round(value)}
        if off:
            return {"action": "turn_off", "value": None}
        if on:
            return {"action": "turn_on", "value": None}
    if device_type == "switch":
        if off:
            return {"action": "turn_off", "value": None}
        if on:
            return {"action": "turn_on", "value": None}
    if device_type == "curtain":
        if value is not None:
            return {"action": "set_position", "value": round(value)}
        if off or re.search(r"拉上|合上", text):
            return {"action": "set_position", "value": 0}
        if on or re.search(r"拉开", text):
            return {"action": "set_position", "value": 100}
    if device_type == "climate":
        if off:
            return {"action": "turn_off", "value": None}
        mode = "heat" if "制热" in text else "cool" if "制冷" in text else None
        if value is not None and 16 <= value <= 30:
            return {"action": "set_temperature", "value": value, "mode": mode}
        if mode is not None:
            return {"action": "set_hvac_mode", "value": mode}
        if on:
            return {"action": "set_hvac_mode", "value": "cool"}
    return None


def _normalize_action(action: str) -> str:
    aliases = {
        "open": "turn_on",
        "close": "turn_off",
        "set_curtain_position": "set_position",
        "query": "read_state",
        "get_state": "read_state",
        "run_proactive_analysis": "analyze_proactive",
    }
    return aliases.get(action, action)


def _validate_action(
    action: str,
    value: object,
    mode: object,
    device: dict[str, object],
) -> str | None:
    device_type = str(device.get("type", ""))
    allowed = {
        "light": {"turn_on", "turn_off", "set_brightness", "read_state"},
        "switch": {"turn_on", "turn_off", "read_state"},
        "curtain": {"set_position", "read_state"},
        "climate": {"turn_off", "set_temperature", "set_hvac_mode", "read_state"},
        "presence": {"read_state"},
        "contact": {"read_state"},
    }.get(device_type, {"read_state"})
    if action not in allowed:
        return f"{device.get('name')} 不支持动作 {action}"
    if action in {"set_brightness", "set_position"}:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 100:
            return f"{action} 参数必须在 0 到 100 之间"
    if action == "set_temperature":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 16 <= float(value) <= 30:
            return "空调温度必须在 16 到 30 度之间"
        if mode is not None and str(mode) not in {"off", "cool", "heat"}:
            return "空调模式无效"
    if action == "set_hvac_mode" and str(value) not in {"off", "cool", "heat"}:
        return "空调模式必须是 off、cool 或 heat"
    return None


def _normalized_value(action: str, value: object) -> object:
    if action in {"set_brightness", "set_position"} and isinstance(value, (int, float)):
        return round(float(value))
    if action == "set_temperature" and isinstance(value, (int, float)):
        normalized = float(value)
        return int(normalized) if normalized.is_integer() else normalized
    return value


def _normalized_mode(value: object) -> str | None:
    normalized = str(value).strip().lower() if value is not None else ""
    return normalized if normalized in {"off", "cool", "heat"} else None


def _coerce_value(action: str, value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"", "null", "none"}:
            return None
        if action in {"set_brightness", "set_position", "set_temperature"}:
            try:
                return float(stripped)
            except ValueError:
                return value
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return None if normalized.lower() in {"", "null", "none"} else normalized


def _control_payload(step: dict[str, object], device: dict[str, object]) -> dict[str, object]:
    action = str(step["action"])
    if action in {"turn_on", "turn_off"}:
        if device.get("type") == "climate":
            return {"mode": "off" if action == "turn_off" else "cool"}
        return {"power": "on" if action == "turn_on" else "off"}
    if action == "set_brightness":
        return {"power": "on", "brightness": int(step["value"])}
    if action == "set_position":
        return {"position": int(step["value"])}
    if action == "set_hvac_mode":
        mode = str(step["value"])
        temperature = _current_temperature(device)
        return {"mode": mode, **({"temperature": temperature} if mode != "off" else {})}
    if action == "set_temperature":
        current_mode = str((device.get("state") or {}).get("mode", "off"))
        mode = str(step.get("mode") or (current_mode if current_mode in {"cool", "heat"} else "cool"))
        return {"mode": mode, "temperature": float(step["value"])}
    raise ValueError(f"unsupported task action: {action}")


def _step_matches(step: dict[str, object], device: dict[str, object]) -> bool:
    state = device.get("state")
    if not isinstance(state, dict):
        return False
    action = step["action"]
    if action == "turn_on":
        return str(state.get("power", "")).upper() == "ON"
    if action == "turn_off":
        if device.get("type") == "climate":
            return state.get("mode") == "off"
        return str(state.get("power", "")).upper() == "OFF"
    if action == "set_brightness":
        brightness = state.get("brightness")
        if not isinstance(brightness, (int, float)):
            return False
        percent = round(float(brightness) * 100 / 255)
        return str(state.get("power", "")).upper() == "ON" and abs(percent - int(step["value"])) <= 1
    if action == "set_position":
        return isinstance(state.get("position"), (int, float)) and round(float(state["position"])) == int(step["value"])
    if action == "set_hvac_mode":
        return state.get("mode") == step["value"]
    if action == "set_temperature":
        return (
            isinstance(state.get("temperature"), (int, float))
            and abs(float(state["temperature"]) - float(step["value"])) <= 0.1
            and state.get("mode") == (step.get("mode") or state.get("mode"))
        )
    return False


def _current_temperature(device: dict[str, object]) -> float:
    state = device.get("state")
    value = state.get("temperature", 24) if isinstance(state, dict) else 24
    return float(value) if isinstance(value, (int, float)) else 24.0


def _state_evidence(device: dict[str, object]) -> dict[str, object]:
    return {
        "device_id": device.get("device_id"),
        "entity_id": device.get("entity_id"),
        "online": device.get("online"),
        "fault_mode": device.get("fault_mode"),
        "state": deepcopy(device.get("state")),
        "ha_state": deepcopy(device.get("ha_state")),
        "feedback": deepcopy(device.get("feedback")),
        "updated_at": device.get("updated_at"),
    }


def _proactive_evidence(result: dict[str, object]) -> dict[str, object]:
    response = result.get("response")
    status = result.get("status")
    return {
        "response": deepcopy(response),
        "trigger_ready": status.get("proactive", {}).get("trigger_ready")
        if isinstance(status, dict)
        else None,
        "data_source": status.get("proactive", {}).get("data_source")
        if isinstance(status, dict)
        else None,
    }


def _execution_reply(plan: dict[str, object]) -> str:
    steps = plan.get("steps", [])
    if plan.get("intent") == "query_state":
        descriptions = [
            f"{step.get('device_name')}：{_state_description(step.get('after'))}"
            for step in steps
            if step.get("status") == "success"
        ]
        return "；".join(descriptions) if descriptions else "没有读取到可用设备状态。"
    if plan.get("intent") == "proactive_service":
        step = steps[0] if steps else {}
        after = step.get("after")
        response = after.get("response") if isinstance(after, dict) else None
        if isinstance(response, dict):
            return str(response.get("message") or "主动服务分析已完成。")
        return "主动服务分析已完成。"
    successful = [step for step in steps if step.get("status") == "success"]
    failed = [step for step in steps if step.get("status") == "failed"]
    if not failed:
        return f"任务已完成，{len(successful)} 个步骤均通过 Home Assistant 状态回读。"
    first = failed[0]
    return (
        f"任务未全部完成：{len(successful)} 个步骤成功，"
        f"{first.get('device_name')}在“{_ACTION_LABELS.get(str(first.get('action')), first.get('action'))}”时失败："
        f"{first.get('error')}。后续步骤已停止。"
    )


def _state_description(evidence: object) -> str:
    if not isinstance(evidence, dict):
        return "不可用"
    state = evidence.get("state")
    if not isinstance(state, dict):
        return "不可用"
    if "power" in state:
        description = "开启" if str(state.get("power")).upper() == "ON" else "关闭"
        if str(state.get("power")).upper() == "ON" and isinstance(state.get("brightness"), (int, float)):
            description += f"，亮度 {round(float(state['brightness']) * 100 / 255)}%"
        return description
    if "mode" in state:
        mode = {"off": "关闭", "cool": "制冷", "heat": "制热"}.get(str(state.get("mode")), state.get("mode"))
        return f"{mode}，设定 {state.get('temperature')}°C"
    if "position" in state:
        return f"位置 {state.get('position')}%"
    if "occupied" in state:
        return "有人" if state.get("occupied") else "无人"
    if "open" in state:
        return "打开" if state.get("open") else "关闭"
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _summary(intent: str, steps: list[dict[str, object]]) -> str:
    if intent == "device_control":
        return f"执行 {len(steps)} 个设备控制步骤"
    if intent == "query_state":
        return f"读取 {len(steps)} 个设备状态"
    if intent == "proactive_service":
        return "分析当前空间的主动服务机会"
    if intent == "general_chat":
        return "普通对话"
    return "需要补充任务信息"


def _reasoning(intent: str, steps: list[dict[str, object]]) -> str:
    if intent == "device_control":
        targets = "、".join(str(step.get("device_name")) for step in steps)
        return f"从用户目标中识别到设备控制意图，目标为 {targets}；执行前已校验设备注册表和动作参数。"
    if intent == "query_state":
        return "这是只读状态查询，不发送设备控制命令。"
    if intent == "proactive_service":
        return "需要读取存在、门窗和空调信号，再运行主动服务判断。"
    return "本轮不包含可执行设备动作。"


def _step_reason(action: str, device: dict[str, object] | None) -> str:
    name = str(device.get("name")) if device else "主动服务"
    return f"根据用户目标对{name}执行{_ACTION_LABELS.get(action, action)}"


def _type_label(device_type: str) -> str:
    return {
        "light": "灯光",
        "switch": "开关",
        "curtain": "窗帘",
        "climate": "空调",
        "presence": "存在传感器",
        "contact": "门窗传感器",
    }.get(device_type, "设备")


def _device_by_id(devices: list[dict[str, object]], device_id: str) -> dict[str, object] | None:
    return next((item for item in devices if item.get("device_id") == device_id), None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
