# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Rules-based NLU: question text -> a small structured intent, driven by the semantic_map synonyms.

No model and no API key. The output is a `Parsed` object; it never contains SQL. Other NLU implementations
(for example a customer-hosted model) can implement the same `NLU` protocol and be passed to `Engine`.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol


@dataclass
class Parsed:
    question: str
    intent: str | None                       # an intent key from the map, "help", or None (not understood)
    as_of: dict | None = None                # {"mode": "previous_month_end"} | {"mode": "month_end", "month": "YYYY-MM"} | {"mode": "date", "date": ...}
    also_attendance: bool = False
    department: str | None = None            # e.g. 営業部 (scope by attributed department)
    claim_id: str | None = None
    confidence: float = 0.0
    in_domain: bool = False                  # mentions the business domain (gate for calling a model)
    source: str = "rules"
    notes: list = field(default_factory=list)


class NLU(Protocol):
    def parse(self, text: str, base: date) -> Parsed: ...


def nfkc(t: str | None) -> str:
    return unicodedata.normalize("NFKC", t or "")


OUT_OF_DOMAIN = re.compile(r"給与|給料|賞与|ボーナス|年収|評価|査定|salary|bonus")
IN_DOMAIN = re.compile(r"精算|出張|未払|未精算|支払|勤怠|打刻|締め|補正|経費|unpaid|outstanding|attendance|expense", re.I)
WEAK = {"いくら", "残高", "勤怠"}             # only count inside the domain
CONFIRM_SQL = re.compile(r"(さっき|先ほど|直前|今)の\s*(SQL|クエリ)|SQL\s*(と|や|の)?\s*(根拠|口径|定義)|根拠\s*(と|や)\s*SQL|"
                         r"show\s+(me\s+)?the\s+sql|刚才的\s*SQL|SQL\s*和依据", re.I)
BREAKDOWN = re.compile(r"別(?!に)|ごと|毎月|月次推移|推移|ランキング|上位|下位|多い順|少ない順|高い順|平均|比較|最も|一番|トップ|top\s*\d", re.I)
DOMAIN_EXTRA = re.compile(r"部門|部署|社員|従業員|シフト|休暇|承認|申請|明細|件数|金額|残高|夜勤|日当|宿泊|交通費")
HELP = re.compile(r"使い方|ヘルプ|help|何ができ|できること", re.I)
CLAIM = re.compile(r"(?<![A-Z0-9])C\d{4}(?!\d)")
DATE = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日")
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
MONTH_END = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*末")
DEPT = re.compile(r"([一-龥ァ-ヴー]{1,8}部)(?:の|で|に|における|は)")


def _year_for(month: int, base: date) -> int:
    return base.year if month <= base.month else base.year - 1


class RulesNLU:
    """Keyword / regex rules built from `intents.*.synonyms` and `time.as_of_modes.*.synonyms`."""

    def __init__(self, semantic_map: dict):
        self.intent_synonyms = {k: [nfkc(s).lower() for s in v.get("synonyms", [])]
                                for k, v in (semantic_map.get("intents") or {}).items()}
        self.terms = [nfkc(k) for k in (semantic_map.get("terms") or {})]
        modes = (semantic_map.get("time") or {}).get("as_of_modes") or {}
        self.prev_month_end = [nfkc(s) for s in (modes.get("previous_month_end") or {}).get("synonyms", [])]

    def parse(self, text: str, base: date) -> Parsed:
        t = nfkc(text)
        tl = t.lower()
        p = Parsed(question=text, intent=None)
        if CONFIRM_SQL.search(t):
            p.intent, p.confidence, p.in_domain = "confirm_sql", 0.95, True
            return p
        hit = {k: [s for s in v if s in tl] for k, v in self.intent_synonyms.items()}
        domain = bool(IN_DOMAIN.search(t))
        p.in_domain = domain or bool(DOMAIN_EXTRA.search(t)) or any(k in t for k in self.terms)
        if BREAKDOWN.search(t):
            p.notes.append("breakdown")
        if OUT_OF_DOMAIN.search(t) and not re.search(r"精算|出張|経費", t):
            hit = {k: [] for k in hit}
            p.notes.append("out_of_domain")
            p.in_domain = False
        for k in hit:                      # weak words alone never trigger a query outside the domain
            if hit[k] and not domain and all(s in WEAK for s in hit[k]):
                hit[k] = []
        cm = CLAIM.search(t.upper())
        if cm:
            p.claim_id = cm.group(0)
        has = lambda k: bool(hit.get(k))   # noqa: E731
        if cm and "claim_detail" in hit and (has("claim_detail") or not (has("unpaid_summary") or has("attendance_unconfirmed"))):
            p.intent = "claim_detail"
        elif has("unpaid_summary") and has("attendance_unconfirmed"):
            # 「いくら」+「勤怠」: the amount question with the attendance breakdown attached
            strong_unpaid = any(s not in WEAK for s in hit["unpaid_summary"])
            if strong_unpaid or "いくら" in t:
                p.intent, p.also_attendance = "unpaid_summary", True
            else:
                p.intent = "attendance_unconfirmed"
        elif has("attendance_unconfirmed"):
            p.intent = "attendance_unconfirmed"
        elif has("unpaid_summary"):
            p.intent = "unpaid_summary"
        elif HELP.search(t):
            p.intent = "help"
        # as-of
        d = DATE.search(t) or ISO_DATE.search(t)
        me = MONTH_END.search(t)
        if d:
            mo, dd = int(d.group(2)), int(d.group(3))
            y = int(d.group(1)) if d.group(1) else _year_for(mo, base)
            p.as_of = {"mode": "date", "date": f"{y:04d}-{mo:02d}-{dd:02d}"}
        elif me:
            mo = int(me.group(2))
            y = int(me.group(1)) if me.group(1) else _year_for(mo, base)
            p.as_of = {"mode": "month_end", "month": f"{y:04d}-{mo:02d}"}
        elif any(s and s in t for s in self.prev_month_end) or "月末" in t:
            p.as_of = {"mode": "previous_month_end"}
        sm = DEPT.search(t)
        if sm and sm.group(1) not in ("全部", "一部"):
            p.department = sm.group(1)
        p.confidence = 0.9 if p.intent else 0.2
        return p
