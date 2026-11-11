# Paper-Book-Smart-Companion（纸书智伴）

“人工智能+”竞赛项目：**纸质图书多模态智能升级与智能伴读**。将纸质绘本逐页转为结构化数据，生成贴合原书的儿童朗读旁白、配音与图生视频，并支持语音问答（含视频内检索）、触控式万物识别和多 Agent 协同编排。

## 仓库内容

| 路径 | 说明 |
|------|------|
| `纸书智伴_五人分工说明_v2.txt` | 五人分工（A 后端/生成链路，B 内容理解/检索/触控，C 前端，D 题库与评测，E 合规/文档/演示）、共同约定与里程碑 |
| `参考代码/` | 参考实现：Flask 后端 `story_tts_server.py` + 前端 `tts_v2.html`，含故事生成、TTS、图像/视频/音效/音乐生成、问答等模块，详见其 README |
| `赛题规则.pdf` | 赛题规则 |
| `项目说明书模板.docx` | 官方项目说明书模板（9 章结构） |
| `项目说明视频.mp4` | 参考演示视频（通过 Git LFS 存储） |
| `server/` | 后端服务：真实接口 `/api/v1`、假接口 `/mock/api/v1`、任务队列、上传校验、运行记录 |
| `agents/` | 生成节点：旁白、配音、图生视频、视频合成、费用台账 |
| `configs/` | 配置入口、单价与预算、提示词（文件名即版本号） |
| `scripts/` | 命令行脚本：跑生成链路、生成示例绘本、测试和启动参考后端 |
| `tests/` | 自动化测试 |
| `data/samples/demo_book/` | 队伍自制的三页模拟绘本，假接口的示例数据 |
| `docs/` | 文档，先看 `docs/A-步骤1-5结果.md`、`docs/接口约定_v0.1.md`、`docs/后端使用说明.md` |
| `logs/` | 运行记录说明与样例（运行时产生的记录不入库） |

## 快速开始

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

```bash
.venv\Scripts\python -m server
```

启动后访问 `http://127.0.0.1:8000/mock/api/v1/books` 可以看到示例绘本。外部服务的地址和密钥写在 `.env` 里（参照 `.env.example`），没配置时自动走降级方案，详见 `docs/后端使用说明.md`。

## 约定（摘自分工说明）

- 目录规划：`configs/ agents/ server/ web/ eval/ scripts/ tests/ logs/ docs/ data/`
- 密钥只放环境变量，不进代码、文档或仓库。
- 官方受限数据不入公开仓库；官方绘本到手前仅使用 AI 生成或自绘的模拟绘本。

## 克隆

视频文件使用 Git LFS，克隆前请安装：

```bash
git lfs install
git clone https://github.com/Eloven-art/Paper-Book-Smart-Companion.git
```
