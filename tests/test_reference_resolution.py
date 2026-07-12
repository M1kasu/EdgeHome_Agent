"""Pronouns and relative brightness use the current verified HA state."""

from custom_components.llama_conversation.edgehome.context.models import ConversationState
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.understanding.reference_resolver import ReferencePlanResolver
from tests.context_fakes import FakeResolver


def test_relative_brightness_reads_current_state_and_turn_off_uses_last_entity() -> None:
    plan = TaskPlan(
        goal="\u8bbe\u4e3a50%",
        steps=[
            PlanStep(
                id="step_1",
                tool="set_light_brightness",
                arguments={"entity_id": "light.living", "brightness": 20},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )
    state = ConversationState(session_id="c" * 64, conversation_id="session-a", last_plan=plan, last_entity_id="light.living")
    resolver = ReferencePlanResolver(FakeResolver(brightness=128))

    dimmed = resolver.resolve("\u518d\u6697\u4e00\u70b9", state)
    turned_off = resolver.resolve("\u5173\u6389\u5427", state)

    assert dimmed is not None
    assert dimmed.steps[0].arguments["brightness"] == 40
    assert turned_off is not None
    assert turned_off.steps[0].tool == "turn_off_device"
    assert turned_off.steps[0].arguments["entity_id"] == "light.living"
