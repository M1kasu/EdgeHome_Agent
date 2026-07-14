"""Expired conversation state is unavailable and cleaned from SQLite."""

import asyncio
from datetime import timedelta

from custom_components.llama_conversation.edgehome.context.store import ConversationStateStore
from tests.context_fakes import FakeHass


def test_context_expires_without_falling_back_to_another_session(tmp_path) -> None:
    async def exercise() -> None:
        store = ConversationStateStore(FakeHass(tmp_path), tmp_path / "edgehome_context.db", ttl=timedelta(seconds=-1))
        await store.async_save(store.new_state("expired-session", "user-a", "assistant-a"))

        assert await store.async_get("expired-session", "user-a", "assistant-a") is None
        assert await store.async_clear_expired() == 0

    asyncio.run(exercise())
