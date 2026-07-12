"""SQLite persistence for bounded EdgeHome conversation state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from typing import Any

from homeassistant.core import HomeAssistant

from ..energy.schemas import EnergyPlan
from ..models import TaskPlan
from .models import ConversationState, PendingClarification, PendingEnergyPlan, PendingProtectedAction


@dataclass(frozen=True, slots=True)
class ConversationSession:
    """Identity used to isolate a conversation between agents and HA users."""

    conversation_id: str
    user_id: str | None = None
    assistant_id: str | None = None

    @property
    def session_id(self) -> str:
        material = "\x1f".join((self.conversation_id, self.user_id or "", self.assistant_id or ""))
        return sha256(material.encode("utf-8")).hexdigest()


class ConversationStateStore:
    """Persist session-isolated state without blocking Home Assistant's event loop."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversation_state (
        session_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        user_id TEXT,
        assistant_id TEXT,
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_conversation_state_expires_at
        ON conversation_state(expires_at);
    """

    def __init__(
        self,
        hass: HomeAssistant,
        database_path: Path | str | None = None,
        ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self._hass = hass
        configured_path = os.getenv("EDGEHOME_CONTEXT_DB_PATH")
        default_path = Path(hass.config.path("edgehome_context.db"))
        self._database_path = Path(database_path or configured_path or default_path)
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._initialized = False

    async def async_get(
        self,
        conversation_id: str,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState | None:
        """Load only the unexpired state for exactly one isolated session."""
        session = ConversationSession(conversation_id, user_id, assistant_id)
        return await self._async_run(self._load_state, session)

    def new_state(
        self,
        conversation_id: str,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Build an unsaved, session-isolated state for a first turn."""
        return self._new_state(ConversationSession(conversation_id, user_id, assistant_id))

    async def async_save(self, state: ConversationState) -> ConversationState:
        """Persist one validated state using a parameterized upsert."""
        refreshed = state.refreshed(self._ttl)
        await self._async_run(self._save_state, refreshed)
        return refreshed

    async def async_clear_state(
        self,
        conversation_id: str,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> None:
        """Delete exactly one session; never affect another session with a similar ID."""
        session = ConversationSession(conversation_id, user_id, assistant_id)
        await self._async_run(self._delete_state, session.session_id)

    async def async_clear_expired(self) -> int:
        """Remove expired sessions and return the number deleted for diagnostics."""
        return await self._async_run(self._delete_expired, datetime.now(timezone.utc))

    async def async_set_clarification(
        self,
        conversation_id: str,
        original_text: str,
        question: str,
        *,
        intent: str | None = None,
        candidate_entity_id: str | None = None,
        missing_slots: list[str] | None = None,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Persist a constrained clarification rather than guessing a missing slot."""
        session = ConversationSession(conversation_id, user_id, assistant_id)
        prior = await self.async_get(conversation_id, user_id, assistant_id)
        state = prior or self._new_state(session)
        pending = PendingClarification(
            original_text=original_text,
            question=question,
            intent=intent,
            candidate_entity_id=candidate_entity_id,
            missing_slots=missing_slots or [],
        )
        return await self.async_save(state.model_copy(update={"current_goal": original_text, "pending_clarification": pending}))

    async def async_consume_clarification(
        self,
        conversation_id: str,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> PendingClarification | None:
        """Return and clear the pending question so it cannot leak into later tasks."""
        state = await self.async_get(conversation_id, user_id, assistant_id)
        if state is None or state.pending_clarification is None:
            return None
        pending = state.pending_clarification
        await self.async_save(state.model_copy(update={"pending_clarification": None}))
        return pending

    async def async_set_protected_confirmation(
        self,
        conversation_id: str,
        plan: TaskPlan,
        entity_ids: list[str],
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Save a single protected-operation confirmation for this session only."""
        session = ConversationSession(conversation_id, user_id, assistant_id)
        prior = await self.async_get(conversation_id, user_id, assistant_id)
        state = prior or self._new_state(session)
        pending = PendingProtectedAction(plan=plan, entity_ids=entity_ids)
        return await self.async_save(state.model_copy(update={"pending_protected_action": pending, "pending_plan": plan}))

    async def async_consume_protected_confirmation(
        self,
        conversation_id: str,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> PendingProtectedAction | None:
        """Consume an approval after one use so it cannot authorize a replay."""
        state = await self.async_get(conversation_id, user_id, assistant_id)
        if state is None or state.pending_protected_action is None:
            return None
        pending = state.pending_protected_action
        await self.async_save(state.model_copy(update={"pending_protected_action": None, "pending_plan": None}))
        return pending

    async def async_set_energy_plan(
        self,
        conversation_id: str,
        plan: EnergyPlan,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Save a bounded energy proposal without storing raw model history."""
        session = ConversationSession(conversation_id, user_id, assistant_id)
        prior = await self.async_get(conversation_id, user_id, assistant_id)
        state = prior or self._new_state(session)
        return await self.async_save(
            state.model_copy(
                update={
                    "last_energy_analysis_id": plan.analysis_id,
                    "last_energy_recommendations": plan.recommendations,
                    "pending_energy_plan": PendingEnergyPlan(plan=plan),
                }
            )
        )

    async def async_get_energy_plan(
        self,
        conversation_id: str,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> EnergyPlan | None:
        """Return the session's still-valid energy proposal, if any."""
        state = await self.async_get(conversation_id, user_id, assistant_id)
        return state.pending_energy_plan.plan if state and state.pending_energy_plan else None

    async def async_update_energy_plan(
        self,
        conversation_id: str,
        plan: EnergyPlan,
        *,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ConversationState:
        """Replace the pending energy proposal for the same isolated session."""
        return await self.async_set_energy_plan(
            conversation_id,
            plan,
            user_id=user_id,
            assistant_id=assistant_id,
        )

    @property
    def database_path(self) -> Path:
        """Expose the configured location for diagnostics without opening the database."""
        return self._database_path

    async def _async_run(self, callback, *args):
        async with self._lock:
            if not self._initialized:
                await self._hass.async_add_executor_job(self._initialize)
                self._initialized = True
            return await self._hass.async_add_executor_job(partial(callback, *args))

    def _initialize(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(self._SCHEMA)

    def _load_state(self, session: ConversationSession) -> ConversationState | None:
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json, expires_at FROM conversation_state WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()
            if row is None:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute("DELETE FROM conversation_state WHERE session_id = ?", (session.session_id,))
                return None
            try:
                state = ConversationState.model_validate_json(row["state_json"])
            except ValueError:
                connection.execute("DELETE FROM conversation_state WHERE session_id = ?", (session.session_id,))
                return None
            if (
                state.session_id != session.session_id
                or state.conversation_id != session.conversation_id
                or state.user_id != session.user_id
                or state.assistant_id != session.assistant_id
            ):
                connection.execute("DELETE FROM conversation_state WHERE session_id = ?", (session.session_id,))
                return None
            return state

    def _save_state(self, state: ConversationState) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_state (
                    session_id, conversation_id, user_id, assistant_id, state_json,
                    created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    user_id = excluded.user_id,
                    assistant_id = excluded.assistant_id,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    state.session_id,
                    state.conversation_id,
                    state.user_id,
                    state.assistant_id,
                    state.model_dump_json(),
                    state.created_at.isoformat(),
                    state.updated_at.isoformat(),
                    state.expires_at.isoformat(),
                ),
            )

    def _delete_state(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM conversation_state WHERE session_id = ?", (session_id,))

    def _delete_expired(self, now: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM conversation_state WHERE expires_at <= ?", (now.isoformat(),))
            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _new_state(session: ConversationSession) -> ConversationState:
        return ConversationState(
            session_id=session.session_id,
            conversation_id=session.conversation_id,
            user_id=session.user_id,
            assistant_id=session.assistant_id,
        )


# Kept for external integrations that imported the pre-M1.5 class name.
DialogueStateStore = ConversationStateStore

__all__ = ["ConversationSession", "ConversationStateStore", "DialogueStateStore"]
