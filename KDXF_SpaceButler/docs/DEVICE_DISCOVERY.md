# HA 有设备、工作台没有设备时如何检查

工作台通过 HA `/api/states` 读取实体，按固定的客厅演示角色映射灯、窗帘、空调、人体存在和温度。
四个 ESPHome 节点对应五个实体（空调与温度属于同一个节点）。目前不自动接管 HA 中的所有设备，也不按 HA 区域自动修改演示角色。

## 匹配规则

- 优先使用 `.env` 中显式指定的实体 ID；配置错误或该实体消失时不会改为控制另一个设备。
- 未显式指定时，匹配预设名称、已知实体 ID，以及 ESPHome 节点名称和组件名称组合。
- 显示名称忽略大小写、空格、连字符和下划线的差异，例如 `Living-Room-Light`。
- 支持 `light.living_room_light_living_room_light`、`sensor.living_room_ac_living_room_temperature` 等前缀形式。
- 对 `_2` 等重复 ID，只有唯一候选才能绑定。如果两个实体都匹配，即使一个离线也不擅自选择。
- 类型必须对应：灯为 `light`、窗帘为 `cover`、空调为 `climate`、人体为 `binary_sensor`、温度为 `sensor`。

## 查看诊断

修改代码或 `.env` 后，先 Ctrl+C 停止工作台，再重新运行 `scripts/run_workbench.py --port 8765`。
刷新浏览器后进入「空间设备」。也可以打开：

- `http://127.0.0.1:8765/api/devices`：匹配成功的设备及摘要。
- `http://127.0.0.1:8765/api/devices/diagnostics`：HA 返回的相关实体 ID、名称、状态、匹配结果和缺失原因。
- `http://127.0.0.1:8765/api/health`：HA、LLM、设备与 Agent 运行状态。

`home_assistant: ok` 只说明 HA API 可读，不说明五个演示实体已经匹配。
`ha_entity_count: 0` 表示该 HA 实例没有返回实体；请核对 HA 实例和设备是否产生实体。
有实体但缺失原因是 `not_found` 时，请检查 `ha_entities` 中的实际名称和 ID。
`ambiguous` 表示同一角色有多个候选，需显式指定。

不依赖运行中的工作台也可以只读检查：

```powershell
Set-Location 'D:\Project\EdgeHome_Agent-main\KDXF_SpaceButler'
$python = 'E:\Anaconda\envs\Agent-Hub\python.exe'
& $python scripts\check_ha_devices.py
```

这个命令使用 `.env` 中的 HA 地址与 `HA_TOKEN`，仅发起状态读取，不创建账户、令牌、集成，不发送设备控制。
返回码 0 表示五个角色均已匹配；2 表示仍有未匹配角色；1 表示无法读取 HA。
诊断不输出密钥或实体的其他属性；其中的实体名称和状态仍属于你的家庭信息，分享前请检查。

## 手动绑定

从诊断结果或 HA 实体详情复制真正的 `entity_id`，在现有 `.env` 中填写需要的项。以下只是格式示例，不能原样使用占位 ID：

```dotenv
SPACEBUTLER_ENTITY_LIGHT=light.actual_entity_id
SPACEBUTLER_ENTITY_CURTAIN=cover.actual_entity_id
SPACEBUTLER_ENTITY_CLIMATE=climate.actual_entity_id
SPACEBUTLER_ENTITY_PRESENCE=binary_sensor.actual_entity_id
SPACEBUTLER_ENTITY_TEMPERATURE=sensor.actual_entity_id
```

不要重建 ESPHome、重新生成密钥或为匹配随意修改 HA 名称。绑定后重启工作台并刷新页面即可。
设备展示与控制共享同一套解析规则，避免清单显示一个实体、执行时却选中另一个。

## 语音验收

在 `http://127.0.0.1:8765` 的「AI 管家」中允许麦克风，长按 V 或话筒按钮，看到「正在聆听」再说话。
字幕应实时更新；松开后最终文字提交一次。先问设备状态，再测试会改变设备状态的命令。
设备尚未匹配时可以验证字幕，但不能把 Agent 的设备执行失败当作 ASR 失败。
