"""A new context service instance restores state from the same SQLite database."""

import asyncio

from custom_components.llama_conversation.edgehome.context.store import ConversationStateStore
from tests.context_fakes import FakeHass


def test_context_survives_component_restart(tmp_path) -> None:
    async def exercise() -> None:
        database_path = tmp_path / "edgehome_context.db"
        first_store = ConversationStateStore(FakeHass(tmp_path), database_path)
        await first_store.async_save(
            first_store.new_state("session-a", "user-a", "assistant-a").model_copy(
                update={"last_entity_id": "light.edgehome_living_room_main", "last_action": "set_light_brightness"}
            )
        )

        restarted_store = ConversationStateStore(FakeHass(tmp_path), database_path)
        restored = await restarted_store.async_get("session-a", "user-a", "assistant-a")

        assert restored is not None
        assert restored.last_entity_id == "light.edgehome_living_room_main"

    asyncio.run(exercise())
