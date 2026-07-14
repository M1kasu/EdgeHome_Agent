# 并发压力测试

测试使用 12 条彼此独立的只读模型查询，避免同一设备竞争被计为性能失败。当前模型服务为 4 slots，CPU 路径。

| 并发 | 请求 | 成功 | 平均延迟 | P95 | 平均生成速度 | 吞吐量 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 12 | 12 | 593.369 ms | 712.125 ms | 24.938 tok/s | 1.685 req/s |
| 2 | 12 | 12 | 705.012 ms | 963.577 ms | 20.563 tok/s | 2.837 req/s |
| 4 | 12 | 12 | 1,015.397 ms | 1,378.077 ms | 16.021 tok/s | 3.939 req/s |

未测试并发 8：当前开发机空闲内存约 5GB，且 Q4/ctx2048 服务采样峰值约 4.8GiB；继续提高并发会造成不合理的内存压力。原始数据：[concurrency_results.csv](../benchmark/results/concurrency_results.csv)，图表：[concurrency_throughput.svg](../benchmark/figures/concurrency_throughput.svg)。
