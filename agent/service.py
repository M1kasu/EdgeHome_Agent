from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from agent.executor import PlanExecutor
from agent.plan_validator import PlanValidator
from agent.planner import ScenePlanner
from agent.replanner import Replanner
from agent.verifier import ResultVerifier
from app.config import Settings, settings
from dialogue.session_manager import SessionManager
from energy.analyzer import EnergyAnalyzer
from home.client import HomeAssistantClient, SimulatedHomeClient
from home.entity_registry import EntityRegistry
from knowledge.repository import KnowledgeRepository
from safety.action_guard import ActionGuard
from tools.registry import ToolRegistry
from understanding.complexity_router import ComplexityRouter
from understanding.intent_classifier import IntentClassifier
from understanding.slot_extractor import SlotExtractor


class EdgeHomeAgent:
    def __init__(self, config: Settings = settings, *, data_dir: Path | None = None) -> None:
        if data_dir is not None:
            config = replace(config, data_dir=data_dir)
        config.ensure_data_dir()
        self.config = config
        self.registry = EntityRegistry()
        self.home = (
            SimulatedHomeClient(self.registry)
            if config.simulation or not (config.home_assistant_url and config.home_assistant_token)
            else HomeAssistantClient(self.registry, config.home_assistant_url, config.home_assistant_token)
        )
        self.sessions = SessionManager(config.data_dir / "edgehome.sqlite3")
        self.knowledge = KnowledgeRepository(config.data_dir / "knowledge.sqlite3")
        self.energy = EnergyAnalyzer()
        self.extractor = SlotExtractor(self.registry)
        self.classifier = IntentClassifier()
        self.router = ComplexityRouter()
        self.planner = ScenePlanner(self.registry, self.home)
        self.tools = ToolRegistry()
        self.guard = ActionGuard(self.registry, config.protected_entities)
        self.validator = PlanValidator(self.tools, self.guard, config.max_plan_steps)
        self.executor = PlanExecutor(self.home, self.tools, self.guard, ResultVerifier(self.home), Replanner(), self.sessions)

    def chat(self, session_id: str, text: str, *, confirm: bool = False) -> dict[str, Any]:
        text = text.strip()
        if not text:
            return {"reply": "请输入要控制、查询或分析的家庭需求。", "need_confirmation": False, "execution_results": []}
        memory = self.sessions.get(session_id)
        if text in {"确认", "确认执行", "好的，确认", "是"} and memory.get("pending_confirmation"):
            confirm = True
        if confirm and memory.get("pending_confirmation"):
            plan_payload = memory.pop("pending_confirmation")
            # A confirmation is only replayed from an internally saved structured plan.
            from agent.plan_schema import PlanStep, TaskPlan
            plan = TaskPlan(plan_payload["goal"], [PlanStep(**step) for step in plan_payload["steps"]], need_confirmation=False)
            return self._execute_and_remember(session_id, memory, plan, confirm=True)

        slots = self.extractor.extract(text, memory)
        slots["raw_text"] = text
        intent = self.classifier.classify(text, slots, memory)
        if intent.intent == "query_energy":
            data = self.energy.analyze()
            self._save_memory(session_id, memory, intent.intent, slots)
            top = data["top_devices"][0] if data["top_devices"] else None
            reply = f"最近一周总用电 {data['total_kwh']} kWh，较上一周期变化 {data['change_percent']}%。"
            if top:
                reply += f"主要耗电设备是 {top['device']}（{top['kwh']} kWh）。"
            return {"reply": reply, "goal": "家庭能耗分析", "execution_results": [], "need_confirmation": False, "data": data}
        if intent.intent == "query_manual":
            hits = self.knowledge.search(text)
            self._save_memory(session_id, memory, intent.intent, slots)
            if not hits:
                return {"reply": "本地说明书中没有找到与该问题匹配的内容，因此不会猜测故障含义。", "goal": "本地知识检索", "execution_results": [], "need_confirmation": False, "data": {"hits": []}}
            return {
                "reply": f"已从本地资料找到 {hits[0].title}：{hits[0].excerpt}",
                "goal": "本地知识检索",
                "execution_results": [],
                "need_confirmation": False,
                "data": {"hits": [hit.to_dict() for hit in hits]},
            }

        plan = self.planner.plan(intent.intent, slots, memory)
        if plan.need_clarification:
            memory["active_scene"] = slots.get("scene") or memory.get("active_scene")
            self._save_memory(session_id, memory, intent.intent, slots)
            return {
                "reply": plan.explanation or "请补充设备或约束。",
                "goal": plan.goal,
                "plan": plan.to_dict(),
                "execution_results": [],
                "need_confirmation": False,
            }
        validation = self.validator.validate(plan, confirm=confirm)
        if validation.requires_confirmation or plan.need_confirmation:
            memory["pending_confirmation"] = plan.to_dict()
            self._save_memory(session_id, memory, intent.intent, slots)
            return {
                "reply": plan.explanation or "该操作涉及受保护设备，回复“确认”后执行。",
                "goal": plan.goal,
                "plan": plan.to_dict(),
                "execution_results": [],
                "need_confirmation": True,
            }
        if not validation.valid:
            return {
                "reply": "已拦截不安全或不合法的计划：" + "；".join(validation.errors),
                "goal": plan.goal,
                "plan": plan.to_dict(),
                "execution_results": [],
                "need_confirmation": False,
            }
        return self._execute_and_remember(session_id, memory, plan, intent=intent.intent, slots=slots, confirm=confirm)

    def _execute_and_remember(self, session_id: str, memory: dict[str, Any], plan: Any, *, intent: str | None = None, slots: dict[str, Any] | None = None, confirm: bool = False) -> dict[str, Any]:
        results, notes = self.executor.execute(plan, confirm=confirm)
        successes = sum(1 for result in results if result["success"])
        if intent and slots is not None:
            self._save_memory(session_id, memory, intent, slots, plan)
        else:
            self.sessions.save(session_id, memory)
        scheduled = next((item.get("data", {}).get("task_id") for item in results if item.get("data") and item["data"].get("task_id")), None)
        reply = f"已完成 {successes}/{len(results)} 个步骤。"
        if notes:
            reply += " " + " ".join(notes)
        return {
            "reply": reply,
            "goal": plan.goal,
            "plan": plan.to_dict(),
            "execution_results": results,
            "need_confirmation": False,
            "task_id": scheduled,
        }

    def _save_memory(self, session_id: str, memory: dict[str, Any], intent: str, slots: dict[str, Any], plan: Any | None = None) -> None:
        memory["last_intent"] = intent
        memory["last_slots"] = {key: value for key, value in slots.items() if key != "raw_text"}
        memory["active_scene"] = slots.get("scene") or memory.get("active_scene")
        if plan:
            entities = [step.arguments.get("entity_id") for step in plan.steps if step.arguments.get("entity_id")]
            if entities:
                memory["last_entity_id"] = entities[-1]
        self.sessions.save(session_id, memory)

    def home_state(self, room: str | None = None) -> dict[str, Any]:
        return self.home.get_room_state(room) if room else self.home.get_home_state()

    def task(self, task_id: str) -> dict[str, Any] | None:
        return self.sessions.get_task(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        return self.sessions.cancel_task(task_id)
