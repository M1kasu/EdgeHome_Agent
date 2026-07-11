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

from .errors import EntityResolutionError
from .models import EntitySnapshot


ExposureChecker = Callable[[HomeAssistant, str, str], bool]


@dataclass(frozen=True, slots=True)
class ResolvedEntity:
    """A verified entity and the snapshot captured during resolution."""

    snapshot: EntitySnapshot


class ExposedEntityResolver:
    """Prevent plans from addressing entities outside the HA exposure boundary."""

    def __init__(self, hass: HomeAssistant, exposure_checker: ExposureChecker = async_should_expose) -> None:
        self.hass = hass
        self._exposure_checker = exposure_checker

    def resolve(
        self,
        entity_id: str,
        allowed_domains: Container[str] | None = None,
    ) -> ResolvedEntity:
        """Return an entity snapshot or a stable reason why it is disallowed."""
        state = self.hass.states.get(entity_id)
        if state is None:
            raise EntityResolutionError("entity_not_found", f"实体不存在: {entity_id}")

        domain = state.domain
        if allowed_domains and domain not in allowed_domains:
            raise EntityResolutionError("entity_domain_not_allowed", f"实体域不允许: {domain}")
        if not self._exposure_checker(self.hass, conversation.DOMAIN, entity_id):
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
