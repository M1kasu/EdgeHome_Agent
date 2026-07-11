"""实体、型号、说明书三段式约束测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.llama_conversation.edgehome.knowledge.indexer import LocalKnowledgeIndex
from custom_components.llama_conversation.edgehome.knowledge.service import KnowledgeService
from custom_components.llama_conversation.edgehome.knowledge.tools import KnowledgeToolRuntime


@pytest.mark.asyncio
async def test_device_manual_tool_returns_e3_evidence_for_registered_demo_ac(tmp_path, hass) -> None:
    runtime = KnowledgeToolRuntime(KnowledgeService(hass, LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite"), Path("knowledge")))

    result = await runtime.query_device_manual("climate.edgehome_living_room_ac", "E3")

    assert result.success is True
    assert result.hits[0].device_model == "EdgeHome AC-Demo-1"
    assert "风机" in result.hits[0].content


@pytest.mark.asyncio
async def test_unknown_device_cannot_borrow_another_manual(tmp_path, hass) -> None:
    runtime = KnowledgeToolRuntime(KnowledgeService(hass, LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite"), Path("knowledge")))

    result = await runtime.query_device_manual("switch.unknown_socket", "过载")

    assert result.success is False
    assert result.error_code == "UNKNOWN_DEVICE_MODEL"
