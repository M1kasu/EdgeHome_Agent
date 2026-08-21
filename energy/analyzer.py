from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any


class EnergyAnalyzer:
    """Computes statistics locally; language generation never calculates raw energy."""

    def __init__(self) -> None:
        today = date.today()
        self.samples: list[dict[str, Any]] = []
        # Stable demonstration dataset: recent heat raises AC consumption.
        baselines = {"climate.living_room_ac": 2.8, "switch.living_room_socket": 0.45, "media_player.living_room_tv": 0.32, "switch.refrigerator": 0.95}
        for offset in range(14):
            day = today - timedelta(days=13 - offset)
            recent = offset >= 7
            for device, base in baselines.items():
                multiplier = 1.45 if recent and device == "climate.living_room_ac" else (1.10 if recent and device == "media_player.living_room_tv" else 1.0)
                self.samples.append({"date": day, "device": device, "kwh": round(base * multiplier, 2)})

    def analyze(self, time_range: str = "last_7_days", room: str | None = None, device: str | None = None) -> dict[str, Any]:
        days = self._range_days(time_range)
        end = date.today()
        start = end - timedelta(days=days - 1)
        previous_start = start - timedelta(days=days)
        current = [sample for sample in self.samples if start <= sample["date"] <= end]
        previous = [sample for sample in self.samples if previous_start <= sample["date"] < start]
        if device:
            current = [sample for sample in current if sample["device"] == device]
            previous = [sample for sample in previous if sample["device"] == device]
        totals = defaultdict(float)
        for sample in current:
            totals[sample["device"]] += sample["kwh"]
        total_kwh = round(sum(totals.values()), 2)
        prior_total = round(sum(sample["kwh"] for sample in previous), 2)
        change_percent = round(((total_kwh - prior_total) / prior_total * 100) if prior_total else 0, 1)
        top_devices = [
            {"device": item[0], "kwh": round(item[1], 2), "share_percent": round(item[1] / total_kwh * 100, 1) if total_kwh else 0}
            for item in sorted(totals.items(), key=lambda value: value[1], reverse=True)
        ]
        ac = totals.get("climate.living_room_ac", 0)
        abnormal = []
        if change_percent >= 10 and ac:
            abnormal.append({"period": "最近 7 天", "device": "climate.living_room_ac", "reason": "制冷能耗较上一周期明显上升"})
        suggestions = []
        if ac:
            suggestions.append("无人时关闭客厅空调；将制冷目标温度提高 1℃ 可降低能耗。")
        suggestions.append("离家模式仅关闭电视、照明和空调，冰箱与路由器保持运行。")
        return {
            "time_range": time_range,
            "total_kwh": total_kwh,
            "previous_total_kwh": prior_total,
            "change_percent": change_percent,
            "top_devices": top_devices,
            "abnormal_periods": abnormal,
            "suggestions": suggestions,
        }

    @staticmethod
    def _range_days(time_range: str) -> int:
        mapping = {"last_7_days": 7, "last_30_days": 30, "last_day": 1}
        return mapping.get(time_range, 7)
