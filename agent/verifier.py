from __future__ import annotations

from typing import Any

from home.client import SimulatedHomeClient


class ResultVerifier:
    def __init__(self, home: SimulatedHomeClient) -> None:
        self.home = home

    def verify(self, entity_id: str | None, expected: dict[str, Any]) -> tuple[bool, str | None]:
        if not expected or not entity_id:
            return True, None
        state = self.home.get_state(entity_id)
        if state is None:
            return False, "entity_not_found"
        if not state["online"]:
            return False, "device_offline"
        if "state" in expected and state["state"] != expected["state"]:
            return False, f"expected_state:{expected['state']} actual:{state['state']}"
        for field in ("brightness", "temperature", "position"):
            if field in expected and state["attributes"].get(field) != expected[field]:
                return False, f"expected_{field}:{expected[field]} actual:{state['attributes'].get(field)}"
        return True, None
