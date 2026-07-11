<!-- edgehome: {"id":"light-demo","title":"EdgeHome 测试灯 Light-Demo-1 亮度校验","source":"原创测试设备资料，2026-07","category":"device_manual","device_type":"light","device_model":"EdgeHome Light-Demo-1"} -->
# EdgeHome 测试灯 Light-Demo-1

## 状态与亮度参数
类型为 light，测试型号为 Light-Demo-1。亮度输入为 0–100%，执行时转换为 Home Assistant `brightness_pct`。成功必须由后读状态确认，而不是只依据服务调用返回。

## 亮度未更新
如果调用后亮度属性没有更新，结果应标记为验证失败。先读取当前状态、检查实体是否可用，再由用户重新发起设置；不能回答“已调亮”或假定已生效。

## 夜间节能
房间无人且夜间测试灯持续开启时，只有在用户确认后，才可创建 EdgeHome Demo 自动化关闭该灯。
