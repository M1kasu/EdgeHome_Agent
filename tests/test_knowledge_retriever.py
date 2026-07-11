"""知识类别和规则检索测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.llama_conversation.edgehome.knowledge.indexer import LocalKnowledgeIndex
from custom_components.llama_conversation.edgehome.knowledge.service import KnowledgeService


@pytest.mark.asyncio
async def test_household_rule_retrieval_keeps_protected_device_evidence(tmp_path, hass) -> None:
    service = KnowledgeService(hass, LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite"), Path("knowledge"))

    result = await service.query_household_rule("为什么离家不能关闭冰箱和路由器")

    assert result.success is True
    assert result.hits[0].category == "household_rule"
    assert "受保护设备" in result.hits[0].content


@pytest.mark.asyncio
async def test_energy_guide_is_not_mixed_with_device_manuals(tmp_path, hass) -> None:
    service = KnowledgeService(hass, LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite"), Path("knowledge"))

    result = await service.query_energy_guide("空调高温用电")

    assert result.success is True
    assert {item.category for item in result.hits} == {"energy_guide"}
