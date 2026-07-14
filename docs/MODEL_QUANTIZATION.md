# 模型与量化配置

当前部署只发现一个 GGUF：`Home-Llama-3.2-3B.q4_k_m.gguf`，大小 2,019,378,080 bytes。GGUF 元数据标识 `Home LLM`、`Llama-3.2`、`Home Assistant` 微调，许可证字段为 `other`，并指向 `https://huggingface.co/acon96/Home-Llama-3.2-3B/raw/main/LICENSE`。本阶段没有原始 F16/F32 权重，故没有伪造 Q8/Q5 模型结果。

比较的是同一真实 Q4_K_M 文件的两项实际推理配置：上下文 1024 和 2048。两者均由独立 llama.cpp server 实例加载并执行相同的 20 条简单请求；结果见 `benchmark/results/model_comparison.csv`。这是一项**推理配置比较**，不是不同量化精度的质量比较。
