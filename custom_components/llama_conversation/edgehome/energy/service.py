"""第 12 阶段能耗工具的本地数据服务。"""

from __future__ import annotations

from pathlib import Path

from homeassistant.core import HomeAssistant

from .analyzer import HomeEnergyAnalyzer
from .automation import EnergyAutomationManager
from .loader import EnergyDatasetLoader
from .recommendation_engine import EnergyRecommendationEngine
from .schemas import EnergyAnalysis, EnergyAutomationResult, EnergyLoadResult, EnergyPlan


class EnergyService:
    """为 query/analyse/generate/apply 四类工具提供确定性后端。"""

    def __init__(
        self,
        hass: HomeAssistant,
        abnormal_dataset: Path | None = None,
        normal_dataset: Path | None = None,
        automation_manager: EnergyAutomationManager | None = None,
    ) -> None:
        configured_root = Path(hass.config.path("edgehome_energy"))
        root = configured_root if configured_root.exists() else Path(__file__).resolve().parents[4] / "datasets" / "energy"
        self._abnormal_dataset = abnormal_dataset or root / "abnormal_week.csv"
        self._normal_dataset = normal_dataset or root / "normal_week.csv"
        self._loader = EnergyDatasetLoader()
        self._analyzer = HomeEnergyAnalyzer()
        self._recommendations = EnergyRecommendationEngine()
        self.automations = automation_manager or EnergyAutomationManager(hass)
        self._last_analysis: EnergyAnalysis | None = None

    async def query_energy_data(self) -> EnergyLoadResult:
        """读取当前测试数据；原始记录不交给语言模型。"""
        return self._loader.load(self._abnormal_dataset)

    async def analyse_home_energy(self) -> EnergyAnalysis:
        """计算当前异常周并与正常周对比。"""
        current = self._loader.load(self._abnormal_dataset)
        normal = self._loader.load(self._normal_dataset)
        if not current.success or not current.records:
            raise ValueError(current.error_code or "ENERGY_DATA_UNAVAILABLE")
        self._last_analysis = self._analyzer.analyze(current.records, normal.records if normal.success else None)
        return self._last_analysis

    async def generate_energy_plan(self) -> EnergyPlan:
        """仅在分析存在异常证据时创建待确认方案。"""
        analysis = self._last_analysis or await self.analyse_home_energy()
        return self._recommendations.generate(analysis)

    async def apply_energy_plan(
        self,
        plan: EnergyPlan,
        selected_recommendation_ids: list[str],
        confirmed: bool,
    ) -> EnergyAutomationResult:
        """应用前由调用方完成用户确认，管理器仍会再次校验。"""
        return await self.automations.async_apply(plan, selected_recommendation_ids, confirmed)
