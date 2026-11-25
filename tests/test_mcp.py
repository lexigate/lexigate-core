# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""MCP server through the SDK's in-process client (no subprocess, no network)."""
import asyncio
import json
from datetime import date

import pytest

mcp = pytest.importorskip("mcp")

from lexigate.engine import CONFIRM_HINT_JA, Engine  # noqa: E402
from lexigate.mcp_server import NotDemoDatabase, assert_demo_database, create_server  # noqa: E402
from tests.conftest import DEPT_OUT, FakeLLM  # noqa: E402

BASE = date(2026, 3, 10)


def _payload(result):
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def _session(server, calls):
    if hasattr(mcp, "Client"):                                   # mcp >= 2
        async with mcp.Client(server) as c:
            tools = sorted(t.name for t in (await c.list_tools()).tools)
            return tools, [_payload(await c.call_tool(n, a)) for n, a in calls]
    from mcp.shared.memory import create_connected_server_and_client_session as connect   # mcp 1.x
    async with connect(server._mcp_server) as c:
        tools = sorted(t.name for t in (await c.list_tools()).tools)
        return tools, [_payload(await c.call_tool(n, a)) for n, a in calls]


def test_tools_over_mcp(demo_db, semantic_map, expected):
    eng = Engine(semantic_map, demo_db["dsn"], BASE, llm=FakeLLM(DEPT_OUT))
    server = create_server(eng)
    tools, (fixed, gen, ev, conf, catalog, smap, res, qm, claim, ev_lines, bad) = asyncio.run(_session(server, [
        ("ask", {"question": "未払い出張精算はいくら？"}),
        ("ask", {"question": "部門別の未払い残高は？", "session_id": "t1"}),
        ("get_evidence", {"kind": "sql_and_rationale", "session_id": "t1"}),
        ("ask", {"question": CONFIRM_HINT_JA, "session_id": "t1"}),
        ("list_questions", {}),
        ("get_semantic_map", {}),
        ("resolve_intent", {"question": "勤怠未確定は何名？"}),
        ("query_metric", {"intent": "unpaid_summary", "params": {"as_of": {"mode": "month_end", "month": "2026-01"}}}),
        ("claim_evidence", {"claim_id": "c1002"}),
        ("get_evidence", {"evidence_id": "CLM-C1002:payments"}),
        ("query_metric", {"intent": "drop_tables"}),
    ]))
    assert tools == sorted(["ask", "list_questions", "list_metrics", "get_semantic_map", "resolve_intent", "query_metric",
                            "get_evidence", "claim_evidence"])
    assert not any("sql" in t and t != "get_evidence" for t in tools)          # no tool takes SQL
    assert fixed["path"] == "fixed" and fixed["result"]["total_balance_yen"] == 371240 and fixed["disclaimer"] is None
    assert gen["path"] == "generated" and gen["disclaimer"].startswith("※ AI が作成した回答です") and gen["sql_available"]
    assert CONFIRM_HINT_JA in gen["hints"] and "SELECT" not in json.dumps(gen, ensure_ascii=False)
    assert ev["status"] == "ok" and "sem.claim_balance_at" in ev["sql"]
    assert conf["path"] == "evidence" and "【SQL】" in conf["answer_ja"]
    assert {i["intent"] for i in catalog["intents"]} == {"unpaid_summary", "attendance_unconfirmed", "claim_detail"}
    assert "permissions" not in smap and smap["sql_guard"]["allowed_relations"]
    assert res == {"status": "ok", "intent": "attendance_unconfirmed", "params": {}, "path": "fixed"}
    want = expected["cutoffs"]["2026-01_month_end"]["summary"]["total_balance_yen"]
    assert qm["result"]["total_balance_yen"] == want and qm["as_of"]["label_ja"] == "2026年1月末時点"
    assert claim["result"]["claim_id"] == "C1002" and claim["result"]["net_paid_yen"] == 8000
    assert [p["entry_type"] for p in ev_lines["result"]["payments"]] == ["PAYMENT", "REVERSAL", "PAYMENT"]
    assert bad["status"] == "error"


def test_refuses_non_demo_databases(demo_db, monkeypatch):
    assert_demo_database(demo_db["dsn"])                                 # the loaded quickstart DB is accepted
    with pytest.raises(NotDemoDatabase):
        assert_demo_database("postgresql://app@prod-db.example.com:5432/erp")
    import psycopg
    base = demo_db["dsn"].rsplit("/", 1)[0]
    with psycopg.connect(demo_db["dsn"], autocommit=True) as cx:
        cx.execute("DROP DATABASE IF EXISTS lexigate_not_demo")
        cx.execute("CREATE DATABASE lexigate_not_demo")
    with pytest.raises(NotDemoDatabase):
        assert_demo_database(f"{base}/lexigate_not_demo")               # local, but no demo marker
