# M0：硬编码审计

**审计状态：READY_FOR_EXTERNAL_ACCEPTANCE**
**审计时间：2026-07-12**
**范围：** `custom_components/llama_conversation/edgehome/`、`device_simulator/`、`deployment/` 与 `scripts/` 中的生产/部署路径；不把测试断言本身计为生产硬编码。

## 方法与结论

使用静态搜索检查了：完整中文语句分支、`if/elif` 文本匹配、正则、固定实体映射、生产代码对 `tests`/`benchmark`/`fixtures` 的导入，以及部署中的 `input_boolean`/`input_number` 模板设备。

没有发现 `把客厅主灯调到35%`、`刚才设置的是多少？`、`再调高10%` 这三条验收输入被完整句子映射到固定结果，也没有生产模块导入测试夹具或期望结果。这个结论不等于当前实现没有硬编码风险：以下路径仍会使系统对少量措辞和固定家庭拓扑过度依赖。

|级别|位置|发现|影响|M0 结论|
|---|---|---|---|---|
|阻断后续里程碑|`edgehome/planner/demo_scenes.py:31-92`|观影、离家、睡眠计划直接写入灯、电视、空调、窗帘等实体与固定参数。|除 MQTT 主灯外，目标实体目前主要是模板/Helper 设备；不能作为 M2–M6 的真实设备闭环证据。|冻结这些场景作为交付证据，迁移到设备能力/注册表驱动的计划构造。|
|高|`edgehome/understanding/reference_resolver.py:23-43`|“关掉吧”“再亮一点”“再暗一点”等短语列表直接决定控制语义，且亮度短语有重复分支。|无法泛化到带数值的相对命令，例如“再调高10%”；容易演变为针对验收句子的补丁。|改为受限的引用语义解析：动作、方向、显式增量、指代、目标实体分离解析。|
|中|`edgehome/coordinator.py:158,656,665,742-750`|保护确认、离家检查、恢复观影、查询/取消延时任务依赖精确短语或单一正则。|限定命令可用，但同义表达不稳定；保护确认还不应仅依赖可复述的一句话。|保留安全确认语义，改为“待确认操作 + 会话绑定”的结构化确认解析，并把读操作命令迁入语义路由。|
|中|`edgehome/energy/conversation.py:21-48`|能耗分析/节能方案/撤销等存在完整自然语言集合匹配。|这是典型完整句子白名单，容易只通过已知演示词句。|在 M1/M1.5 之后改为参数化意图和槽位语法；在独立黑盒用例验证前不作为比赛能力。|
|中|`edgehome/entity_registry.py:74-95`|运行时注册表把 M1 与尚未 MQTT 化的设备混在同一可控清单。|规划器和快速路由仍可选择 Helper 来源的 M2–M6 实体。|按设备能力和“真实协议已验收”状态过滤；当前生产可控集只保留 M1 MQTT 主灯。|
|低（允许）|`edgehome/understanding/intent_classifier.py:13-23`、`router.py`|动词、设备类别、百分比等参数化语法规则。|这是受限意图解析，而非完整测试句子匹配。|保留，但为每种语义写同义改写和反例测试。|
|低（允许）|`device_simulator/` MQTT 主题、设备 ID 与初始能力配置。|协议设备需要稳定身份和 topic。|保留为配置数据；设备能力不得在对话分支中重复硬编码。|

## 需消除的重复与脆弱点

1. `ReferencePlanResolver` 同时保存 Unicode 转义短语和直接中文短语，`再调亮一点`/`再亮一点`、`再调暗一点`/`再暗一点` 被重复判断。这不是完整句子作弊，但会掩盖语法覆盖不足。
2. `_relative_brightness()` 固定使用 `±10`，没有从用户文本解析百分比增量；它只能处理“点/一些”这类隐式增量。
3. `DemoScenePlanFactory` 的静态场景在模型不可用时仍可产生含未迁移实体的计划。后续里程碑必须先让该实体具备独立 MQTT（或等价真实协议）闭环，才能重新开放对应步骤。
4. `deployment/demo/homeassistant/edgehome_demo_templates.yaml` 和 `edgehome_demo_climate.yaml` 明确仍用 `input_boolean`/`input_number` 作为状态来源。除 M1 主灯外，这些只能作为兼容演示，不得作为最终验收来源。

## 反规避要求

已在 M1 新增并运行 `scripts/audit_hardcoding.py`。它在 `reports/acceptance/m1_mqtt_light/hardcoding_audit.json` 中输出了 0 个 error 与 6 个待整改 warning；至少检查以下规则并在命中时以非零退出：

- 生产目录中出现验收输入的完整句子或与其高度相似的长中文文本分支；
- 生产模块导入 `tests`、`benchmark`、`acceptance`、`fixtures` 或 `expected_results`；
- 非诊断实体使用 `hass.states.async_set`/`hass.states.set` 伪造设备回读；
- 新设备仍由 `input_boolean`、`input_number` 或模板实体作为最终验收状态源。

脚本必须对命中位置输出文件与行号；允许项只能由显式、短小的审计白名单说明理由，不能静默忽略。
