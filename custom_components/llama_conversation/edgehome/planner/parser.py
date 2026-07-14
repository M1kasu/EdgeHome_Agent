"""Parse and validate untrusted structured planner output before execution."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..errors import PlanValidationError
from ..models import TaskPlan
from ..plan_validator import PlanValidator


class PlanParseStatus(StrEnum):
    """Why a planner response is or is not safe to pass to the executor."""

    ACCEPTED = "accepted"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_PLAN = "invalid_plan"


class ParsedPlan(BaseModel):
    """A structured parse result with no execution side effect."""

    model_config = ConfigDict(extra="forbid")

    status: PlanParseStatus
    plan: TaskPlan | None = None
    reason: str | None = None


class StructuredPlanParser:
    """Accept a model response only when JSON, Pydantic, and deterministic checks agree."""

    def __init__(self, validator: PlanValidator) -> None:
        self._validator = validator

    def parse(self, payload: str | dict[str, Any]) -> ParsedPlan:
        """Return a validated TaskPlan or an auditable rejection reason."""
        raw = self._decode(payload)
        if raw is None:
            return ParsedPlan(status=PlanParseStatus.INVALID_JSON, reason="规划器输出不是 JSON 对象")
        try:
            plan = TaskPlan.model_validate(raw)
        except ValueError as error:
            return ParsedPlan(status=PlanParseStatus.INVALID_SCHEMA, reason=str(error))
        try:
            return ParsedPlan(status=PlanParseStatus.ACCEPTED, plan=self._validator.validate(plan))
        except PlanValidationError as error:
            details = "; ".join(
                f"{issue.code}: {issue.message}" for issue in error.issues[:4]
            )
            return ParsedPlan(status=PlanParseStatus.INVALID_PLAN, reason=details)

    @staticmethod
    def _decode(payload: str | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            return payload
        normalized = payload.strip()
        try:
            decoded = json.loads(normalized)
        except json.JSONDecodeError:
            # Small edge models occasionally add a short acknowledgement or a
            # Markdown fence despite the JSON-only instruction.  Extract one
            # complete object, then still apply the same strict schema and
            # plan validator.  This is a parser recovery, never execution of
            # free-form text or a model tool call.
            start = normalized.find("{")
            if start < 0:
                return None
            try:
                decoded, _ = json.JSONDecoder().raw_decode(normalized[start:])
            except json.JSONDecodeError:
                return None
        return decoded if isinstance(decoded, dict) else None
