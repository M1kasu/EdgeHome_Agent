from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from agent.plan_schema import TaskPlan
from agent.replanner import Replanner
from agent.verifier import ResultVerifier
from dialogue.session_manager import SessionManager
from home.client import SimulatedHomeClient
from safety.action_guard import ActionGuard
from tools.registry import ToolRegistry


class PlanExecutor:
    def __init__(
        self,
        home: SimulatedHomeClient,
        tools: ToolRegistry,
        guard: ActionGuard,
        verifier: ResultVerifier,
        replanner: Replanner,
        sessions: SessionManager,
    ) -> None:
        self.home, self.tools, self.guard, self.verifier, self.replanner, self.sessions = home, tools, guard, verifier, replanner, sessions

    def execute(self, plan: TaskPlan, *, confirm: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
        results: list[dict[str, Any]] = []
        notes: list[str] = []
        for step in plan.steps:
            result = self._run_step(step, confirm=confirm)
            results.append(result)
            if not result["success"]:
                alternative = self.replanner.alternative(step, result.get("error"))
                if alternative:
                    fallback = self._run_step(alternative, confirm=confirm)
                    fallback["replanned_from"] = step.id
                    results.append(fallback)
                    notes.append(f"{step.id} 失败，已尝试替代设备。")
                elif step.optional:
                    notes.append(f"可选步骤 {step.id} 未完成：{result.get('error')}。")
                else:
                    notes.append(f"步骤 {step.id} 未完成：{result.get('error')}。")
        return results, notes

    def _run_step(self, step: Any, *, confirm: bool) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        if self.tools.get(step.tool) is None:
            return self._result(step, False, "unregistered_tool", started)
        guard = self.guard.check(step.tool, step.arguments, confirm=confirm)
        if not guard.allowed:
            return self._result(step, False, "; ".join(guard.errors), started, requires_confirmation=guard.requires_confirmation)
        if step.tool == "create_delayed_task":
            task_id = str(uuid4())
            due_at = datetime.now(timezone.utc) + timedelta(minutes=step.arguments["delay_minutes"])
            task = {
                "task_id": task_id,
                "status": "scheduled",
                "source": "agent_chat",
                "action": step.arguments["action"],
                "entity_id": step.arguments["entity_id"],
                "due_at": due_at.isoformat(),
                "completed_steps": [],
                "pending_steps": [step.to_dict()],
                "replan_count": 0,
            }
            self.sessions.save_task(task)
            return self._result(step, True, None, started, data={"task_id": task_id, "due_at": due_at.isoformat()})
        response = self.home.execute(step.tool, step.arguments)
        if not response.get("ok"):
            return self._result(step, False, response.get("error", "execution_failed"), started)
        entity_id = step.arguments.get("entity_id")
        verified, error = self.verifier.verify(entity_id, step.verify)
        return self._result(step, verified, error, started, data=response.get("data"), verified=verified)

    @staticmethod
    def _result(step: Any, success: bool, error: str | None, started: datetime, *, data: Any = None, verified: bool = False, requires_confirmation: bool = False) -> dict[str, Any]:
        return {
            "step_id": step.id,
            "tool": step.tool,
            "success": success,
            "verified": verified,
            "error": error,
            "requires_confirmation": requires_confirmation,
            "data": data,
            "latency_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
        }
