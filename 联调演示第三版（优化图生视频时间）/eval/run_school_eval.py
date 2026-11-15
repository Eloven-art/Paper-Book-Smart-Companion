"""Repeatable school evaluation with only self-created content."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import requests

FIXTURE = Path(__file__).with_name("demo_questions.json")


def ms(start):
    return round((perf_counter()-start)*1000,1)


def p95(values):
    return sorted(values)[math.ceil(.95*len(values))-1] if values else None


def score(case, turn):
    answer = turn.get("answer_text") or ""
    evidence = turn.get("evidence") or []
    if not case["expected"]:
        return not turn.get("grounded") and not evidence and "没有讲到" in answer, None
    matched = all(any(alt in answer for alt in group.split("|")) for group in case["expected"].split("&"))
    expected_type = "video" if case["category"] == "视频画面" else "page"
    grounded = bool(turn.get("grounded")) and any(
        e["type"] == expected_type and e["page_no"] in case["evidence_pages"]
        and (expected_type!="video" or e.get("timestamp") is not None and bool(e.get("quote")))
        for e in evidence)
    return matched, grounded


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8000")
    parser.add_argument("--audio-dir",type=Path,help="directory with q01.wav ... q30.wav; omit for text-only")
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--label",required=True,help="model and hardware configuration label")
    parser.add_argument("--limit",type=int)
    args=parser.parse_args()
    cases=json.loads(FIXTURE.read_text(encoding="utf-8"))[:args.limit]
    session="school-eval-"+uuid4().hex
    rows=[]
    for case in cases:
        for mode in (["text","audio"] if args.audio_dir else ["text"]):
            started=perf_counter()
            row={**case,"mode":mode}
            payload={"book_id":"demo_book","page_no":case["page"],"session_id":session}
            try:
                target=args.url.rstrip("/")+"/api/v1/qa/ask"
                if mode=="audio":
                    with (args.audio_dir/(case["id"]+".wav")).open("rb") as audio:
                        response=requests.post(target,data=payload,files={"audio":(audio.name,audio,"audio/wav")},timeout=150)
                else:
                    response=requests.post(target,json={**payload,"question":case["question"]},timeout=150)
                row["response_ms"]=ms(started)
                row["response_headers_ms"]=round(response.elapsed.total_seconds()*1000,1)
                response.raise_for_status()
                turn=response.json()["data"]
                correct,evidence_hit=score(case,turn)
                row.update(correct=correct,evidence_hit=evidence_hit,answer=turn["answer_text"],
                           transcript=turn.get("question_text"),evidence=turn.get("evidence"),
                           stages=turn.get("timings"))
                if turn.get("answer_audio_url"):
                    audio=requests.get(args.url.rstrip("/")+turn["answer_audio_url"],timeout=30)
                    audio.raise_for_status()
                    row["audio_download_ms"]=ms(started)
            except (requests.RequestException,OSError,ValueError,KeyError) as exc:
                row.update(error=type(exc).__name__,status=getattr(getattr(exc,"response",None),"status_code",None))
            rows.append(row)
            print(case["id"], mode, row.get("response_ms"), row.get("error", ""), flush=True)
    groups=defaultdict(list)
    for row in rows:
        groups[row["mode"]].append(row)
        groups[row["mode"]+"/"+row["category"]].append(row)
    report={}
    for name, items in groups.items():
        valid=[r for r in items if "correct" in r]
        evidence=[r["evidence_hit"] for r in valid if r["evidence_hit"] is not None]
        report[name]={"cases":len(items),"completed":len(valid),"errors":sum("error" in r for r in items),
                      "answer_accuracy":sum(r["correct"] for r in valid)/len(items) if valid else None,
                      "evidence_hit_rate":sum(evidence)/len(evidence) if evidence else None,
                      "response_headers_p95_ms":p95([r["response_headers_ms"] for r in valid]),
                      "response_p95_ms":p95([r["response_ms"] for r in valid]),
                      "audio_download_p95_ms":p95([r["audio_download_ms"] for r in valid if "audio_download_ms" in r])}
    result={"scope":"校赛自测·自制绘本·关键词初判，需人工复核","configuration":args.label,
            "metrics":report,"items":rows}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if rows and all("error" not in row for row in rows) else 1


if __name__=="__main__":
    raise SystemExit(main())
