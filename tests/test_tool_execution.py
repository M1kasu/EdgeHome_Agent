"""第 11 阶段：工具统一 JSON 结果与服务后状态回读测试。"""

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.tools.runtime import EdgeHomeToolRuntime


@pytest.mark.asyncio
async def test_turn_on_device_returns_verified_structured_result(hass) -> None:
    hass.states.async_set("switch.edgehome_test_socket", "off", {"friendly_name": "测试插座"})

    async def turn_on(call) -> None:
        hass.states.async_set(call.data["entity_id"], "on", {"friendly_name": "测试插座"}, context=call.context)

    hass.services.async_register("switch", "turn_on", turn_on)
    runtime = EdgeHomeToolRuntime(hass, EntityNameResolver(hass, exposure_checker=lambda *_: True))

    result = await runtime.turn_on_device("switch.edgehome_test_socket")

    assert result.model_dump(mode="json") == {
        "success": True,
        "tool": "turn_on_device",
        "entity_id": "switch.edgehome_test_socket",
        "requested": {},
        "actual": {"state": "on", "attributes": {"friendly_name": "测试插座"}, "friendly_name": "测试插座"},
        "error_code": None,
        "message": "switch.edgehome_test_socket 已打开",
    }
