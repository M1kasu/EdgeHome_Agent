"""本地知识索引：优先 SQLite FTS5，不可用时才回退到确定性 BM25。"""

from __future__ import annotations

import asyncio
import math
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant
from pydantic import BaseModel, ConfigDict, Field


_ResultT = TypeVar("_ResultT")


class KnowledgeDocument(BaseModel):
    """一个可审计的本地知识段，而不是未经来源约束的模型上下文。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    source: str = Field(min_length=1, max_length=500)
    section: str = Field(default="概述", min_length=1, max_length=200)
    category: str = Field(default="device_manual", max_length=64)
    device_type: str | None = Field(default=None, max_length=64)
    device_model: str | None = Field(default=None, max_length=200)


class KnowledgeHit(BaseModel):
    """返回原始证据字段，调用方只能基于这些字段组织回答。"""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    section: str
    content: str
    source: str
    score: float = Field(ge=0.0)
    category: str
    device_type: str | None = None
    device_model: str | None = None

    @property
    def excerpt(self) -> str:
        """兼容第 8 阶段的只读回答接口。"""
        return self.content


class KnowledgeSearchResult(BaseModel):
    """无证据是一个正常且显式的结果，不能由模型补全为事实。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    query: str
    hits: list[KnowledgeHit] = Field(default_factory=list)
    error_code: str | None = None

    @property
    def evidence_found(self) -> bool:
        return self.success and bool(self.hits)


class LocalKnowledgeIndex:
    """小型、离线、可重建的知识索引；所有 SQLite 工作在 HA executor 内完成。"""

    def __init__(self, hass: HomeAssistant, database_path: str | Path | None = None, timeout_seconds: float = 5.0) -> None:
        self._hass = hass
        self._path = Path(database_path) if database_path else Path(hass.config.path(".storage", "edgehome_knowledge.sqlite"))
        self._timeout_seconds = timeout_seconds
        self._ready = False
        self._fts_available = False

    async def async_upsert(self, document: KnowledgeDocument) -> None:
        """创建或更新一个知识段。"""
        await self._async_ensure_ready()
        await self._async_run(self._upsert, document.model_dump())

    async def async_rebuild(self, documents: Iterable[KnowledgeDocument]) -> int:
        """以传入的受控文档集原子重建索引，避免保留已删除资料。"""
        await self._async_ensure_ready()
        values = [document.model_dump() for document in documents]
        return await self._async_run(self._rebuild, values)

    async def async_search(
        self,
        query: str,
        device_type: str | None = None,
        device_model: str | None = None,
        category: str | None = None,
        limit: int = 5,
    ) -> KnowledgeSearchResult:
        """只返回检索到的证据；查询为空或无命中均有稳定错误码。"""
        await self._async_ensure_ready()
        normalized_limit = max(1, min(limit, 20))
        if not _query_terms(query):
            return KnowledgeSearchResult(success=False, query=query, error_code="EMPTY_QUERY")
        rows = await self._async_run(self._search, query, device_type, device_model, category, normalized_limit)
        if not rows:
            return KnowledgeSearchResult(success=False, query=query, error_code="NO_EVIDENCE")
        return KnowledgeSearchResult(success=True, query=query, hits=[KnowledgeHit.model_validate(row) for row in rows])

    async def _async_ensure_ready(self) -> None:
        if self._ready:
            return
        self._fts_available = await self._async_run(self._initialize)
        self._ready = True

    async def _async_run(self, function: Callable[..., _ResultT], *args: object) -> _ResultT:
        return await asyncio.wait_for(self._hass.async_add_executor_job(function, *args), timeout=self._timeout_seconds)

    def _initialize(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    section TEXT NOT NULL,
                    category TEXT NOT NULL,
                    device_type TEXT,
                    device_model TEXT
                )
                """
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5("
                    "id UNINDEXED, title, content, tokenize='unicode61')"
                )
            except sqlite3.OperationalError:
                return False
        return True

    def _upsert(self, document: dict[str, Any]) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT INTO documents (id, title, content, source, section, category, device_type, device_model)
                VALUES (:id, :title, :content, :source, :section, :category, :device_type, :device_model)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, content=excluded.content, source=excluded.source,
                    section=excluded.section, category=excluded.category,
                    device_type=excluded.device_type, device_model=excluded.device_model
                """,
                document,
            )
            if self._fts_available:
                connection.execute("DELETE FROM documents_fts WHERE id = :id", document)
                connection.execute(
                    "INSERT INTO documents_fts (id, title, content) VALUES (:id, :title, :content)",
                    {"id": document["id"], "title": _fts_text(document["title"]), "content": _fts_text(document["content"])},
                )

    def _rebuild(self, documents: list[dict[str, Any]]) -> int:
        with sqlite3.connect(self._path) as connection:
            connection.execute("DELETE FROM documents")
            if self._fts_available:
                connection.execute("DELETE FROM documents_fts")
            for document in documents:
                connection.execute(
                    """INSERT INTO documents (id, title, content, source, section, category, device_type, device_model)
                    VALUES (:id, :title, :content, :source, :section, :category, :device_type, :device_model)""",
                    document,
                )
                if self._fts_available:
                    connection.execute(
                        "INSERT INTO documents_fts (id, title, content) VALUES (:id, :title, :content)",
                        {"id": document["id"], "title": _fts_text(document["title"]), "content": _fts_text(document["content"])},
                    )
        return len(documents)

    def _search(
        self,
        query: str,
        device_type: str | None,
        device_model: str | None,
        category: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if self._fts_available:
            return self._fts_search(query, device_type, device_model, category, limit)
        return self._bm25_search(query, device_type, device_model, category, limit)

    def _fts_search(
        self,
        query: str,
        device_type: str | None,
        device_model: str | None,
        category: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        terms = _query_terms(query)
        match = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        filters = ["(? IS NULL OR docs.device_type = ?)", "(? IS NULL OR docs.device_model = ?)", "(? IS NULL OR docs.category = ?)"]
        values: list[Any] = [device_type, device_type, device_model, device_model, category, category, match, limit]
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT docs.id, docs.title, docs.content, docs.source, docs.section, docs.category,
                          docs.device_type, docs.device_model, bm25(documents_fts) AS rank
                   FROM documents_fts JOIN documents AS docs ON docs.id = documents_fts.id
                   WHERE """
                + " AND ".join(filters)
                + " AND documents_fts MATCH ? ORDER BY rank LIMIT ?",
                values,
            ).fetchall()
        filtered = [row for row in rows if _required_terms_present(f"{row['title']} {row['content']}", terms)]
        return [self._row_hit(row, max(0.0, 1.0 / (1.0 + abs(float(row["rank"])))), terms) for row in filtered]

    def _bm25_search(
        self,
        query: str,
        device_type: str | None,
        device_model: str | None,
        category: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        terms = _query_terms(query)
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """SELECT * FROM documents WHERE (? IS NULL OR device_type = ?) AND (? IS NULL OR device_model = ?)
                   AND (? IS NULL OR category = ?)""",
                (device_type, device_type, device_model, device_model, category, category),
            ).fetchall()
        documents = [row for row in rows if any(term in _fts_text(f"{row['title']} {row['content']}") for term in terms)]
        if not documents:
            return []
        document_count = len(documents)
        average_length = sum(len(_tokens(f"{row['title']} {row['content']}")) for row in documents) / document_count
        results: list[tuple[float, sqlite3.Row]] = []
        for row in documents:
            tokens = _tokens(f"{row['title']} {row['content']}")
            frequencies = Counter(tokens)
            score = 0.0
            for term in terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                document_frequency = sum(1 for item in documents if term in _tokens(f"{item['title']} {item['content']}"))
                inverse_frequency = math.log((document_count - document_frequency + 0.5) / (document_frequency + 0.5) + 1.0)
                score += inverse_frequency * (frequency * 2.2) / (frequency + 1.2 * (1 - 0.75 + 0.75 * len(tokens) / average_length))
            if score:
                results.append((score, row))
        return [self._row_hit(row, score, terms) for score, row in sorted(results, key=lambda value: (-value[0], value[1]["id"]))[:limit]]

    @staticmethod
    def _row_hit(row: sqlite3.Row, score: float, terms: tuple[str, ...]) -> dict[str, Any]:
        content = str(row["content"])
        return {
            "document_id": row["id"],
            "title": row["title"],
            "section": row["section"],
            "content": _excerpt(content, terms),
            "source": row["source"],
            "score": score,
            "category": row["category"],
            "device_type": row["device_type"],
            "device_model": row["device_model"],
        }


def _fts_text(value: str) -> str:
    """把中文拆为单字 token，让 FTS5 unicode61 可以可靠检索中文关键词。"""
    return " ".join(_query_terms(value))


def _tokens(value: str) -> list[str]:
    return list(_query_terms(value))


def _query_terms(query: str) -> tuple[str, ...]:
    words: list[str] = []
    current: list[str] = []
    for character in query.lower():
        if character.isascii() and character.isalnum():
            current.append(character)
            continue
        if current:
            words.append("".join(current))
            current.clear()
        if "\u4e00" <= character <= "\u9fff":
            words.append(character)
    if current:
        words.append("".join(current))
    return tuple(dict.fromkeys(word for word in words if word))


def _required_terms_present(content: str, terms: tuple[str, ...]) -> bool:
    """若查询包含型号/代码，必须实际命中它，不能被常见中文单字误导。"""
    lowered = content.lower()
    ascii_terms = [term for term in terms if term.isascii() and term.isalnum()]
    return all(term in lowered for term in ascii_terms)


def _excerpt(content: str, terms: tuple[str, ...], limit: int = 280) -> str:
    lowered = content.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, min(positions, default=0) - 70)
    return content[start : start + limit].strip()
