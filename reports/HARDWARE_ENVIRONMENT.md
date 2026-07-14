# 实际硬件与运行环境

环境标识：**开发测试环境，不是最终嵌入式开发板**。检测时间为 2026-07-12（Asia/Shanghai），已省略用户名、设备名称、序列号与网络标识。

| 项目 | 实测值 |
| --- | --- |
| 操作系统 | Microsoft Windows 11 专业工作站版，10.0.26100，64 位 |
| CPU | 12th Gen Intel Core i7-12800HX |
| CPU 核心 / 逻辑处理器 | 16 / 24 |
| 内存总量 / 检测时可用量 | 31.74 GB / 5.38 GB |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU，8,188 MiB（`nvidia-smi`） |
| GPU 驱动 | 580.97 |
| NPU / 其他 AI 加速器 | 未获取；未发现可记录的 NPU 设备 |
| 推理 GPU 卸载 | 未配置：llama.cpp Docker 容器无 GPU DeviceRequest，不能视为 GPU 推理 |
| 磁盘 | KINGSTON 1TB SSD、KIOXIA 2TB SSD，均 Healthy |
| 工作盘可用空间 | E: NTFS，约 905.4 GiB 可用 |
| 容器运行方式 | Docker Desktop / Docker Compose，Linux x86_64 容器 |
| 功耗 / 温度 | 未获取；本阶段不猜测功耗或设备温度 |

## 检测命令与原始输出摘要

```powershell
Get-CimInstance Win32_OperatingSystem
Get-CimInstance Win32_Processor
Get-CimInstance Win32_ComputerSystem
Get-CimInstance Win32_VideoController
Get-PhysicalDisk
Get-Volume -DriveLetter E
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
docker compose -f deployment/docker-compose.yml ps
docker inspect deployment-llama-server-1 --format '{{json .HostConfig.DeviceRequests}} {{json .HostConfig.Devices}}'
```

原始关键摘要：CPU `i7-12800HX`；物理内存 `31.74 GB`；GPU 查询为 `NVIDIA GeForce RTX 4070 Laptop GPU, 8188 MiB, 580.97`；Docker GPU 设备请求输出 `null null`。所有后续端侧性能结论只描述该开发测试环境。
