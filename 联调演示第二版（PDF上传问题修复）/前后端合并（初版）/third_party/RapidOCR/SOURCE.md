# RapidOCR（第三方组件）

- 上游仓库：https://github.com/RapidAI/RapidOCR
- 来源压缩包：`RapidOCR-main.zip`（2026-11-15 下载，`python/rapidocr/` 包）
- 引入方式：完整复制上游 `python/rapidocr/` 包到本目录，**未修改源码**；运行时由 `agents/ocr_agent.py` 把本目录加入 `sys.path`，再 `import rapidocr`。
- 许可：Apache-2.0，见同目录 `LICENSE`；模型许可见 `MODEL_LICENSES.md`。
- 本项目只使用 ONNX Runtime CPU 推理，模型文件放在 `data/models/rapidocr/`：
  - `PP-OCRv6_det_small.onnx`（文字检测）
  - `PP-OCRv6_rec_small.onnx`（文字识别）
  - `ch_ppocr_mobile_v2.0_cls_mobile.onnx`（方向分类）
- 模型可用 `scripts/download_ocr_models.py` 重新下载；模型目录不入库。
- 升级方式：用新版本上游的 `python/rapidocr/` 覆盖本目录，重跑 `scripts/download_ocr_models.py` 和 `tests/test_ocr.py`。
