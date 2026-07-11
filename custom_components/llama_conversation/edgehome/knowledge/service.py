"""知识库生命周期与设备实体到受控测试型号的映射。"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant

from ..entity_resolver import EntityNameResolver
from .document_loader import KnowledgeDocumentLoader
from .indexer import KnowledgeSearchResult, LocalKnowledgeIndex
from .retriever import KnowledgeRetriever


_DEVICE_MODELS = {
    "climate.edgehome_living_room_ac": ("climate", "EdgeHome AC-Demo-1"),
    "climate.edgehome_bedroom_ac": ("climate", "EdgeHome AC-Demo-1"),
    "switch.edgehome_test_socket": ("switch", "EdgeHome Socket-Demo-1"),
    "cover.edgehome_living_room_curtain": ("cover", "EdgeHome Curtain-Demo-1"),
    "light.edgehome_living_room_main": ("light", "EdgeHome Light-Demo-1"),
    "light.edgehome_bedroom_main": ("light", "EdgeHome Light-Demo-1"),
    "switch.edgehome_living_room_tv": ("switch", "EdgeHome TV-Demo-1"),
}


class KnowledgeService:
    """首次查询时加载仓库知识；索引内容完全来自版本控制的本地 Markdown。"""

    def __init__(
        self,
        hass: HomeAssistant,
        index: LocalKnowledgeIndex | None = None,
        documents_path: Path | None = None,
        resolver: EntityNameResolver | None = None,
    ) -> None:
        self._index = index or LocalKnowledgeIndex(hass)
        self._retriever = KnowledgeRetriever(self._index)
        self._documents_path = documents_path or _default_documents_path(hass)
        self._resolver = resolver
        self._loaded = False

    async def async_initialize(self) -> int:
        """从受控资料生成可重建索引；缺少资料时返回空索引而非伪造答案。"""
        if self._loaded:
            return 0
        documents = KnowledgeDocumentLoader().load(self._documents_path) if self._documents_path.exists() else []
        count = await self._index.async_rebuild(documents)
        self._loaded = True
        return count

    async def search_knowledge(
        self,
        query: str,
        device_type: str | None = None,
        model: str | None = None,
        top_k: int = 3,
        category: str | None = None,
    ) -> KnowledgeSearchResult:
        await self.async_initialize()
        return await self._retriever.search_knowledge(query, device_type, model, top_k, category)

    async def query_device_manual(self, entity_id: str, query: str, top_k: int = 3) -> KnowledgeSearchResult:
        """实体只能查询其测试型号的说明资料，无法识别型号时拒绝泛化诊断。"""
        await self.async_initialize()
        mapping = _DEVICE_MODELS.get(entity_id)
        if mapping is None:
            return KnowledgeSearchResult(success=False, query=query, error_code="UNKNOWN_DEVICE_MODEL")
        device_type, model = mapping
        if self._resolver is not None:
            try:
                self._resolver.snapshot(entity_id)
            except Exception:
                return KnowledgeSearchResult(success=False, query=query, error_code="ENTITY_UNAVAILABLE")
        return await self._retriever.search_knowledge(query, device_type, model, top_k, "device_manual")

    async def query_household_rule(self, query: str, top_k: int = 3) -> KnowledgeSearchResult:
        await self.async_initialize()
        return await self._retriever.search_knowledge(query, top_k=top_k, category="household_rule")

    async def query_energy_guide(self, query: str, top_k: int = 3) -> KnowledgeSearchResult:
        await self.async_initialize()
        return await self._retriever.search_knowledge(query, top_k=top_k, category="energy_guide")


def _default_documents_path(hass: HomeAssistant) -> Path:
    configured = Path(hass.config.path("edgehome_knowledge"))
    if configured.exists():
        return configured
    return Path(__file__).resolve().parents[4] / "knowledge"
