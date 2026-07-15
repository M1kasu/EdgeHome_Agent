"""Deterministic goal and constraint extraction before model planning."""

from __future__ import annotations

import re

from ..models import (
    ConstraintType,
    EntitySnapshot,
    GoalConstraint,
    GoalSpec,
    GoalType,
    PlanConstraint,
    RiskLevel,
)


_ROOM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("living_room", ("客厅", "大厅", "living")),
    ("bedroom", ("卧室", "睡房", "bedroom")),
    ("home", ("全屋", "家里", "所有房间", "全部房间")),
)
_DEVICE_ALIASES: dict[str, tuple[str, ...]] = {
    "light": ("灯", "主灯", "照明", "light"),
    "cover": ("窗帘", "帘子", "curtain"),
    "climate": ("空调", "制冷", "制热", "温度", "hvac"),
    "switch": ("电视", "插座", "开关", "tv", "switch"),
}
_PROTECTED_ALIASES = ("冰箱", "路由器", "refrigerator", "fridge", "router", "nas", "服务器")
_NEGATION = re.compile(r"(不要|别|禁止|不得|无需|不需要|不用)")
_CLAUSE_END = re.compile(r"[。；，,！？!?]")
_CONTROL_VERBS = re.compile(r"(打开|开启|启动|关闭|关掉|关上|调到|调成|设置|设为|拉到|全部断电|关闭所有)")


class GoalSpecBuilder:
    """Build a conservative, auditable GoalSpec from text and live snapshots."""

    def build(self, goal: str, scene: str | None, entities: list[EntitySnapshot]) -> GoalSpec:
        normalized = "".join(goal.lower().split())
        target_rooms = self._target_rooms(normalized, scene)
        target_devices = self._target_devices(normalized, entities)
        protected = self._protected_entities(normalized, entities)
        constraints = self._constraints(goal, normalized)
        forbidden_actions = [
            constraint.statement
            for constraint in constraints
            if constraint.constraint_type == ConstraintType.PROHIBITION
        ]
        requires_confirmation = bool(protected and _CONTROL_VERBS.search(normalized))
        return GoalSpec(
            raw_goal=goal,
            goal_type=self._goal_type(normalized, scene),
            scene=scene,
            target_rooms=target_rooms,
            target_devices=target_devices,
            desired_outcomes=self._desired_outcomes(goal, normalized, scene),
            constraints=constraints,
            preferences=[],
            forbidden_actions=forbidden_actions,
            protected_entities=protected,
            risk_tolerance=self._risk_tolerance(normalized, protected),
            requires_confirmation=requires_confirmation,
        )

    def as_plan_constraints(self, spec: GoalSpec) -> list[PlanConstraint]:
        """Mirror typed goal constraints into the legacy plan constraint field."""
        return [PlanConstraint.model_validate(constraint.model_dump(mode="json")) for constraint in spec.constraints[:8]]

    @staticmethod
    def _goal_type(normalized: str, scene: str | None) -> GoalType:
        if scene is not None:
            return GoalType.SCENE
        if any(token in normalized for token in ("节能", "省电", "能耗", "耗电", "用电")):
            return GoalType.ENERGY
        if any(token in normalized for token in ("离家", "出门", "我走了", "安全", "安防", "保护")):
            return GoalType.SAFETY
        if any(token in normalized for token in ("查询", "查看", "多少", "状态")) and not _CONTROL_VERBS.search(normalized):
            return GoalType.QUERY
        if any(token in normalized for token in ("定时", "分钟后", "小时后", "稍后")):
            return GoalType.SCHEDULE
        if _CONTROL_VERBS.search(normalized):
            return GoalType.DEVICE_CONTROL
        return GoalType.UNKNOWN

    @staticmethod
    def _target_rooms(normalized: str, scene: str | None) -> list[str]:
        rooms = [room for room, aliases in _ROOM_ALIASES if any(alias in normalized for alias in aliases)]
        if "bedroom" in rooms and GoalSpecBuilder._forbids_living_room(normalized):
            rooms = [room for room in rooms if room != "living_room"]
        if not rooms and scene in {"movie", "leaving"}:
            rooms.append("living_room" if scene == "movie" else "home")
        return rooms

    @staticmethod
    def _target_devices(normalized: str, entities: list[EntitySnapshot]) -> list[str]:
        targets: list[str] = []
        for entity in entities:
            object_id = entity.entity_id.partition(".")[2].replace("_", "")
            names = [object_id]
            if entity.friendly_name:
                names.append("".join(entity.friendly_name.lower().split()))
            if any(name and name in normalized for name in names):
                targets.append(entity.entity_id)
                continue
            aliases = _DEVICE_ALIASES.get(entity.domain, ())
            if any(alias in normalized for alias in aliases):
                targets.append(entity.entity_id)
        return sorted(dict.fromkeys(targets))

    @staticmethod
    def _protected_entities(normalized: str, entities: list[EntitySnapshot]) -> list[str]:
        protected: list[str] = []
        goal_mentions_protected = any(token in normalized for token in _PROTECTED_ALIASES)
        if not goal_mentions_protected:
            return protected
        for entity in entities:
            friendly_name = "".join((entity.friendly_name or "").lower().split())
            entity_text = f"{entity.entity_id.lower()}:{friendly_name}"
            if any(token in entity_text for token in _PROTECTED_ALIASES):
                protected.append(entity.entity_id)
        return sorted(dict.fromkeys(protected))

    @staticmethod
    def _constraints(goal: str, normalized: str) -> list[GoalConstraint]:
        constraints: list[GoalConstraint] = []
        if GoalSpecBuilder._forbids_living_room(normalized):
            constraints.append(
                GoalConstraint(
                    statement="禁止控制客厅设备",
                    constraint_type=ConstraintType.PROHIBITION,
                    target="room:living_room",
                    operator="not_control",
                    source="user",
                    hard=True,
                    priority=100,
                    conflict_policy="reject",
                )
            )
        for match in _NEGATION.finditer(normalized):
            fragment = normalized[match.start() : match.start() + 30]
            clause_end = _CLAUSE_END.search(fragment)
            if clause_end is not None:
                fragment = fragment[: clause_end.start()]
            target = GoalSpecBuilder._constraint_target(fragment)
            if target is not None:
                constraints.append(
                    GoalConstraint(
                        statement=f"禁止控制{target}",
                        constraint_type=ConstraintType.PROHIBITION,
                        target=target,
                        operator="not_control",
                        source="user",
                        hard=True,
                        priority=100,
                        conflict_policy="reject",
                    )
                )
        min_brightness = re.search(r"(主灯|灯光|照明|灯)[^。；，,]{0,16}(不要低于|不能低于|不得低于|别低于|不低于|至少|最低)\s*(\d{1,3})\s*(%|％|成|百分之)?", goal)
        if min_brightness is not None:
            raw_value = int(min_brightness.group(3))
            value = raw_value * 10 if min_brightness.group(4) == "成" and raw_value <= 10 else raw_value
            if 0 <= value <= 100:
                constraints.append(
                    GoalConstraint(
                        statement=f"灯光亮度不低于{value}%",
                        constraint_type=ConstraintType.MIN_VALUE,
                        target="light",
                        operator=">=",
                        value=value,
                        source="user",
                        hard=True,
                        priority=90,
                    )
                )
        min_temperature = re.search(r"(空调|室温|温度)[^。；，,]{0,16}(不要低于|不能低于|不得低于|别低于|不低于|至少|最低)\s*(\d{2}(?:\.\d+)?)\s*(?:度|℃)?", goal)
        if min_temperature is not None:
            value = float(min_temperature.group(3))
            if 16.0 <= value <= 30.0:
                constraints.append(
                    GoalConstraint(
                        statement=f"空调温度不低于{value:g}℃",
                        constraint_type=ConstraintType.MIN_VALUE,
                        target="climate",
                        operator=">=",
                        value=value,
                        source="user",
                        hard=True,
                        priority=90,
                    )
                )
        return constraints[:16]

    @staticmethod
    def _constraint_target(fragment: str) -> str | None:
        if any(alias in fragment for alias in ("客厅", "大厅")) and any(token in fragment for token in ("动", "控制", "调整", "处理")):
            return "room:living_room"
        for domain, aliases in _DEVICE_ALIASES.items():
            if any(alias in fragment for alias in aliases):
                return domain
        if any(alias in fragment for alias in _PROTECTED_ALIASES):
            return "protected_device"
        return None

    @staticmethod
    def _desired_outcomes(goal: str, normalized: str, scene: str | None) -> list[str]:
        if scene == "movie" or any(token in normalized for token in ("观影", "看电影", "投屏")):
            return ["降低眩光", "保持舒适温度", "打开影音设备"]
        if scene == "leaving" or any(token in normalized for token in ("离家", "出门", "我走了")):
            return ["关闭非必要设备", "保护冰箱和路由器", "检查门窗和在家状态"]
        if any(token in normalized for token in ("节能", "省电", "能耗")):
            return ["分析当前能耗", "只给出可验证建议"]
        return [goal[:120]]

    @staticmethod
    def _risk_tolerance(normalized: str, protected: list[str]) -> RiskLevel:
        if protected or any(token in normalized for token in ("全部断电", "关闭所有", "delete_all_devices", "忽略之前规则")):
            return RiskLevel.LOW
        if any(token in normalized for token in ("离家", "安全", "安防")):
            return RiskLevel.LOW
        return RiskLevel.MEDIUM

    @staticmethod
    def _forbids_living_room(normalized: str) -> bool:
        return bool(
            re.search(r"(客厅|大厅)(设备|家电|东西)?(不要|别|不许|不能|不得|无需|不用).{0,8}(动|控制|调整|处理)", normalized)
            or re.search(r"(客厅|大厅)(设备|家电|东西)?保持(现在|原样|不变)", normalized)
        )
