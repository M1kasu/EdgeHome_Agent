"""Tests for bounded dialogue persistence and Chinese reference plan construction."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.context.dialogue_state import DialogueState
from custom_components.llama_conversation.edgehome.context.store import DialogueStateStore
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.understanding.reference_resolver import ReferencePlanResolver


def _brightness_plan(entity_id: str = "light.living") -> TaskPlan:
    return TaskPlan(
        goal="客厅灯调到 50%",
        steps=[
            PlanStep(
                id="step_1",
                tool="set_light_brightness",
                arguments={"entity_id": entity_id, "brightness": 50},
                verify=VerificationRule(entity_id=entity_id, state="on", attributes={"brightness": 128}, tolerance=1.0),
            )
        ],
    )


@pytest.mark.asyncio
async def test_dialogue_store_persists_plan_and_consumes_clarification(hass) -> None:
    store = DialogueStateStore(hass)

    state = await store.async_save(
        store.new_state("conversation-1").model_copy(
            update={
                "last_plan": _brightness_plan(),
                "last_entity_id": "light.living",
                "last_parameters": {"entity_id": "light.living", "brightness": 50},
            }
        )
    )
    await store.async_set_clarification("conversation-1", "打开灯", "请说明设备名称")
    loaded = await store.async_get("conversation-1")
    pending = await store.async_consume_clarification("conversation-1")

    assert state.last_entity_id == "light.living"
    assert loaded is not None
    assert loaded.pending_clarification is not None
    assert pending is not None
    assert pending.question == "请说明设备名称"
    cleared_state = await store.async_get("conversation-1")
    assert cleared_state is not None
    assert cleared_state.pending_clarification is None


def test_reference_resolver_copies_target_and_inherits_brightness_slot(hass) -> None:
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅灯"})
    hass.states.async_set("light.bedroom", "off", {"friendly_name": "卧室灯"})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    state = DialogueState(
        session_id="a" * 64,
        conversation_id="conversation-1",
        last_plan=_brightness_plan(),
        last_entity_id="light.living",
    )
    reference_resolver = ReferencePlanResolver(resolver)

    copied = reference_resolver.resolve("卧室的也一样", state)
    brighter = reference_resolver.resolve("它再调亮一点", state)

    assert copied is not None
    assert copied.steps[0].arguments["entity_id"] == "light.bedroom"
    assert copied.steps[0].verify is not None
    assert copied.steps[0].verify.entity_id == "light.bedroom"
    assert brighter is not None
    assert brighter.steps[0].arguments["brightness"] == 60
