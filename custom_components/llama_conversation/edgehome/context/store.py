"""Async Home Assistant Store adapter for bounded structured dialogue state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .dialogue_state import DialogueState, PendingClarification, PendingProtectedAction
from ..models import TaskPlan


class DialogueStateStore:
    """Store per-conversation EdgeHome state without blocking the Home Assistant loop."""

    _KEY = "edgehome_dialogue_state"
    _VERSION = 1

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, self._VERSION, self._KEY)
        self._states: dict[str, DialogueState] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load valid, non-expired state once; corrupt records are ignored instead of breaking chat."""
        if self._loaded:
            return
        payload = await self._store.async_load() or {}
        states = payload.get("states", {}) if isinstance(payload, dict) else {}
        now = datetime.now(timezone.utc)
        if isinstance(states, dict):
            for conversation_id, value in states.items():
                try:
                    state = DialogueState.model_validate(value)
                except ValueError:
                    continue
                if state.expires_at > now and state.conversation_id == conversation_id:
                    self._states[conversation_id] = state
        self._loaded = True

    async def async_get(self, conversation_id: str) -> DialogueState | None:
        """Return current non-expired state for a conversation."""
        await self.async_load()
        state = self._states.get(conversation_id)
        if state and state.expires_at <= datetime.now(timezone.utc):
            self._states.pop(conversation_id, None)
            await self._async_save()
            return None
        return state

    async def async_save(self, state: DialogueState) -> DialogueState:
        """Persist a refreshed state and return exactly what was stored."""
        await self.async_load()
        refreshed = state.refreshed()
        self._states[refreshed.conversation_id] = refreshed
        await self._async_save()
        return refreshed

    async def async_remember_plan(self, conversation_id: str, plan: TaskPlan) -> DialogueState:
        """Record the last plan's primary target and parameters for safe references."""
        first_step = plan.steps[0]
        entity_id = first_step.arguments.get("entity_id")
        state = DialogueState(
            conversation_id=conversation_id,
            last_plan=plan,
            last_entity_id=entity_id if isinstance(entity_id, str) else None,
            last_arguments=first_step.arguments,
        )
        return await self.async_save(state)

    async def async_set_clarification(
        self,
        conversation_id: str,
        original_goal: str,
        question: str,
    ) -> DialogueState:
        """Save a bounded clarification request instead of guessing the missing slot."""
        prior = await self.async_get(conversation_id)
        state = prior or DialogueState(conversation_id=conversation_id)
        state = state.model_copy(update={"pending_clarification": PendingClarification(original_goal=original_goal, question=question)})
        return await self.async_save(state)

    async def async_consume_clarification(self, conversation_id: str) -> PendingClarification | None:
        """Return and clear a pending question when the next user turn supplies an answer."""
        state = await self.async_get(conversation_id)
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
    ) -> DialogueState:
        """Persist exactly one requested protected shutdown until the next confirmation turn."""
        prior = await self.async_get(conversation_id)
        state = prior or DialogueState(conversation_id=conversation_id)
        pending = PendingProtectedAction(plan=plan, entity_ids=entity_ids)
        return await self.async_save(state.model_copy(update={"pending_protected_action": pending}))

    async def async_consume_protected_confirmation(self, conversation_id: str) -> PendingProtectedAction | None:
        """Return and clear an approval record so it cannot authorize another later action."""
        state = await self.async_get(conversation_id)
        if state is None or state.pending_protected_action is None:
            return None
        pending = state.pending_protected_action
        await self.async_save(state.model_copy(update={"pending_protected_action": None}))
        return pending

    async def async_clear_expired(self) -> int:
        """Remove expired sessions and return the count for diagnostics."""
        await self.async_load()
        now = datetime.now(timezone.utc)
        expired = [conversation_id for conversation_id, state in self._states.items() if state.expires_at <= now]
        for conversation_id in expired:
            self._states.pop(conversation_id, None)
        if expired:
            await self._async_save()
        return len(expired)

    async def _async_save(self) -> None:
        await self._store.async_save({"states": {key: state.model_dump(mode="json") for key, state in self._states.items()}})
