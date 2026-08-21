from __future__ import annotations

import re
from typing import Any

from home.entity_registry import EntityRegistry


class SlotExtractor:
    def __init__(self, registry: EntityRegistry) -> None:
        self.registry = registry

    def extract(self, text: str, memory: dict[str, Any] | None = None) -> dict[str, Any]:
        memory = memory or {}
        slots: dict[str, Any] = {}
        room = self.registry.rooms_in(text)
        if room:
            slots["room"] = room

        normalized = text.lower()
        device_terms = {
            "air_conditioner": ("空调",),
            "curtain": ("窗帘",),
            "television": ("电视", "tv"),
            "light": ("灯", "灯光", "照明"),
            "refrigerator": ("冰箱",),
            "router": ("路由器",),
            "vacuum": ("扫地机器人",),
            "switch": ("插座",),
        }
        for device, terms in device_terms.items():
            if any(term in normalized for term in terms):
                slots["device"] = device
                break
        if "调到一半" in text or "一半亮" in text:
            slots["brightness"] = 50
        else:
            match = re.search(r"(?:亮度|灯(?:光)?(?:调到|设为)?|调到)\s*(\d{1,3})\s*(?:%|％)?", text)
            if match and ("灯" in text or "亮" in text):
                slots["brightness"] = int(match.group(1))
            elif "调暗" in text:
                slots["brightness"] = 20
        temperature = re.search(r"(?:到|为|调到|设为)?\s*(1[0-9]|2[0-9]|30)\s*度", text)
        if temperature and (
            slots.get("device") == "air_conditioner"
            or "空调" in text
            or (memory.get("active_scene") == "sleep" and slots.get("room") == "bedroom")
        ):
            slots["temperature"] = int(temperature.group(1))
            slots.setdefault("device", "air_conditioner")
        position = re.search(r"(?:窗帘)?(?:开到|关到|打开到|关闭到)?\s*(\d{1,3})\s*(?:%|％)", text)
        if position and slots.get("device") == "curtain":
            slots["position"] = int(position.group(1))
        elif "打开窗帘" in text or "拉开窗帘" in text:
            slots["position"] = 100
            slots.setdefault("device", "curtain")
        elif "关闭窗帘" in text or "拉上窗帘" in text:
            slots["position"] = 0
            slots.setdefault("device", "curtain")
        delay = self._delay_minutes(text)
        if delay is not None:
            slots["delay_minutes"] = delay
        excluded = []
        for phrase, identifier in (("冰箱", "switch.refrigerator"), ("路由器", "switch.router"), ("扫地机器人", "vacuum.robot")):
            if phrase in text and any(word in text for word in ("不要", "别", "不动", "保留")):
                excluded.append(identifier)
        if excluded:
            slots["exclude_entities"] = excluded
        if any(word in text for word in ("看电影", "观影")):
            slots["scene"] = "movie"
        elif any(word in text for word in ("出门", "离家")):
            slots["scene"] = "away"
        elif any(word in text for word in ("睡觉", "睡眠")):
            slots["scene"] = "sleep"

        if any(phrase in text for phrase in ("也一样", "和刚才一样", "一样")) and memory.get("last_slots"):
            inherited = memory["last_slots"].copy()
            inherited.update(slots)  # explicitly stated room/device always wins
            slots = inherited
            slots["referenced_previous"] = True
        return slots

    @staticmethod
    def _delay_minutes(text: str) -> int | None:
        if "半小时" in text:
            return 30
        hour = re.search(r"(\d+(?:\.5)?)\s*(?:个)?小时后", text)
        if hour:
            return int(float(hour.group(1)) * 60)
        minute = re.search(r"(\d+)\s*分钟后", text)
        if minute:
            return int(minute.group(1))
        return None
