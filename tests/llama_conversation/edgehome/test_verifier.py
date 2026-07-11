"""Tests for post-action state verification."""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.execution.verifier import StateVerifier
from custom_components.llama_conversation.edgehome.models import VerificationRule


@pytest.mark.asyncio
async def test_verifier_supports_numeric_attribute_tolerance(hass) -> None:
    hass.states.async_set("climate.demo", "cool", {"temperature": 25.4})
    verifier = StateVerifier(ExposedEntityResolver(hass, exposure_checker=lambda *_: True))

    result = await verifier.async_verify(
        VerificationRule(entity_id="climate.demo", state="cool", attributes={"temperature": 25.0}, tolerance=0.5)
    )

    assert result.passed is True


@pytest.mark.asyncio
async def test_verifier_reports_missing_or_mismatched_state(hass) -> None:
    hass.states.async_set("light.demo", "off", {"brightness": 10})
    verifier = StateVerifier(ExposedEntityResolver(hass, exposure_checker=lambda *_: True))

    result = await verifier.async_verify(VerificationRule(entity_id="light.demo", state="on"))

    assert result.passed is False
    assert result.reason is not None
    assert "状态不匹配" in result.reason
