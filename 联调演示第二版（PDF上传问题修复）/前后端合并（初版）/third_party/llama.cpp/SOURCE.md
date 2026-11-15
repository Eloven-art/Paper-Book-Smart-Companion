# llama.cpp（第三方运行时）

- 上游：https://github.com/ggml-org/llama.cpp
- 版本：`b12393`（2026-11-15 下载）
- 来源压缩包：`llama-b12393-bin-win-vulkan-x64.zip`（GitHub Releases 官方预编译包，经 gh-proxy.com 镜像下载）
- 许可：MIT，见同目录 `LICENSE` 等上游随包文件。
- 用途：本地运行 Qwen3-4B-Instruct-2507 GGUF，提供 OpenAI 兼容接口 `llama-server`。

## 为什么用 Vulkan 构建

CUDA 版需要额外的 426 MB CUDA 运行时包，而 Vulkan 版（36 MB）已包含全部依赖，
在本机 RTX 5060 Laptop 上可直接调用独显。速度略低于 CUDA，但对短问答足够。
驱动过旧、Vulkan 不可用时，可换 `win-cpu-x64` 构建并加 `-Cpu` 参数（速度明显下降）。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_llm.ps1
```

默认参数：`--host 127.0.0.1 --port 8080 --ctx-size 8192 --n-gpu-layers 99 --threads 8
--flash-attn on --ubatch-size 1024`，并自动选择第一块 NVIDIA 显卡（没有 NVIDIA 时用 Intel 核显/CPU）。
模型：`data/models/qwen3/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`（不入库，用 `scripts/download_llm_models.py` 下载）。

启动后建议预热一次：`.venv\Scripts\python scripts\warmup_llm.py`。Vulkan 首次大 prompt
会编译管线（冷启动 10–20 秒），预热后本机实测生成约 65 tokens/s、问答端到端 1–2 秒。

## 升级

整包替换本目录即可，不要修改第三方文件；升级后确认 `llama-server.exe --version`，
并重跑 `tests/test_qa.py` 与一次真实问答冒烟。
