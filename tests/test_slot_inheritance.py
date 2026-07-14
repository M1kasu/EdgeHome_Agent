"""Slot inheritance remains limited to a unique compatible device."""

from custom_components.llama_conversation.edgehome.context.models import ConversationState
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.understanding.reference_resolver import ReferencePlanResolver
from tests.context_fakes import FakeResolver


def test_same_as_last_inherits_brightness_but_switches_to_unique_bedroom_light() -> None:
    plan = TaskPlan(
        goal="\u628a\u5ba2\u5385\u706f\u8c03\u523050%",
        steps=[
            PlanStep(
                id="step_1",
                tool="set_light_brightness",
                arguments={"entity_id": "light.living", "brightness": 50},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )
    state = ConversationState(
        session_id="b" * 64,
        conversation_id="session-a",
        last_plan=plan,
        last_entity_id="light.living",
        last_action="set_light_brightness",
        last_parameters={"brightness": 50},
    )

    resolved = ReferencePlanResolver(FakeResolver()).resolve(
        "\u5367\u5ba4\u7684\u4e5f\u4e00\u6837",
        state,
    )

    assert resolved is not None
    assert resolved.steps[0].arguments == {"entity_id": "light.bedroom", "brightness": 50}
