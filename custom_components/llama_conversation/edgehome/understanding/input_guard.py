"""Production input-size policy applied before routing or planning."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InputLengthPolicy(BaseModel):
    """Keep input limits configurable without coupling them to model context size."""

    model_config = ConfigDict(extra="forbid")

    max_characters: int = Field(default=2_000, ge=64, le=32_768)


class InputLengthGuard:
    """Reject empty or unbounded user input before any routing work begins."""

    def __init__(self, policy: InputLengthPolicy | None = None) -> None:
        self.policy = policy or InputLengthPolicy()

    def accepts(self, text: str) -> bool:
        return bool(text.strip()) and len(text) <= self.policy.max_characters
