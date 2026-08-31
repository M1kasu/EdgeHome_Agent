"""Read server settings from the project .env without executing or logging it."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def project_environment(path: Path | None = None) -> dict[str, str]:
    """Process values override .env, including explicit empty values.

    An absolute default path prevents accidentally loading another app's .env
    when invoked from the repository root or a different working directory.
    Interpolation is disabled so '$' and '${...}' in credentials remain literal.
    """
    values = dotenv_values(path if path is not None else ENV_FILE, encoding="utf-8-sig", interpolate=False)
    return {**{key: value for key, value in values.items() if value is not None}, **os.environ}


def load_project_environment(path: Path | None = None) -> None:
    """Expose project configuration to legacy helpers that use os.environ."""
    for key, value in project_environment(path).items():
        if key.startswith(("SPACEBUTLER_", "ESPHOME_", "EDGE_LLM_")) or key == "HA_TOKEN":
            os.environ.setdefault(key, value)
