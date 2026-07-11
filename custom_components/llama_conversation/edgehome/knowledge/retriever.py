"""为知识工具提供受类型、型号和资料类别约束的证据检索。"""

from __future__ import annotations

from .indexer import KnowledgeSearchResult, LocalKnowledgeIndex


class KnowledgeRetriever:
    """不生成事实，只为上层返回可追溯的本地资料片段。"""

    def __init__(self, index: LocalKnowledgeIndex) -> None:
        self._index = index

    async def search_knowledge(
        self,
        query: str,
        device_type: str | None = None,
        model: str | None = None,
        top_k: int = 3,
        category: str | None = None,
    ) -> KnowledgeSearchResult:
        """第 12 阶段统一检索接口。"""
        return await self._index.async_search(
            query,
            device_type=device_type,
            device_model=model,
            category=category,
            limit=top_k,
        )
