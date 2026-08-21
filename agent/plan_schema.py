from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    id: str
    tool: str
    arguments: dict[str, Any]
    verify: dict[str, Any] = field(default_factory=dict)
    optional: bool = False
    on_failure: str = "retry_then_report"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "arguments": self.arguments,
            "verify": self.verify,
            "optional": self.optional,
            "on_failure": self.on_failure,
        }


@dataclass
class TaskPlan:
    goal: str
    steps: list[PlanStep]
    need_clarification: bool = False
    need_confirmation: bool = False
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "need_clarification": self.need_clarification,
            "need_confirmation": self.need_confirmation,
            "explanation": self.explanation,
            "steps": [step.to_dict() for step in self.steps],
        }
