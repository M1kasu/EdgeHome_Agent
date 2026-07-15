"""Deterministic plan scoring and risk assessment for EdgeHome goals."""

from __future__ import annotations

from ..models import EntitySnapshot, PlanScore, RiskAssessment, RiskLevel, TaskPlan


_CONTROL_TOOLS = {
    "turn_on_device",
    "turn_off_device",
    "set_light_brightness",
    "set_temperature",
    "set_hvac_mode",
    "control_curtain",
    "activate_security_mode",
}
_PROTECTED_TOKENS = ("fridge", "refrigerator", "router", "nas", "server", "冰箱", "路由器")


class PlanEvaluator:
    """Attach a compact score and risk assessment before execution."""

    def evaluate(self, plan: TaskPlan, entities: list[EntitySnapshot]) -> TaskPlan:
        entity_map = {entity.entity_id: entity for entity in entities}
        reasons: list[str] = []
        blocked = False
        level = RiskLevel.LOW
        for step in plan.steps:
            entity_id = step.arguments.get("entity_id")
            if step.tool in _CONTROL_TOOLS and step.verify is None:
                reasons.append(f"{step.id} 缺少状态回读规则")
                level = self._max_level(level, RiskLevel.HIGH)
            if isinstance(entity_id, str):
                snapshot = entity_map.get(entity_id)
                if snapshot is not None and snapshot.state in {"unknown", "unavailable"} and step.tool in _CONTROL_TOOLS:
                    reasons.append(f"{entity_id} 当前不可用")
                    level = self._max_level(level, RiskLevel.HIGH)
                if step.tool == "turn_off_device" and self._is_protected(entity_id, snapshot):
                    reasons.append(f"{entity_id} 是受保护设备，不能直接关闭")
                    level = RiskLevel.CRITICAL
                    blocked = True
        if plan.goal_spec and plan.goal_spec.requires_confirmation:
            reasons.append("目标涉及受保护设备，需要用户确认或拒绝")
            level = self._max_level(level, RiskLevel.HIGH)
        if plan.risk_assessment is not None:
            reasons.extend(reason for reason in plan.risk_assessment.reasons if reason not in reasons)
            level = self._max_level(level, plan.risk_assessment.level)
            blocked = blocked or plan.risk_assessment.blocked
        if not reasons:
            reasons.append("计划只使用白名单工具并要求状态回读")

        score = self._score(plan, level, blocked)
        return plan.model_copy(
            update={
                "risk_assessment": RiskAssessment(level=level, reasons=reasons[:12], blocked=blocked),
                "plan_score": score,
            }
        )

    @classmethod
    def _score(cls, plan: TaskPlan, level: RiskLevel, blocked: bool) -> PlanScore:
        control_steps = [step for step in plan.steps if step.tool in _CONTROL_TOOLS]
        verified_controls = [step for step in control_steps if step.verify is not None]
        feasibility = 0 if blocked else 90 if len(verified_controls) == len(control_steps) else 60
        safety = {RiskLevel.LOW: 95, RiskLevel.MEDIUM: 75, RiskLevel.HIGH: 45, RiskLevel.CRITICAL: 0}[level]
        goal_satisfaction = 50 if plan.need_clarification else min(100, 55 + 10 * len(control_steps) + 5 * len(plan.constraints))
        comfort = 80 if plan.scene in {"movie", "sleeping", "returning"} else 60
        energy = 85
        if any(step.tool in {"set_hvac_mode", "set_temperature"} for step in control_steps):
            energy -= 10
        if any(step.tool == "turn_on_device" for step in control_steps):
            energy -= 5
        values = {
            "goal_satisfaction": max(0, min(100, goal_satisfaction)),
            "safety": max(0, min(100, safety)),
            "comfort": max(0, min(100, comfort)),
            "energy": max(0, min(100, energy)),
            "feasibility": max(0, min(100, feasibility)),
        }
        total = round(
            values["goal_satisfaction"] * 0.30
            + values["safety"] * 0.30
            + values["feasibility"] * 0.20
            + values["comfort"] * 0.10
            + values["energy"] * 0.10
        )
        return PlanScore(**values, total=max(0, min(100, total)))

    @staticmethod
    def _is_protected(entity_id: str, snapshot: EntitySnapshot | None) -> bool:
        name = (snapshot.friendly_name if snapshot is not None else "") or ""
        haystack = f"{entity_id.lower()} {name.lower()}"
        return any(token in haystack for token in _PROTECTED_TOKENS)

    @staticmethod
    def _max_level(left: RiskLevel, right: RiskLevel) -> RiskLevel:
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
        return right if order[right] > order[left] else left
