"""Deterministic, read-only answers for local knowledge and energy data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from .energy.statistics import EnergyAnalyzer, HomeAssistantEnergyStatistics
from .knowledge.associations import DeviceKnowledgeRetriever, DeviceManualRegistry
from .knowledge.indexer import LocalKnowledgeIndex


_KNOWLEDGE_QUERY = re.compile(
    r"^(?:查询|查找|检索)(?:本地)?(?:知识库|说明书|设备说明书)\s+"
    r"(?:(?P<entity_id>[a-z_]+\.[a-z0-9_]+)\s+)?(?P<query>.+)$"
)
_ENERGY_QUERY = re.compile(
    r"^(?:分析|查看|查询)\s*(?P<statistic_id>[a-z_]+\.[a-z0-9_]+)\s*"
    r"(?:最近|近)\s*(?P<hours>[1-9]\d{0,2})\s*小时(?:能耗|用电|耗电)$"
)
_MAX_ENERGY_HOURS = 720


@dataclass(frozen=True, slots=True)
class _KnowledgeRequest:
    entity_id: str | None
    query: str


@dataclass(frozen=True, slots=True)
class _EnergyRequest:
    statistic_id: str
    hours: int


class ReadOnlyInformationHandler:
    """Answer explicit information requests without calling a model or HA services."""

    def __init__(
        self,
        knowledge_index: LocalKnowledgeIndex,
        manual_registry: DeviceManualRegistry,
        energy_statistics: HomeAssistantEnergyStatistics,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._knowledge_index = knowledge_index
        self._device_knowledge = DeviceKnowledgeRetriever(knowledge_index, manual_registry)
        self._energy_statistics = energy_statistics
        self._energy_analyzer = EnergyAnalyzer()
        self._clock = clock or _utc_now

    async def async_handle(self, text: str) -> str | None:
        """Return a response only when text matches a supported read-only query."""
        knowledge = _parse_knowledge_query(text)
        if knowledge is not None:
            return await self._async_answer_knowledge(knowledge)

        energy = _parse_energy_query(text)
        if energy is not None:
            return await self._async_answer_energy(energy)
        return None

    async def _async_answer_knowledge(self, request: _KnowledgeRequest) -> str:
        if request.entity_id:
            result = await self._device_knowledge.async_search(request.entity_id, request.query)
        else:
            result = await self._knowledge_index.async_search(request.query)
        if not result.evidence_found:
            return "本地资料未找到可引用内容，无法据此回答。"
        hit = result.hits[0]
        return f"依据《{hit.title}》({hit.source})：{hit.excerpt}"

    async def _async_answer_energy(self, request: _EnergyRequest) -> str:
        end = _as_utc(self._clock())
        start = end - timedelta(hours=request.hours)
        try:
            samples = await self._energy_statistics.async_samples(request.statistic_id, start, end)
        except Exception:
            return "能耗统计暂不可用，未生成建议。"
        report = self._energy_analyzer.build_report(request.statistic_id, samples)
        if not report.intervals:
            return f"{request.statistic_id} 最近 {request.hours} 小时没有足够的累计能耗统计。"
        response = f"{request.statistic_id} 最近 {request.hours} 小时累计能耗 {report.total_kwh:.2f} kWh。"
        if report.recommendations:
            return f"{response}{report.recommendations[0].message}"
        return response


def _parse_knowledge_query(text: str) -> _KnowledgeRequest | None:
    match = _KNOWLEDGE_QUERY.fullmatch(text.strip())
    if match is None:
        return None
    query = match.group("query").strip()
    if not query:
        return None
    return _KnowledgeRequest(entity_id=match.group("entity_id"), query=query)


def _parse_energy_query(text: str) -> _EnergyRequest | None:
    match = _ENERGY_QUERY.fullmatch(text.strip())
    if match is None:
        return None
    hours = int(match.group("hours"))
    if hours > _MAX_ENERGY_HOURS:
        return None
    return _EnergyRequest(statistic_id=match.group("statistic_id"), hours=hours)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
