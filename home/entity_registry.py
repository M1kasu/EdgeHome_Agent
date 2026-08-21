from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str
    room: str
    kind: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    protected: bool = False


ROOM_ALIASES = {
    "客厅": "living_room",
    "卧室": "bedroom",
    "玄关": "entryway",
    "living room": "living_room",
    "bedroom": "bedroom",
}


class EntityRegistry:
    """Only registered entities may be selected by a plan or an input sentence."""

    def __init__(self) -> None:
        self.entities = [
            Entity("light.living_room_main", "客厅主灯", "living_room", "light", ("客厅灯", "主灯", "灯光")),
            Entity("light.living_room_ambient", "客厅氛围灯", "living_room", "light", ("氛围灯",)),
            Entity("climate.living_room_ac", "客厅空调", "living_room", "air_conditioner", ("客厅空调", "空调")),
            Entity("cover.living_room_curtain", "客厅窗帘", "living_room", "curtain", ("窗帘",)),
            Entity("media_player.living_room_tv", "客厅电视", "living_room", "television", ("电视", "TV")),
            Entity("switch.living_room_socket", "客厅插座", "living_room", "switch", ("插座",)),
            Entity("light.bedroom_main", "卧室主灯", "bedroom", "light", ("卧室灯",)),
            Entity("climate.bedroom_ac", "卧室空调", "bedroom", "air_conditioner", ("卧室空调",)),
            Entity("media_player.bedroom_tv", "卧室电视", "bedroom", "television", ("卧室电视",)),
            Entity("switch.refrigerator", "冰箱", "kitchen", "switch", ("冰箱",), True),
            Entity("switch.router", "路由器", "entryway", "switch", ("路由器",), True),
            Entity("vacuum.robot", "扫地机器人", "living_room", "vacuum", ("扫地机器人",), True),
        ]
        self._by_id = {entity.entity_id: entity for entity in self.entities}

    def rooms_in(self, text: str) -> str | None:
        normalized = text.lower()
        for alias, room in ROOM_ALIASES.items():
            if alias in normalized:
                return room
        return None

    def resolve(self, *, room: str | None = None, device: str | None = None) -> Entity | None:
        candidates = self.entities
        if room:
            candidates = [entity for entity in candidates if entity.room == room]
        if device:
            aliases = {
                "light": ("灯", "灯光", "主灯"),
                "air_conditioner": ("空调", "空调机"),
                "curtain": ("窗帘",),
                "television": ("电视", "tv"),
                "switch": ("插座", "开关"),
                "refrigerator": ("冰箱",),
                "router": ("路由器",),
                "vacuum": ("扫地机器人",),
            }
            terms = aliases.get(device, (device,))
            candidates = [
                entity for entity in candidates
                if entity.kind == device or any(term.lower() in (entity.name + " " + " ".join(entity.aliases)).lower() for term in terms)
            ]
        return candidates[0] if len(candidates) == 1 else (candidates[0] if candidates else None)

    def get(self, entity_id: str) -> Entity | None:
        return self._by_id.get(entity_id)

    def in_room(self, room: str) -> list[Entity]:
        return [entity for entity in self.entities if entity.room == room]
