from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, examples=["session_001"])
    text: str = Field(min_length=1, max_length=500, examples=["把客厅灯调到一半"])
    confirm: bool = False


class ChatResponse(BaseModel):
    reply: str
    goal: str | None = None
    plan: dict[str, Any] | None = None
    execution_results: list[dict[str, Any]] = Field(default_factory=list)
    need_confirmation: bool = False
    task_id: str | None = None
    data: dict[str, Any] | None = None


class EnergyRequest(BaseModel):
    time_range: str = "last_7_days"
    room: str | None = None
    device: str | None = None
