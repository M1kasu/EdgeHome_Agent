"""第 12 阶段能耗多轮对话：分析、方案、选择应用与撤销。"""

from __future__ import annotations

import re

from ..context.store import DialogueStateStore
from .schemas import EnergyPlan
from .service import EnergyService


class EnergyConversationHandler:
    """解析有限中文命令；涉及自动化的分支始终先读取已确认的待执行方案。"""

    def __init__(self, store: DialogueStateStore, service: EnergyService) -> None:
        self._store = store
        self._service = service

    async def async_handle(self, text: str, conversation_id: str) -> str | None:
        normalized = text.strip().rstrip("。！？?!")
        if normalized in {"最近一周为什么用电量变高了", "分析最近一周能耗", "分析家庭能耗"}:
            analysis = await self._service.analyse_home_energy()
            return self._analysis_speech(analysis.total_kwh, analysis.comparison_delta_kwh, analysis.high_consumption_rank, len(analysis.anomalies))
        if normalized.startswith("帮我制定一个节能方案") or normalized == "制定节能方案":
            plan = await self._service.generate_energy_plan()
            await self._store.async_set_energy_plan(conversation_id, plan)
            labels = "；".join(f"第{index}条：{item.title}" for index, item in enumerate(plan.recommendations, start=1))
            return f"已生成不影响冰箱和路由器的节能方案：{labels}。这是长期自动化，正在等待确认；请回复要应用的条目并明确确认。"
        if normalized.startswith("调整空调计划为"):
            return await self._adjust_ac_plan(normalized, conversation_id)
        if normalized.startswith("应用"):
            return await self._apply_selected(normalized, conversation_id)
        if normalized in {"撤销上一次自动化", "撤销最近自动化"}:
            result = await self._service.automations.async_undo_last()
            return "已撤销最近的 EdgeHome Demo 自动化。" if result.success else "没有可撤销的 EdgeHome Demo 自动化。"
        if normalized in {"查询节能自动化", "查看节能自动化"}:
            result = await self._service.automations.async_list()
            if not result.automations:
                return "当前没有 EdgeHome Demo 节能自动化。"
            return "当前自动化：" + "、".join(f"{item.name}（{item.id}，{'启用' if item.enabled else '停用'}）" for item in result.automations)
        return None

    async def _adjust_ac_plan(self, text: str, conversation_id: str) -> str:
        match = re.fullmatch(r"调整空调计划为\s*(\d{1,3})\s*分钟", text)
        plan = await self._store.async_get_energy_plan(conversation_id)
        if match is None or plan is None:
            return "没有可调整的待确认空调节能方案。"
        minutes = int(match.group(1))
        if not 15 <= minutes <= 180:
            return "空调无人关闭时间只支持 15 到 180 分钟。"
        recommendations = [
            item.model_copy(update={"parameters": {**item.parameters, "absence_minutes": minutes}})
            if item.rule_type == "unoccupied_ac"
            else item
            for item in plan.recommendations
        ]
        if recommendations == plan.recommendations:
            return "当前方案没有可调整的空调建议。"
        await self._store.async_update_energy_plan(conversation_id, plan.model_copy(update={"recommendations": recommendations}))
        return f"已将客厅测试空调的无人关闭条件调整为 {minutes} 分钟，仍等待确认。"

    async def _apply_selected(self, text: str, conversation_id: str) -> str:
        plan = await self._store.async_get_energy_plan(conversation_id)
        if plan is None:
            return "没有待确认的节能方案，请先制定方案。"
        selected = _selected_ids(text, plan)
        if not selected:
            return "请明确说明要应用的方案条目，例如“应用第一条和第三条”。"
        result = await self._service.apply_energy_plan(plan, selected, confirmed=True)
        if not result.success:
            return f"节能自动化未创建：{result.error_code or 'UNKNOWN'}。"
        return "已创建 EdgeHome Demo 自动化：" + "、".join(result.automation_ids) + "。可随时查询、停用、删除或撤销。"

    @staticmethod
    def _analysis_speech(total: float, delta: float | None, rank: list[str], anomaly_count: int) -> str:
        leading = "、".join(rank[:3]) or "无"
        comparison = f"，比正常周增加 {delta:.2f} kWh" if delta is not None else ""
        return f"最近一周累计用电 {total:.2f} kWh{comparison}；主要设备为 {leading}，发现 {anomaly_count} 个可核查异常区间。未修改任何设备或自动化。"


def _selected_ids(text: str, plan: EnergyPlan) -> list[str]:
    number_words = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    selected_indexes = {int(number) for number in re.findall(r"第\s*(\d+)\s*条", text)}
    selected_indexes.update(number_words[word] for word in number_words if f"第{word}条" in text)
    # “第二条先不要”是明确排除，不得因出现序号而获得执行权限。
    excluded = {int(number) for number in re.findall(r"第\s*(\d+)\s*条[^，。；]*?(?:不要|不应用|先不)", text)}
    for word, index in number_words.items():
        if re.search(rf"第{word}条[^，。；]*?(?:不要|不应用|先不)", text):
            excluded.add(index)
    selected_indexes.difference_update(excluded)
    return [item.id for index, item in enumerate(plan.recommendations, start=1) if index in selected_indexes]
