# M1.5 未验证边界

以下项目没有被标记为通过：

- M1 灯光的 10 条独立黑盒表达泛化测试尚未由外部验收方执行；
- M1 的 Home Assistant、device-simulator、Mosquitto 三类单独重启矩阵尚未完成三轮稳定性重复；
- M1 的 delay、reject、非法亮度和错误状态消息等完整故障矩阵尚未补齐；
- 多用户真实前端身份可得性取决于 Home Assistant 调用入口。实现已在可获得 `context.user_id` 时隔离，缺失时仍由 `conversation_id + assistant_id` 隔离；
- 代词、槽位继承只覆盖已列出的有限中文规则，不应宣称能理解任意自然语言或任意复杂多轮计划；
- 开发者工具的一次性调用若不回传 `conversation_id`，将被安全地视为新会话；这不是可绕过的客户端协议限制。

因此，M1 仍保持“实现完成，等待外部黑盒验收”，PR #12 不应合并。M2 窗帘、空调和多设备观影场景均不在本次工作范围。

READY_FOR_EXTERNAL_CONTEXT_ACCEPTANCE
