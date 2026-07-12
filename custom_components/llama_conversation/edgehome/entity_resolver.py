"""Resolve only Home Assistant entities explicitly exposed to conversation."""

from __future__ import annotations

from collections.abc import Callable, Container
from dataclasses import dataclass

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .entity_registry import EdgeHomeEntityRegistry
from .errors import EntityResolutionError
from .models import EntitySnapshot


ExposureChecker = Callable[[HomeAssistant, str, str], bool]


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    """A verified entity and the snapshot captured during resolution."""

    snapshot: EntitySnapshot


class ExposedEntityResolver:
    """Prevent plans from addressing entities outside the HA exposure boundary."""

    def __init__(
        self,
        hass: HomeAssistant,
        exposure_checker: ExposureChecker | None = None,
        registry: EdgeHomeEntityRegistry | None = None,
    ) -> None:
        self.hass = hass
        self._exposure_checker = exposure_checker or async_should_expose
        self._registry = registry

    def resolve(
        self,
        entity_id: str,
        allowed_domains: Container[str] | None = None,
    ) -> ResolvedEntity:
        """Return an entity snapshot or a stable reason why it is disallowed."""
        registered = self._registry.get(entity_id) if self._registry is not None else None
        if self._registry is not None and registered is None:
            raise EntityResolutionError("entity_not_registered", f"实体未在 EdgeHome 注册表中登记: {entity_id}")

        state = self.hass.states.get(entity_id)
        if state is None:
            raise EntityResolutionError("entity_not_found", f"实体不存在: {entity_id}")

        domain = state.domain
        if allowed_domains and domain not in allowed_domains:
            raise EntityResolutionError("entity_domain_not_allowed", f"实体域不允许: {domain}")
        # Home Assistant's generic Assist filter intentionally hides some security
        # helpers.  For EdgeHome, the explicit virtual-only registry is the stricter
        # per-agent exposure boundary; it permits the dedicated demo security mode
        # while still rejecting every real or unregistered entity.
        if registered is None and not self._exposure_checker(self.hass, conversation.DOMAIN, entity_id):
            raise EntityResolutionError("entity_not_exposed", f"实体未暴露给对话服务: {entity_id}")

        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        entity_entry = entity_registry.async_get(entity_id)
        device_entry = device_registry.async_get(entity_entry.device_id) if entity_entry and entity_entry.device_id else None

        area_id = None
        if device_entry and device_entry.area_id:
            area_id = device_entry.area_id
        elif entity_entry and entity_entry.area_id:
            area_id = entity_entry.area_id
        area = area_registry.async_get_area(area_id) if area_id else None

        return ResolvedEntity(
            snapshot=EntitySnapshot(
                entity_id=entity_id,
                domain=domain,
                state=state.state,
                attributes=dict(state.attributes),
                friendly_name=state.attributes.get(ATTR_FRIENDLY_NAME),
                area_id=area_id,
                area_name=area.name if area else None,
            )
        )

    def snapshot(self, entity_id: str, allowed_domains: Container[str] | None = None) -> EntitySnapshot:
        """Return only the snapshot after applying the same access boundary."""
        return self.resolve(entity_id, allowed_domains).snapshot

    def registered_entities(self, room: str | None = None) -> tuple[str, ...]:
        """Return registered entity ids for structured home or room-state tools."""
        if self._registry is None:
            return ()
        records = self._registry.for_room(room) if room is not None else self._registry.all()
        return tuple(record.entity_id for record in records)

    def is_protected(self, entity_id: str) -> bool:
        """Return the explicit protected flag when a production registry is in use."""
        return bool(self._registry and self._registry.is_protected(entity_id))

    def protocol_feedback(self, entity_id: str) -> tuple[str, str | None] | None:
        """Read a registered device's non-controlling MQTT feedback sensor, if present."""
        if self._registry is None:
            return None
        feedback_entity_id = self._registry.feedback_entity_for(entity_id)
        if feedback_entity_id is None:
            return None
        feedback_state = self.hass.states.get(feedback_entity_id)
        if feedback_state is None:
            return None
        reason = feedback_state.attributes.get("reason")
        return feedback_state.state, reason if isinstance(reason, str) and reason else None


class EntityNameResolver(ExposedEntityResolver):
    """Resolve a Chinese display name or configured alias to one exposed entity."""

    def resolve_name(
        self,
        text: str,
        allowed_domains: Container[str] | None = None,
    ) -> ResolvedEntity:
        """Choose a unique longest entity-name match from the exposed state machine."""
        if self._registry is not None:
            entity_id = self._registry.resolve_query(text, allowed_domains)
            return self.resolve(entity_id, allowed_domains)

        query = self._normalize(text)
        candidates: list[tuple[int, str]] = []
        entity_registry = er.async_get(self.hass)

        for state in self.hass.states.async_all():
            if allowed_domains and state.domain not in allowed_domains:
                continue
            if not self._exposure_checker(self.hass, conversation.DOMAIN, state.entity_id):
                continue
            for name in self._names_for_entity(state.entity_id, entity_registry):
                normalized_name = self._normalize(name)
                if len(normalized_name) >= 2 and normalized_name in query:
                    candidates.append((len(normalized_name), state.entity_id))

        if not candidates:
            raise EntityResolutionError("entity_name_not_found", "未找到匹配且已暴露的设备名称")

        longest_match = max(length for length, _ in candidates)
        entity_ids = {entity_id for length, entity_id in candidates if length == longest_match}
        if len(entity_ids) != 1:
            raise EntityResolutionError("entity_name_ambiguous", "设备名称匹配到多个已暴露实体")
        return self.resolve(entity_ids.pop(), allowed_domains)

    def _names_for_entity(self, entity_id: str, entity_registry: er.EntityRegistry) -> set[str]:
        state = self.hass.states.get(entity_id)
        if state is None:
            return set()

        names = {entity_id.split(".", maxsplit=1)[1].replace("_", " ")}
        friendly_name = state.attributes.get(ATTR_FRIENDLY_NAME)
        if isinstance(friendly_name, str):
            names.add(friendly_name)

        entry = entity_registry.async_get(entity_id)
        if entry:
            names.update(er.async_get_entity_aliases(self.hass, entry))
        snapshot = self.snapshot(entity_id)
        if snapshot.area_name and snapshot.friendly_name:
            names.add(f"{snapshot.area_name}{snapshot.friendly_name}")
        return names

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(character.lower() for character in value if character.isalnum())
