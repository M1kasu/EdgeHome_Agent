from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Secrets remain in this server-side object only."""

    data_dir: Path = field(default_factory=lambda: Path(os.getenv("EDGEHOME_DATA_DIR", "data")))
    simulation: bool = os.getenv("EDGEHOME_SIMULATION", "true").lower() == "true"
    home_assistant_url: str | None = os.getenv("HOME_ASSISTANT_URL")
    home_assistant_token: str | None = os.getenv("HOME_ASSISTANT_TOKEN")
    llm_url: str | None = os.getenv("EDGEHOME_LLM_URL")
    max_plan_steps: int = int(os.getenv("EDGEHOME_MAX_PLAN_STEPS", "12"))
    session_timeout_minutes: int = int(os.getenv("EDGEHOME_SESSION_TIMEOUT_MINUTES", "120"))
    proactive_service: bool = os.getenv("EDGEHOME_PROACTIVE_SERVICE", "false").lower() == "true"
    protected_entities: tuple[str, ...] = (
        "switch.refrigerator",
        "switch.router",
        "switch.home_server",
        "switch.nas",
        "vacuum.robot",
    )

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
