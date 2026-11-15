# -*- coding: utf-8 -*-
"""命令行识别一个音频文件（不启动服务），用于本地验收 SenseVoice。

用法（在项目根目录）：
    .venv\\Scripts\\python scripts\\transcribe_audio.py path\\to\\ask.wav
    .venv\\Scripts\\python scripts\\transcribe_audio.py ask.webm --language zh --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from agents.asr_agent import ASRAgent  # noqa: E402
from configs.settings import get_settings  # noqa: E402
from server.run_logger import RunLogger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 SenseVoice 语音识别")
    parser.add_argument("audio", help="音频文件（wav/mp3/webm/ogg/m4a/mp4）")
    parser.add_argument("--language", default=None, help="auto/zh/en/yue/ja/ko/nospeech")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    settings = get_settings()
    agent = ASRAgent(settings, RunLogger(settings.runs_dir))
    status = agent.status()
    if not status["available"]:
        print("语音识别模块未就绪：" + json.dumps(status, ensure_ascii=False), file=sys.stderr)
        return 1

    result = agent.transcribe_file(Path(args.audio), language=args.language,
                                   max_seconds=settings.asr_max_seconds)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["text"])
        print(f"[{result['language']} / {result['emotion']} / {result['event']}] "
              f"{result['duration']} 秒，{result['elapsed_ms']} ms，run_id={result['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
