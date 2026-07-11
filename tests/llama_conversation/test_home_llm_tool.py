"""Baseline test for the original Home LLM service tool."""

from __future__ import annotations

import pytest

from homeassistant.helpers import llm

from custom_components.llama_conversation import HassServiceTool
from custom_components.llama_conversation.const import DOMAIN, SERVICE_TOOL_NAME


@pytest.mark.asyncio
async def test_original_service_tool_turns_on_exposed_light(hass):
    """The original registered tool can complete a blocking light service call."""
    entity_id = "light.edgehome_baseline"
    hass.states.async_set(entity_id, "off", {"friendly_name": "EdgeHome Baseline Light"})

    async def turn_on_service(call):
        target = call.data["entity_id"]
        current = hass.states.get(target)
        hass.states.async_set(target, "on", dict(current.attributes), context=call.context)

    hass.services.async_register("light", "turn_on", turn_on_service)

    result = await HassServiceTool().async_call(
        hass,
        llm.ToolInput(
            tool_name=SERVICE_TOOL_NAME,
            tool_args={"service": "light.turn_on", "target_device": entity_id},
        ),
        llm.LLMContext(
            platform=DOMAIN,
            context=None,
            language="en",
            assistant=None,
            device_id=None,
        ),
    )

    assert result == {"result": "success"}
    final_state = hass.states.get(entity_id)
    assert final_state is not None
    assert final_state.state == "on"
