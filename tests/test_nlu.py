# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
from datetime import date

import pytest

from lexigate.nlu import RulesNLU

BASE = date(2026, 3, 10)


@pytest.fixture(scope="module")
def nlu(semantic_map):
    return RulesNLU(semantic_map)


@pytest.mark.parametrize("q, intent, as_of, also", [
    ("未払い出張精算はいくら？", "unpaid_summary", None, False),
    ("先月末時点で承認済み・未払いの出張精算はいくら？", "unpaid_summary", {"mode": "previous_month_end"}, False),
    ("勤怠未確定は何名？", "attendance_unconfirmed", None, False),
    ("先月末時点で未払いの出張精算はいくら？そのうち勤怠未確定は誰が確認？", "unpaid_summary", {"mode": "previous_month_end"}, True),
    ("1月末時点の未払いは？", "unpaid_summary", {"mode": "month_end", "month": "2026-01"}, False),
    ("2月10日時点の未払い残高", "unpaid_summary", {"mode": "date", "date": "2026-02-10"}, False),
    ("１２月末の未払い", "unpaid_summary", {"mode": "month_end", "month": "2025-12"}, False),
    ("C1002 の明細を見せて", "claim_detail", None, False),
    ("使い方を教えて", "help", None, False),
    ("来月の給与はいくら？", None, None, False),
    ("今日の天気は？", None, None, False),
])
def test_rules(nlu, q, intent, as_of, also):
    p = nlu.parse(q, BASE)
    assert p.intent == intent
    assert p.as_of == as_of
    assert p.also_attendance is also
    assert p.source == "rules"


def test_department_and_claim(nlu):
    p = nlu.parse("営業部の未払い出張精算はいくら？", BASE)
    assert p.department == "営業部"
    assert nlu.parse("ｃ１００１の証跡", BASE).claim_id == "C1001"
