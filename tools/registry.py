from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: str
    timeout_seconds: int = 5
    retryable: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        specs = [
            ToolSpec("get_home_state", "获取家庭结构化状态", "low"),
            ToolSpec("get_room_state", "获取房间状态", "low"),
            ToolSpec("get_device_state", "获取设备状态", "low"),
            ToolSpec("turn_on_device", "开启注册设备", "medium"),
            ToolSpec("turn_off_device", "关闭注册设备", "medium"),
            ToolSpec("set_light_brightness", "设置灯光亮度", "low"),
            ToolSpec("set_temperature", "设置空调目标温度", "medium"),
            ToolSpec("control_curtain", "设置窗帘位置", "low"),
            ToolSpec("create_delayed_task", "创建本地延时任务", "low"),
            ToolSpec("restore_previous_state", "恢复已保存状态", "medium"),
        ]
        self._specs = {spec.name: spec for spec in specs}

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> set[str]:
        return set(self._specs)
