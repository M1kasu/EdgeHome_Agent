"""Counterfactual Predictor: what will the room look like AFTER a plan runs?

简化但与 ESPHome 固件参数一致的环境模型：
- 空调制冷：室温以 0.2°C/秒下降（ac.yaml interval lambda），趋向设定温度以下
- 空调关闭/空闲：室温以 0.05°C/秒回升，上限 30.2°C
未来可替换为学习型动力学模型，接口不变。
"""

from __future__ import annotations

from spacebutler.agent.models import PlannedAction, PredictedState, RoomState

COOLING_RATE_PER_MIN = 12.0
RECOVERY_RATE_PER_MIN = 3.0
RECOVERY_CEILING = 30.2


def _field(action: object, name: str, default: object = None) -> object:
    """Actions arrive as either PlannedAction objects or raw dicts."""
    if isinstance(action, dict):
        return action.get(name, default)
    return getattr(action, name, default)


class CounterfactualPredictor:
    def predict(
        self,
        actions: list[object],
        state: RoomState,
        horizon_minutes: float = 5.0,
    ) -> PredictedState:
        brightness = state.brightness_pct
        curtain = state.curtain_position
        ac_mode = state.ac_mode
        ac_target = state.ac_target
        for action in actions:
            capability = _field(action, "capability")
            value = _field(action, "value")
            if capability == "set_brightness":
                brightness = float(value) if isinstance(value, (int, float)) else brightness
            elif capability == "set_position":
                curtain = float(value) if isinstance(value, (int, float)) else curtain
            elif capability == "set_hvac_mode":
                ac_mode = str(value or "off")
                if ac_mode != "off":
                    ac_target = ac_target or 26.0
            elif capability == "set_temperature":
                ac_mode = _field(action, "mode") or (ac_mode if ac_mode != "off" else "cool")
                if isinstance(value, (int, float)):
                    ac_target = float(value)
            elif capability == "turn_off" and str(_field(action, "device_id", "")).endswith("_ac"):
                ac_mode = "off"

        current = state.temperature or 26.0
        notes: list[str] = []
        if ac_mode == "cool" and ac_target is not None:
            floor = ac_target - 1.0
            predicted_temperature = max(current - COOLING_RATE_PER_MIN * horizon_minutes, floor)
            trend = "decreasing"
            notes.append(f"制冷中，预计 {horizon_minutes:g} 分钟后约 {predicted_temperature:.1f}°C")
        elif ac_mode == "heat" and ac_target is not None:
            predicted_temperature = min(current + COOLING_RATE_PER_MIN * horizon_minutes, ac_target + 1.0)
            trend = "increasing"
        else:
            predicted_temperature = min(current + RECOVERY_RATE_PER_MIN * horizon_minutes, RECOVERY_CEILING)
            trend = "increasing" if predicted_temperature > current + 0.05 else "stable"
            if predicted_temperature >= RECOVERY_CEILING - 0.1:
                notes.append("无制冷时室温将回到环境基线 30.2°C")
        return PredictedState(
            temperature=round(predicted_temperature, 2),
            temperature_trend=trend,
            brightness_pct=round(brightness, 1) if brightness is not None else None,
            curtain_position=round(curtain, 1) if curtain is not None else None,
            ac_mode=ac_mode,
            horizon_minutes=horizon_minutes,
            notes=notes,
        )
