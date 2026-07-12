"""Minimal Home Assistant-shaped fakes for standalone M1.5 storage tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from custom_components.llama_conversation.edgehome.models import EntitySnapshot


class FakeHass:
    """Supply only the async executor and config path used by the SQLite store."""

    def __init__(self, root: Path) -> None:
        self.config = SimpleNamespace(path=lambda name: str(root / name))

    async def async_add_executor_job(self, target):
        return await asyncio.get_running_loop().run_in_executor(None, target)


class FakeResolver:
    """Provide deterministic state lookup and a unique bedroom target for references."""

    def __init__(self, brightness: int = 128) -> None:
        self.hass = SimpleNamespace(
            states=SimpleNamespace(
                get=lambda entity_id: SimpleNamespace(
                    domain=entity_id.split(".", maxsplit=1)[0],
                    attributes={"brightness": brightness},
                )
                if entity_id == "light.living"
                else None
            )
        )

    def resolve_name(self, _text: str, _domains):
        return SimpleNamespace(
            snapshot=EntitySnapshot(entity_id="light.bedroom", domain="light", state="off", attributes={})
        )

    def snapshot(self, entity_id: str):
        return EntitySnapshot(
            entity_id=entity_id,
            domain=entity_id.split(".", maxsplit=1)[0],
            state="on",
            attributes={"brightness": self.hass.states.get(entity_id).attributes["brightness"]},
            friendly_name="客厅主灯",
        )
