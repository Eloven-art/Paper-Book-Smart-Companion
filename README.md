# Paper-Book-Smart-Companion（纸书智伴）

“人工智能+”竞赛项目：**纸质图书多模态智能升级与智能伴读**。将纸质绘本逐页转为结构化数据，生成贴合原书的儿童朗读旁白、配音与图生视频，并支持语音问答（含视频内检索）、触控式万物识别和多 Agent 协同编排。

## 仓库内容

| 路径 | 说明 |
|------|------|
| `纸书智伴_五人分工说明_v2.txt` | 五人分工（A 后端/生成链路，B 内容理解/检索/触控，C 前端，D 题库与评测，E 合规/文档/演示）、共同约定与里程碑 |
| `参考代码/` | 参考实现：Flask 后端 `story_tts_server.py` + 前端 `tts_v2.html`，含故事生成、TTS、图像/视频/音效/音乐生成、问答等模块，详见其 README |
| `赛题规则.pdf` | 赛题规则 |
| `赛题方向对比分析.docx` | 赛题方向对比分析 |
| `项目说明书模板.docx` | 官方项目说明书模板（9 章结构） |
| `项目说明视频.mp4` | 参考演示视频（通过 Git LFS 存储） |

## 约定（摘自分工说明）

- 目录规划：`configs/ agents/ web/ eval/ logs/ docs/ data/`
- 密钥只放环境变量，不进代码、文档或仓库。
- 官方受限数据不入公开仓库；官方绘本到手前仅使用 AI 生成或自绘的模拟绘本。

## 克隆

视频文件使用 Git LFS，克隆前请安装：

```bash
git lfs install
git clone https://github.com/Eloven-art/Paper-Book-Smart-Companion.git
```
