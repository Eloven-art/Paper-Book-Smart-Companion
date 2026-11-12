# 运行记录

对应共同约定第 3 条：任何一个生成结果都能找到它是怎么来的。

## 文件

| 路径 | 内容 | 是否入库 |
|---|---|---|
| `runs/<日期>/<run_id>.json` | 每次生成一个文件，完整记录 | 否 |
| `runs/index.jsonl` | 每次生成一行索引，方便检索 | 否 |
| `tasks/<task_id>.json` | 异步任务的状态 | 否 |
| `cost_ledger.jsonl` | 费用台账，每次外部调用一行 | 否 |
| `examples/` | 在自制示例绘本上跑出来的记录样例（5 个成功、2 个失败） | 是 |

运行记录里有绘本原文和旁白。官方绘本属于受限数据，所以 `runs/` 默认不入库。需要给说明书留证据时，挑选后放到 `examples/` 或 `docs/` 里再提交。

## 一条记录里有什么

```json
{
  "run_id": "i2v-20261111T085818-8201e9",
  "kind": "i2v",
  "parent_run_id": "video_page-20261111T085818-0342f2",
  "status": "ok",
  "started_at": "2026-11-11T08:58:18.931+08:00",
  "finished_at": "2026-11-11T08:58:21.989+08:00",
  "elapsed_ms": 3058,
  "code_version": "326ebd2",
  "input": {"book_id": "demo_book", "page_no": 1, "first_frame": "pages/page_001.png", "first_frame_sha256": "…", "prompt": "…", "seconds": 5.0},
  "prompt": {"version": "video_v1", "sha256": "…", "rendered": "实际发出去的提示词全文"},
  "model": {"name": null, "provider": "local_kenburns", "params": {"seconds": 5.0}},
  "outputs": [{"role": "raw_clip", "path": "data/books/demo_book/video/raw/page_001.mp4", "bytes": 255929, "sha256": "…"}],
  "cost": {"service": "i2v", "units": {"seconds": 5.0, "calls": 1}, "billable": false, "amount": 0.0},
  "notes": {"degraded": true},
  "error": null
}
```

- `kind`：`narration_book` → `narration`；`video_book` → `video_page` → `tts` / `i2v`。箭头表示上下级，下级的 `parent_run_id` 指向上级。
- `prompt.version` 是提示词文件名，`prompt.sha256` 是文件内容的哈希。版本号相同而哈希不同，说明有人直接改了旧文件。
- `model.provider` 说明实际用的是什么：`openai_compatible_chat`、`reference_tts_http`、`reference_i2v_gateway` 是真实服务；`local_passthrough`、`placeholder_tone`、`local_kenburns` 是降级方案。
- `notes.degraded` 为 `true` 表示这次结果用了降级方案。
- 失败时 `status` 为 `error`，`error` 里有错误类型、错误码、原因和调用栈。
- 名字里带 key、token、secret、password、authorization 的字段会被写成 `***`。

## 怎么查

- 分页数据里每个生成结果都带着 `run_id`：`narration_meta.run_id`、`narration_audio.run_id`、`video_meta.run_id`、`video_meta.i2v_run_id`、`video.run_id`。
- 有了 `run_id`：`GET /api/v1/runs/<run_id>`，或直接打开 `runs/<日期>/<run_id>.json`。
- 按绘本或页码找：在 `runs/index.jsonl` 里搜 `"book_id": "…"`。
