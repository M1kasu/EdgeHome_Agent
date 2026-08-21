from __future__ import annotations

from typing import Any


class ParameterRules:
    def validate(self, tool: str, arguments: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if tool in {"turn_on_device", "turn_off_device", "set_light_brightness", "set_temperature", "control_curtain", "get_device_state"}:
            if not isinstance(arguments.get("entity_id"), str):
                errors.append("缺少或非法的 entity_id")
        if tool == "set_light_brightness":
            value = arguments.get("brightness")
            if not isinstance(value, int) or not 0 <= value <= 100:
                errors.append("灯光亮度必须为 0 到 100 的整数")
        if tool == "set_temperature":
            value = arguments.get("temperature")
            if not isinstance(value, (int, float)) or not 16 <= value <= 30:
                errors.append("空调温度必须为 16 到 30 摄氏度")
        if tool == "control_curtain":
            value = arguments.get("position")
            if not isinstance(value, int) or not 0 <= value <= 100:
                errors.append("窗帘位置必须为 0 到 100 的整数")
        if tool == "create_delayed_task":
            value = arguments.get("delay_minutes")
            if not isinstance(value, int) or value < 0:
                errors.append("延时分钟数必须是非负整数")
        return errors
