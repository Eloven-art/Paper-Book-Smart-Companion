# -*- coding: utf-8 -*-
"""问答节点：拿「画面理解」写好的分页数据当依据，回答小朋友的问题。

两条路，都返回同一套字段（见 docs/接口约定_v0.1.md 3.2）：
- 首选文本大模型（ChatClient）：快、便宜，把本页原文/画面摘要/情节/物体名和相邻页原文拼成上下文；
- 没配文本模型时退回视觉模型（VLM）：直接把页图发过去问，靠画面本身回答。

防编造：
- 要求模型只输出 {"answer", "grounded", "quote"}；
- 模型说 grounded=true 时，还要验证 quote 真的出现在上下文里，否则降级为 grounded=false，
  这样前端不会把“凭印象编的答案”当成书里有的内容展示。
"""
from __future__ import annotations

import re
import time
from typing import Optional

from configs.settings import Settings
from server.book_store import BookStore
from server.errors import ExternalServiceError, ServiceNotConfigured
from server.retry import call_with_retry
from server.run_logger import RunLogger, now_iso

from .llm_client import ChatClient
from .understanding import parse_json
from .vlm_client import VlmClient

SYSTEM = "你是儿童绘本的伴读助手，只根据给出的书中内容回答，不编造。只输出 JSON。"
MAX_ANSWER_CHARS = 120
UNGROUNDED_ANSWER = "书里没有提到这个，我们翻回去再看看别的地方吧。"
_PUNCT = re.compile(r"[\s，。！？、；：“”‘’（）()\[\]【】,.!?;:'\"-]+")


def _squash(text: str) -> str:
    """去掉空白和标点，只比文字本身，避免标点差异把正确引用判成不支持。"""
    return _PUNCT.sub("", text or "")


def quote_supported(quote: str, context: str) -> bool:
    """quote 是否真的在上下文里出现过（去标点后做子串比较）。"""
    needle = _squash(quote)
    return bool(needle) and needle in _squash(context)


class QaAgent:
    def __init__(self, settings: Settings, store: BookStore, runs: RunLogger,
                 chat: Optional[ChatClient] = None, vlm: Optional[VlmClient] = None):
        self.settings, self.store, self.runs = settings, store, runs
        self.chat = chat or ChatClient(settings.llm.url, settings.llm.api_key, settings.llm.model,
                                       timeout=settings.request_timeout)
        self.vlm = vlm

    @property
    def configured(self) -> bool:
        return self.chat.configured or bool(self.vlm and self.vlm.configured)

    # ---------- 上下文 ----------
    def build_context(self, book: dict, page: dict) -> str:
        ocr = (page.get("ocr_text") or {}).get("full_text", "")
        objects = "、".join(o.get("name", "") for o in page.get("objects") or []) or "无"
        parts = [f"绘本：《{book.get('title') or book['book_id']}》第 {page['page_no']} 页",
                 f"本页原文：{ocr or '（本页没有文字）'}",
                 f"画面：{page.get('scene_summary') or '（未描述）'}",
                 f"情节：{'；'.join(page.get('plot_events') or []) or '（未描述）'}",
                 f"可点物体：{objects}"]
        pages = book.get("pages") or []
        index = next((i for i, item in enumerate(pages) if item.get("page_no") == page.get("page_no")), None)
        if index is not None:
            for offset, label in ((-1, "上一页原文"), (1, "下一页原文")):
                target = index + offset
                if 0 <= target < len(pages):
                    text = (pages[target].get("ocr_text") or {}).get("full_text", "")
                    if text:
                        parts.append(f"{label}：{text}")
        return "\n".join(parts)

    # ---------- 提问 ----------
    def ask(self, book_id: str, page_no: int, question: str, play_time: Optional[float] = None,
            session_id: Optional[str] = None) -> dict:
        started = time.perf_counter()
        book = self.store.load(book_id)
        page = BookStore.get_page(book, page_no)
        context = self.build_context(book, page)
        prompt = (f"{context}\n\n小朋友的问题：{question}\n\n"
                  f'只输出 JSON：{{"answer": "给 6 到 8 岁孩子的一句话回答，不超过 {MAX_ANSWER_CHARS} 字",'
                  f' "grounded": true 或 false, "quote": "支撑回答的原文片段，没有就填空字符串"}}\n'
                  f"书上没有的内容不要编，grounded 填 false，answer 就写“{UNGROUNDED_ANSWER}”。")

        with self.runs.start("qa", input={"book_id": book_id, "page_no": page_no,
                                          "question_text": question}) as run:
            run.set_model(self.chat.model if self.chat.configured else (self.vlm.model if self.vlm else None),
                          {"temperature": 0.2})
            if self.chat.configured:
                method = "chat"
                messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
                result = call_with_retry(lambda: self.chat.chat(messages, temperature=0.2, max_tokens=400),
                                         attempts=self.settings.retry_attempts,
                                         base_delay=self.settings.retry_base_delay, label="问答")
                raw, model_used = result.text, result.model
            elif self.vlm and self.vlm.configured:
                method = "vlm"
                image = self.store.book_dir(book_id) / page["page_image"]
                result = call_with_retry(lambda: self.vlm.analyze(image, prompt, system=SYSTEM,
                                                                  temperature=0.2),
                                         attempts=self.settings.retry_attempts,
                                         base_delay=self.settings.retry_base_delay, label="问答(VLM)")
                raw, model_used = result.text, result.model
            else:
                raise ServiceNotConfigured("问答需要大模型：配置 SIMPLE_STORY_WRITER_URL 或 IMAGE_ANALYSIS_URL",
                                           details={"service": "llm"})

            answer, grounded, quote = self._parse_answer(raw)
            if grounded and quote and not quote_supported(quote, context):
                grounded, quote = False, ""            # 引用查不到，说明是编的
            llm_ms = int((time.perf_counter() - started) * 1000)
            run.note("method", method)
            run.note("grounded", grounded)

        turn = {
            "turn_id": f"turn-{run.run_id.rsplit('-', 1)[-1]}",
            "session_id": session_id or f"session-{book_id}-{page_no}",
            "created_at": now_iso(),
            "book_id": book_id, "page_no": page_no, "play_time": play_time,
            "question_text": question, "question_audio_url": None,
            "answer_text": answer, "answer_audio_url": None,
            "grounded": grounded,
            "evidence": ([{"type": "page", "page_no": page_no, "quote": quote,
                           "page_image_url": f"/api/v1/media/{book_id}/{page['page_image']}"}]
                         if grounded and quote else []),
            "snapshot_url": f"/api/v1/media/{book_id}/{page['page_image']}",
            "timings": {"asr_ms": 0, "retrieve_ms": 0, "llm_ms": llm_ms, "tts_ms": 0, "first_audio_ms": None},
        }
        return turn

    @staticmethod
    def _parse_answer(raw: str) -> tuple[str, bool, str]:
        """从模型回复里取 (回答, 是否有依据, 引用)。解析不了就整段当回答，标成无依据。"""
        try:
            data = parse_json(raw)
        except ExternalServiceError:
            text = re.sub(r"\s+", "", raw or "")[:MAX_ANSWER_CHARS]
            return (text or UNGROUNDED_ANSWER), False, ""
        if not isinstance(data, dict):
            return UNGROUNDED_ANSWER, False, ""
        answer = str(data.get("answer") or "").strip()[:MAX_ANSWER_CHARS]
        grounded = bool(data.get("grounded"))
        quote = str(data.get("quote") or "").strip()
        if not answer:
            answer, grounded = UNGROUNDED_ANSWER, False
        return answer, grounded, quote
