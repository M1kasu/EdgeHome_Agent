"""加载仓库内经过来源标注的 Markdown 知识，不读取网络或用户私密文档。"""

from __future__ import annotations

import json
from pathlib import Path

from .indexer import KnowledgeDocument


class KnowledgeDocumentLoader:
    """把每个 Markdown 的元数据和二级标题拆为可引用的索引段。"""

    def load(self, root: Path) -> list[KnowledgeDocument]:
        documents: list[KnowledgeDocument] = []
        for path in sorted(root.rglob("*.md")):
            documents.extend(self._load_file(path, root))
        return documents

    def _load_file(self, path: Path, root: Path) -> list[KnowledgeDocument]:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or not lines[0].startswith("<!-- edgehome:") or not lines[0].endswith(" -->"):
            raise ValueError(f"知识文档缺少 edgehome 元数据: {path}")
        metadata = json.loads(lines[0][len("<!-- edgehome:") : -len(" -->")].strip())
        required = {"id", "title", "source", "category"}
        if not required.issubset(metadata):
            raise ValueError(f"知识文档元数据不完整: {path}")
        sections: list[tuple[str, list[str]]] = []
        current_title = "概述"
        current_lines: list[str] = []
        for line in lines[1:]:
            if line.startswith("## "):
                if current_lines:
                    sections.append((current_title, current_lines))
                current_title, current_lines = line[3:].strip(), []
            elif not line.startswith("# "):
                current_lines.append(line)
        if current_lines:
            sections.append((current_title, current_lines))
        relative_source = path.relative_to(root).as_posix()
        documents: list[KnowledgeDocument] = []
        for index, (section, content_lines) in enumerate(sections, start=1):
            content = "\n".join(content_lines).strip()
            if not content:
                continue
            documents.append(
                KnowledgeDocument(
                    id=f"{metadata['id']}-{index}",
                    title=str(metadata["title"]),
                    content=content,
                    source=f"{metadata['source']} ({relative_source})",
                    section=section,
                    category=str(metadata["category"]),
                    device_type=metadata.get("device_type"),
                    device_model=metadata.get("device_model"),
                )
            )
        return documents
