# -*- coding: utf-8 -*-
"""故事旁白节点（A 步骤 4）：把每页 OCR 原文改写成适合儿童朗读的旁白。

定位是“改写与讲述原书”，不是另编新故事：
- 输入来自分页数据（B 提供）：本页原文、画面摘要、情节、人物设定表、上一页旁白；
- 调用大模型（沿用参考代码故事模块的接口，提示词换成 configs/prompts/narration_v1.txt）；
- 生成后做程序化检查：人物称呼是否保留、长度、是否夹带标记、内容是否有原文依据；
  不通过就带着问题重试，仍不通过则退回“直接朗读原文”，保证不会把编造的内容讲给孩子；
- 结果写回分页数据的 narration / narration_meta，并把 safety_check 重置为待复核
  （赛题要求生成内容须人工复核，旁白一变，之前的复核结论就不再有效）。

大模型未配置时走降级方案：旁白 = 清理后的原文，并在 narration_meta.method 里标明。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Callable, Optional

from configs.settings import Settings
from server.book_store import BookStore
from server.errors import AppError
from server.retry import call_with_retry
from server.run_logger import RunLogger, now_iso, sha256_text

from .cost import CostLedger
from .llm_client import ChatClient

PROMPT_NAME = "narration_v1"
HARD_MIN_SUPPORT = 0.15     # 低于此值基本可以认定是另编了内容
SOFT_MIN_SUPPORT = 0.30
SOFT_MIN_COVERAGE = 0.35
_MARKUP_RE = re.compile(r"[<>{}\[\]#*`]|【|】|SFX", re.IGNORECASE)
_INTERACTION_WORDS = ("小朋友", "你知道", "你觉得", "你猜", "你们")
_KEEP_RE = re.compile(r"[一-鿿A-Za-z0-9]")


# ---------------------------------------------------------------- 提示词
@dataclass
class Prompt:
    version: str
    sha256: str
    sections: dict


def load_prompt(prompts_dir: Path, name: str) -> Prompt:
    """读取提示词文件。文件名即版本号；'=== 名称 ===' 分节；首节之前以 # 开头的行是注释。"""
    path = Path(prompts_dir) / f"{name}.txt"
    raw = path.read_text(encoding="utf-8")
    sections: dict[str, list[str]] = {}
    current = None
    for line in raw.splitlines():
        marker = re.fullmatch(r"=== (\w+) ===", line.strip())
        if marker:
            current = marker.group(1).lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return Prompt(version=name, sha256=sha256_text(raw),
                  sections={k: "\n".join(v).strip() for k, v in sections.items()})


# ---------------------------------------------------------------- 文本处理与检查
def clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def parse_model_output(raw: str) -> str:
    """从模型输出里取出旁白。优先按 JSON 解析，解析不了就把整段文字当旁白。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict) and isinstance(obj.get("narration"), str):
                text = obj["narration"]
        except ValueError:
            pass
    text = re.sub(r"^(旁白|讲述)\s*[:：]\s*", "", text.strip())
    text = text.strip().strip("\"“”")
    return re.sub(r"\s*\n\s*", "", text)


def bigrams(text: str) -> set:
    chars = "".join(_KEEP_RE.findall(text or ""))
    return {chars[i:i + 2] for i in range(len(chars) - 1)}


def support_ratio(narration: str, sources: list[str]) -> float:
    """旁白里有多大比例的内容能在原文和画面信息里找到依据（按相邻两字统计）。"""
    grams = bigrams(narration)
    if not grams:
        return 0.0
    known: set = set()
    for s in sources:
        known |= bigrams(s)
    return round(len(grams & known) / len(grams), 3)


def coverage_ratio(original: str, narration: str) -> float:
    """原文有多大比例被旁白保留。"""
    grams = bigrams(original)
    if not grams:
        return 1.0
    return round(len(grams & bigrams(narration)) / len(grams), 3)


def character_names(character: dict) -> list[str]:
    names = [character.get("name", "")] + list(character.get("aliases") or [])
    return [n for n in names if n]


@dataclass
class NarrationCheck:
    hard: list = field(default_factory=list)      # 必须修正的问题
    soft: list = field(default_factory=list)      # 需要人工看一眼的问题
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.hard

    def to_dict(self) -> dict:
        return {"passed": self.ok, "hard_issues": self.hard, "soft_issues": self.soft, "metrics": self.metrics}


def page_sources(page: dict, book: dict) -> list[str]:
    """本页可以作为讲述依据的全部文字。"""
    sources = [(page.get("ocr_text") or {}).get("full_text", ""), page.get("scene_summary", "")]
    sources += list(page.get("plot_events") or [])
    sources += [o.get("name", "") for o in page.get("objects") or []]
    for c in book.get("characters") or []:
        sources += character_names(c) + [c.get("appearance", "")]
    return [s for s in sources if s]


def validate_narration(narration: str, page: dict, book: dict, max_chars: int = 220) -> NarrationCheck:
    check = NarrationCheck()
    original = clean_text((page.get("ocr_text") or {}).get("full_text", ""))
    text = clean_text(narration)
    check.metrics = {"length": len(text), "original_length": len(original)}
    if not text:
        check.hard.append({"code": "empty", "message": "旁白为空"})
        return check
    # 上限取“绝对上限”和“原文长度的 2.5 倍加 20 字”中较小的一个：原文很短时，旁白也不能拉得很长
    limit = min(max_chars, max(40, int(len(original) * 2.5) + 20)) if original else max_chars
    check.metrics["max_length"] = limit
    if len(text) > limit:
        check.hard.append({"code": "too_long", "message": f"旁白 {len(text)} 字，超过上限 {limit} 字"})
    if original and len(text) < max(6, len(original) // 2):
        check.hard.append({"code": "too_short", "message": "旁白明显短于原文，可能漏讲了内容"})
    if _MARKUP_RE.search(narration):
        check.hard.append({"code": "markup", "message": "旁白里有标记、括号说明或音效标签"})

    for character in book.get("characters") or []:
        names = character_names(character)
        if not any(n in original for n in names):
            continue
        canonical = character.get("name", "")
        if not any(n in text for n in names):
            check.hard.append({"code": "missing_character", "message": f"原文里的人物“{canonical}”没有被讲到"})
        elif canonical in original and canonical not in text:
            check.soft.append({"code": "name_changed", "message": f"原文称呼“{canonical}”在旁白里被换成了别的叫法"})

    sources = page_sources(page, book)
    support = support_ratio(text, sources)
    coverage = coverage_ratio(original, text)
    check.metrics.update({"support": support, "coverage": coverage})
    if support < HARD_MIN_SUPPORT:
        check.hard.append({"code": "unsupported_content",
                           "message": f"旁白内容大多找不到原文依据（依据度 {support}），疑似另编了故事"})
    elif support < SOFT_MIN_SUPPORT:
        check.soft.append({"code": "low_support", "message": f"旁白与原文的重合度偏低（依据度 {support}）"})
    if len(original) >= 8 and coverage < SOFT_MIN_COVERAGE:
        check.soft.append({"code": "low_coverage", "message": f"原文内容保留偏少（保留度 {coverage}）"})
    hits = [w for w in _INTERACTION_WORDS if w in text and w not in original]
    if hits:
        check.soft.append({"code": "reader_interaction", "message": f"出现了与听众互动的说法：{'、'.join(hits)}"})
    return check


def passthrough_narration(page: dict) -> str:
    """降级方案：直接朗读原文；没有原文时讲画面摘要。"""
    original = re.sub(r"\s*\n\s*", "", ((page.get("ocr_text") or {}).get("full_text") or "").strip())
    return original or (page.get("scene_summary") or "").strip()


# ---------------------------------------------------------------- 节点
class NarrationAgent:
    def __init__(self, settings: Settings, store: BookStore, run_logger: RunLogger, ledger: CostLedger,
                 chat: Optional[ChatClient] = None):
        self.settings = settings
        self.store = store
        self.runs = run_logger
        self.ledger = ledger
        self.chat = chat or ChatClient(settings.llm.url, settings.llm.api_key, settings.llm.model,
                                       timeout=settings.request_timeout)
        self.prompt = load_prompt(settings.prompts_dir, PROMPT_NAME)

    # ----- 提示词 -----
    def build_messages(self, book: dict, page: dict, previous: str, feedback: str = "") -> list[dict]:
        characters = "\n".join(
            f"- {c.get('name')}：{c.get('appearance', '')}"
            + (f"（原文里也可能写作：{'、'.join(c['aliases'])}）" if c.get("aliases") else "")
            for c in book.get("characters") or []) or "（未提供）"
        values = {
            "age_range": book.get("age_range") or "3-8",
            "max_chars": self.settings.narration_max_chars,
            "title": book.get("title") or "",
            "characters": characters,
            "previous_narration": previous or "（这是第一页）",
            "page_no": page["page_no"],
            "page_count": len(book.get("pages") or []),
            "ocr_text": (page.get("ocr_text") or {}).get("full_text") or "（本页没有文字）",
            "scene_summary": page.get("scene_summary") or "（未提供）",
            "plot_events": "；".join(page.get("plot_events") or []) or "（未提供）",
            "page_characters": "、".join(c.get("name", "") for c in page.get("characters") or []) or "（未提供）",
            "feedback": feedback,
        }
        return [{"role": "system", "content": Template(self.prompt.sections["system"]).safe_substitute(values)},
                {"role": "user", "content": Template(self.prompt.sections["user"]).safe_substitute(values).strip()}]

    # ----- 单页 -----
    def generate_page(self, book: dict, page_no: int, previous: str = "",
                      parent_run_id: Optional[str] = None) -> dict:
        page = BookStore.get_page(book, page_no)
        max_chars = self.settings.narration_max_chars
        run_input = {"book_id": book["book_id"], "page_no": page_no,
                     "ocr_text": (page.get("ocr_text") or {}).get("full_text", ""),
                     "scene_summary": page.get("scene_summary", ""), "plot_events": page.get("plot_events", []),
                     "previous_narration": previous}
        with self.runs.start("narration", run_input, parent_run_id) as run:
            attempts: list[dict] = []
            fallback_reason = None
            narration, method, check = "", "", NarrationCheck()

            if not self.chat.configured:
                fallback_reason = "大模型未配置（SIMPLE_STORY_WRITER_URL），直接朗读原文"
                run.set_model(None, provider="local_passthrough")
            else:
                params = {"temperature": self.settings.narration_temperature}
                run.set_model(self.chat.model, params, provider="openai_compatible_chat")
                messages = self.build_messages(book, page, previous)
                run.set_prompt(self.prompt.version, self.prompt.sha256, rendered=messages)
                candidate: Optional[tuple] = None       # 只有软性问题的最好结果
                feedback = ""
                for attempt in range(1, self.settings.narration_attempts + 1):
                    if attempt > 1:
                        messages = self.build_messages(book, page, previous, feedback)
                    try:
                        self.ledger.check("llm")
                        result = call_with_retry(
                            lambda: self.chat.chat(messages, temperature=self.settings.narration_temperature),
                            attempts=self.settings.retry_attempts, base_delay=self.settings.retry_base_delay,
                            label="旁白生成")
                    except AppError as exc:
                        attempts.append({"attempt": attempt, "error": exc.to_dict()})
                        fallback_reason = f"大模型调用失败（{exc.code}）：{exc.message}"
                        break
                    self.ledger.record("llm", result.model, result.usage or {"calls": 1}, run_id=run.run_id,
                                       book_id=book["book_id"], page_no=page_no, note="narration")
                    text = parse_model_output(result.text)
                    attempt_check = validate_narration(text, page, book, max_chars)
                    attempts.append({"attempt": attempt, "raw_output": result.text, "narration": text,
                                     "check": attempt_check.to_dict(), "elapsed_ms": result.elapsed_ms,
                                     "usage": result.usage})
                    if attempt_check.ok and not attempt_check.soft:
                        narration, method, check = text, "llm", attempt_check
                        break
                    if attempt_check.ok:
                        if candidate is None or attempt_check.metrics.get("support", 0) > candidate[1].metrics.get("support", 0):
                            candidate = (text, attempt_check)
                        if attempt >= 2:               # 软性问题只多试一次，避免为小问题反复花钱
                            break
                    problems = attempt_check.hard + attempt_check.soft
                    feedback = ("\n上一次输出的旁白是：" + text + "\n它没有通过检查："
                                + "；".join(p["message"] for p in problems) + "。请修正这些问题后重新输出。")
                if not narration and candidate is not None:
                    narration, check = candidate
                    method = "llm"
                if not narration and fallback_reason is None:
                    fallback_reason = "多次生成都没有通过检查，退回直接朗读原文"

            if not narration:
                narration = passthrough_narration(page)
                method = "passthrough_ocr" if not self.chat.configured else "fallback_passthrough"
                check = validate_narration(narration, page, book, max(max_chars, len(clean_text(narration))))

            needs_review = bool(check.soft or check.hard or method != "llm")
            meta = {
                "run_id": run.run_id, "method": method, "prompt_version": self.prompt.version if method == "llm" else None,
                "model": self.chat.model if method == "llm" else None, "generated_at": now_iso(),
                "attempts": len(attempts), "fallback_reason": fallback_reason, "needs_review": needs_review,
                "check": check.to_dict(),
            }
            run.note("attempts", attempts)
            run.note("result", {"narration": narration, **meta})
            run.note("degraded", method != "llm")
        return {"page_no": page_no, "narration": narration, "meta": meta}

    # ----- 整本 -----
    def generate_book(self, book_id: str, pages: Optional[list[int]] = None,
                      progress: Optional[Callable[[float, str], None]] = None,
                      check_cancelled: Optional[Callable[[], None]] = None) -> dict:
        """按页序逐页生成并写回。pages 为空表示全部页；前一页的旁白会作为下一页的承接上下文。"""
        book = self.store.load(book_id)
        all_pages = sorted(p["page_no"] for p in book.get("pages") or [])
        targets = [n for n in all_pages if not pages or n in pages]
        missing = sorted(set(pages or []) - set(all_pages))
        if missing:
            BookStore.get_page(book, missing[0])       # 抛出 PAGE_NOT_FOUND
        results = []
        with self.runs.start("narration_book", {"book_id": book_id, "pages": targets}) as parent:
            for index, page_no in enumerate(targets):
                if check_cancelled:
                    check_cancelled()
                if progress:
                    progress(index / max(1, len(targets)), f"正在生成第 {page_no} 页旁白")
                book = self.store.load(book_id)
                prev_no = max((n for n in all_pages if n < page_no), default=None)
                previous = BookStore.get_page(book, prev_no).get("narration", "") if prev_no else ""
                result = self.generate_page(book, page_no, previous, parent.run_id)

                def mutate(b: dict, r=result) -> None:
                    page = BookStore.get_page(b, r["page_no"])
                    page["narration"] = r["narration"]
                    page["narration_meta"] = r["meta"]
                    page.pop("narration_audio", None)          # 旁白变了，旧配音作废
                    safety = dict(page.get("safety_check") or {})
                    safety.update(status="pending_review", reviewer=None,
                                  notes="旁白已重新生成，需人工复核后才能标为通过")
                    page["safety_check"] = safety

                self.store.update(book_id, mutate)
                results.append({"page_no": page_no, "narration": result["narration"],
                                "method": result["meta"]["method"], "needs_review": result["meta"]["needs_review"],
                                "check": result["meta"]["check"], "run_id": result["meta"]["run_id"]})
            parent.note("pages", [{k: r[k] for k in ("page_no", "method", "needs_review", "run_id")} for r in results])
        if progress:
            progress(1.0, "旁白生成完成")
        return {"run_id": parent.run_id, "book_id": book_id, "pages": results}
