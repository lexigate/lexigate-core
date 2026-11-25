# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Path 2: model-generated SQL, used only when the fixed queries cannot answer.

Flow: question -> LLM (sees the question and the model-visible semantic_map, never rows) -> one SELECT ->
`lexigate.sqlguard.check` (allow lists, single SELECT, LIMIT) -> execution in a READ ONLY transaction as the
NOLOGIN role ``lexigate_query`` (EXECUTE on sem.* only), with statement_timeout, an EXPLAIN cost ceiling and
search_path = pg_catalog -> a deterministic Japanese answer built from the rows (no second model call).

The answer is marked as AI-generated (soft footer, extra "show me the SQL" hint). The SQL and the semantic
definitions used are kept server-side and returned only when the user asks (`Engine` session state).
"""
from __future__ import annotations

import datetime as dt
import decimal
import json
import os
from typing import Any

import psycopg
import sqlglot
from psycopg import sql as pgsql

from lexigate.engine import STARTER_HINTS, Answer, Context, resolve_as_of, yen
from lexigate.llm import LLMError, LLMProvider
from lexigate.nlu import Parsed
from lexigate.sqlguard import SQLRejected, check

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean", "description": "false if the listed relations cannot answer the question"},
        "sql": {"type": ["string", "null"], "description": "ONE PostgreSQL SELECT over the listed relations only; use :cutoff"},
        "title_ja": {"type": "string", "description": "何を集計したかの短い見出し（日本語）"},
        "basis_ja": {"type": "string", "description": "使った指標・条件の短い説明（日本語）"},
        "columns": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "label_ja": {"type": "string"}}, "required": ["name", "label_ja"]}},
    },
    "required": ["answerable", "sql", "title_ja", "basis_ja"],
}


class GenerationDisabled(RuntimeError):
    pass


def model_visible_map(m: dict) -> dict:
    """The subset of the semantic_map a model may see: no permissions, no connection data."""
    guard = m.get("sql_guard") or {}
    rels = {k: v for k, v in (m.get("relations") or {}).items() if k in set(guard.get("allowed_relations") or [])}
    return {"timezone": m.get("timezone"), "currency": m.get("currency"), "money": m.get("money"),
            "time": {k: v for k, v in (m.get("time") or {}).items() if k in ("cutoff_rule", "cutoff_rule_ja")},
            "terms": m.get("terms") or {}, "metrics": m.get("metrics") or {}, "relations": rels,
            "rules": m.get("rules") or {},
            "sql_guard": {k: guard.get(k) for k in ("statement", "allowed_relations", "allowed_functions", "parameters_required")}}


def build_system_prompt(m: dict) -> str:
    vis = model_visible_map(m)
    return (
        "You write ONE read-only PostgreSQL SELECT for a month-end close analytics gateway (LexiGate).\n"
        "Rules:\n"
        "- Read ONLY the relations listed under `relations`. Call them exactly as shown in their `call` field, "
        "e.g. FROM sem.claim_balance_at(:cutoff) b. Never reference any other schema or table (no demo.*, pg_catalog.*).\n"
        "- `:cutoff` is the exclusive upper bound (timestamptz) of the as-of point. Always pass :cutoff where a relation takes it. "
        "Do not invent other parameters; put literal values (IDs, dates) inline as SQL literals.\n"
        f"- Use only these functions: {', '.join(vis['sql_guard']['allowed_functions'] or [])} (CASE / CAST are fine).\n"
        "- Follow the metric definitions and filters (for 未払い: balance_yen > 0 AND anomaly_code IS NULL). Money is integer yen.\n"
        "- Give every output column a short snake_case alias, add ORDER BY, return at most 50 rows, and prefer aggregates.\n"
        "- If the relations cannot answer the question, set answerable=false and sql=null.\n"
        "- title_ja: a short Japanese heading; basis_ja: one Japanese sentence on the metric/filters used; "
        "columns: Japanese labels for each output column.\n\n"
        "semantic_map (model-visible subset):\n" + json.dumps(vis, ensure_ascii=False, default=str))


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def _fmt(name: str, label: str, v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, int) and not isinstance(v, bool) and (name.endswith("_yen") or any(k in label for k in ("円", "金額", "残高"))):
        return yen(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


class GeneratedSQLPath:
    name = "generated"

    def __init__(self, semantic_map: dict, provider: LLMProvider, role: str | None = None,
                 max_rows: int | None = None, timeout_ms: int | None = None, max_cost: float | None = None):
        guard = semantic_map.get("sql_guard") or {}
        if not guard.get("allowed_relations"):
            raise GenerationDisabled("sql_guard.allowed_relations is empty")
        self.map, self.guard, self.provider = semantic_map, guard, provider
        self.role = role or os.environ.get("LEXIGATE_SQL_ROLE", "lexigate_query")
        self.max_rows = max_rows or int(os.environ.get("LEXIGATE_SQL_MAX_ROWS", "50"))
        self.timeout_ms = timeout_ms or int(os.environ.get("LEXIGATE_SQL_TIMEOUT_MS", "3000"))
        self.max_cost = max_cost or float(os.environ.get("LEXIGATE_SQL_MAX_COST", "1000000"))
        self.system = build_system_prompt(semantic_map)

    # -------------------------------------------------------------- execution
    def execute(self, ctx: Context, final_sql: str) -> tuple[list[str], list[tuple]]:
        with psycopg.connect(ctx.dsn, autocommit=False) as cx:
            cx.execute("SET TRANSACTION READ ONLY")
            cx.execute(pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(int(self.timeout_ms))))
            cx.execute(pgsql.SQL("SET LOCAL ROLE {}").format(pgsql.Identifier(self.role)))
            cx.execute("SET LOCAL search_path = pg_catalog")
            plan = cx.execute("EXPLAIN (FORMAT JSON) " + final_sql).fetchone()[0]
            cost = float(plan[0]["Plan"]["Total Cost"])
            if cost > self.max_cost:
                raise SQLRejected(f"estimated cost {cost:.0f} exceeds the limit")
            cur = cx.execute(final_sql)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
            cx.rollback()
        return cols, rows

    # -------------------------------------------------------------- path
    def answer(self, parsed: Parsed, ctx: Context) -> Answer | None:
        if parsed.intent in ("help", "confirm_sql") or "out_of_domain" in parsed.notes or not parsed.in_domain:
            return None
        default = (self.map.get("time") or {}).get("default_as_of")
        as_of_req = parsed.as_of or ({"mode": default} if default else None)
        if as_of_req is None:
            return None
        try:
            as_of = resolve_as_of(as_of_req, ctx.base, ctx.tz)
        except ValueError:
            return None
        as_of["assumed"] = parsed.as_of is None
        user = (f"質問: {parsed.question}\n時点: {as_of['label_ja']}（:cutoff = {as_of['cutoff']}、排他的上限）\n"
                f"基準日: {ctx.base.isoformat()}")
        params = {"cutoff": (as_of["cutoff"], "timestamptz")}
        feedback, last_err, result = "", "none", None
        for _attempt in range(2):
            try:
                out = self.provider.generate_json(self.system, user + feedback, OUTPUT_SCHEMA)
            except LLMError:
                last_err = "llm"
                break
            if not out.get("answerable") or not out.get("sql"):
                return None
            try:
                g = check(str(out["sql"]), self.guard, params, self.max_rows)
                cols, rows = self.execute(ctx, g.sql)
                result = (out, g, cols, rows)
                break
            except SQLRejected as e:
                last_err, feedback = "rejected", f"\n\n前回の SQL は拒否されました（{e}）。規則に従って作り直してください。"
            except psycopg.Error as e:
                msg = (getattr(getattr(e, "diag", None), "message_primary", None) or type(e).__name__)[:200]
                last_err, feedback = "db", f"\n\n前回の SQL は実行エラーでした（{msg}）。作り直してください。"
        if result is None:
            return Answer("clarify", "この質問には定型の回答がなく、AI による照会もできませんでした。次のように聞いてみてください。",
                          hints=STARTER_HINTS[:2], data={"generated_error": last_err})
        out, g, cols, rows = result
        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        labels = {c["name"]: c["label_ja"] for c in (out.get("columns") or []) if isinstance(c, dict) and c.get("name")}
        title = str(out.get("title_ja") or "集計結果")
        text = self._text(as_of, title, cols, rows, labels, truncated)
        data = {"columns": [{"name": c, "label_ja": labels.get(c, c)} for c in cols],
                "rows": [{c: _jsonable(v) for c, v in zip(cols, r)} for r in rows],
                "row_count": len(rows), "truncated": truncated}
        a = Answer("generated", text, data=data, hints=STARTER_HINTS[:2])
        a.as_of = as_of
        a.rationale = self._rationale(parsed, as_of, g, out)
        return a.mark_generated()

    def _text(self, as_of, title, cols, rows, labels, truncated) -> str:
        head = f"{as_of['label_ja']}：{title}"
        if as_of.get("assumed"):
            head = f"（時点の指定がないため{as_of['label_ja']}で回答）\n" + head
        if not rows:
            return head + "\n該当するデータはありません。"
        lab = lambda c: labels.get(c, c)  # noqa: E731
        if len(rows) == 1 and len(cols) == 1:
            return f"{head}\n{lab(cols[0])}：{_fmt(cols[0], lab(cols[0]), _jsonable(rows[0][0]))}"
        lines = []
        for r in rows[:20]:
            vals = [_jsonable(v) for v in r]
            first = _fmt(cols[0], lab(cols[0]), vals[0])
            rest = "、".join(f"{lab(c)} {_fmt(c, lab(c), v)}" for c, v in zip(cols[1:], vals[1:]))
            lines.append(f"・{first}" + (f"：{rest}" if rest else ""))
        more = len(rows) - 20
        tail = f"\nほか {more} 件" if more > 0 else ""
        return head + "\n" + "\n".join(lines) + tail + ("（上限で打ち切り）" if truncated else "")

    def _rationale(self, parsed: Parsed, as_of: dict, g, out: dict) -> dict:
        rels = self.map.get("relations") or {}
        used = {r: {k: rels.get(r, {}).get(k) for k in ("call", "grain", "key", "columns", "note") if rels.get(r, {}).get(k)}
                for r in g.relations}
        metrics = {k: {f: v.get(f) for f in ("definition_ja", "definition", "source", "measure", "filter", "unit") if v.get(f)}
                   for k, v in (self.map.get("metrics") or {}).items()
                   if any(str(v.get("source", "")).startswith(r) for r in g.relations)}
        terms = {k: v for k, v in (self.map.get("terms") or {}).items() if k in parsed.question}
        rules = {k: v for k, v in (self.map.get("rules") or {}).items() if k == "CUTOFF"}
        inner = g.sql[len("SELECT * FROM ("): g.sql.rindex(") AS lexigate_q")]
        try:
            pretty = sqlglot.transpile(inner, read="postgres", write="postgres", pretty=True, normalize_functions="lower")[0]
        except Exception:  # noqa: BLE001
            pretty = inner
        return {"sql": pretty, "executed_sql": g.sql, "relations": used, "metrics": metrics, "terms": terms, "rules": rules,
                "functions": g.functions, "as_of": as_of, "basis_ja": out.get("basis_ja"),
                "model": f"{self.provider.name}:{self.provider.model}"}
