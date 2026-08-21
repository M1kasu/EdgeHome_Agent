from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from home.entity_registry import EntityRegistry
from safety.parameter_rules import ParameterRules


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    errors: list[str]
    requires_confirmation: bool = False


class ActionGuard:
    def __init__(self, registry: EntityRegistry, protected_entities: tuple[str, ...]) -> None:
        self.registry = registry
        self.protected_entities = set(protected_entities)
        self.rules = ParameterRules()

    def check(self, tool: str, arguments: dict[str, Any], *, confirm: bool = False) -> GuardResult:
        errors = self.rules.validate(tool, arguments)
        entity_id = arguments.get("entity_id")
        if entity_id and self.registry.get(entity_id) is None:
            errors.append("设备实体未在注册表中")
        requires_confirmation = bool(
            tool == "turn_off_device" and entity_id in self.protected_entities and not confirm
        )
        if requires_confirmation:
            errors.append("关闭受保护设备需要用户二次确认")
        return GuardResult(not errors, errors, requires_confirmation)
