"""由分析证据生成有限、保护设备排除的节能建议。"""

from __future__ import annotations

from .schemas import EnergyAnalysis, EnergyPlan, EnergyRecommendation


class EnergyRecommendationEngine:
    """只生成预定义的三类虚拟设备建议，不从数据动态生成控制目标。"""

    def generate(self, analysis: EnergyAnalysis) -> EnergyPlan:
        recommendations: list[EnergyRecommendation] = []
        if analysis.unoccupied_intervals:
            recommendations.append(
                EnergyRecommendation(
                    id="unoccupied-ac",
                    title="无人 15 分钟后关闭客厅测试空调",
                    rationale="检测到客厅无人时空调持续运行。",
                    target_entity_id="climate.edgehome_living_room_ac",
                    rule_type="unoccupied_ac",
                    parameters={"absence_minutes": 15},
                    evidence=analysis.unoccupied_intervals,
                )
            )
        if analysis.standby_intervals:
            recommendations.append(
                EnergyRecommendation(
                    id="tv-standby-socket",
                    title="电视关闭且待机偏高时处理测试插座",
                    rationale="电视关闭期间测试插座存在异常待机区间。",
                    target_entity_id="switch.edgehome_test_socket",
                    rule_type="tv_standby",
                    parameters={"minimum_power_w": 18},
                    evidence=analysis.standby_intervals,
                )
            )
        night = [item for item in analysis.anomalies if item.kind == "night_light"]
        if night:
            recommendations.append(
                EnergyRecommendation(
                    id="night-light-off",
                    title="夜间无人时关闭客厅测试灯",
                    rationale="检测到夜间无人时测试灯持续开启。",
                    target_entity_id="light.edgehome_living_room_main",
                    rule_type="night_light",
                    parameters={"start_hour": 23, "end_hour": 6},
                    evidence=night,
                )
            )
        if not recommendations:
            raise ValueError("没有证据支持创建节能方案")
        return EnergyPlan(analysis_id=analysis.analysis_id, recommendations=recommendations)
