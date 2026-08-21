from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeHit:
    title: str
    source: str
    excerpt: str
    score: int

    def to_dict(self) -> dict[str, str | int]:
        return {"title": self.title, "source": self.source, "excerpt": self.excerpt, "score": self.score}


class KnowledgeRepository:
    """Small local knowledge base with transparent lexical ranking.

    Returned answers are constrained to matching excerpts; no match is explicitly
    reported rather than guessed as a device fault meaning.
    """

    SEED = [
        (
            "客厅空调使用说明",
            "local/manuals/living-room-ac.md",
            "客厅空调可在制冷模式下设置 16 到 30 摄氏度。出现 E1 时，请先断电 5 分钟并检查室内外机通讯线；持续出现 E1 请联系售后。",
        ),
        (
            "智能窗帘离线排查",
            "local/manuals/living-room-curtain.md",
            "窗帘显示离线时，请检查供电、网关连接和设备距离。离线状态下不要重复发送开关命令。",
        ),
        (
            "家庭节能规则",
            "local/rules/energy.md",
            "无人时建议关闭电视、照明和空调。冰箱、路由器、家庭服务器和网络存储属于受保护设备，不应被普通离家场景关闭。",
        ),
    ]

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        connection = self._connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL, body TEXT NOT NULL)"
            )
            count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if count == 0:
                connection.executemany("INSERT INTO documents(title, source, body) VALUES (?, ?, ?)", self.SEED)
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def search(self, query: str, limit: int = 3) -> list[KnowledgeHit]:
        tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{1,4}", query) if len(token) > 0}
        connection = self._connect()
        try:
            rows = connection.execute("SELECT title, source, body FROM documents").fetchall()
        finally:
            connection.close()
        hits: list[KnowledgeHit] = []
        for title, source, body in rows:
            corpus = f"{title} {body}".lower()
            score = sum(1 for token in tokens if token in corpus)
            if score:
                hits.append(KnowledgeHit(title, source, body, score))
        return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]
