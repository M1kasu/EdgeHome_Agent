"""Validated edge-LLM routing for natural-language household preferences."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .memory import HouseholdMemory


class LanguageRoute(str, Enum):
    DETERMINISTIC = "deterministic"
    EDGE_LLM = "edge_llm"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class LanguageRouteResult:
    route: LanguageRoute
    learned: str
    validated_intent: str = "none"


class EdgeLlmClient:
    def __init__(
        self,
        base_url: str,
        model: str = "/models/Home-Llama-3.2-3B.q4_k_m.gguf",
        timeout_seconds: float = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def classify_energy_feedback(self, text: str) -> dict[str, object]:
        return self.complete_json(_CLASSIFIER_PROMPT, text, max_tokens=80)

    def plan_home_task(
        self,
        text: str,
        devices: list[dict[str, object]],
        context: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        inventory = [
            {
                "device_id": item.get("device_id"),
                "name": item.get("name"),
                "room": item.get("room"),
                "type": item.get("type"),
                "capabilities": item.get("capabilities"),
                "state": item.get("state"),
            }
            for item in devices[:32]
        ]
        prompt = "\n".join(
            [
                "你是智能家居意图理解和任务拆解器。只输出 JSON 对象，不要额外文字。",
                "你只规划，不执行。device_id 必须逐字复制真实设备清单，禁止编造。",
                "用户请求中每个并列设备目标必须对应一个 step，不能只处理第一项。",
                "同一句中后续设备省略空间时，继承紧邻前一个设备的空间，不要继承更早出现的空间。",
                "格式："
                '{"intent":"device_control|query_state|proactive_service|general_chat|clarification",'
                '"summary":"简短目标","reasoning":"拆解依据","reply":"普通对话回复或空字符串",'
                '"requires_confirmation":true,'
                '"steps":[{"device_id":"真实ID或null",'
                '"action":"turn_on|turn_off|set_brightness|set_temperature|set_hvac_mode|set_position|read_state|analyze_proactive",'
                '"value":数字或字符串或null,"mode":"cool|heat|off或null","reason":"步骤原因"}]}',
                "亮度和位置为 0-100；温度为 16-30；查询使用 read_state；节能分析使用 analyze_proactive。",
                "信息不足时 intent=clarification 且 steps=[]。普通聊天 intent=general_chat 且 steps=[]。",
                "真实设备清单：" + json.dumps(inventory, ensure_ascii=False, separators=(",", ":")),
                "上文：" + json.dumps((context or [])[-6:], ensure_ascii=False, separators=(",", ":")),
            ]
        )
        return self.complete_json(prompt, text, max_tokens=640)

    def complete_json(self, system_prompt: str, text: str, *, max_tokens: int = 256) -> dict[str, object]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        request = Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"edge LLM returned HTTP {error.code}: {detail}") from error
        except (TimeoutError, socket.timeout) as error:
            raise TimeoutError("edge LLM request timed out") from error
        except URLError as error:
            raise RuntimeError(f"edge LLM connection failed: {error.reason}") from error
        try:
            content = decoded["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("edge LLM response has no assistant content") from error
        return _parse_json_object(str(content))


class EdgeLanguageRouter:
    """Keep execution deterministic while using a local model for unrecognized feedback."""

    def __init__(self, client: EdgeLlmClient) -> None:
        self._client = client

    def apply_energy_feedback(self, memory: HouseholdMemory, member_id: str, text: str) -> LanguageRouteResult:
        deterministic = memory.apply_energy_feedback(member_id, text)
        if deterministic != "no_structured_energy_feedback":
            return LanguageRouteResult(LanguageRoute.DETERMINISTIC, deterministic, deterministic)
        try:
            candidate = self._client.classify_energy_feedback(text)
        except (RuntimeError, TimeoutError, ValueError):
            return LanguageRouteResult(LanguageRoute.FALLBACK, "no_structured_energy_feedback")
        intent = candidate.get("intent")
        if intent == "auto_execute":
            memory.learn_preference(
                member_id,
                "empty_room_open_window_energy_guard",
                "auto_execute",
                True,
                confidence=0.75,
                source="edge_llm_validated",
            )
            return LanguageRouteResult(LanguageRoute.EDGE_LLM, "learned_auto_execute", intent)
        if intent == "suppress_reminder":
            memory.learn_preference(
                member_id,
                "empty_room_open_window_energy_guard",
                "suppress_reminder",
                True,
                confidence=0.75,
                source="edge_llm_validated",
            )
            return LanguageRouteResult(LanguageRoute.EDGE_LLM, "learned_suppress_reminder", intent)
        if intent == "only_peak_price":
            memory.learn_preference(
                member_id,
                "empty_room_open_window_energy_guard",
                "only_peak_price",
                True,
                confidence=0.75,
                source="edge_llm_validated",
            )
            return LanguageRouteResult(LanguageRoute.EDGE_LLM, "learned_only_peak_price", intent)
        if intent == "set_unoccupied_minutes":
            minutes = candidate.get("minutes")
            if isinstance(minutes, int) and not isinstance(minutes, bool) and 1 <= minutes <= 180:
                memory.learn_preference(
                    member_id,
                    "empty_room_open_window_energy_guard",
                    "unoccupied_minutes",
                    minutes,
                    confidence=0.75,
                    source="edge_llm_validated",
                )
                return LanguageRouteResult(
                    LanguageRoute.EDGE_LLM,
                    f"learned_unoccupied_{minutes}",
                    intent,
                )
        return LanguageRouteResult(LanguageRoute.FALLBACK, "no_structured_energy_feedback")


def _parse_json_object(content: str) -> dict[str, object]:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else stripped
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("edge LLM did not return a JSON object")
    decoded = json.loads(stripped[start : end + 1])
    if not isinstance(decoded, dict):
        raise RuntimeError("edge LLM JSON is not an object")
    return decoded


_CLASSIFIER_PROMPT = """You classify Chinese smart-home energy preferences. Output one JSON object only.
Allowed schemas:
{"intent":"auto_execute","minutes":null} for future direct execution without asking.
{"intent":"suppress_reminder","minutes":null} only for no reminders without execution authorization.
{"intent":"set_unoccupied_minutes","minutes":20} for an empty-room duration.
{"intent":"only_peak_price","minutes":null} for peak electricity price only.
{"intent":"none","minutes":null} otherwise.
Examples:
以后别问我，直接关掉 -> {"intent":"auto_execute","minutes":null}
下次别提醒我 -> {"intent":"suppress_reminder","minutes":null}
等房间空了四十分钟再弄 -> {"intent":"set_unoccupied_minutes","minutes":40}
Only use integer minutes from 1 through 180. Never propose or execute a device action."""
