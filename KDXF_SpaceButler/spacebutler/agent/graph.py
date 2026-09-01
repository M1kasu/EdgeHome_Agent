"""LangGraph orchestration for the state-aware SpaceButler agent.

The graph only coordinates stages. Semantic decisions remain model outputs,
device choices are grounded in the live capability registry, and every write is
verified from a fresh Home Assistant observation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import operator
from typing import Annotated, Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from spacebutler.agent.models import ActionRecord, CompiledGoal, FreshObservation, RoomState
from spacebutler.agent.planner import PlanningOutcome


class AgentGraphState(TypedDict, total=False):
    text: str
    background: bool
    devices: list[dict[str, object]]
    room_state: RoomState
    registry: dict[str, object]
    goal: CompiledGoal | None
    compile_mode: str
    semantic: dict[str, object] | None
    accepted: bool
    outcome: PlanningOutcome
    records: list[ActionRecord]
    observation: FreshObservation
    failures: int
    reply: str
    trace: Annotated[list[str], operator.add]


GraphNode = Callable[[AgentGraphState], dict[str, Any]]


@dataclass(frozen=True)
class AgentGraphHandlers:
    observe: GraphNode
    understand: GraphNode
    activate: GraphNode
    plan: GraphNode
    execute: GraphNode
    verify: GraphNode
    respond: GraphNode


class LangGraphAgentWorkflow:
    """Compiled observe → understand → plan → act → verify workflow."""

    def __init__(
        self,
        handlers: AgentGraphHandlers,
        on_stage: Callable[[str], None] | None = None,
    ) -> None:
        self._on_stage = on_stage
        builder = StateGraph(AgentGraphState)
        builder.add_node("observe", self._traced("observe", handlers.observe))
        builder.add_node("understand", self._traced("understand", handlers.understand))
        builder.add_node("activate", self._traced("activate", handlers.activate))
        builder.add_node("plan", self._traced("plan", handlers.plan))
        builder.add_node("execute", self._traced("execute", handlers.execute))
        builder.add_node("verify", self._traced("verify", handlers.verify))
        builder.add_node("respond", self._traced("respond", handlers.respond))
        builder.add_edge(START, "observe")
        builder.add_conditional_edges(
            "observe",
            lambda state: "plan" if state.get("goal") is not None else "understand",
            {"plan": "plan", "understand": "understand"},
        )
        builder.add_conditional_edges(
            "understand",
            lambda state: "activate" if state.get("goal") is not None else "respond",
            {"activate": "activate", "respond": "respond"},
        )
        builder.add_edge("activate", "plan")
        builder.add_conditional_edges(
            "plan",
            lambda state: "respond" if state["outcome"].no_action else "execute",
            {"respond": "respond", "execute": "execute"},
        )
        builder.add_edge("execute", "verify")
        builder.add_edge("verify", "respond")
        builder.add_edge("respond", END)
        self._graph = builder.compile()

    def _traced(self, name: str, node: GraphNode) -> GraphNode:
        def invoke(state: AgentGraphState) -> dict[str, Any]:
            # 阶段开始时先播报：UI 要在这一步真正耗时之前就显示"正在做什么"。
            if self._on_stage is not None:
                self._on_stage(name)
            return {**node(state), "trace": [name]}

        return invoke

    def invoke(self, state: AgentGraphState) -> AgentGraphState:
        return self._graph.invoke(state)

    @property
    def node_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self._graph.get_graph().nodes
            if name not in {START, END, "__start__", "__end__"}
        )
