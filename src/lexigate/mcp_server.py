# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""`lexigate mcp`: MCP server for the local quickstart demo database (spec/mcp.md).

* Connects ONLY to the quickstart demo database: the host must be local (or the compose service `db`, see
  LEXIGATE_MCP_ALLOWED_HOSTS) and schema `demo` must carry the marker comment written by `lexigate demo load`.
  Any other connection string is refused at start-up.
* No tool accepts SQL. Questions go through the same engine as `lexigate ask`: fixed queries first, then (only
  when an LLM key is configured) guarded model-generated SQL. SQL is returned only by get_evidence on request.
* Transports: stdio (default, for Claude Desktop and most clients) or streamable HTTP (`--transport http`).
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

from lexigate import __version__, config
from lexigate import semantic_map as sm
from lexigate.engine import CONFIRM_HINT_JA, Engine, FixedQueryPath, resolve_as_of
from lexigate.nlu import Parsed

DEMO_MARKER = "LexiGate quickstart demo data"
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1,::1,db"
DEFAULT_SESSION = "mcp"
EVIDENCE_ID = re.compile(r"^CLM-(C\d{4}):(lines|approvals|payments|trip|all)$")


class NotDemoDatabase(SystemExit):
    pass


def assert_demo_database(dsn: str) -> None:
    allowed = {h.strip().lower() for h in os.environ.get("LEXIGATE_MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",") if h.strip()}
    try:
        info = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        raise NotDemoDatabase("lexigate mcp: invalid connection string") from None
    hosts = [h for h in (info.get("host") or os.environ.get("PGHOST") or "").split(",") if h]
    for h in hosts:
        if not (h.startswith("/") or h.lower() in allowed):
            raise NotDemoDatabase(f"lexigate mcp only connects to the local demo database (host {h!r} is not allowed)")
    try:
        with psycopg.connect(dsn, connect_timeout=5) as cx:
            row = cx.execute("SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = 'demo'").fetchone()
    except psycopg.Error as e:
        raise NotDemoDatabase(f"lexigate mcp: cannot connect to the demo database ({type(e).__name__})") from None
    if not row or not (row[0] or "").startswith(DEMO_MARKER):
        raise NotDemoDatabase("lexigate mcp: this is not a LexiGate quickstart demo database (run `lexigate demo load` first)")


def _server_class():
    try:
        from mcp.server.mcpserver import MCPServer       # mcp >= 2
        return MCPServer, 2
    except ImportError:
        from mcp.server.fastmcp import FastMCP           # mcp 1.x
        return FastMCP, 1


def _wire(a: dict) -> dict:
    """Answer as returned to MCP clients (spec/mcp.md naming). Never contains SQL."""
    return {"status": "clarify" if a["path"] == "clarify" else "ok", "path": a["path"], "answer_ja": a["answer_ja"],
            "intent": a.get("intent"), "as_of": a.get("as_of"), "result": a.get("data") or {},
            "disclaimer": a.get("footer_ja"), "ai_generated": a.get("ai_generated", False),
            "hints": a.get("hints") or [], "sql_available": a.get("sql_available", False),
            "query_id": a.get("query_id"), "session_id": a.get("session_id"),
            "semantic_version": a.get("semantic_version"), "data_label": a.get("data_label")}


def create_server(engine: Engine | None = None, check_demo: bool = True):
    if engine is None:
        dsn = config.dsn()
        if check_demo:
            assert_demo_database(dsn)
        m = sm.load(config.semantic_map_path())
        engine = Engine(m, dsn, config.base_date(m.get("timezone", "Asia/Tokyo")), llm="auto")
    m = engine.ctx.semantic_map
    fixed = next((p for p in engine.paths if isinstance(p, FixedQueryPath)), FixedQueryPath(m))
    Server, _major = _server_class()
    server = Server(
        name="lexigate-demo",
        instructions=("LexiGate month-end close demo (fictional data, Japanese). Use `ask` for questions in Japanese; "
                      "show `hints` as follow-up suggestions; if `disclaimer` is set, show it in small red text. "
                      f"Call get_evidence(kind='sql_and_rationale') only when the user asks for the SQL ({CONFIRM_HINT_JA})."),
    )

    @server.tool(description="Ask a month-end close question in Japanese (e.g. 未払い出張精算はいくら？ / 勤怠未確定は何名？). "
                             "Fixed semantic queries first; AI-generated SQL only if enabled. Returns answer_ja, result, hints, "
                             "disclaimer (show in small red text when set). Keep the same session_id for follow-ups such as "
                             "「確認：さっきの SQL と根拠は？」.")
    def ask(question: str, session_id: str | None = None) -> dict:
        return _wire(engine.ask(question[:500], session_id=session_id or DEFAULT_SESSION).to_dict())

    @server.tool(description="List the questions the demo answers with fixed queries: intents, synonyms, example questions, "
                             "as-of modes and metric definitions.")
    def list_questions() -> dict:
        return _catalog(engine)

    @server.tool(description="Same as list_questions (spec/mcp.md name).")
    def list_metrics() -> dict:
        return _catalog(engine)

    @server.tool(description="Return the model-visible part of the semantic_map (intents, terms, metrics, relations, sql_guard). "
                             "No permissions or connection data.")
    def get_semantic_map() -> dict:
        from lexigate.generated import model_visible_map
        return {"semantic_version": m.get("semantic_version"), "intents": m.get("intents") or {}, **model_visible_map(m)}

    @server.tool(description="Map a question to an intent and parameters without running it.")
    def resolve_intent(question: str) -> dict:
        p = engine.nlu.parse(question[:500], engine.ctx.base)
        if p.intent == "help":
            return {"status": "clarify", "message": "質問の例は list_questions を参照してください。"}
        if p.intent in fixed.handlers and not ("breakdown" in p.notes and engine.ctx.generated_enabled):
            return {"status": "ok", "intent": p.intent, "params": _params(p), "path": "fixed"}
        if engine.ctx.generated_enabled and p.in_domain:
            return {"status": "ok", "intent": None, "params": _params(p), "path": "generated_sql"}
        return {"status": "unsupported", "message": "定型の質問に該当しません。list_questions の例を参考にしてください。"}

    @server.tool(description="Run a catalog intent with parameters (intent + params), or a question. "
                             "params: as_of {mode: previous_month_end | month_end(month YYYY-MM) | date(date YYYY-MM-DD)}, "
                             "claim_id, department.")
    def query_metric(intent: str | None = None, params: dict | None = None, question: str | None = None,
                     session_id: str | None = None) -> dict:
        if question:
            return ask(question, session_id)
        if not intent or intent not in fixed.handlers:
            return {"status": "error", "message": f"unknown intent; choose one of {sorted(fixed.handlers)}"}
        params = params or {}
        p = Parsed(question=f"[{intent}]", intent=intent, as_of=params.get("as_of"), claim_id=params.get("claim_id"),
                   department=params.get("department"), confidence=1.0, source="structured", in_domain=True)
        if p.as_of:
            try:
                resolve_as_of(p.as_of, engine.ctx.base, engine.ctx.tz)
            except (ValueError, KeyError, TypeError):
                return {"status": "error", "message": "invalid as_of"}
        a = fixed.answer(p, engine.ctx)
        a.semantic_version, a.data_label = m.get("semantic_version"), m.get("data_label_ja") or m.get("data_label")
        return _wire(a.to_dict())

    @server.tool(description="Evidence for a previous answer. kind='sql_and_rationale' with query_id returns the SQL and the "
                             "semantic definitions of an AI-generated answer (only when the user asks). evidence_id like "
                             "'CLM-C1002:lines' (or :approvals, :payments, :all) returns claim evidence.")
    def get_evidence(query_id: str | None = None, kind: str | None = None, evidence_id: str | None = None,
                     session_id: str | None = None) -> dict:
        if evidence_id:
            mm = EVIDENCE_ID.match(evidence_id)
            if not mm:
                return {"status": "error", "message": "evidence_id must look like CLM-C1002:lines"}
            r = claim_evidence(mm.group(1))
            section = mm.group(2)
            if section in ("lines", "approvals", "payments") and r.get("result"):
                r["result"] = {"claim_id": mm.group(1), section: r["result"].get(section, [])}
            return r
        if kind == "sql_and_rationale":
            return engine.evidence(query_id or engine.last_query_id(session_id or DEFAULT_SESSION))
        return {"status": "error", "message": "pass kind='sql_and_rationale' (with query_id) or an evidence_id"}

    @server.tool(description="Lines, approval history and settled payments of one expense claim (e.g. C1002), as of last "
                             "month-end unless as_of is given.")
    def claim_evidence(claim_id: str, as_of: dict | None = None) -> dict:
        cid = claim_id.strip().upper()
        if not re.fullmatch(r"C\d{4}", cid):
            return {"status": "error", "message": "claim_id must look like C1002"}
        return query_metric("claim_detail", {"claim_id": cid, **({"as_of": as_of} if as_of else {})})

    return server


def _params(p: Parsed) -> dict:
    return {k: v for k, v in {"as_of": p.as_of, "claim_id": p.claim_id, "department": p.department,
                              "also_attendance": p.also_attendance or None}.items() if v}


def _catalog(engine: Engine) -> dict:
    m = engine.ctx.semantic_map
    examples = {"unpaid_summary": ["未払い出張精算はいくら？", "1月末時点の未払いは？", "営業部の未払い出張精算はいくら？"],
                "attendance_unconfirmed": ["勤怠未確定は何名？", "先月末時点で勤怠未確定の案件は誰が確認すべき？"],
                "claim_detail": ["C1002 の明細を見せて"]}
    return {"semantic_version": m.get("semantic_version"), "base_date": engine.ctx.base.isoformat(),
            "intents": [{"intent": k, "title_ja": v.get("title_ja"), "synonyms": v.get("synonyms", []),
                         "examples": examples.get(k, [])} for k, v in (m.get("intents") or {}).items()],
            "as_of_modes": (m.get("time") or {}).get("as_of_modes") or {},
            "default_as_of": (m.get("time") or {}).get("default_as_of"),
            "metrics": [{"metric": k, "definition_ja": v.get("definition_ja"), "unit": v.get("unit")}
                        for k, v in (m.get("metrics") or {}).items()],
            "generated_sql": engine.ctx.generated_enabled}


def run(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8090) -> None:
    server = create_server()
    Server, major = _server_class()
    if transport == "stdio":
        server.run("stdio")
        return
    print(f"lexigate mcp: streamable HTTP on http://{host}:{port}/mcp (demo only, no authentication)", file=sys.stderr)
    if major >= 2:
        kwargs: dict[str, Any] = {}
        if host not in ("127.0.0.1", "localhost", "::1"):
            # e.g. 0.0.0.0 inside the compose container: keep DNS-rebinding protection and accept only
            # localhost Host / Origin headers (compose publishes the port on 127.0.0.1 only).
            from mcp.server.transport_security import TransportSecuritySettings
            kwargs["transport_security"] = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
                allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"])
        server.run("streamable-http", host=host, port=port, **kwargs)
    else:  # mcp 1.x keeps host/port in settings
        server.settings.host, server.settings.port = host, port
        server.run("streamable-http")


def _tool_names(server: Any) -> list[str]:  # used by tests on both SDK majors
    tm = getattr(server, "_tool_manager", None)
    return sorted(t.name for t in tm.list_tools()) if tm else []
