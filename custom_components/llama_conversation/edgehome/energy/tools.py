"""能耗工具的受控运行时入口，模型只可消费其结构化输出。"""

from __future__ import annotations

from .schemas import EnergyAnalysis, EnergyAutomationResult, EnergyLoadResult, EnergyPlan
from .service import EnergyService


class EnergyToolRuntime:
    """实现 query/analyse/generate/apply 四个固定工具名称。"""

    def __init__(self, service: EnergyService) -> None:
        self._service = service

    async def query_energy_data(self) -> EnergyLoadResult:
        return await self._service.query_energy_data()

    async def analyse_home_energy(self) -> EnergyAnalysis:
        return await self._service.analyse_home_energy()

    async def generate_energy_plan(self) -> EnergyPlan:
        return await self._service.generate_energy_plan()

    async def apply_energy_plan(
        self,
        plan: EnergyPlan,
        selected_recommendation_ids: list[str],
        confirmed: bool,
    ) -> EnergyAutomationResult:
        return await self._service.apply_energy_plan(plan, selected_recommendation_ids, confirmed)
