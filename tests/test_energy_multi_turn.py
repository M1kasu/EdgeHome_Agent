"""分析→方案→部分应用与参数调整的多轮测试。"""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.context.store import DialogueStateStore
from custom_components.llama_conversation.edgehome.energy.conversation import EnergyConversationHandler
from custom_components.llama_conversation.edgehome.energy.service import EnergyService


@pytest.mark.asyncio
async def test_energy_multi_turn_keeps_plan_selection_and_adjustment(hass) -> None:
    store = DialogueStateStore(hass)
    handler = EnergyConversationHandler(store, EnergyService(hass))

    analysis = await handler.async_handle("最近一周为什么用电量变高了？", "energy-turn")
    plan = await handler.async_handle("帮我制定一个节能方案，但不要影响冰箱和路由器。", "energy-turn")
    adjusted = await handler.async_handle("调整空调计划为30分钟", "energy-turn")
    applied = await handler.async_handle("应用第一条和第三条，第二条先不要。", "energy-turn")

    assert "未修改" in (analysis or "")
    assert "等待确认" in (plan or "")
    assert "30 分钟" in (adjusted or "")
    assert (applied or "").count("edgehome-demo-") == 2
