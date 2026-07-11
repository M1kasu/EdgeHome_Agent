"""可供对话层调用的知识工具；返回完整证据 JSON，不生成诊断结论。"""

from __future__ import annotations

from .indexer import KnowledgeSearchResult
from .service import KnowledgeService


class KnowledgeToolRuntime:
    """将三个受限知识工具公开为稳定异步接口。"""

    def __init__(self, service: KnowledgeService) -> None:
        self._service = service

    async def query_device_manual(self, entity_id: str, query: str) -> KnowledgeSearchResult:
        return await self._service.query_device_manual(entity_id, query)

    async def query_household_rule(self, query: str) -> KnowledgeSearchResult:
        return await self._service.query_household_rule(query)

    async def query_energy_guide(self, query: str) -> KnowledgeSearchResult:
        return await self._service.query_energy_guide(query)
