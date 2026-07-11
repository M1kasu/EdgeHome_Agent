"""Small SQLite knowledge index whose blocking work always runs in a Home Assistant executor job."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar

from homeassistant.core import HomeAssistant
from pydantic import BaseModel, ConfigDict, Field


_ResultT = TypeVar("_ResultT")


class KnowledgeDocument(BaseModel):
    """A local manual, policy, or device note that can be cited by deterministic retrieval."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    source: str = Field(min_length=1, max_length=500)
    device_model: str | None = Field(default=None, max_length=200)


class KnowledgeHit(BaseModel):
    """A concise retrieval result with source metadata rather than generated prose."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    source: str
    excerpt: str
    score: int = Field(ge=1)
    device_model: str | None = None


class KnowledgeSearchResult(BaseModel):
    """Explicitly represent the absence of evidence."""

    model_config = ConfigDict(extra="forbid")

    query: str
    hits: list[KnowledgeHit] = Field(default_factory=list)

    @property
    def evidence_found(self) -> bool:
        return bool(self.hits)


class LocalKnowledgeIndex:
    """SQLite-backed lexical index designed for small local manuals and notes."""

    def __init__(self, hass: HomeAssistant, database_path: str | Path | None = None, timeout_seconds: float = 5.0) -> None:
        self._hass = hass
        self._path = Path(database_path) if database_path else Path(hass.config.path(".storage", "edgehome_knowledge.sqlite"))
        self._timeout_seconds = timeout_seconds
        self._ready = False

    async def async_upsert(self, document: KnowledgeDocument) -> None:
        """Create/update a document through an executor job to keep the event loop responsive."""
        await self._async_ensure_ready()
        await self._async_run(self._upsert, document.model_dump())

    async def async_search(self, query: str, device_model: str | None = None, limit: int = 5) -> KnowledgeSearchResult:
        """Return lexical evidence only; empty hits mean the caller must decline to guess."""
        await self._async_ensure_ready()
        rows = await self._async_run(self._search, query, device_model, limit)
        return KnowledgeSearchResult(query=query, hits=[KnowledgeHit.model_validate(row) for row in rows])

    async def _async_ensure_ready(self) -> None:
        if self._ready:
            return
        await self._async_run(self._initialize)
        self._ready = True

    async def _async_run(self, function: Callable[..., _ResultT], *args: object) -> _ResultT:
        return await asyncio.wait_for(self._hass.async_add_executor_job(function, *args), timeout=self._timeout_seconds)

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    device_model TEXT
                )
                """
            )

    def _upsert(self, document: dict[str, Any]) -> None:
        with sqlite3.connect(self._path) as connection:
            connection.execute(
                """
                INSERT INTO documents (id, title, content, source, device_model)
                VALUES (:id, :title, :content, :source, :device_model)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    content=excluded.content,
                    source=excluded.source,
                    device_model=excluded.device_model
                """,
                document,
            )

    def _search(self, query: str, device_model: str | None, limit: int) -> list[dict[str, Any]]:
        terms = _terms(query)
        if not terms:
            return []
        with sqlite3.connect(self._path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, title, content, source, device_model FROM documents WHERE (? IS NULL OR device_model = ?)",
                (device_model, device_model),
            ).fetchall()
        hits: list[dict[str, Any]] = []
        for row in rows:
            searchable = f"{row['title']} {row['content']}".lower()
            score = sum(searchable.count(term) for term in terms)
            if score:
                hits.append(
                    {
                        "document_id": row["id"],
                        "title": row["title"],
                        "source": row["source"],
                        "excerpt": _excerpt(row["content"], terms),
                        "score": score,
                        "device_model": row["device_model"],
                    }
                )
        return sorted(hits, key=lambda hit: (-hit["score"], hit["document_id"]))[: max(1, min(limit, 20))]


def _terms(query: str) -> set[str]:
    normalized = "".join(character.lower() for character in query if character.isalnum())
    if not normalized:
        return set()
    words = {word.lower() for word in query.split() if len(word) >= 2}
    pairs = {normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))}
    return {term for term in words | pairs if len(term) >= 2}


def _excerpt(content: str, terms: set[str], limit: int = 240) -> str:
    lowered = content.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, min(positions, default=0) - 60)
    return content[start : start + limit]
