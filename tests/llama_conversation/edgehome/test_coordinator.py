"""Tests for execution and safe fallback behavior of the Chinese fast path."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.llama_conversation.edgehome.coordinator import EdgeHomeCoordinator
from custom_components.llama_conversation.edgehome.energy.statistics import HomeAssistantEnergyStatistics
from custom_components.llama_conversation.edgehome.entity_resolver import EntityNameResolver
from custom_components.llama_conversation.edgehome.execution.executor import FixedPlanExecutor
from custom_components.llama_conversation.edgehome.knowledge.associations import DeviceManualAssociation, DeviceManualRegistry
from custom_components.llama_conversation.edgehome.knowledge.indexer import KnowledgeDocument, LocalKnowledgeIndex
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule


def _coordinator(hass) -> EdgeHomeCoordinator:
    resolver = EntityNameResolver(hass, exposure_checker=lambda *_: True)
    return EdgeHomeCoordinator(hass, resolver=resolver, executor=FixedPlanExecutor(hass, resolver=resolver))


@pytest.mark.asyncio
async def test_coordinator_executes_simple_light_command_without_model(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(call) -> None:
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)

    result = await _coordinator(hass).async_handle("打开客厅灯")

    assert result.handled is True
    assert result.speech == "已打开客厅灯。"
    assert hass.states.get("light.living").state == "on"


@pytest.mark.asyncio
async def test_coordinator_reports_query_and_never_executes_complex_request(hass) -> None:
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅灯", "brightness": 128})

    query = await _coordinator(hass).async_handle("客厅灯状态？")
    complex_request = await _coordinator(hass).async_handle("打开客厅灯然后关闭卧室灯")

    assert query.handled is True
    assert query.speech == "客厅灯当前状态为 on，亮度约 50%。"
    assert complex_request.handled is False


@pytest.mark.asyncio
async def test_coordinator_does_not_fallback_after_a_direct_execution_failure(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(_call) -> None:
        return None

    hass.services.async_register("light", "turn_on", turn_on)

    result = await _coordinator(hass).async_handle("打开客厅灯")

    assert result.handled is True
    assert result.speech is not None
    assert result.speech.startswith("未能完成对客厅灯的操作：")


@pytest.mark.asyncio
async def test_coordinator_uses_dialogue_reference_and_manages_delayed_tasks(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})
    hass.states.async_set("light.bedroom", "off", {"friendly_name": "卧室灯"})

    async def turn_on(call) -> None:
        previous = hass.states.get(call.data["entity_id"])
        attributes = dict(previous.attributes) if previous else {}
        attributes["brightness"] = round(call.data.get("brightness_pct", 100) * 255 / 100)
        hass.states.async_set(
            call.data["entity_id"],
            "on",
            attributes,
            context=call.context,
        )

    hass.services.async_register("light", "turn_on", turn_on)
    coordinator = _coordinator(hass)

    first = await coordinator.async_handle_with_dialogue("把客厅灯亮度调到50%", "conversation-1")
    reference = await coordinator.async_handle_with_dialogue("卧室的也一样", "conversation-1")
    delayed = await coordinator.async_handle_with_dialogue("10分钟后打开客厅灯", "conversation-1")
    listed = await coordinator.async_handle_with_dialogue("查询延时任务", "conversation-1")
    task = (await coordinator._delayed_tasks.async_list("conversation-1"))[0]
    cancelled = await coordinator.async_handle_with_dialogue(f"取消延时任务 {task.id[:8]}", "conversation-1")

    assert first.handled is True
    assert reference.speech == "已按上一条设置完成操作。"
    assert hass.states.get("light.bedroom").attributes["brightness"] == 128
    assert delayed.speech is not None
    assert delayed.speech.startswith("已安排延时任务")
    assert listed.speech is not None
    assert listed.speech.startswith("待执行任务：")
    assert cancelled.speech == "已取消延时任务。"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_coordinator_resumes_pending_clarification_on_next_turn(hass) -> None:
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_on(call) -> None:
        previous = hass.states.get(call.data["entity_id"])
        attributes = dict(previous.attributes) if previous else {}
        attributes["brightness"] = round(call.data.get("brightness_pct", 100) * 255 / 100)
        hass.states.async_set(call.data["entity_id"], "on", attributes, context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    coordinator = _coordinator(hass)

    clarification = await coordinator.async_handle_with_dialogue("把客厅灯调亮", "conversation-1")
    resumed = await coordinator.async_handle_with_dialogue("调到50%", "conversation-1")

    assert clarification.speech is not None
    assert clarification.speech.startswith("暂时无法安全执行")
    assert resumed.speech == "已设置亮度客厅灯。"
    assert hass.states.get("light.living").attributes["brightness"] == 128


@pytest.mark.asyncio
async def test_coordinator_reports_away_risk_and_restores_movie_state(hass) -> None:
    hass.states.async_set("person.alice", "home", {"friendly_name": "Alice"})
    hass.states.async_set("binary_sensor.front_door", "on", {"friendly_name": "前门", "device_class": "door"})
    hass.states.async_set("light.living", "off", {"friendly_name": "客厅灯"})

    async def turn_off(call) -> None:
        hass.states.async_set(call.data["entity_id"], "off", {"friendly_name": "客厅灯"}, context=call.context)

    hass.services.async_register("light", "turn_off", turn_off)
    coordinator = _coordinator(hass)
    scenario_plan = TaskPlan(
        goal="观影模式",
        steps=[
            PlanStep(
                id="movie_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.living"},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )
    await coordinator._scenarios.async_capture("conversation-1", "movie", scenario_plan)
    hass.states.async_set("light.living", "on", {"friendly_name": "客厅灯"})

    report = await coordinator.async_handle_with_dialogue("离家安全检查", "conversation-1")
    restored = await coordinator.async_handle_with_dialogue("恢复观影前状态", "conversation-1")

    assert report.speech is not None
    assert "在家人员 Alice" in report.speech
    assert "打开门窗 前门" in report.speech
    assert restored.speech == "已恢复观影前状态。"
    assert hass.states.get("light.living").state == "off"


@pytest.mark.asyncio
async def test_coordinator_returns_only_local_manual_evidence_or_an_explicit_no_result(tmp_path, hass) -> None:
    index = LocalKnowledgeIndex(hass, tmp_path / "knowledge.sqlite")
    await index.async_upsert(
        KnowledgeDocument(
            id="vacuum-filter",
            title="扫地机滤网说明",
            content="清理滤网前请关闭设备并完全晾干。",
            source="manual/vacuum.pdf#filter",
            device_model="Vacuum V1",
        )
    )
    registry = DeviceManualRegistry(hass)
    await registry.async_associate(DeviceManualAssociation(entity_id="vacuum.demo", device_model="Vacuum V1"))
    coordinator = EdgeHomeCoordinator(hass, knowledge_index=index, manual_registry=registry)

    evidence = await coordinator.async_handle_with_dialogue("查询说明书 vacuum.demo 如何清理滤网", "conversation-1")
    missing = await coordinator.async_handle_with_dialogue("查询说明书 vacuum.demo 不存在的问题", "conversation-1")

    assert evidence.handled is True
    assert evidence.speech == "依据《扫地机滤网说明》(manual/vacuum.pdf#filter)：清理滤网前请关闭设备并完全晾干。"
    assert missing.speech == "本地资料未找到可引用内容，无法据此回答。"


@pytest.mark.asyncio
async def test_coordinator_reads_energy_statistics_without_controlling_devices(hass) -> None:
    end = datetime(2026, 1, 1, 4, tzinfo=timezone.utc)

    def reader(_hass, _start, _end, _statistic_ids):
        return {
            "sensor.energy": [
                {"start": end - timedelta(hours=4), "sum": 0.0},
                {"start": end - timedelta(hours=3), "sum": 1.0},
                {"start": end - timedelta(hours=2), "sum": 2.0},
                {"start": end - timedelta(hours=1), "sum": 6.0},
            ]
        }

    coordinator = EdgeHomeCoordinator(
        hass,
        energy_statistics=HomeAssistantEnergyStatistics(hass, reader=reader),
        clock=lambda: end,
    )

    result = await coordinator.async_handle_with_dialogue("分析 sensor.energy 最近4小时能耗", "conversation-1")

    assert result.handled is True
    assert result.speech is not None
    assert result.speech.startswith("sensor.energy 最近 4 小时累计能耗 6.00 kWh。")
    assert "请检查设备运行时段和待机负载。" in result.speech
