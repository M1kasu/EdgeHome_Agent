"""The explicit, virtual-device-only registry used by EdgeHome planning and tools."""

from __future__ import annotations

from collections.abc import Container, Iterable
from dataclasses import dataclass

from .errors import EntityResolutionError


_ROOM_ALIASES: dict[str, tuple[str, ...]] = {
    "living_room": ("客厅", "大厅"),
    "bedroom": ("卧室",),
    "home": ("全屋", "家里"),
}


@dataclass(frozen=True, slots=True)
class RegisteredEntity:
    """One device that EdgeHome may name, read, or control."""

    name: str
    entity_id: str
    room: str
    device_type: str
    aliases: tuple[str, ...]
    protected: bool = False
    feedback_entity_id: str | None = None
    protocol: str = "template"
    scene_names: frozenset[str] = frozenset()

    @property
    def domain(self) -> str:
        return self.entity_id.partition(".")[0]

    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class EdgeHomeEntityRegistry:
    """Resolve only explicit aliases, never an inferred Home Assistant entity id."""

    def __init__(self, entities: Iterable[RegisteredEntity]) -> None:
        records = tuple(entities)
        by_id = {record.entity_id: record for record in records}
        if len(by_id) != len(records):
            raise ValueError("实体注册表包含重复 entity_id")
        self._by_id = by_id

    def get(self, entity_id: str) -> RegisteredEntity | None:
        return self._by_id.get(entity_id)

    def all(self) -> tuple[RegisteredEntity, ...]:
        return tuple(self._by_id.values())

    def for_room(self, room: str) -> tuple[RegisteredEntity, ...]:
        return tuple(record for record in self._by_id.values() if record.room == room)

    def is_protected(self, entity_id: str) -> bool:
        record = self.get(entity_id)
        return bool(record and record.protected)

    def feedback_entity_for(self, entity_id: str) -> str | None:
        """Return the optional read-only protocol-feedback entity for a device."""
        record = self.get(entity_id)
        return record.feedback_entity_id if record else None

    def for_scene(self, scene_name: str, *, protocol: str | None = None) -> tuple[RegisteredEntity, ...]:
        """Return the explicitly assigned devices for one named scene."""
        return tuple(
            record
            for record in self._by_id.values()
            if scene_name in record.scene_names and (protocol is None or record.protocol == protocol)
        )

    def resolve_query(self, query: str, allowed_domains: Container[str] | None = None) -> str:
        """Choose a unique longest alias match or return a stable clarification error."""
        normalized_query = _normalize(query)
        matches: list[tuple[int, str]] = []
        for record in self._by_id.values():
            if allowed_domains and record.domain not in allowed_domains:
                continue
            for name in record.all_names():
                normalized_name = _normalize(name)
                if len(normalized_name) >= 2 and normalized_name in normalized_query:
                    room_bonus = max(
                        (len(_normalize(alias)) for alias in _ROOM_ALIASES.get(record.room, ()) if _normalize(alias) in normalized_query),
                        default=0,
                    )
                    matches.append((len(normalized_name) + room_bonus, record.entity_id))

        if not matches:
            raise EntityResolutionError("entity_name_not_found", "未找到已注册的 EdgeHome 测试设备")
        longest = max(length for length, _ in matches)
        candidates = {entity_id for length, entity_id in matches if length == longest}
        if len(candidates) != 1:
            raise EntityResolutionError("entity_name_ambiguous", "设备名称匹配到多个已注册设备")
        return candidates.pop()


def build_demo_entity_registry() -> EdgeHomeEntityRegistry:
    """Return the only entities enabled during the virtual-device validation phase."""
    return EdgeHomeEntityRegistry(
        (
            RegisteredEntity(
                "客厅主灯",
                "light.edgehome_living_room_main",
                "living_room",
                "light",
                ("客厅灯", "大厅主灯", "大厅灯", "客厅主照明", "客厅大灯", "大灯", "主灯"),
                feedback_entity_id="sensor.edgehome_living_room_main_feedback",
                protocol="mqtt",
                scene_names=frozenset({"movie"}),
            ),
            RegisteredEntity("客厅氛围灯", "light.edgehome_living_room_ambient", "living_room", "light", ("氛围灯", "客厅灯带")),
            RegisteredEntity(
                "客厅电视",
                "switch.edgehome_living_room_tv",
                "living_room",
                "switch",
                ("电视", "客厅 TV"),
                feedback_entity_id="sensor.edgehome_living_room_tv_feedback",
                protocol="mqtt",
                scene_names=frozenset({"movie"}),
            ),
            RegisteredEntity(
                "客厅空调",
                "climate.edgehome_living_room_ac",
                "living_room",
                "climate",
                ("客厅 AC", "客厅冷气"),
                feedback_entity_id="sensor.edgehome_living_room_ac_feedback",
                protocol="mqtt",
                scene_names=frozenset({"movie"}),
            ),
            RegisteredEntity(
                "客厅窗帘",
                "cover.edgehome_living_room_curtain",
                "living_room",
                "cover",
                ("窗帘", "客厅帘子"),
                feedback_entity_id="sensor.edgehome_living_room_curtain_feedback",
                protocol="mqtt",
                scene_names=frozenset({"movie"}),
            ),
            RegisteredEntity("客厅温度", "sensor.edgehome_living_room_temperature", "living_room", "sensor", ("客厅室温",)),
            RegisteredEntity("客厅光照", "sensor.edgehome_living_room_illuminance", "living_room", "sensor", ("客厅亮度", "客厅照度")),
            RegisteredEntity("客厅有人", "binary_sensor.edgehome_living_room_presence", "living_room", "binary_sensor", ("客厅在场", "客厅有人吗")),
            RegisteredEntity("卧室主灯", "light.edgehome_bedroom_main", "bedroom", "light", ("卧室灯", "主灯")),
            RegisteredEntity("卧室空调", "climate.edgehome_bedroom_ac", "bedroom", "climate", ("卧室 AC", "卧室冷气")),
            RegisteredEntity("卧室温度", "sensor.edgehome_bedroom_temperature", "bedroom", "sensor", ("卧室室温",)),
            RegisteredEntity("卧室有人", "binary_sensor.edgehome_bedroom_presence", "bedroom", "binary_sensor", ("卧室在场", "卧室有人吗")),
            RegisteredEntity("路由器", "switch.edgehome_router", "home", "switch", ("网络", "网络设备"), protected=True),
            RegisteredEntity("冰箱", "switch.edgehome_refrigerator", "home", "switch", ("电冰箱",), protected=True),
            RegisteredEntity("测试插座", "switch.edgehome_test_socket", "home", "switch", ("插座", "测试电源")),
            RegisteredEntity("窗户", "binary_sensor.edgehome_window", "home", "binary_sensor", ("窗",)),
            RegisteredEntity("安防模式", "input_boolean.edgehome_security_mode", "home", "input_boolean", ("安防", "安全模式")),
        )
    )


def _normalize(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())
