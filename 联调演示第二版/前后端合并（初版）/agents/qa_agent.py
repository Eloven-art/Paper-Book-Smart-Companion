# -*- coding: utf-8 -*-
"""有依据的绘本问答（检索 + 本地 Qwen3 + 依据校验 + 回答配音）。

流程：
1. 检索：把每页的原文、旁白、画面描述、情节、物体说明切成小段，用中文字符二元组 + IDF 打分，
   选出与问题最相关的若干页（找不到任何词面依据时退化为当前页及相邻页）。
2. 生成：把候选页内容按“页码 + 字段名”喂给 OpenAI 兼容的大模型（默认本地 llama.cpp + Qwen3），
   要求只输出 JSON：answer / grounded / evidence[{page_no, quote}]。
3. 校验：evidence 的 quote 必须能在该页内容里找到；找不到就用最接近的原文替换，替换不了就丢弃。
   最终 grounded 只由“是否留下有效依据”决定，模型自称 grounded 不算数。
4. 配音：回答文本可选走现有 TTS（CosyVoice），生成 audio_url 并缓存到 data/tts/。

注意：检索是纯词面匹配，不做语义向量；绘本用词重复度高时足够用。书很大时靠 top_k 限制上下文长度。
"""
from __future__ import annotations

import hashlib
import os
import requests

from .video_index import cosine, embed
from .content_review import screen_answer
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from string import Template
from typing import Callable, Optional

from server.book_store import BookStore
from server.errors import ExternalServiceError, ServiceNotConfigured
from server.run_logger import now_iso
from server.voice_store import selected_voice_id

from .llm_client import ChatClient
from .story_agent import bigrams, load_prompt

PROMPT_NAME = "qa_v1"
UNGROUNDED_ANSWER = "这本书里没有讲到哦。"
MAX_EVIDENCE = 2
_PUNCT_RE = re.compile(r"[^一-鿿A-Za-z0-9]+")
# 去掉疑问句里的套话，剩余字符用于检索（长词在前，避免“什么”先删掉“什么时候”的一部分）
_STOP_PHRASES = sorted((
    "小朋友", "请问", "告诉我", "能不能", "可不可以", "为什么", "是什么", "什么时候", "什么地方",
    "在哪里", "在哪里", "哪里", "哪儿", "怎么样", "怎么", "是不是", "有没有", "多少", "几个",
    "哪些", "哪个", "谁", "还有", "这个", "那个", "这里", "那里", "一下", "这本书", "故事里",
    "吗", "呢", "呀", "啊", "吧", "的", "了", "着", "和", "跟", "与", "是",
), key=len, reverse=True)
_TYPE_RULES = (
    ("画面/物体", ("颜色", "几个", "多少", "哪里", "位置", "在哪儿", "手上", "身上", "穿", "戴",
                "长什么样", "形状", "表情", "动作")),
    ("人物", ("是谁", "谁", "名字", "叫什么")),
    ("情节/原因", ("为什么", "怎么", "后来", "最后", "发生", "结果", "因为")),
)
_OBJECT_BONUS = 1.5
_EXACT_BONUS = 6.0


@dataclass
class Passage:
    page_no: int
    text: str
    source: str
    weight: float
    frame: Optional[dict] = None


# ---------------------------------------------------------------- 文本切分与打分
def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", (text or "").strip())
    return [p.strip() for p in parts if p and len(p.strip()) >= 2]


def _page_passages(book: dict, page: dict) -> list[Passage]:
    page_no = int(page["page_no"])
    out: list[Passage] = []
    for frame in page.get("keyframes") or []:
        summary = frame.get("scene_summary")
        if summary:
            out.append(Passage(page_no, summary, "视频画面", 1.25, frame))
    ocr = page.get("ocr_text") or {}
    blocks = [b.get("text", "").strip() for b in (ocr.get("blocks") or []) if b.get("text", "").strip()]
    for text in (blocks or _split_sentences(ocr.get("full_text") or "")):
        out.append(Passage(page_no, text, "原文", 1.0))
    for text in _split_sentences(page.get("narration") or ""):
        out.append(Passage(page_no, text, "旁白", 0.8))
    for text in _split_sentences(page.get("scene_summary") or ""):
        out.append(Passage(page_no, text, "画面", 0.75))
    for event in page.get("plot_events") or []:
        if event and len(str(event).strip()) >= 2:
            out.append(Passage(page_no, str(event).strip(), "情节", 0.85))
    for obj in page.get("objects") or []:
        name = str(obj.get("name") or "").strip()
        expl = str(obj.get("explanation") or "").strip()
        if name and expl:
            out.append(Passage(page_no, f"{name}：{expl}", "物体", 0.75))
        elif name:
            out.append(Passage(page_no, name, "物体", 0.6))
    characters = "、".join(c.get("name", "") for c in page.get("characters") or [] if c.get("name"))
    if characters:
        out.append(Passage(page_no, f"本页人物：{characters}", "人物", 0.6))
    return out


def _query_terms(question: str) -> tuple[set, str]:
    cleaned = _PUNCT_RE.sub("", question or "")
    for phrase in _STOP_PHRASES:
        cleaned = cleaned.replace(phrase, "")
    return bigrams(cleaned), cleaned


def _question_type(question: str) -> str:
    for label, keywords in _TYPE_RULES:
        if any(k in question for k in keywords):
            return label
    return "故事内容"


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _clamp_confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(min(1.0, max(0.0, number)), 2)


def _clean_answer(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip("\"“”")
    text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    match = list(re.finditer(r"[。！？!?]", cut))
    return (cut[:match[-1].end()] if match else cut.rstrip("，,、;；") + "。").strip()


# ---------------------------------------------------------------- 节点
class QAAgent:
    def __init__(self, settings, store: BookStore, run_logger, ledger, chat: Optional[ChatClient] = None,
                 tts=None):
        self.settings = settings
        self.store = store
        self.runs = run_logger
        self.ledger = ledger
        self.chat = chat or ChatClient(settings.llm.url, settings.llm.api_key, settings.llm.model,
                                       timeout=settings.request_timeout)
        self.tts = tts
        self.prompt = load_prompt(settings.prompts_dir, PROMPT_NAME)

    # ----- 检索 -----
    def retrieve(self, book: dict, page_no: int, question: str,
                 top_k: Optional[int] = None) -> list[tuple[int, list[Passage]]]:
        top_k = max(1, int(top_k or self.settings.qa_top_k))
        pages = book.get("pages") or []
        passages = {int(p["page_no"]): _page_passages(book, p) for p in pages}
        if not passages:
            return []
        qgrams, exact = _query_terms(question)
        df: dict[str, int] = {}
        for items in passages.values():
            for gram in set(bigrams("".join(p.text for p in items))):
                df[gram] = df.get(gram, 0) + 1
        total = max(1, len(passages))
        idf = {gram: math.log(1 + (total - count + 0.5) / (count + 0.5)) for gram, count in df.items()}

        query = None
        if os.getenv("PBSC_EMBEDDINGS_MODEL") and any(
            f.get("embedding_model") == os.getenv("PBSC_EMBEDDINGS_MODEL")
            for p in book["pages"] for f in p.get("keyframes") or []
        ):
            try:
                vectors = embed([question])
                query = vectors[0] if vectors else None
            except (ValueError, KeyError, requests.RequestException):
                pass  # lexical retrieval remains available
        scored_pages: list[tuple[float, int, list[Passage]]] = []
        for no, items in passages.items():
            scored: list[tuple[float, Passage]] = []
            for passage in items:
                overlap = qgrams & bigrams(passage.text)
                score = sum(idf.get(gram, 1.0) for gram in overlap)
                if exact and len(exact) >= 2 and exact in _squash(passage.text):
                    score += _EXACT_BONUS
                if passage.source == "物体" and any(
                        str(o.get("name") or "") and str(o.get("name")) in question
                        for o in (next((p for p in pages if int(p["page_no"]) == no), {}).get("objects") or [])):
                    score += _OBJECT_BONUS
                if query and passage.frame and passage.frame.get("embedding_model") == os.getenv("PBSC_EMBEDDINGS_MODEL"):
                    similarity = cosine(query, passage.frame.get("embedding"))
                    score += max(0, similarity - 0.35) * 12
                score *= passage.weight
                if no == page_no:
                    score *= 1.12          # “这一页/这里”稍作加权
                if score > 0:
                    scored.append((score, passage))
            scored.sort(key=lambda item: -item[0])
            if scored:
                aggregate = scored[0][0] + sum(s for s, _ in scored[1:4]) * 0.2
                scored_pages.append((aggregate, no, [p for _, p in scored]))
        scored_pages.sort(key=lambda item: -item[0])
        selected = [(no, items) for score, no, items in scored_pages if score > 0][:top_k]
        if not selected:
            # 完全没有词面命中：给模型当前页和相邻页，让它按“书里没有”回答
            neighbours = [page_no] + [n for n in sorted(passages) if n != page_no and abs(n - page_no) <= 1]
            selected = [(no, [(1.0, p) for p in passages[no]]) for no in neighbours[:3]]
        return self._select_passages(selected)

    @staticmethod
    def _select_passages(selected: list[tuple[int, list[Passage]]]) -> list[tuple[int, list[Passage]]]:
        """每页按来源配额挑小段，并去掉跨页重复的整段（物体说明常在各页重复）。

        不能只取总分最高的 6 段：物体说明重复度高，容易把真正回答问题的原文挤掉。
        """
        caps = {"原文": 4, "旁白": 2, "画面": 2, "情节": 3, "物体": 3, "人物": 1}
        seen: set[str] = set()
        result: list[tuple[int, list[Passage]]] = []
        for page_no, items in selected:
            if items and isinstance(items[0], tuple):          # 兜底分支给的是 (分数, Passage)
                items = [passage for _, passage in items]
            used: dict[str, int] = {}
            picked: list[Passage] = []
            for passage in items:
                if used.get(passage.source, 0) >= caps.get(passage.source, 2):
                    continue
                if passage.text in seen:
                    continue
                seen.add(passage.text)
                used[passage.source] = used.get(passage.source, 0) + 1
                picked.append(passage)
                if len(picked) >= 8:
                    break
            if picked:
                result.append((page_no, picked))
        return result

    def _context(self, chosen: list[tuple[int, list[Passage]]]) -> str:
        chunks = []
        for page_no, items in chosen:
            lines = [f"[第{page_no}页]"]
            seen = set()
            for passage in items:
                key = (passage.source, passage.text)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"{passage.source}：{passage.text}")
            chunks.append("\n".join(lines))
        return "\n\n".join(chunks) or "（这本书还没有可用的文字或画面内容）"

    def build_messages(self, book: dict, page_no: int, question: str, play_time: Optional[float],
                       chosen: list[tuple[int, list[Passage]]]) -> list[dict]:
        values = {
            "title": book.get("title") or "这本绘本",
            "age_range": book.get("age_range") or "3-8",
            "max_chars": self.settings.qa_max_chars,
            "question": question,
            "question_type": _question_type(question),
            "page_no": page_no,
            "play_time": "—" if play_time is None else f"{float(play_time):.1f}",
            "context": self._context(chosen),
        }
        return [{"role": "system", "content": Template(self.prompt.sections["system"]).safe_substitute(values)},
                {"role": "user", "content": Template(self.prompt.sections["user"]).safe_substitute(values).strip()}]

    # ----- 模型输出解析与依据校验 -----
    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.IGNORECASE)
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None

    def _validate_evidence(self, book: dict, items) -> list[dict]:
        pool = {int(p["page_no"]): _page_passages(book, p) for p in book.get("pages") or []}
        evidence: list[dict] = []
        seen = set()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                page_no = int(item.get("page_no"))
            except (TypeError, ValueError):
                continue
            quote = str(item.get("quote") or "").strip()
            if page_no not in pool or not quote:
                continue
            candidates = pool[page_no]
            matched = None
            matched_frame = None
            squashed = _squash(quote)
            for passage in candidates:
                if squashed and squashed in _squash(passage.text):
                    matched = passage.text
                    matched_frame = passage.frame
                    break
            if matched is None:
                # 允许模型轻微改写：取二元组重合度最高、且不低于 0.6 的原文句
                quote_grams = bigrams(quote)
                best, best_ratio = None, 0.0
                for passage in candidates:
                    grams = bigrams(passage.text)
                    if not quote_grams or not grams:
                        continue
                    ratio = len(quote_grams & grams) / len(quote_grams)
                    if ratio > best_ratio:
                        best, best_ratio = passage, ratio
                if best is not None and best_ratio >= 0.6:
                    matched = best.text
                    matched_frame = best.frame
            if matched is None or (page_no, matched) in seen:
                continue
            seen.add((page_no, matched))
            evidence.append({"page_no": page_no, "quote": matched, **({"timestamp": matched_frame["time"]} if matched_frame else {})})
            if len(evidence) >= MAX_EVIDENCE:
                break
        return evidence

    def _decorate_evidence(self, book: dict, evidence: list[dict],
                           media_url: Callable[[str], str]) -> list[dict]:
        pages = {int(p["page_no"]): p for p in book.get("pages") or []}
        clips = {int(c.get("page_no")): c for c in (book.get("video") or {}).get("clips") or []
                 if c.get("page_no") is not None}
        video_path = (book.get("video") or {}).get("path")
        out: list[dict] = []
        for item in evidence:
            page_no, quote = item["page_no"], item["quote"]
            page = pages.get(page_no) or {}
            out.append({"type": "page", "page_no": page_no, "quote": quote,
                        "page_image_url": media_url(page["page_image"]) if page.get("page_image") else None})
            clip = clips.get(page_no)
            if video_path and "timestamp" in item:
                out.append({"type":"video","page_no":page_no,"timestamp":item["timestamp"],
                            "quote":quote,"video_url":media_url(video_path)})
            elif clip and video_path and len(clip.get("time_range") or []) == 2:
                start, end = float(clip["time_range"][0]), float(clip["time_range"][1])
                out.append({"type": "video", "page_no": page_no, "video_clip_id": clip.get("video_clip_id"),
                            "time_range": [start, end],
                            "timestamp": round(start + min(1.5, max(0.0, end - start) / 4), 2),
                            "video_url": media_url(video_path)})
        return out

    # ----- 回答配音 -----
    def _answer_audio(self, text: str) -> tuple[Optional[str], int]:
        tts = self.tts
        if not text or not self.settings.qa_tts or tts is None or not getattr(tts, "configured", False):
            return None, 0
        voice = (selected_voice_id(self.settings.voice_selection_file)
                 or getattr(tts, "voice", "") or self.settings.tts_voice)
        key = hashlib.sha256(f"{text}|{voice}|{tts.provider}|{getattr(tts, 'model', None)}"
                             .encode("utf-8")).hexdigest()[:20]
        path = self.settings.tts_dir / f"qa_{key}.wav"
        if path.is_file():
            return f"/api/v1/tts-audio/{path.name}", 0
        try:
            result = tts.synthesize(text, path, voice)
        except Exception:       # noqa: BLE001 - 配音失败不影响文字回答
            return None, 0
        if result.is_placeholder:
            return None, 0
        return f"/api/v1/tts-audio/{path.name}", result.elapsed_ms

    # ----- 主流程 -----
    def ask(self, book: dict, page_no: int, question: str, *, play_time: Optional[float] = None,
            media_url: Optional[Callable[[str], str]] = None, asr_ms: int = 0,
            session_id: Optional[str] = None) -> dict:
        if not self.chat.configured:
            raise ServiceNotConfigured(
                "问答模型未配置：请先启动本地 Qwen3（scripts/start_llm.ps1），或填写 SIMPLE_STORY_WRITER_URL",
                details={"service": "llm", "hint": "本地默认地址 http://127.0.0.1:8080/v1/chat/completions"})
        media_url = media_url or (lambda relative: relative)
        session_id = session_id or f"session-{secrets.token_hex(4)}"
        started = time.perf_counter()
        chosen = self.retrieve(book, page_no, question)
        retrieve_ms = int((time.perf_counter() - started) * 1000)
        messages = self.build_messages(book, page_no, question, play_time, chosen)

        run_input = {"book_id": book.get("book_id"), "page_no": page_no, "question": question,
                     "play_time": play_time, "session_id": session_id,
                     "retrieved_pages": [no for no, _ in chosen]}
        with self.runs.start("qa", run_input) as run:
            run.set_model(self.chat.model, {"temperature": self.settings.qa_temperature},
                          provider="openai_compatible_chat")
            run.set_prompt(self.prompt.version, self.prompt.sha256, rendered=messages)
            self.ledger.check("llm")
            try:
                result = self.chat.chat(messages, temperature=self.settings.qa_temperature, max_tokens=420)
                parsed = self._parse_json(result.text)
                if parsed is None:          # 只给一次纠正机会，避免本地模型偶发不按 JSON 输出
                    messages.append({"role": "user", "content": "上一次输出不是合法 JSON。请只输出 JSON 对象，不要解释。"})
                    retry = self.chat.chat(messages, temperature=self.settings.qa_temperature, max_tokens=420)
                    result = retry
                    parsed = self._parse_json(retry.text)
            except ExternalServiceError:
                raise
            llm_ms = int((time.perf_counter() - started) * 1000) - retrieve_ms
            self.ledger.record("llm", result.model, result.usage or {"calls": 1}, run_id=run.run_id,
                               book_id=book.get("book_id"), page_no=page_no, note="qa")

            if parsed is None:
                answer = _clean_answer(result.text, self.settings.qa_max_chars) or UNGROUNDED_ANSWER
                raw_evidence, claimed_grounded, confidence = [], False, 0.0
            else:
                answer = _clean_answer(parsed.get("answer") or parsed.get("答案") or "", self.settings.qa_max_chars)
                raw_evidence = parsed.get("evidence") or parsed.get("依据") or []
                claimed_grounded = bool(parsed.get("grounded", True))
                confidence = _clamp_confidence(parsed.get("confidence"))

            evidence = self._validate_evidence(book, raw_evidence) if claimed_grounded else []
            grounded = bool(claimed_grounded and evidence)
            if not grounded:
                answer, confidence = UNGROUNDED_ANSWER, 0.0
            evidence = self._decorate_evidence(book, evidence, media_url)

            review_started = time.perf_counter()
            blocked = grounded and not screen_answer(self.chat, question, answer, evidence)
            if blocked:
                run.note("safety_review", {"state":"needs_review","candidate":answer,"evidence":evidence})
                answer, grounded, evidence = "这部分请和大人一起讨论吧。", False, []
            review_ms = int((time.perf_counter()-review_started)*1000)
            audio_url, tts_ms = self._answer_audio(answer)
            page = BookStore.get_page(book, page_no)
            timings = {"asr_ms": int(asr_ms or 0), "retrieve_ms": retrieve_ms,
                       "llm_ms": max(0, llm_ms), "review_ms": review_ms, "tts_ms": tts_ms,
                       "first_audio_ms": int(asr_ms or 0) + retrieve_ms + max(0, llm_ms) + review_ms + tts_ms}
            turn = {
                "turn_id": f"turn-{secrets.token_hex(4)}", "session_id": session_id, "created_at": now_iso(),
                "book_id": book.get("book_id"), "page_no": page_no, "play_time": play_time,
                "question_text": question, "question_audio_url": None,
                "answer_text": answer, "answer_audio_url": audio_url,
                "grounded": grounded, "evidence": evidence, "safety_review": "needs_review" if blocked else None,
                "snapshot_url": media_url(page["page_image"]) if page.get("page_image") else None,
                "timings": timings,
            }
            run.note("result", {"answer": answer, "grounded": grounded,
                                "evidence": [{"page_no": e["page_no"], "quote": e["quote"]}
                                             for e in evidence if e.get("type") == "page"],
                                "timings": timings, "confidence": confidence,
                                "degraded": not grounded})
        return turn
