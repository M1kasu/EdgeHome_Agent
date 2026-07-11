"""第 11 阶段：服务成功但窗帘状态不变时必须重试并报告部分成功。"""

import pytest

from custom_components.llama_conversation.edgehome.entity_resolver import ExposedEntityResolver
from custom_components.llama_conversation.edgehome.execution.executor import FixedPlanExecutor
from custom_components.llama_conversation.edgehome.models import OnFailurePolicy, PlanStep, TaskPlan, TaskStatus, VerificationRule


@pytest.mark.asyncio
async def test_failure_recovery_reports_partial_success_after_curtain_state_does_not_change(hass) -> None:
    hass.states.async_set("cover.edgehome_living_room_curtain", "open", {"current_position": 100})

    async def service_returns_without_state_change(_call) -> None:
        return None

    hass.services.async_register("cover", "set_cover_position", service_returns_without_state_change)
    executor = FixedPlanExecutor(hass, resolver=ExposedEntityResolver(hass, exposure_checker=lambda *_: True))
    plan = TaskPlan(
        goal="故障恢复测试",
        steps=[
            PlanStep(id="read_before", tool="get_device_state", arguments={"entity_id": "cover.edgehome_living_room_curtain"}),
            PlanStep(
                id="close_curtain",
                tool="control_curtain",
                arguments={"entity_id": "cover.edgehome_living_room_curtain", "position": 0},
                verify=VerificationRule(entity_id="cover.edgehome_living_room_curtain", attributes={"current_position": 0}),
                on_failure=OnFailurePolicy.SKIP,
            ),
        ],
    )

    report = await executor.async_execute(plan)

    assert report.status == TaskStatus.PARTIAL_SUCCESS
    assert report.step_results[-1].error_code == "verification_failed"
    assert report.step_results[-1].attempts == 2
