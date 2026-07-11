"""Domain errors returned by the EdgeHome execution boundary."""

from __future__ import annotations

from dataclasses import dataclass


class EdgeHomeError(Exception):
    """Base error with a stable, user-safe error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EntityResolutionError(EdgeHomeError):
    """Raised when a plan references an unavailable or unexposed entity."""


class ToolValidationError(EdgeHomeError):
    """Raised when a registered tool receives invalid arguments."""


@dataclass(frozen=True, slots=True)
class PlanValidationIssue:
    """A single actionable reason why a plan cannot be executed."""

    step_id: str
    code: str
    message: str


class PlanValidationError(EdgeHomeError):
    """Raised when a fixed plan fails deterministic validation."""

    def __init__(self, issues: list[PlanValidationIssue]) -> None:
        message = "; ".join(f"{issue.step_id}: {issue.message}" for issue in issues)
        super().__init__("plan_validation_failed", message)
        self.issues = tuple(issues)
