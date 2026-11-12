# Paper-Book-Smart-Companion（纸书智伴）

“人工智能+”竞赛项目，面向纸质图书的多模态智能升级与智能伴读。

| 目录 | 内容 |
| --- | --- |
| [前端](前端/README.md) | 独立的「冬日故事小屋」前端，含演示数据、素材和可选音色服务。 |
| [后端](后端/docs/后端使用说明.md) | 独立的 Flask 后端，含模拟接口、生成链路、示例绘本和测试。 |
| [赛题内容](赛题内容/) | 赛题规则、项目说明书模板、官方示例代码和演示视频。视频通过 Git LFS 管理。 |
| [前后端合并（初版）](前后端合并（初版）/docs/框架联调与项目缺口.md) | 联调初版，含前端、后端、启动脚本和团队分工说明。 |

## 启动联调初版

克隆前先执行 `git lfs install`。在仓库根目录运行以下命令。

```powershell
cd '前后端合并（初版）'
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m server
```

打开 http://127.0.0.1:8000/。Windows 下安装依赖后也可双击该目录中的 `启动项目.bat`。外部服务通过本地环境变量配置，变量见同目录下的 `.env.example`。
