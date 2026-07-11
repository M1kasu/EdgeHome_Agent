"""Tests for local evidence retrieval and device-manual associations."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.knowledge.associations import (
    DeviceKnowledgeRetriever,
    DeviceManualAssociation,
    DeviceManualRegistry,
)
from custom_components.llama_conversation.edgehome.knowledge.indexer import KnowledgeDocument, LocalKnowledgeIndex


@pytest.mark.asyncio
async def test_local_knowledge_search_returns_manual_evidence_only(tmp_path, hass) -> None:
    index = LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite")
    await index.async_upsert(
        KnowledgeDocument(
            id="vacuum-v1-filter",
            title="Acme V1 滤网说明",
            content="清理滤网前请关闭设备。滤网可用清水冲洗并完全晾干。",
            source="manual/acme-v1.pdf#filter",
            device_model="Acme V1",
        )
    )
    await index.async_upsert(
        KnowledgeDocument(
            id="lamp-v1-reset",
            title="灯具复位",
            content="长按按钮十秒后复位。",
            source="manual/lamp-v1.pdf#reset",
            device_model="Lamp V1",
        )
    )

    result = await index.async_search("如何清理滤网", device_model="Acme V1")
    empty = await index.async_search("不存在的问题", device_model="Acme V1")

    assert result.evidence_found is True
    assert result.hits[0].document_id == "vacuum-v1-filter"
    assert "滤网" in result.hits[0].excerpt
    assert empty.evidence_found is False


@pytest.mark.asyncio
async def test_device_association_scopes_retrieval_to_the_associated_manual(tmp_path, hass) -> None:
    index = LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite")
    await index.async_upsert(
        KnowledgeDocument(
            id="vacuum-v1-filter",
            title="Acme V1 滤网说明",
            content="清理滤网前请关闭设备。",
            source="manual/acme-v1.pdf#filter",
            device_model="Acme V1",
        )
    )
    registry = DeviceManualRegistry(hass)
    await registry.async_associate(DeviceManualAssociation(entity_id="vacuum.demo", device_model="Acme V1"))
    retriever = DeviceKnowledgeRetriever(index, registry)

    result = await retriever.async_search("vacuum.demo", "清理滤网")
    no_association = await retriever.async_search("vacuum.unknown", "清理滤网")

    assert result.evidence_found is True
    assert result.hits[0].device_model == "Acme V1"
    assert no_association.evidence_found is False
