# 第 12 阶段能耗分析设计

`datasets/energy/generator.py` 以固定种子 `20260711` 生成正常周和异常周各 1176 条小时级合成数据。记录字段为时间、实体、设备、房间、功率、能耗、状态、在场、温度和场景，部署后位于 `/config/edgehome_energy`。

`EnergyDatasetLoader` 只接收带时区、字段齐全、非负的记录，去除 `(timestamp, entity_id)` 重复项；缺状态、负功率/能耗、无时区时间或缺字段均拒绝。模型永远不读取原始 CSV。

`HomeEnergyAnalyzer` 以确定性 Python 计算总能耗、设备/房间/日汇总、正常周比较、峰值、排行、无人区间、异常待机、温度与客厅空调功率相关性。`EnergyAnomalyDetector` 仅检测三个定义明确的测试异常：无人客厅空调、电视关闭后的测试插座待机、夜间无人测试灯长亮。`EnergyRecommendationEngine` 只把有证据的异常转换为三类白名单建议。

本地模型（如启用）只可解释已生成的 `EnergyAnalysis` JSON；不能决定阈值、修改数字、读取 CSV 或创建规则。
