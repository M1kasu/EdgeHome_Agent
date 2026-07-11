"""Tests for the closed EdgeHome tool registry."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.errors import ToolValidationError
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


def test_registry_exposes_only_known_tools() -> None:
    registry = build_default_tool_registry()

    assert registry.get("turn_on_device") is not None
    assert registry.get("arbitrary_rest_request") is None


def test_brightness_arguments_are_bounded_and_mapped_to_ha_percent() -> None:
    registry = build_default_tool_registry()
    tool = registry.get("set_light_brightness")
    assert tool is not None

    arguments = tool.validate_arguments({"entity_id": "light.demo", "brightness": 45})
    assert tool.build_service_data(arguments) == {"entity_id": "light.demo", "brightness_pct": 45}

    with pytest.raises(ToolValidationError, match="less than or equal"):
        tool.validate_arguments({"entity_id": "light.demo", "brightness": 101})
