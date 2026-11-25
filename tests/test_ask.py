# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
from datetime import date

import pytest

from lexigate.engine import CONFIRM_HINT_JA, GENERATED_FOOTER_JA, Answer, Engine, FixedQueryPath

BASE = date(2026, 3, 10)


@pytest.fixture(scope="module")
def engine(demo_db, semantic_map):
    return Engine(semantic_map, demo_db["dsn"], BASE, paths=[FixedQueryPath(semantic_map)])


def test_loader_row_counts(demo_db):
    assert demo_db["rows"] == 4602 and demo_db["tables"] == 16


@pytest.mark.parametrize("question, label", [
    ("未払い出張精算はいくら？", "2026-02_month_end"),
    ("先月末時点の未払い出張精算はいくら？", "2026-02_month_end"),
    ("1月末時点の未払いは？", "2026-01_month_end"),
    ("2月10日時点の未払い残高", "as_of_2026-02-10"),
    ("2月13日時点の未払い精算", "as_of_2026-02-13"),
])
def test_unpaid_matches_reference_model(engine, expected, question, label):
    want = expected["cutoffs"][label]["summary"]
    a = engine.ask(question)
    assert a.path == "fixed" and a.intent == "unpaid_summary"
    assert a.data["total_balance_yen"] == want["total_balance_yen"]
    assert a.data["claim_count"] == want["claim_count"]
    assert a.data["employee_count"] == want["employee_count"]
    assert sorted(c["claim_id"] for c in a.data["claims"]) == want["claim_ids"]
    assert sorted([x["claim_id"], x["anomaly_code"]] for x in a.data["anomalies"]) == want["anomalies"]
    assert f"¥{want['total_balance_yen']:,}" in a.answer_ja
    assert a.footer_ja is None and not a.ai_generated and CONFIRM_HINT_JA not in a.hints
    assert 1 <= len(a.hints) <= 2


def test_main_demo_answer_text(engine):
    a = engine.ask("未払い出張精算はいくら？")
    assert "¥371,240（17件・15名）" in a.answer_ja
    assert a.as_of["assumed"] is True and a.as_of["cutoff"] == "2026-03-01T00:00:00+09:00"
    assert "時点の指定がないため" in a.answer_ja


def test_attendance_matches_reference_model(engine, expected):
    want = expected["cutoffs"]["2026-02_month_end"]["summary"]["attendance_unconfirmed"]
    a = engine.ask("勤怠未確定は何名？")
    assert a.intent == "attendance_unconfirmed"
    assert a.data["claim_count"] == want["claim_count"] and a.data["balance_yen"] == want["balance_yen"]
    got = sorted([i["claim_id"], i["work_date"], i["reason_code"], i["responsible_role"]] for i in a.data["issues"])
    assert got == [list(x) for x in want["issues"]]
    assert "2名・2件" in a.answer_ja


def test_combined_question(engine, expected):
    a = engine.ask("先月末時点で承認済み・未払いの出張精算はいくら？そのうち勤怠未確定の案件は誰が確認すべき？")
    assert a.data["total_balance_yen"] == 371240
    assert a.data["attendance_unconfirmed"]["issue_count"] == 4


def test_claim_detail(engine):
    a = engine.ask("C1002 の明細を見せて")
    assert a.intent == "claim_detail" and a.data["found"]
    assert a.data["approved_total_yen"] == 20000 and a.data["net_paid_yen"] == 8000
    assert [p["entry_type"] for p in a.data["payments"]] == ["PAYMENT", "REVERSAL", "PAYMENT"]
    assert engine.ask("C9999 の明細").data["found"] is False


def test_department_scope(engine, expected):
    a = engine.ask("営業部の未払い出張精算はいくら？")
    assert a.data["claims"] and all(c["attributed_department_ja"] == "営業部" for c in a.data["claims"])
    assert a.data["total_balance_yen"] < 371240


def test_clarify_and_help(engine):
    for q in ("来月の給与はいくら？", "今日の天気は？"):
        a = engine.ask(q)
        assert a.path == "clarify" and a.data == {} and a.hints
    assert engine.ask("使い方").path == "help"
    assert engine.ask("2027年1月末時点の未払い").path == "clarify"      # after the base date


def test_extension_point_for_generated_path(demo_db, semantic_map):
    class FakeGenerated:
        name = "generated"

        def answer(self, parsed, ctx):
            return Answer("fixed", "stub", hints=["a", "b"]).mark_generated()

    eng = Engine(semantic_map, demo_db["dsn"], BASE, paths=[FixedQueryPath(semantic_map), FakeGenerated()])
    assert eng.ask("未払い出張精算はいくら？").path == "fixed"          # fixed path wins
    a = eng.ask("今日の天気は？")                                     # not covered -> next path
    assert a.path == "generated" and a.ai_generated and a.footer_ja == GENERATED_FOOTER_JA
    assert a.hints == ["a", "b", CONFIRM_HINT_JA]


def test_fixed_path_requires_declared_relations(semantic_map):
    broken = {**semantic_map, "relations": {}}
    with pytest.raises(ValueError):
        FixedQueryPath(broken)


def test_http_service_and_cli(demo_db, engine, monkeypatch, capsys):
    from fastapi.testclient import TestClient
    from lexigate.service import create_app
    c = TestClient(create_app(engine))
    assert c.get("/healthz").json()["ok"] is True
    r = c.post("/v1/ask", json={"question": "未払い出張精算はいくら？"}).json()
    assert r["data"]["total_balance_yen"] == 371240 and r["path"] == "fixed"
    assert c.post("/v1/ask", json={"question": ""}).status_code == 422
    assert "unpaid_summary" in c.get("/v1/intents").json()

    from lexigate.cli import main
    monkeypatch.setenv("LEXIGATE_DSN", demo_db["dsn"])
    monkeypatch.setenv("LEXIGATE_BASE_DATE", "2026-03-10")
    assert main(["ask", "--local", "勤怠未確定は何名？"]) == 0
    out = capsys.readouterr().out
    assert "勤怠未確定は 2名・2件" in out and "次の質問例" in out
