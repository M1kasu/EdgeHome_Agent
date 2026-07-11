"""Tests for persistent delayed fixed plans and deterministic Chinese delay parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.execution.executor import FixedPlanExecutor
from custom_components.llama_conversation.edgehome.models import PlanStep, TaskPlan, VerificationRule
from custom_components.llama_conversation.edgehome.tasks.delayed import DelayedTaskManager, DelayedTaskStatus
from custom_components.llama_conversation.edgehome.tasks.parser import ChineseDelayedCommandParser


def _executor(hass) -> FixedPlanExecutor:
    return FixedPlanExecutor(hass, resolver=ExposedEntityResolver(hass, exposure_checker=lambda *_: True))


def _plan() -> TaskPlan:
    return TaskPlan(
        goal="打开客厅灯",
        steps=[
            PlanStep(
                id="delayed_1",
                tool="turn_on_device",
                arguments={"entity_id": "light.living"},
                verify=VerificationRule(entity_id="light.living", state="on"),
            )
        ],
    )


@pytest.mark.asyncio
async def test_delayed_task_create_list_cancel_and_run(hass) -> None:
    hass.states.async_set("light.living", "off")

    async def turn_on(call) -> None:
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)

    hass.services.async_register("light", "turn_on", turn_on)
    manager = DelayedTaskManager(hass, _executor(hass))
    task = await manager.async_create("conversation-1", _plan(), datetime.now(timezone.utc) + timedelta(minutes=5))

    assert [item.id for item in await manager.async_list("conversation-1")] == [task.id]
    cancelled = await manager.async_cancel("conversation-1", task.id)
    assert cancelled is not None
    assert cancelled.status == DelayedTaskStatus.CANCELLED

    runnable = await manager.async_create("conversation-1", _plan(), datetime.now(timezone.utc) + timedelta(minutes=5))
    await manager._async_run(runnable.id)

    completed = (await manager.async_list("conversation-1"))[-1]
    assert completed.status == DelayedTaskStatus.COMPLETED
    assert hass.states.get("light.living").state == "on"


@pytest.mark.parametrize(
    ("text", "seconds", "command"),
    [("10分钟后打开客厅灯", 600, "打开客厅灯"), ("2小时后关闭卧室灯", 7200, "关闭卧室灯"), ("30秒后打开灯", 30, "打开灯")],
)
def test_delayed_command_parser_accepts_bounded_relative_delays(text: str, seconds: int, command: str) -> None:
    parsed = ChineseDelayedCommandParser().parse(text)

    assert parsed is not None
    assert parsed.delay_seconds == seconds
    assert parsed.command == command


def test_delayed_command_parser_rejects_unbounded_or_non_delay_input() -> None:
    parser = ChineseDelayedCommandParser()

    assert parser.parse("明天打开客厅灯") is None
    assert parser.parse("9999小时后打开客厅灯") is None
