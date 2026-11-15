# SenseVoice（第三方组件）

- 上游代码：https://github.com/QwenAudio/SenseVoice
- 来源压缩包：`SenseVoice-main.zip`（2026-11-15 下载）
- 许可：代码 MIT，见同目录 `LICENSE`。本目录保留 `model.py`、`utils/`、`demo_onnx.py`、`export.py` 等上游文件，便于核对导出流程；运行时实际调用的是下方 `funasr_onnx`。

## 运行时封装

- `funasr_onnx/`：PyPI 包 `funasr-onnx==0.4.11` 的完整源码，MIT，许可见 `funasr_onnx/LICENSE`。由 `agents/asr_agent.py` 把本目录加入 `sys.path` 后 `from funasr_onnx import SenseVoiceSmall`。
- 运行时**不需要 torch / funasr**；只需 onnxruntime、librosa、soundfile、scipy、kaldi-native-fbank、sentencepiece、jieba。
- 版本说明：`funasr-onnx` 元数据声明 `numpy<=1.26.4`，但本项目在 **numpy 2.2.6 + onnxruntime 1.23.2 + opencv-python 5** 上实测通过（纯 Python 路径没有用到被移除的 numpy API）。如果以后遇到 numpy 兼容问题，可把 numpy 降到 1.26.4，同时把 opencv-python 降到 4.x（opencv 5 要求 numpy>=2）。
- **本地兼容性补丁**（仅 1 处）：`funasr_onnx/utils/sentencepiece_tokenizer.py` 把 `sp.load(path)` 改为先读文件字节再 `sp.load_from_serialized_proto(...)`。原因是 sentencepiece 的 C++ 文件加载在 Windows 上不支持非 ASCII 路径，而本项目可能位于中文目录（如“新建文件夹 (4)”）。

## 模型

- 模型来源：Hugging Face `haixuantao/SenseVoiceSmall-onnx`，是 `iic/SenseVoiceSmall` 的 ONNX 量化导出版本，Apache-2.0。
- 模型文件在 `data/models/sensevoice/`（约 230 MB，不入库）：
  - `model_quant.onnx`：int8 量化模型
  - `config.yaml`、`am.mvn`、`tokens.json`、`chn_jpn_yue_eng_ko_spectok.bpe.model`、`configuration.json`
  - `MODEL_CARD.md`：上游模型卡
- 重新下载：`scripts/download_asr_models.py`（默认走 hf-mirror.com，失败再试 huggingface.co）。

## 重新导出（可选）

如果要从原始 PyTorch 权重重新导出 ONNX，需要额外安装 `requirements-export.txt`（torch、funasr、modelscope、onnx），然后按 `funasr_onnx.SenseVoiceSmall(model_dir, quantize=True)` 的流程导出。日常部署和运行不需要这些依赖。
