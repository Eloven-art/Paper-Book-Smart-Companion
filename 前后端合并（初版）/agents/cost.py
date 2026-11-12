# -*- coding: utf-8 -*-
"""费用台账与预算控制。

- 每次调用收费的外部服务（大模型、TTS、图生视频）都在 logs/cost_ledger.jsonl 追加一行：
  时间、run_id、服务、模型、用量、单价、金额、对应的绘本和页码。
- 单价和预算写在 configs/pricing.json。单价没填时金额记为 null（只记用量），不会编造价格。
- 调用前先 check()：当天调用次数或累计金额达到预算上限时抛出 BudgetExceeded，拒绝继续调用。
  本地降级方案（不收费）也会记一行，但 billable=false，不计入预算。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from server.errors import BudgetExceeded
from server.run_logger import now_iso

_lock = threading.Lock()


class CostLedger:
    def __init__(self, ledger_file: Path, pricing_file: Optional[Path] = None):
        self.ledger_file = Path(ledger_file)
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        self.pricing: dict = {"currency": "CNY", "services": {}, "budgets": {}}
        if pricing_file and Path(pricing_file).is_file():
            self.pricing.update(json.loads(Path(pricing_file).read_text(encoding="utf-8")))

    # ---------- 单价 ----------
    def _price_entry(self, service: str, model: Optional[str]) -> dict:
        table = self.pricing.get("services", {}).get(service, {})
        return table.get(model or "", table.get("*", {})) or {}

    def estimate(self, service: str, model: Optional[str], units: dict) -> Optional[float]:
        """按单价估算金额；有任何一项用量缺单价就返回 None。"""
        price = self._price_entry(service, model)
        mapping = {"seconds": "per_second", "calls": "per_call", "chars": "per_1k_chars",
                   "prompt_tokens": "per_1k_input_tokens", "completion_tokens": "per_1k_output_tokens"}
        total, used = 0.0, False
        for unit, amount in units.items():
            key = mapping.get(unit)
            if key is None:
                continue
            unit_price = price.get(key)
            if unit_price is None:
                if unit in ("seconds", "calls") and (price.get("per_second") is not None
                                                     or price.get("per_call") is not None):
                    continue          # 按秒和按次二选一，填了其中一个即可
                return None
            scale = 1000.0 if key.startswith("per_1k") else 1.0
            total += float(unit_price) * float(amount) / scale
            used = True
        return round(total, 6) if used else None

    # ---------- 读台账 ----------
    def entries(self) -> list[dict]:
        if not self.ledger_file.is_file():
            return []
        out = []
        with open(self.ledger_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def summary(self, day: Optional[str] = None) -> dict:
        """按服务汇总。day 形如 2026-11-11，只统计当天；不传则统计全部。"""
        result: dict[str, dict] = {}
        for e in self.entries():
            if day and not str(e.get("time", "")).startswith(day):
                continue
            s = result.setdefault(e["service"], {"calls": 0, "billable_calls": 0, "amount": 0.0,
                                                 "unpriced_calls": 0, "units": {}})
            s["calls"] += 1
            if e.get("billable"):
                s["billable_calls"] += 1
                if e.get("amount") is None:
                    s["unpriced_calls"] += 1
                else:
                    s["amount"] = round(s["amount"] + float(e["amount"]), 6)
            for unit, amount in (e.get("units") or {}).items():
                s["units"][unit] = s["units"].get(unit, 0) + amount
        return result

    # ---------- 预算 ----------
    def check(self, service: str) -> None:
        budget = self.pricing.get("budgets", {}).get(service) or {}
        max_calls = budget.get("max_calls_per_day")
        max_amount = budget.get("max_amount_total")
        if max_calls is None and max_amount is None:
            return
        if max_calls is not None:
            today = self.summary(time.strftime("%Y-%m-%d")).get(service, {})
            if today.get("billable_calls", 0) >= max_calls:
                raise BudgetExceeded(f"{service} 今天已调用 {today['billable_calls']} 次，达到每日上限 {max_calls} 次",
                                     details={"service": service, "limit": "max_calls_per_day", "value": max_calls})
        if max_amount is not None:
            total = self.summary().get(service, {})
            if total.get("amount", 0.0) >= max_amount:
                raise BudgetExceeded(f"{service} 累计费用 {total['amount']} 已达到预算 {max_amount}",
                                     details={"service": service, "limit": "max_amount_total", "value": max_amount})

    # ---------- 记账 ----------
    def record(self, service: str, model: Optional[str], units: dict, *, run_id: Optional[str] = None,
               billable: bool = True, book_id: Optional[str] = None, page_no: Optional[int] = None,
               note: str = "") -> dict:
        amount = self.estimate(service, model, units) if billable else 0.0
        entry = {"time": now_iso(), "service": service, "model": model, "units": units, "billable": billable,
                 "amount": amount, "currency": self.pricing.get("currency", "CNY"),
                 "priced": amount is not None, "run_id": run_id, "book_id": book_id, "page_no": page_no,
                 "note": note}
        with _lock:
            with open(self.ledger_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
