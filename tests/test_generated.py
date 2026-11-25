# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
from datetime import date

import psycopg
import pytest

from lexigate.engine import CONFIRM_HINT_JA, GENERATED_FOOTER_JA, Engine
from lexigate.generated import GeneratedSQLPath, model_visible_map
from lexigate.llm import LLMError
from lexigate.sqlguard import SQLRejected
from tests.conftest import DEPT_OUT, DEPT_SQL, FakeLLM

BASE = date(2026, 3, 10)


def make(demo_db, semantic_map, *outs):
    llm = FakeLLM(*outs)
    return Engine(semantic_map, demo_db["dsn"], BASE, llm=llm), llm


def test_fixed_first_then_generated_then_confirm(demo_db, semantic_map, expected):
    eng, llm = make(demo_db, semantic_map, DEPT_OUT)
    a = eng.ask("未払い出張精算はいくら？", session_id="s1")
    assert a.path == "fixed" and not a.ai_generated and a.footer_ja is None and CONFIRM_HINT_JA not in a.hints
    assert llm.calls == []                                   # fixed path answered; the model was not called
    b = eng.ask("部門別の未払い残高は？", session_id="s1")
    assert b.path == "generated" and b.ai_generated and b.sql_available
    assert b.footer_ja == GENERATED_FOOTER_JA == "※ AI が作成した回答です。念のため内容をご確認ください"
    assert b.hints[-1] == CONFIRM_HINT_JA and 2 <= len(b.hints) <= 3
    rows = b.data["rows"]
    assert sum(r["balance_yen"] for r in rows) == expected["cutoffs"]["2026-02_month_end"]["summary"]["total_balance_yen"]
    assert "開発部" in b.answer_ja and "¥178,650" in b.answer_ja
    d = b.to_dict()
    assert "rationale" not in d and "SELECT" not in str(d)          # SQL is not shown by default
    # the model saw the question and the map subset, never rows
    assert "部門別の未払い残高" in llm.calls[0]["user"] and "permissions" not in llm.calls[0]["system"]
    c = eng.ask(CONFIRM_HINT_JA, session_id="s1")
    assert c.path == "evidence" and c.data["status"] == "ok"
    assert "sem.claim_balance_at" in c.data["sql"] and "GROUP BY" in c.data["sql"]
    assert "unpaid_trip_balance" in c.data["metrics"] and "sem.claim_balance_at" in c.data["relations"]
    assert "【SQL】" in c.answer_ja and "【口径】" in c.answer_ja
    assert c.footer_ja is None and CONFIRM_HINT_JA not in c.hints and 1 <= len(c.hints) <= 2
    # a different session has no such answer
    assert eng.ask(CONFIRM_HINT_JA, session_id="other").data["status"] != "ok"


def test_confirm_after_fixed_answer_is_not_applicable(demo_db, semantic_map):
    eng, _ = make(demo_db, semantic_map, DEPT_OUT)
    eng.ask("勤怠未確定は何名？", session_id="s2")
    c = eng.ask("さっきのSQLと根拠を見せて", session_id="s2")
    assert c.data["status"] == "not_applicable" and "定型クエリ" in c.answer_ja


def test_no_provider_means_no_path2(demo_db, semantic_map):
    eng = Engine(semantic_map, demo_db["dsn"], BASE, llm="auto")      # env has no key (conftest)
    assert not eng.ctx.generated_enabled
    a = eng.ask("部門別の未払い残高は？")
    assert a.path == "fixed" and a.data["total_balance_yen"] == 371240   # falls back to the fixed total


def test_out_of_domain_never_calls_model(demo_db, semantic_map):
    eng, llm = make(demo_db, semantic_map, DEPT_OUT)
    for q in ("今日の天気は？", "来月の給与はいくら？"):
        assert eng.ask(q).path == "clarify"
    assert llm.calls == []


def test_rejected_sql_is_retried_once_with_feedback(demo_db, semantic_map):
    bad = {**DEPT_OUT, "sql": "SELECT * FROM demo.employees"}
    eng, llm = make(demo_db, semantic_map, bad, DEPT_OUT)
    a = eng.ask("部門別の未払い残高は？")
    assert a.path == "generated" and len(llm.calls) == 2
    assert "拒否" in llm.calls[1]["user"]


def test_rejected_twice_gives_clarify(demo_db, semantic_map):
    eng, llm = make(demo_db, semantic_map, {**DEPT_OUT, "sql": "DELETE FROM demo.expense_lines"})
    a = eng.ask("部門別の未払い残高は？")
    assert a.path == "clarify" and a.data["generated_error"] == "rejected" and not a.ai_generated
    assert len(llm.calls) == 2


def test_db_error_feedback_and_llm_error(demo_db, semantic_map):
    eng, llm = make(demo_db, semantic_map, {**DEPT_OUT, "sql": DEPT_SQL.replace("attributed_department_ja", "no_such_col")}, DEPT_OUT)
    assert eng.ask("部門別の未払い残高は？").path == "generated"
    assert "実行エラー" in llm.calls[1]["user"]
    eng2, _ = make(demo_db, semantic_map, LLMError("HTTP 503 from provider", 503))
    assert eng2.ask("部門別の未払い残高は？").data["generated_error"] == "llm"


def test_unanswerable_falls_through(demo_db, semantic_map):
    eng, _ = make(demo_db, semantic_map, {"answerable": False, "sql": None, "title_ja": "", "basis_ja": ""})
    assert eng.ask("部門別の平均勤続年数は？").path == "clarify"


def test_execution_runs_as_low_privilege_read_only_role(demo_db, semantic_map):
    eng, _ = make(demo_db, semantic_map, DEPT_OUT)
    path = next(p for p in eng.paths if isinstance(p, GeneratedSQLPath))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):        # even if the guard were bypassed
        path.execute(eng.ctx, "SELECT * FROM demo.employees")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        path.execute(eng.ctx, "SELECT count(*) FROM demo.expense_claims")
    cols, rows = path.execute(eng.ctx, "SELECT current_user AS u, current_setting('transaction_read_only') AS ro")
    assert rows[0] == ("lexigate_query", "on")
    path.max_cost = 0.001
    with pytest.raises(SQLRejected):
        path.execute(eng.ctx, "SELECT count(*) FROM sem.clock_workdays()")


def test_model_visible_map_has_no_permissions(semantic_map):
    v = model_visible_map(semantic_map)
    assert "permissions" not in v and set(v["relations"]) <= set(semantic_map["sql_guard"]["allowed_relations"])


def test_service_sessions_card_and_html(demo_db, semantic_map):
    from fastapi.testclient import TestClient
    from lexigate.service import create_app
    eng, _ = make(demo_db, semantic_map, DEPT_OUT)
    c = TestClient(create_app(eng))
    r = c.post("/v1/ask", json={"question": "部門別の未払い残高は？"}).json()
    sid = r["session_id"]
    assert sid and r["ai_generated"] and r["footer_ja"] and "rationale" not in r
    f = c.post("/v1/ask", json={"question": CONFIRM_HINT_JA, "session_id": sid}).json()
    assert f["data"]["status"] == "ok" and "sem.claim_balance_at" in f["data"]["sql"]
    card = c.post("/v1/ask/card", json={"question": "部門別の未払い残高は？", "session_id": "k"}).json()
    foot = [b for b in card["body"] if b.get("id") == "aiGeneratedFooter"][0]
    assert foot["color"] == "Attention" and foot["size"] == "Small" and card["metadata"]["lexigate"]["ai_generated"]
    assert CONFIRM_HINT_JA in [a["title"] for a in card["actions"]]
    html = c.post("/v1/ask/html", json={"question": "部門別の未払い残高は？"}).text
    assert 'class="lexigate-ai-footer" style="color:#c00;font-size:small"' in html and "SELECT" not in html
    fixed_card = c.post("/v1/ask/card", json={"question": "未払い出張精算はいくら？"}).json()
    assert not any(b.get("id") == "aiGeneratedFooter" for b in fixed_card["body"])
    assert "lexigate-ai-footer" not in c.post("/v1/ask/html", json={"question": "未払い出張精算はいくら？"}).text
