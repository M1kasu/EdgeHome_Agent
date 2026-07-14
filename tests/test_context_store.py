"""Persistence tests for the M1.5 SQLite conversation store."""

from __future__ import annotations

import asyncio

from custom_components.llama_conversation.edgehome.context.store import ConversationStateStore
from tests.context_fakes import FakeHass


def test_context_store_saves_and_loads_one_typed_session(tmp_path) -> None:
    async def exercise() -> None:
        store = ConversationStateStore(FakeHass(tmp_path), tmp_path / "edgehome_context.db")
        state = store.new_state("assist-session-001", "user-a", "assistant-a").model_copy(
            update={"last_entity_id": "light.edgehome_living_room_main", "last_action": "set_light_brightness"}
        )

        saved = await store.async_save(state)
        loaded = await store.async_get("assist-session-001", "user-a", "assistant-a")

        assert saved.session_id == state.session_id
        assert loaded is not None
        assert loaded.last_entity_id == "light.edgehome_living_room_main"
        assert store.database_path.exists()

    asyncio.run(exercise())
