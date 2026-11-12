# CLAUDE.md

本文件供 Claude Code 等 AI 助手及团队成员参考，说明「纸书智伴」项目的协作约定。

## 项目简介

“人工智能+”竞赛项目：纸质图书多模态智能升级与智能伴读。分工、里程碑与数据约定见 `前后端合并（初版）/docs/纸书智伴_五人分工说明_v2.txt`；参考实现见 `赛题内容/参考代码/`（Flask 后端 `story_tts_server.py` + 前端 `tts_v2.html`）。参考代码只读不改，独立后端位于 `后端/`，联调项目位于 `前后端合并（初版）/`。

## Git 约定

- 远程仓库：`origin` = https://github.com/Eloven-art/Paper-Book-Smart-Companion
- 不要直接在 `main` 上修改；每个新功能先从最新的 `main` 建 feature 分支（如 `feature/xxx`）。
- 提交信息用中文，说明改了什么、为什么改。
- 完成一个功能后：提交 → 推送到 `origin` → 用 `gh pr create` 发 PR，经确认后再合并。
- 大文件（如 `*.mp4`）通过 Git LFS 存储，克隆前先执行 `git lfs install`。

## 其他约定

- 密钥只放环境变量，不写入代码、文档或仓库。
- 官方受限数据不入公开仓库；官方绘本到手前只使用 AI 生成或自绘的模拟绘本。
- 目录规划：`configs/ agents/ server/ web/ eval/ scripts/ tests/ logs/ docs/ data/`。其中 `server/`（后端服务）、`scripts/`（命令行脚本）、`tests/`（自动化测试）是后端开发时新增的。
- 后端：`python -m server` 启动；改动后运行 `python -m pytest`；接口字段以 `docs/接口约定_v0.1.md` 为准。
- 提示词放在 `configs/prompts/`，文件名即版本号；修改时另存为新版本，不直接改旧文件。
