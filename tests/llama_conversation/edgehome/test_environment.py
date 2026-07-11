"""Tests for task-scoped environment selection, caching, security reporting, and revalidation."""

from __future__ import annotations

from custom_components.llama_conversation.edgehome.context.environment import EnvironmentContextBuilder
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver, ExposedEntityResolver
from custom_components.llama_conversation.edgehome.execution.environment_guard import EnvironmentPlanGuard
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.safety.away_report import AwaySecurityReporter
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


def test_environment_context_selects_relevant_entities_and_invalidates_cache(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    hass.states.async_set("sensor.temperature", "24", {"friendly_name": "室温", "device_class": "temperature"})
    builder = EnvironmentContextBuilder(EntityNameResolver(hass, exposure_checker=lambda *_: True))

    first = builder.build("客厅灯和温度怎么样")
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅灯"})
    cached = builder.build("客厅灯和温度怎么样")
    builder.invalidate("light.living")
    refreshed = builder.build("客厅灯和温度怎么样")

    assert {entity.entity_id for entity in first.entities} == {"light.living", "sensor.temperature"}
    assert cached.cache_hit is True
    assert next(entity for entity in cached.entities if entity.entity_id == "light.living").state == "off"
    assert refreshed.cache_hit is False
    assert next(entity for entity in refreshed.entities if entity.entity_id == "light.living").state == "on"


def test_away_report_includes_people_and_open_or_unavailable_entries(hass) -> None:
    hass.states.async_set("person.alice", "home", {"friendly_name": "Alice"})
    hass.states.async_set("binary_sensor.front_door", "on", {"friendly_name": "前门", "device_class": "door"})
    hass.states.async_set("binary_sensor.window", "unavailable", {"friendly_name": "窗户", "device_class": "window"})
    context = EnvironmentContextBuilder(EntityNameResolver(hass, exposure_checker=lambda *_: True)).build("离家安全检查")

    report = AwaySecurityReporter().build(context)

    assert report.safe_to_leave is False
    assert [entity.entity_id for entity in report.people_home] == ["person.alice"]
    assert [entity.entity_id for entity in report.open_entries] == ["binary_sensor.front_door"]
    assert [entity.entity_id for entity in report.unavailable_entries] == ["binary_sensor.window"]


def test_environment_guard_rejects_plan_after_target_state_changes(hass) -> None:
    hass.states.async_set("light.living", "off")
    resolver = ExposedEntityResolver(hass, exposure_checker=lambda *_: True)
    validator = PlanValidator(build_default_tool_registry(), resolver)
    guard = EnvironmentPlanGuard(validator, resolver)
    plan = TaskPlan(
        goal="打开客厅灯",
        steps=[
            PlanStep(
                id="step_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.living"},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )
    baseline = guard.capture(plan)
    hass.states.async_set("light.living", "on")

    result = guard.revalidate(plan, baseline)

    assert result.accepted is False
    assert result.reason == "environment_state_changed"
