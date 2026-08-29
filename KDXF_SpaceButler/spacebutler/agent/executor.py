"""Agent executor: send validated actions through the real HA control path.

每个命令都会记录 action_id 与期望状态到 World Model，供状态变化归因
（AGENT_ACTION / HUMAN_OVERRIDE）使用。执行链路复用工作台的
control_device（内含 Home Assistant 调用与回读等待）。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from spacebutler.agent.models import ActionRecord, CandidatePlan, now


def payload_for(capability: str, value: object, mode: str | None) -> dict[str, object]:
    """Capability vocabulary -> workbench control payload."""
    if capability == "set_brightness":
        return {"power": "on", "brightness": int(float(value))}
    if capability == "set_position":
        return {"position": int(float(value))}
    if capability == "set_temperature":
        effective_mode = mode or "cool"
        return {"mode": effective_mode, "temperature": float(value)}
    if capability == "set_hvac_mode":
        return {"mode": str(value or "off")}
    if capability == "turn_off":
        return {"mode": "off"} if mode == "climate" or mode is None and False else {"power": "off"}
    if capability == "turn_on":
        return {"power": "on"}
    raise ValueError(f"unsupported capability: {capability}")


def expected_for(capability: str, value: object, mode: str | None) -> dict[str, object]:
    if capability == "set_brightness":
        return {"power": "on", "brightness": int(float(value))}
    if capability == "set_position":
        return {"position": int(float(value))}
    if capability in {"set_temperature", "set_hvac_mode"}:
        effective = mode or str(value or "cool") if capability == "set_hvac_mode" else (mode or "cool")
        expected: dict[str, object] = {"state": effective}
        if capability == "set_temperature":
            expected["temperature"] = float(value)
        return expected
    if capability == "turn_off":
        return {"state": "off"}
    return {"state": "on"}


class AgentExecutor:
    def __init__(self, control_device: Callable[[str, dict[str, object]], dict[str, object]]) -> None:
        self._control_device = control_device

    def execute(self, plan: CandidatePlan, world: object) -> list[ActionRecord]:
        records: list[ActionRecord] = []
        for action in plan.actions:
            payload = payload_for(action.capability, action.value, action.mode)
            record = ActionRecord(
                action_id=f"act_{uuid.uuid4().hex[:10]}",
                device_id=action.device_id,
                entity_id=action.entity_id,
                capability=action.capability,
                value=action.value,
                sent_at=now(),
                expected=expected_for(action.capability, action.value, action.mode),
            )
            # 先写入动作日志再执行：并发轮询必须能把随后的状态变化归因为 AGENT_ACTION，
            # 否则自己的命令会被误判为人工纠正。
            world.record_action(record)
            try:
                self._control_device(action.device_id, payload)
                record.verified = True
            except (RuntimeError, TimeoutError, ValueError) as error:
                record.verified = False
                record.observed = {"error": str(error)}
            records.append(record)
        return records
