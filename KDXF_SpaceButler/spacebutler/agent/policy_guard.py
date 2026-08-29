"""Policy Guard: deterministic legality check for every planned action.

LLM 的任何输出都必须经过这里：设备必须存在于 Capability Registry、
动作必须在设备能力白名单内、参数必须在固件同样的边界内、不允许
重复或互相冲突的动作。非法动作直接拒绝，不会到达 Home Assistant。

classify_issue 把拒绝原因归一化为机器可统计的错误码（供 planner
fallback 长尾诊断）；这些 code 描述的是 guard 自身的检查维度，
不是业务场景。
"""

from __future__ import annotations

from spacebutler.agent.capabilities import build_capability_registry
from spacebutler.agent.models import DeviceCapability

_FAILURE_CODES = {
    "不可用设备": "unknown_device",
    "只读传感器": "readonly_device",
    "不支持动作": "unsupported_action",
    "参数必须是数字": "invalid_parameter_type",
    "超出": "out_of_range",
    "不受支持": "unsupported_action",
    "重复": "duplicate_action",
    "不是对象": "schema_failure",
    "无法解析": "schema_failure",
}


def classify_issue(message: str) -> str:
    for keyword, code in _FAILURE_CODES.items():
        if keyword in message:
            return code
    return "other"


class PolicyGuard:
    def __init__(self, registry: dict[str, DeviceCapability]) -> None:
        self._registry = registry

    def issues(self, raw_actions: list[dict[str, object]]) -> list[str]:
        problems: list[str] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(raw_actions, start=1):
            if not isinstance(item, dict):
                problems.append(f"动作 {index} 不是对象")
                continue
            device_id = str(item.get("device_id", ""))
            capability = str(item.get("capability", ""))
            device = self._registry.get(device_id)
            if device is None:
                problems.append(f"动作 {index} 引用了不可用设备 {device_id or '?'}")
                continue
            if device.readonly:
                problems.append(f"动作 {index} 目标 {device.name} 是只读传感器")
                continue
            if capability not in device.actions:
                problems.append(f"{device.name} 不支持动作 {capability}")
                continue
            issue = self._value_issue(index, capability, item.get("value"), item.get("mode"), device)
            if issue:
                problems.append(issue)
                continue
            key = (device_id, f"{capability}:{item.get('value')}:{item.get('mode')}")
            if key in seen:
                problems.append(f"动作 {index} 与之前的动作重复")
            seen.add(key)
        return problems

    def _value_issue(
        self,
        index: int,
        capability: str,
        value: object,
        mode: object,
        device: DeviceCapability,
    ) -> str | None:
        if capability in {"set_brightness", "set_position", "set_temperature"}:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"动作 {index} 的 {capability} 参数必须是数字"
            bounds = {
                "set_brightness": device.brightness_range,
                "set_position": device.position_range,
                "set_temperature": device.temperature_range,
            }.get(capability)
            if bounds is not None and not bounds[0] <= float(value) <= bounds[1]:
                return f"动作 {index} 的 {capability} 参数 {value} 超出 {bounds[0]}~{bounds[1]}"
        if capability == "set_hvac_mode":
            normalized = str(value or "").strip().lower()
            if normalized not in device.modes:
                return f"动作 {index} 的空调模式 {normalized or '?'} 不受支持"
        if capability == "set_temperature" and mode is not None:
            normalized_mode = str(mode).strip().lower()
            if normalized_mode not in {"cool", "heat"}:
                return f"动作 {index} 的温度模式 {normalized_mode or '?'} 不受支持"
        return None


__all__ = ["PolicyGuard", "build_capability_registry"]
