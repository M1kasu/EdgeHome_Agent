"""Windows Home Assistant fixture used by EdgeHome's unit and integration tests."""

from __future__ import annotations

import pytest_asyncio

from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry


@pytest_asyncio.fixture
async def hass(tmp_path):
    """Provide HA state, registry, and ConfigEntries services without POSIX socket guards."""
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    device_registry.async_setup(instance)
    await area_registry.async_load(instance, load_empty=True)
    await device_registry.async_load(instance, load_empty=True)
    await entity_registry.async_load(instance, load_empty=True)
    yield instance
    await instance.async_stop(force=True)
