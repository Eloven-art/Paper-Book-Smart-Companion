# Qwen3（第三方组件）

- 上游代码：https://github.com/QwenLM/Qwen3
- 来源压缩包：`Qwen3-main.zip`（2026-11-15 下载，GitHub 主分支快照）
- 许可：Qwen3 代码与模型为 Apache-2.0（见上游 README 与 `Qwen3_Technical_Report.pdf`）。上游压缩包未附带单独的 `LICENSE` 文件，此处按上游声明记录。
- 本目录只作为**参考文档与示例的完整归档**（`README.md`、`docs/`、`examples/`、`eval/`），运行时不 import 这里的代码，也不修改上游文件。

## 实际运行方式

本项目的问答/旁白生成不直接用本仓库的 transformers 示例，而是：

- 推理引擎：`third_party/llama.cpp/`（上游 ggml-org/llama.cpp 的 Windows CUDA 预编译包，MIT），运行时为 `llama-server`，提供 OpenAI 兼容的 `/v1/chat/completions`。
- 权重：`data/models/qwen3/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`（约 2.33 GB，不入库）。
  - 来源：ModelScope `unsloth/Qwen3-4B-Instruct-2507-GGUF`，Apache-2.0。
  - 重新下载：`scripts/download_llm_models.py`。
- 启动：`scripts/start_llm.ps1`（默认 `127.0.0.1:8080`，`-ngl 99` 尽量放显存，上下文 8192）。
- 接入：`.env` 里 `SIMPLE_STORY_WRITER_URL=http://127.0.0.1:8080/v1/chat/completions`，`SIMPLE_STORY_WRITER_MODEL=qwen3-4b-instruct-2507`，Key 留空。

## 机器要求

- Windows x64 + NVIDIA 显卡；本机实测 RTX 5060 Laptop 8 GB 显存可完整加载 Q4_K_M。
- CUDA 13.5 版 llama.cpp 需要较新的驱动；驱动过旧时改用 `win-vulkan-x64` 构建。
- 没有显卡时改用 `win-cpu-x64` 构建并设置 `-ngl 0`，速度会明显下降。
