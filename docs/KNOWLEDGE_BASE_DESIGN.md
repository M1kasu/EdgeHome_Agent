# 第 12 阶段本地知识库设计

## 资料范围

资料位于仓库 `knowledge/`，包含 5 份 EdgeHome 虚拟设备测试说明、3 份家庭规则和 3 份节能指南。每份 Markdown 首行均有 JSON 元数据，记录稳定 ID、标题、类别、来源、设备类型和测试型号。所有文本都是项目原创测试资料或规则摘要；`metadata.json` 明确禁止导入受版权保护的完整厂商说明书。

## 索引和检索

`KnowledgeDocumentLoader` 将二级标题拆为可引用知识段。`LocalKnowledgeIndex` 优先建立 SQLite FTS5 索引，并把中文拆成单字 token，保证中文、型号和故障代码能离线检索；SQLite 不支持 FTS5 时才使用纯 Python BM25 回退。索引存放在 `/config/.storage/edgehome_knowledge.sqlite`，资料部署到 `/config/edgehome_knowledge`。

`search_knowledge(query, device_type=None, model=None, top_k=3)` 返回 `success/query/hits/error_code`。每个 hit 含 `document_id/title/section/content/score/source` 与设备元数据。没有证据返回 `NO_EVIDENCE`，未知实体型号返回 `UNKNOWN_DEVICE_MODEL`；调用方必须据此拒绝诊断，不得猜测。

## 设备说明书约束

`KnowledgeService.query_device_manual` 先以 EdgeHome 注册实体确定虚拟设备类型和测试型号，再过滤至 `device_manual` 类资料。`query_household_rule` 与 `query_energy_guide` 也分别按类别隔离。这样“空调 E3”“插座过载”“窗帘离线”“灯亮度未更新”“电视/插座断电”“冰箱路由器保护”“空调/窗户冲突”都有明确来源。
