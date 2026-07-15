"""Tests for model-backed planning with no model tool or service permissions."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.llama_conversation.edgehome.context.home_state import TaskHomeStateBuilder
from custom_components.llama_conversation.edgehome.context.environment import EnvironmentContextBuilder
from custom_components.llama_conversation.edgehome.entity_registry import build_demo_entity_registry
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.entity import TextGenerationResult
from custom_components.llama_conversation.const import CONF_MAX_TOKENS
from custom_components.llama_conversation.edgehome.plan_validator import PlanValidator
from custom_components.llama_conversation.edgehome.planner.llm_planner import LLMTaskPlanner, PlanningRequest, PlanningSource, PlanningStatus
from custom_components.llama_conversation.edgehome.planner.parser import StructuredPlanParser
from custom_components.llama_conversation.edgehome.tools.registry import build_default_tool_registry


class DummyPlanningClient:
    """Minimal LocalLLMClient-compatible fake that captures the isolated planning call."""

    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.messages: list[Any] = []
        self.options: dict[str, Any] = {}

    async def _generate(self, messages, _llm_api, _agent_id, options):
        self.messages = messages
        self.options = options
        if self.error:
            raise self.error
        return TextGenerationResult(response=self.response)


class RepairingPlanningClient(DummyPlanningClient):
    """Return a rejected draft followed by a repaired plan, like a constrained edge model."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.calls: list[list[Any]] = []

    async def _generate(self, messages, _llm_api, _agent_id, options):
        self.messages = messages
        self.options = options
        self.calls.append(messages)
        return TextGenerationResult(response=self._responses.pop(0))


def _planner(hass, client: DummyPlanningClient) -> LLMTaskPlanner:
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    tools = build_default_tool_registry()
    return LLMTaskPlanner(
        client,  # type: ignore[arg-type]
        TaskHomeStateBuilder(resolver),
        StructuredPlanParser(PlanValidator(tools, resolver)),
        tools,
    )


@pytest.mark.asyncio
async def test_llm_planner_accepts_valid_json_but_exposes_only_relevant_snapshot(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    hass.states.async_set("switch.bedroom", "off", {"friendly_name": "卧室开关"})
    response = json.dumps(
        {
            "goal": "打开客厅灯",
            "rationale": "客厅灯是用户明确指定的设备。",
            "constraints": [{"statement": "仅控制客厅灯", "source": "user", "hard": True}],
            "steps": [
                {
                    "id": "read_living",
                    "action": "read_device",
                    "entity_id": "light.living",
                    "depends_on": [],
                },
                {
                    "id": "turn_on_living",
                    "action": "turn_on",
                    "entity_id": "light.living",
                    "depends_on": ["read_living"],
                },
            ],
        }
    )
    client = DummyPlanningClient(response=response)

    result = await _planner(hass, client).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})

    assert result.status == PlanningStatus.ACCEPTED
    assert result.plan is not None
    prompt = client.messages[0].content
    assert "light.living" in prompt
    assert "switch.bedroom" not in prompt
    assert "set_curtain_position" in prompt
    assert result.plan.steps[0].tool == "get_device_state"
    assert result.plan.steps[1].tool == "get_device_state"
    assert result.plan.steps[2].tool == "turn_on_device"
    assert result.plan.steps[2].depends_on == ["preflight_turn_on_living"]
    assert result.plan.goal_spec is not None
    assert result.plan.goal_spec.goal_type == "device_control"
    assert client.options["enable_streaming"] is False
    assert "response_json_schema" in client.options
    assert client.options["tool_call_prefix"] == "<edgehome_planning_tool_disabled>"


@pytest.mark.asyncio
async def test_llm_planner_rejects_illegal_model_plan_and_backend_failure(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    illegal = DummyPlanningClient(
        response=json.dumps({"goal": "非法", "steps": [{"id": "step_1", "tool": "shell", "arguments": {"entity_id": "light.living"}}]})
    )
    unavailable = DummyPlanningClient(error=RuntimeError("backend offline"))

    illegal_result = await _planner(hass, illegal).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})
    unavailable_result = await _planner(hass, unavailable).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})

    assert illegal_result.status == PlanningStatus.REJECTED
    assert illegal_result.reason is not None
    assert "tool_not_registered" in illegal_result.reason
    assert unavailable_result.status == PlanningStatus.UNAVAILABLE
    assert unavailable_result.reason == "RuntimeError"


@pytest.mark.asyncio
async def test_llm_planner_compiles_only_snapshot_advertised_semantic_device_keys(hass) -> None:
    hass.states.async_set("light.edgehome_living_room_main", "off", {"friendly_name": "客厅主灯"})
    client = DummyPlanningClient(
        response=json.dumps(
            {
                "goal": "读取客厅主灯后打开",
                "steps": [
                    {"id": "read_main", "action": "read_device", "entity_id": "living_room.light"},
                    {
                        "id": "turn_on_main",
                        "action": "turn_on",
                        "entity_id": "living_room.light",
                        "depends_on": ["read_main"],
                    },
                ],
            }
        )
    )

    result = await _planner(hass, client).async_plan(PlanningRequest(goal="读取客厅主灯后打开", agent_id="planner"), {})

    assert result.status == PlanningStatus.ACCEPTED
    assert result.plan is not None
    assert result.plan.steps[0].arguments["entity_id"] == "light.edgehome_living_room_main"
    assert result.plan.steps[1].arguments["entity_id"] == "light.edgehome_living_room_main"
    assert result.plan.steps[2].arguments["entity_id"] == "light.edgehome_living_room_main"
    assert "light.living_room" in client.messages[0].content


@pytest.mark.asyncio
async def test_named_scene_context_keeps_registered_capabilities_for_semantic_key_resolution(hass) -> None:
    for entity_id, state, name in (
        ("light.edgehome_living_room_main", "off", "客厅主灯"),
        ("cover.edgehome_living_room_curtain", "open", "客厅窗帘"),
        ("switch.edgehome_living_room_tv", "off", "客厅电视"),
        ("climate.edgehome_living_room_ac", "off", "客厅空调"),
    ):
        hass.states.async_set(entity_id, state, {"friendly_name": name})
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True, registry=build_demo_entity_registry())
    tools = build_default_tool_registry()
    client = DummyPlanningClient(
        response=json.dumps(
            {
                "goal": "准备观影",
                "steps": [{"id": "read_light", "action": "read_device", "entity_id": "living_room.light"}],
            }
        )
    )
    planner = LLMTaskPlanner(
        client,  # type: ignore[arg-type]
        EnvironmentContextBuilder(resolver),
        StructuredPlanParser(PlanValidator(tools, resolver)),
        tools,
    )

    result = await planner.async_plan(PlanningRequest(goal="准备观影", agent_id="planner"), {})

    assert result.status == PlanningStatus.ACCEPTED
    assert result.plan is not None
    assert result.plan.steps[0].arguments["entity_id"] == "light.edgehome_living_room_main"


@pytest.mark.asyncio
async def test_llm_planner_repairs_one_invalid_draft_without_expanding_tool_scope(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    client = RepairingPlanningClient(
        [
            json.dumps({"goal": "非法草案", "steps": [{"id": "step_1", "tool": "shell", "arguments": {"entity_id": "light.living"}}]}),
            json.dumps(
                {
                    "goal": "打开客厅灯",
                    "rationale": "用户明确要求打开客厅灯。",
                    "constraints": [{"statement": "仅控制客厅灯", "source": "user", "hard": True}],
                    "steps": [
                        {
                            "id": "read_living",
                            "tool": "get_device_state",
                            "arguments": {"entity_id": "light.living"},
                        },
                        {
                            "id": "turn_on_living",
                            "tool": "turn_on_device",
                            "arguments": {"entity_id": "light.living"},
                            "depends_on": ["read_living"],
                            "verify": {"entity_id": "light.living", "state": "on"},
                        },
                    ],
                }
            ),
        ]
    )

    result = await _planner(hass, client).async_plan(PlanningRequest(goal="打开客厅灯", agent_id="planner"), {})

    assert result.status == PlanningStatus.ACCEPTED
    assert result.source == PlanningSource.MODEL_REPAIRED
    assert result.model_attempts == 2
    assert result.repair_reason is not None and "tool_not_registered" in result.repair_reason
    assert result.plan is not None
    assert result.plan.steps[1].depends_on == ["read_living"]
    assert len(client.calls) == 2
    assert "校验失败原因" in client.calls[1][0].content


@pytest.mark.asyncio
async def test_llm_planner_repairs_a_plan_that_breaks_an_explicit_user_prohibition(hass) -> None:
    hass.states.async_set("climate.living", "off", {"friendly_name": "客厅空调"})
    client = RepairingPlanningClient(
        [
            json.dumps(
                    {
                        "goal": "准备观影",
                        "steps": [
                            {
                                "id": "read_living",
                                "tool": "get_room_state",
                                "arguments": {"room": "living_room"},
                            },
                            {
                                "id": "unrequested_ac_shutdown",
                                "tool": "turn_off_device",
                                "arguments": {"entity_id": "climate.living"},
                                "verify": {"entity_id": "climate.living", "state": "off"},
                                "depends_on": ["read_living"],
                            }
                        ],
                }
            ),
            json.dumps(
                {
                    "goal": "准备观影，但不要打开空调",
                    "constraints": [{"statement": "不要打开空调", "source": "user", "hard": True}],
                    "steps": [{"id": "read_living", "tool": "get_room_state", "arguments": {"room": "living_room"}}],
                }
            ),
        ]
    )

    result = await _planner(hass, client).async_plan(
        PlanningRequest(goal="准备观影，但不要打开空调", agent_id="planner"),
        {},
    )

    assert result.status == PlanningStatus.ACCEPTED
    assert result.source == PlanningSource.MODEL_SANITIZED
    assert result.repair_reason == "安全收缩：移除未获授权的控制步骤 unrequested_ac_shutdown"
    assert result.model_attempts == 1
    assert result.plan is not None
    assert all(step.tool not in {"set_hvac_mode", "turn_off_device"} for step in result.plan.steps)
    assert result.plan.goal_spec is not None
    assert "禁止控制climate" in result.plan.goal_spec.forbidden_actions
    assert '"climate"' in client.calls[0][0].content
    assert client.options[CONF_MAX_TOKENS] == 512
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_llm_planner_compiles_exact_percent_and_temperature_unit_values(hass) -> None:
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅主灯", "brightness": 100})
    hass.states.async_set("climate.living", "off", {"friendly_name": "客厅空调"})
    client = DummyPlanningClient(
        json.dumps(
            {
                "goal": "调节客厅设备",
                "steps": [
                    {"id": "read_light", "action": "read_device", "entity_id": "light.living"},
                    {
                        "id": "set_light",
                        "action": "set_brightness",
                        "entity_id": "light.living",
                        "value": "40%",
                        "depends_on": ["read_light"],
                    },
                    {
                        "id": "set_temperature",
                        "action": "set_temperature",
                        "entity_id": "climate.living",
                        "value": "25.5℃",
                    },
                ],
            }
        )
    )

    result = await _planner(hass, client).async_plan(PlanningRequest(goal="调节客厅设备", agent_id="planner"), {})

    assert result.status == PlanningStatus.ACCEPTED
    assert result.plan is not None
    brightness_step = next(step for step in result.plan.steps if step.tool == "set_light_brightness")
    temperature_step = next(step for step in result.plan.steps if step.tool == "set_temperature")
    assert brightness_step.arguments["brightness"] == 40
    assert temperature_step.arguments["temperature"] == 25.5
    assert all(
        any(
            read.tool == "get_device_state"
            and read.arguments.get("entity_id") == step.arguments.get("entity_id")
            and read.id in step.depends_on
            for read in result.plan.steps
        )
        for step in result.plan.steps
        if step.verify is not None
    )
