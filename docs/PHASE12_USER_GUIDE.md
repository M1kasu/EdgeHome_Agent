# 第 12 阶段使用指南

部署时运行 `docker compose -f deployment/docker-compose.yml up -d --build`。启动安装容器会把受控知识资料复制到 `/config/edgehome_knowledge`，把合成能耗数据复制到 `/config/edgehome_energy`，不需要网络或云端向量数据库。

可在 EdgeHome Assist 中使用：

1. “最近一周为什么用电量变高了？”只返回确定性分析，不会改变设备或自动化。
2. “帮我制定一个节能方案，但不要影响冰箱和路由器。”返回有证据的编号建议并等待确认。
3. “应用第一条和第三条，第二条先不要。”仅创建选中的 `EdgeHome Demo` 自动化，并返回 ID。
4. “调整空调计划为30分钟”“查询节能自动化”“撤销上一次自动化”可在同一会话继续使用。

知识工具可通过 `KnowledgeToolRuntime` 调用 `query_device_manual`、`query_household_rule`、`query_energy_guide`；能耗工具可通过 `EnergyToolRuntime` 调用 `query_energy_data`、`analyse_home_energy`、`generate_energy_plan`、`apply_energy_plan`。无资料证据时请接受“无法据此回答”，而不是把回答当作真实设备诊断。
