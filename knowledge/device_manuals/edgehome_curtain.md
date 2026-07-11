<!-- edgehome: {"id":"curtain-demo","title":"EdgeHome 测试窗帘 Curtain-Demo-1 状态与恢复","source":"原创测试设备资料，2026-07","category":"device_manual","device_type":"cover","device_model":"EdgeHome Curtain-Demo-1"} -->
# EdgeHome 测试窗帘 Curtain-Demo-1

## 常见状态与参数
类型为 cover，测试型号为 Curtain-Demo-1。`current_position` 范围是 0–100；0 为关闭、100 为打开。仅使用 `set_cover_position` 设置位置。

## 离线处理
状态为 `unavailable` 时表示测试窗帘离线。不要重复发送开合指令；先确认虚拟设备是否恢复可用，再重试一次。离线时不能声称窗帘已经移动。

## 安全步骤
调整前留意窗帘轨道附近是否有阻挡物。自动化只能控制登记的 EdgeHome Demo 窗帘，不能发现或操作真实窗帘。
