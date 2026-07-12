"""Different Home Assistant users cannot read each other's reference state."""

import asyncio

from custom_components.llama_conversation.edgehome.context.store import ConversationStateStore
from tests.context_fakes import FakeHass


def test_same_conversation_id_isolated_by_user_and_assistant(tmp_path) -> None:
    async def exercise() -> None:
        store = ConversationStateStore(FakeHass(tmp_path), tmp_path / "edgehome_context.db")
        state = store.new_state("shared-id", "user-a", "assistant-a").model_copy(
            update={"last_entity_id": "light.edgehome_living_room_main"}
        )
        await store.async_save(state)

        assert await store.async_get("shared-id", "user-b", "assistant-a") is None
        assert await store.async_get("shared-id", "user-a", "assistant-b") is None
        assert (await store.async_get("shared-id", "user-a", "assistant-a")) is not None

    asyncio.run(exercise())
