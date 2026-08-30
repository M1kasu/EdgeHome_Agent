# Local model directory

Place local inference model files in this directory. Model binaries are runtime
artifacts and are intentionally excluded from ordinary Git history.

The default edge-LLM profile expects a GGUF file mounted at `/models`. Configure
the exact host directory and container path with:

```text
SPACEBUTLER_MODELS_DIR=./models
SPACEBUTLER_MODEL_PATH=/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
```

Download or copy the model separately after cloning the repository. Do not
commit `*.gguf`, `*.safetensors`, or model `*.bin` files.
