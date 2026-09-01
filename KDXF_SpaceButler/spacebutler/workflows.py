"""Saved workflows with three-tier chat triggering: phrase, alias, semantics.

用户在工作流视图里固化一串固定动作，并给它一个启动口令。之后在对话框里说出口令
就能直接执行，不必等 Agent 重新规划——固定动作要的是确定性和零延迟。

三级匹配，代价从低到高：

1. **精确口令**：归一化后完全一致（忽略大小写、空格、中英标点）。零模型调用。
2. **别名**：用户为同一个工作流登记的其他说法，同样是确定性匹配。
3. **语义兜底**：前两级都没命中时，把候选工作流的名称和口令交给本地模型判断意图。
   命中且置信度过阈值时**不直接执行**，先返回确认卡，由用户点确认。

这样"回家模式"秒触发，"我到家了，老规矩"也能懂，而误触发始终有确认拦着。
语义兜底不可用（模型离线）时静默跳过，请求照常交给 Agent 规划，不报错。
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from typing import Any, Final
import uuid


MAX_WORKFLOWS: Final = 40
MAX_STEPS: Final = 20
MAX_ALIASES: Final = 6
SEMANTIC_CONFIDENCE_FLOOR: Final = 0.55

# 归一化时丢弃的标点：中英文都要覆盖，否则"回家模式。"匹配不上"回家模式"。
_PUNCTUATION: Final = re.compile(r"[\s,.!?;:、，。！？；：·…\-_'\"“”‘’()（）\[\]【】]+")


def normalize_phrase(value: str) -> str:
    """Fold width, case, whitespace and punctuation so口令 matching is forgiving."""
    folded = unicodedata.normalize("NFKC", value).strip().casefold()
    return _PUNCTUATION.sub("", folded)


@dataclass(frozen=True)
class WorkflowStep:
    device_id: str
    action: str
    value: object | None = None
    delay_minutes: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "device_id": self.device_id,
            "action": self.action,
            "value": self.value,
            "delay_minutes": self.delay_minutes,
        }


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    name: str
    phrase: str
    steps: tuple[WorkflowStep, ...]
    aliases: tuple[str, ...] = ()
    created_at: str = ""
    source: str = "manual"

    def to_json(self) -> dict[str, object]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "phrase": self.phrase,
            "aliases": list(self.aliases),
            "steps": [step.to_json() for step in self.steps],
            "created_at": self.created_at,
            "source": self.source,
        }

    def trigger_texts(self) -> tuple[str, ...]:
        return (self.phrase, *self.aliases)


@dataclass
class TriggerMatch:
    """Why one workflow was selected, so the UI can explain itself."""

    workflow: Workflow
    tier: str
    confidence: float
    matched_text: str = ""
    reason: str = ""
    needs_confirmation: bool = False
    alternatives: list[dict[str, object]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "workflow": self.workflow.to_json(),
            "tier": self.tier,
            "confidence": round(self.confidence, 3),
            "matched_text": self.matched_text,
            "reason": self.reason,
            "needs_confirmation": self.needs_confirmation,
            "alternatives": list(self.alternatives),
        }


def _parse_step(raw: object) -> WorkflowStep:
    if not isinstance(raw, dict):
        raise ValueError("工作流步骤必须是对象")
    device_id = str(raw.get("device_id") or raw.get("deviceId") or "").strip()
    action = str(raw.get("action", "")).strip()
    if not device_id or not action:
        raise ValueError("工作流步骤必须包含 device_id 和 action")
    delay_raw = raw.get("delay_minutes", raw.get("delay", 0))
    try:
        delay = int(delay_raw or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("步骤延迟必须是整数分钟") from error
    if not 0 <= delay <= 60:
        raise ValueError("步骤延迟必须在 0 到 60 分钟之间")
    return WorkflowStep(device_id, action, raw.get("value"), delay)


def parse_workflow(raw: dict[str, object], *, workflow_id: str | None = None) -> Workflow:
    """Validate one workflow definition submitted by the browser."""
    name = str(raw.get("name", "")).strip()
    phrase = str(raw.get("phrase", "")).strip()
    if not name:
        raise ValueError("工作流需要名称")
    if not phrase:
        raise ValueError("工作流需要唯一的启动口令")
    if not normalize_phrase(phrase):
        raise ValueError("启动口令不能只包含标点或空格")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("工作流至少需要一个步骤")
    if len(steps_raw) > MAX_STEPS:
        raise ValueError(f"工作流最多 {MAX_STEPS} 个步骤")
    aliases_raw = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
    aliases: list[str] = []
    for item in aliases_raw:
        text = str(item).strip()
        if text and normalize_phrase(text) and text not in aliases:
            aliases.append(text[:60])
    return Workflow(
        workflow_id=workflow_id or str(raw.get("workflow_id") or uuid.uuid4().hex[:12]),
        name=name[:60],
        phrase=phrase[:60],
        steps=tuple(_parse_step(item) for item in steps_raw),
        aliases=tuple(aliases[:MAX_ALIASES]),
        created_at=str(raw.get("created_at") or datetime.now(timezone.utc).isoformat()),
        source=str(raw.get("source", "manual"))[:24],
    )


class WorkflowStore:
    """Server-side workflow persistence.

    Workflows must live on the server, not in localStorage: the chat endpoint has
    to see them to trigger on口令, and voice input reaches chat through the same
    path. Browser-only storage would make triggering impossible.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = Path(database_path) if database_path is not None else None
        self._memory: dict[str, Workflow] = {}
        if self._database_path is not None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
            self._load()

    def all(self) -> list[Workflow]:
        return sorted(self._memory.values(), key=lambda item: item.created_at)

    def get(self, workflow_id: str) -> Workflow | None:
        return self._memory.get(workflow_id)

    def save(self, raw: dict[str, object]) -> Workflow:
        """Insert or update one workflow, rejecting duplicate trigger phrases."""
        workflow = parse_workflow(raw)
        normalized = {normalize_phrase(text) for text in workflow.trigger_texts()}
        for existing in self._memory.values():
            if existing.workflow_id == workflow.workflow_id:
                continue
            clash = normalized & {normalize_phrase(text) for text in existing.trigger_texts()}
            if clash:
                raise ValueError(f"启动口令与「{existing.name}」重复：{sorted(clash)[0]}")
        if workflow.workflow_id not in self._memory and len(self._memory) >= MAX_WORKFLOWS:
            raise ValueError(f"最多保存 {MAX_WORKFLOWS} 个工作流")
        self._memory[workflow.workflow_id] = workflow
        self._persist(workflow)
        return workflow

    def delete(self, workflow_id: str) -> bool:
        existed = self._memory.pop(workflow_id, None) is not None
        if existed and self._database_path is not None:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute(
                        "DELETE FROM workflow WHERE workflow_id = ?", (workflow_id,)
                    )
        return existed

    def match_exact(self, text: str) -> TriggerMatch | None:
        """Tier 1 and 2: deterministic phrase / alias match, no model call."""
        target = normalize_phrase(text)
        if not target:
            return None
        for workflow in self.all():
            if normalize_phrase(workflow.phrase) == target:
                return TriggerMatch(
                    workflow=workflow,
                    tier="phrase",
                    confidence=1.0,
                    matched_text=workflow.phrase,
                    reason="启动口令精确命中",
                )
        for workflow in self.all():
            for alias in workflow.aliases:
                if normalize_phrase(alias) == target:
                    return TriggerMatch(
                        workflow=workflow,
                        tier="alias",
                        confidence=1.0,
                        matched_text=alias,
                        reason=f"命中别名「{alias}」",
                    )
        return None

    def _initialize_database(self) -> None:
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workflow (
                        workflow_id TEXT PRIMARY KEY,
                        definition_json TEXT NOT NULL
                    )
                    """
                )

    def _persist(self, workflow: Workflow) -> None:
        if self._database_path is None:
            return
        value = json.dumps(workflow.to_json(), ensure_ascii=False, separators=(",", ":"))
        with closing(sqlite3.connect(self._database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO workflow (workflow_id, definition_json) VALUES (?, ?)
                    ON CONFLICT(workflow_id) DO UPDATE SET definition_json = excluded.definition_json
                    """,
                    (workflow.workflow_id, value),
                )

    def _load(self) -> None:
        with closing(sqlite3.connect(self._database_path)) as connection:
            rows = connection.execute(
                "SELECT workflow_id, definition_json FROM workflow"
            ).fetchall()
        for workflow_id, definition in rows:
            try:
                raw = json.loads(definition)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            try:
                self._memory[str(workflow_id)] = parse_workflow(raw, workflow_id=str(workflow_id))
            except ValueError:
                # 旧格式或损坏记录不能让整个工作台起不来。
                continue


_SEMANTIC_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "workflow_id": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["workflow_id", "confidence"],
}

_SEMANTIC_SYSTEM: Final = (
    "你是家庭助理的工作流路由器。用户说了一句话，你要判断它是否想启动某个已保存的工作流。\n"
    "只在用户明确表达了要执行该工作流的意图时才匹配；提问、闲聊、询问状态、"
    "或描述与所有工作流都不同的新需求时，返回空的 workflow_id。\n"
    "只输出一个 JSON 对象：{\"workflow_id\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"简短中文理由\"}。\n"
    "无法匹配时 workflow_id 为空字符串，confidence 为 0。"
)


def match_semantic(
    store: WorkflowStore,
    text: str,
    llm_client: object | None,
    *,
    floor: float = SEMANTIC_CONFIDENCE_FLOOR,
) -> TriggerMatch | None:
    """Tier 3: ask the local model whether this utterance means one workflow.

    Returns a match flagged ``needs_confirmation`` so the caller shows a confirm
    card instead of moving devices on a probabilistic decision. Any model or
    parse failure returns ``None`` and the request falls through to planning.
    """
    workflows = store.all()
    if not workflows or not text.strip() or llm_client is None:
        return None
    chat_json = getattr(llm_client, "chat_json", None)
    available = getattr(llm_client, "available", None)
    if not callable(chat_json) or (callable(available) and not available()):
        return None
    catalog = "\n".join(
        f"- workflow_id={item.workflow_id}｜名称：{item.name}｜口令：{item.phrase}"
        + (f"｜别名：{'、'.join(item.aliases)}" if item.aliases else "")
        + f"｜{len(item.steps)} 个动作"
        for item in workflows
    )
    try:
        parsed = chat_json(
            _SEMANTIC_SYSTEM,
            f"已保存的工作流：\n{catalog}\n\n用户说：{text.strip()}",
            _SEMANTIC_SCHEMA,
        )
    except (OSError, RuntimeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    workflow_id = str(parsed.get("workflow_id", "")).strip()
    if not workflow_id:
        return None
    workflow = store.get(workflow_id)
    if workflow is None:
        return None
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    if not floor <= confidence <= 1.0:
        return None
    return TriggerMatch(
        workflow=workflow,
        tier="semantic",
        confidence=confidence,
        matched_text=text.strip(),
        reason=str(parsed.get("reason", "")).strip()[:120] or "语义判断与该工作流意图一致",
        needs_confirmation=True,
    )


def match_trigger(
    store: WorkflowStore,
    text: str,
    llm_client: object | None = None,
) -> TriggerMatch | None:
    """Run the three tiers in cost order and return the first match."""
    exact = store.match_exact(text)
    if exact is not None:
        return exact
    return match_semantic(store, text, llm_client)
