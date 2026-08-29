"""Verification: compare expected vs observed, and prediction vs reality."""

from __future__ import annotations

from spacebutler.agent.models import ActionRecord, PredictedState, RoomState


def action_matches(observed_state: dict[str, object] | None, expected: dict[str, object]) -> bool:
    """Mirror workbench._control_matches semantics on HA entity state."""
    if not isinstance(observed_state, dict):
        return False
    if "state" in expected and str(observed_state.get("state", "")).lower() != str(expected["state"]).lower():
        return False
    attributes = observed_state.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    if "brightness" in expected:
        brightness = attributes.get("brightness")
        if not isinstance(brightness, (int, float)):
            return False
        percent = round(float(brightness) * 100 / 255)
        if abs(percent - int(expected["brightness"])) > 1:
            return False
    if "position" in expected:
        position = attributes.get("current_position")
        if not isinstance(position, (int, float)) or round(float(position)) != int(expected["position"]):
            return False
    if "temperature" in expected:
        temperature = attributes.get("temperature")
        if not isinstance(temperature, (int, float)) or abs(float(temperature) - float(expected["temperature"])) > 0.1:
            return False
    return True


class Verifier:
    def verify_actions(
        self,
        records: list[ActionRecord],
        ha_state_reader,
    ) -> int:
        """Re-read HA state for each action record. Returns failure count."""
        failures = 0
        for record in records:
            try:
                observed = ha_state_reader(record.entity_id)
            except (RuntimeError, OSError):
                observed = None
            record.observed = observed if isinstance(observed, dict) else None
            record.verified = action_matches(record.observed, record.expected)
            if not record.verified:
                failures += 1
        return failures

    def prediction_error(
        self,
        predicted: PredictedState,
        state: RoomState,
    ) -> float | None:
        if predicted.temperature is None or state.temperature is None:
            return None
        return round(abs(predicted.temperature - state.temperature), 2)
