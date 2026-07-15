"""Pydantic models shared by planning, validation, execution, and reports."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OnFailurePolicy(StrEnum):
    """The only failure policies a fixed plan is allowed to request."""

    STOP = "stop"
    SKIP = "skip"
    REPLAN = "replan"


class StepStatus(StrEnum):
    """Terminal status for one attempted plan step."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStatus(StrEnum):
    """Terminal status for a fixed plan execution."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    NEEDS_REPLAN = "needs_replan"


class RiskLevel(StrEnum):
    """Risk level used by planning, UI evidence, and acceptance reports."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GoalType(StrEnum):
    """A structured goal family inferred before device planning."""

    DEVICE_CONTROL = "device_control"
    SCENE = "scene"
    SAFETY = "safety"
    ENERGY = "energy"
    QUERY = "query"
    SCHEDULE = "schedule"
    UNKNOWN = "unknown"


class ConstraintType(StrEnum):
    """Machine-checkable constraint category."""

    PROHIBITION = "prohibition"
    MIN_VALUE = "min_value"
    MAX_VALUE = "max_value"
    RANGE = "range"
    REQUIREMENT = "requirement"
    PREFERENCE = "preference"
    SAFETY = "safety"


class StateCondition(BaseModel):
    """A state that must match before a step may run."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=3)
    state: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    tolerance: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def has_expected_value(self) -> "StateCondition":
        if self.state is None and not self.attributes:
            raise ValueError("状态条件至少需要 state 或 attributes")
        return self


class VerificationRule(StateCondition):
    """A state to verify after a Home Assistant service call."""

    settle_seconds: float = Field(default=0.0, ge=0.0, le=10.0)


class PlanConstraint(BaseModel):
    """A user, safety, or environment constraint made explicit by the planner."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=160)
    source: str = Field(default="user", pattern=r"^(user|safety|environment)$")
    hard: bool = True
    constraint_type: ConstraintType | None = None
    scope: str | None = Field(default=None, max_length=64)
    target: str | None = Field(default=None, max_length=128)
    operator: str | None = Field(default=None, max_length=32)
    value: str | int | float | bool | None = None
    priority: int = Field(default=50, ge=0, le=100)
    conflict_policy: str = Field(default="clarify", pattern=r"^(clarify|reject|prefer_user|prefer_safety)$")


class GoalConstraint(PlanConstraint):
    """A typed constraint attached to the user goal rather than one plan."""


class GoalPreference(BaseModel):
    """Soft preference extracted from the user or environment."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=160)
    target: str | None = Field(default=None, max_length=128)
    weight: int = Field(default=50, ge=0, le=100)


class GoalSpec(BaseModel):
    """The auditable target that the planner must satisfy before building steps."""

    model_config = ConfigDict(extra="forbid")

    raw_goal: str = Field(min_length=1, max_length=300)
    goal_type: GoalType = GoalType.UNKNOWN
    scene: str | None = Field(default=None, max_length=64)
    target_rooms: list[str] = Field(default_factory=list, max_length=8)
    target_devices: list[str] = Field(default_factory=list, max_length=16)
    desired_outcomes: list[str] = Field(default_factory=list, max_length=12)
    constraints: list[GoalConstraint] = Field(default_factory=list, max_length=16)
    preferences: list[GoalPreference] = Field(default_factory=list, max_length=8)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=16)
    protected_entities: list[str] = Field(default_factory=list, max_length=8)
    risk_tolerance: RiskLevel = RiskLevel.MEDIUM
    requires_confirmation: bool = False


class RiskAssessment(BaseModel):
    """Deterministic safety summary for a candidate plan."""

    model_config = ConfigDict(extra="forbid")

    level: RiskLevel = RiskLevel.LOW
    reasons: list[str] = Field(default_factory=list, max_length=12)
    blocked: bool = False


class PlanScore(BaseModel):
    """Compact score shown in UI and acceptance evidence."""

    model_config = ConfigDict(extra="forbid")

    goal_satisfaction: int = Field(default=0, ge=0, le=100)
    safety: int = Field(default=0, ge=0, le=100)
    comfort: int = Field(default=0, ge=0, le=100)
    energy: int = Field(default=0, ge=0, le=100)
    feasibility: int = Field(default=0, ge=0, le=100)
    total: int = Field(default=0, ge=0, le=100)


class PlanStep(BaseModel):
    """A single, registered and auditable operation in a fixed plan."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Plans execute in topological order; this preserves the generated task graph
    # and lets the UI show which state read or action enabled each next step.
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    preconditions: list[StateCondition] = Field(default_factory=list)
    verify: VerificationRule | None = None
    on_failure: OnFailurePolicy = OnFailurePolicy.STOP
    optional: bool = False
    reason: str | None = Field(default=None, max_length=200)
    expected_outcome: str | None = Field(default=None, max_length=200)
    constraint_refs: list[str] = Field(default_factory=list, max_length=8)
    risk_level: RiskLevel = RiskLevel.LOW
    evidence_required: list[str] = Field(default_factory=list, max_length=8)


class TaskPlan(BaseModel):
    """The only plan shape accepted by the fixed-plan executor."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=200)
    scene: str | None = Field(default=None, max_length=64)
    need_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    rationale: str | None = Field(default=None, max_length=240)
    constraints: list[PlanConstraint] = Field(default_factory=list, max_length=8)
    goal_spec: GoalSpec | None = None
    risk_assessment: RiskAssessment | None = None
    plan_score: PlanScore | None = None
    steps: list[PlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def clarification_requires_question(self) -> "TaskPlan":
        if self.need_clarification and not self.clarification_question:
            raise ValueError("需要澄清时必须提供 clarification_question")
        return self


class EntitySnapshot(BaseModel):
    """A minimal state snapshot used for validation and verification."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    domain: str
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    friendly_name: str | None = None
    area_id: str | None = None
    area_name: str | None = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VerificationResult(BaseModel):
    """Result of re-reading an entity after an action."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason: str | None = None
    snapshot: EntitySnapshot | None = None
    timed_out: bool = False


class StepExecutionResult(BaseModel):
    """Structured outcome of one tool step, including every retry attempt."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    status: StepStatus
    attempts: int = Field(ge=0)
    error_code: str | None = None
    error_message: str | None = None
    state_before: EntitySnapshot | None = None
    state_after: EntitySnapshot | None = None
    verification: VerificationResult | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskExecutionReport(BaseModel):
    """A complete fixed-plan report suitable for a future debug panel."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    status: TaskStatus
    step_results: list[StepExecutionResult] = Field(default_factory=list)
    needs_replan: bool = False
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
