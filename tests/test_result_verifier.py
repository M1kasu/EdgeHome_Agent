"""第 11 阶段：状态回读允许合理误差，且拒绝未变化的结果。"""

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.execution.verifier import StateVerifier
from custom_components.llama_conversation.edgehome.models import VerificationRule


@pytest.mark.asyncio
async def test_result_verifier_checks_fresh_state_and_numeric_tolerance(hass) -> None:
    hass.states.async_set("light.edgehome_living_room_main", "on", {"brightness": 128})
    verifier = StateVerifier(ExposedEntityResolver(hass, exposure_checker=lambda *_: True))

    result = await verifier.async_verify(
        VerificationRule(
            entity_id="light.edgehome_living_room_main",
            state="on",
            attributes={"brightness": 127},
            tolerance=1,
        )
    )

    assert result.passed is True
