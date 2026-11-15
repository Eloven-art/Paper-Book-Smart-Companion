"""Model screening is advisory for generated media and fail-closed for live QA."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

RISK = re.compile("自杀|自残|色情|裸照|杀人|虐待|制作炸弹|家庭住址|电话号码")
def review_prompt():
    from .story_agent import load_prompt
    return load_prompt(Path(__file__).resolve().parents[1]/"configs"/"prompts","child_review_v1")


def model_screen(chat, material):
    try:
        result = chat.chat([{"role":"system","content":review_prompt().sections["system"]},
                            {"role":"user","content":json.dumps(material, ensure_ascii=False)}],
                           temperature=0, max_tokens=24)
        return result.text.strip() == "PASS"
    except Exception:
        return False


def enabled():
    return os.getenv("PBSC_CHILD_REVIEW") == "1"


def screen_answer(chat, question, answer, evidence=()):
    if not enabled():
        return True
    quotes = [item.get("quote","") for item in evidence]
    return not RISK.search(question + answer + " ".join(quotes)) and model_screen(
        chat, {"question":question,"answer":answer,"quotes":quotes})


def sha256(path):
    from server.run_logger import sha256_file
    return sha256_file(path)


def read_record(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (OSError, ValueError):
        return {}


def approved(record, fingerprint):
    human = record.get("human") or {}
    return record.get("fingerprint") == fingerprint and human.get("decision") == "approve" and bool(human.get("reviewer"))


def fingerprint(store, book):
    video = book.get("video") or {}
    paths = [video.get("path"), *(f.get("clip_path") for f in video.get("clips", []))]
    for page in book["pages"]:
        paths.extend([(page.get("narration_audio") or {}).get("path"),
                      (page.get("video_meta") or {}).get("clip_path")])
    files = {b: sha256(store.resolve_media(book["book_id"], b)) for b in paths if b}
    pages = [{k:p.get(k) for k in ("page_no","narration","narration_audio","video_meta")} for p in book["pages"]]
    return hashlib.sha256(json.dumps([pages,files],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def review_path(data, book_id):
    return Path(data) / "reviews" / (book_id + ".json")


def timestamp():
    return datetime.now(timezone.utc).isoformat()
