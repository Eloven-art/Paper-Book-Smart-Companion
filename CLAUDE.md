# CLAUDE.md

本文件供 Claude Code 等 AI 助手及团队成员参考，说明「纸书智伴」项目的协作约定。

## 项目简介

“人工智能+”竞赛项目：纸质图书多模态智能升级与智能伴读。分工、里程碑与数据约定见 `纸书智伴_五人分工说明_v2.txt`；参考实现见 `参考代码/`（Flask 后端 `story_tts_server.py` + 前端 `tts_v2.html`）。

## Git 约定

- 远程仓库：`origin` = https://github.com/Eloven-art/Paper-Book-Smart-Companion
- 不要直接在 `main` 上修改；每个新功能先从最新的 `main` 建 feature 分支（如 `feature/xxx`）。
- 提交信息用中文，说明改了什么、为什么改。
- 完成一个功能后：提交 → 推送到 `origin` → 用 `gh pr create` 发 PR，经确认后再合并。
- 大文件（如 `*.mp4`）通过 Git LFS 存储，克隆前先执行 `git lfs install`。

## 其他约定

- 密钥只放环境变量，不写入代码、文档或仓库。
- 官方受限数据不入公开仓库；官方绘本到手前只使用 AI 生成或自绘的模拟绘本。
- 目录规划：`configs/ agents/ web/ eval/ logs/ docs/ data/`。
