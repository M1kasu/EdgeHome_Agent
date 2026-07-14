"""Shared Home Assistant verification semantics for percent-based light control."""

from __future__ import annotations

from .models import VerificationRule


def brightness_verification(entity_id: str, brightness: int, *, tolerance: float = 1.0) -> VerificationRule:
    """Build a target that matches Home Assistant's standard zero-percent behaviour."""
    if brightness == 0:
        # HA publishes an OFF command for a requested brightness of zero.  The
        # device may retain its previous non-zero brightness value while off, so
        # checking that stale attribute would turn a real OFF into a false failure.
        return VerificationRule(entity_id=entity_id, state="off")
    return VerificationRule(
        entity_id=entity_id,
        state="on",
        attributes={"brightness": round(brightness * 255 / 100)},
        tolerance=tolerance,
    )
