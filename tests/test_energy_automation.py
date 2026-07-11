"""确认、白名单执行、查询和撤销自动化测试。"""

from __future__ import annotations

import pytest

from custom_components.llama_conversation.edgehome.energy.service import EnergyService


@pytest.mark.asyncio
async def test_energy_automation_requires_confirmation_and_calls_only_demo_service(hass) -> None:
    calls: list[dict[str, object]] = []

    async def turn_off(call) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("climate", "turn_off", turn_off)
    hass.states.async_set("climate.edgehome_living_room_ac", "on")
    service = EnergyService(hass)
    plan = await service.generate_energy_plan()

    denied = await service.apply_energy_plan(plan, ["unoccupied-ac"], confirmed=False)
    created = await service.apply_energy_plan(plan, ["unoccupied-ac"], confirmed=True)
    evaluated = await service.automations.async_evaluate(
        created.automation_ids[0], {"occupancy": False, "absence_minutes": 15}
    )
    listed = await service.automations.async_list()
    removed = await service.automations.async_delete(created.automation_ids[0])

    assert denied.error_code == "CONFIRMATION_REQUIRED"
    assert created.success and created.automations[0].name.startswith("EdgeHome Demo")
    assert evaluated.success and calls == [{"entity_id": "climate.edgehome_living_room_ac"}]
    assert listed.automation_ids == created.automation_ids
    assert removed.success
