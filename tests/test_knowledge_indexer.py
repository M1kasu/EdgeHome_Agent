"""第 12 阶段本地知识索引测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.llama_conversation.edgehome.knowledge.indexer import LocalKnowledgeIndex
from custom_components.llama_conversation.edgehome.knowledge.service import KnowledgeService


@pytest.mark.asyncio
async def test_fts_index_loads_local_documents_and_returns_structured_evidence(tmp_path, hass) -> None:
    service = KnowledgeService(hass, LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite"), Path("knowledge"))

    count = await service.async_initialize()
    result = await service.search_knowledge("E3", device_type="climate", model="EdgeHome AC-Demo-1")

    assert count >= 10
    assert result.success is True
    assert result.hits[0].section == "故障代码 E3"
    assert result.hits[0].source.startswith("原创测试设备资料")


@pytest.mark.asyncio
async def test_no_evidence_is_explicit_instead_of_a_fabricated_answer(tmp_path, hass) -> None:
    service = KnowledgeService(hass, LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite"), Path("knowledge"))

    result = await service.search_knowledge("不存在的 Z99 故障")

    assert result.success is False
    assert result.error_code == "NO_EVIDENCE"
