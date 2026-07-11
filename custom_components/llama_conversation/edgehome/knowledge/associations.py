"""Persist device-to-manual model associations separately from the document index."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from pydantic import BaseModel, ConfigDict, Field

from .indexer import KnowledgeSearchResult, LocalKnowledgeIndex


class DeviceManualAssociation(BaseModel):
    """A user-configured mapping from one Home Assistant entity to a manual model id."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    device_model: str = Field(min_length=1, max_length=200)


class DeviceManualRegistry:
    """Async Store-backed associations used to constrain retrieval to the right manual."""

    _KEY = "edgehome_device_manuals"
    _VERSION = 1

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, self._VERSION, self._KEY)
        self._associations: dict[str, DeviceManualAssociation] = {}
        self._loaded = False

    async def async_associate(self, association: DeviceManualAssociation) -> None:
        await self._async_load()
        self._associations[association.entity_id] = association
        await self._store.async_save({"associations": {key: value.model_dump(mode="json") for key, value in self._associations.items()}})

    async def async_get(self, entity_id: str) -> DeviceManualAssociation | None:
        await self._async_load()
        return self._associations.get(entity_id)

    async def _async_load(self) -> None:
        if self._loaded:
            return
        payload = await self._store.async_load() or {}
        associations = payload.get("associations", {}) if isinstance(payload, dict) else {}
        if isinstance(associations, dict):
            for entity_id, value in associations.items():
                try:
                    association = DeviceManualAssociation.model_validate(value)
                except ValueError:
                    continue
                if association.entity_id == entity_id:
                    self._associations[entity_id] = association
        self._loaded = True


class DeviceKnowledgeRetriever:
    """Retrieve only evidence from the manual explicitly associated with an entity."""

    def __init__(self, index: LocalKnowledgeIndex, registry: DeviceManualRegistry) -> None:
        self._index = index
        self._registry = registry

    async def async_search(self, entity_id: str, query: str) -> KnowledgeSearchResult:
        association = await self._registry.async_get(entity_id)
        if association is None:
            return KnowledgeSearchResult(query=query)
        return await self._index.async_search(query, device_model=association.device_model)
