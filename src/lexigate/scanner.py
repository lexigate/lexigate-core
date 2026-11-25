# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""`lexigate scan`: read PostgreSQL metadata and write a semantic_map v0 *draft*.

The scanner is offline and deterministic (no model, no network besides the database). It reads catalog
metadata (tables, views, columns, types, primary / unique / foreign keys, CHECK value lists, comments) and,
optionally, the distinct values of low-cardinality text / boolean / enum columns. Everything it infers
(grain, aggregatable measures, date fields, metric and term candidates) is marked ``review: needs_review``.
A person must review the draft before a gateway uses it.

The session is read-only (default_transaction_read_only, statement_timeout). Columns that look like
identifiers or personal data (names, e-mail, phone, address, ...) are never sampled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql

from lexigate import __version__
from lexigate.semantic_map import validation_errors

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
PII_OR_ID = re.compile(r"(name|mail|phone|tel|fax|address|addr|zip|postal|birth|password|passwd|secret|token|"
                       r"salary|account|card|iban|ssn|my_?number|_id$|^id$|_ref$|_number$|_no$|uuid|hash)", re.I)
FREE_TEXT = re.compile(r"(comment|note|memo|reason|remark|description|title|message|body|purpose|destination)", re.I)
NOT_A_MEASURE = re.compile(r"(seq|version|stage|_no$|^no$|order|rank|level|year|month|day|hour|minute|code|status|flag)", re.I)
MONEY = re.compile(r"(_yen$|_jpy$|_usd$|amount|price|cost|total|fee|balance|_amt$)", re.I)
INTEGER_TYPES = {"smallint", "integer", "bigint"}
NUMERIC_PREFIXES = ("numeric", "decimal", "real", "double precision", "money")
CHECK_IN = re.compile(r"\(\(?(\w+)\)?(?:::\w+)? = ANY \(\(?ARRAY\[(.*?)\]")
CHECK_LITERAL = re.compile(r"'((?:[^']|'')*)'")
DEFAULT_FUNCTIONS = ["sum", "count", "min", "max", "avg", "coalesce", "date_trunc", "to_char"]
DEFAULT_FORBIDDEN = ["pg_catalog.*", "information_schema.*", "set_config", "current_setting", "pg_sleep",
                     "dblink*", "lo_*", "pg_read_file", "pg_ls_dir", "copy"]


@dataclass
class ScanOptions:
    schemas: list[str] = field(default_factory=list)       # empty = every non-system schema
    sample_values: bool = True
    max_distinct: int = 12
    sample_rows: int = 100_000                              # rows read per column when sampling
    max_value_length: int = 40
    timezone: str | None = None                             # default: the server's TimeZone
    currency: str | None = None
    statement_timeout_ms: int = 5000
    include_views: bool = True
    generated_at: str | None = None                         # override for reproducible output


def _connect(dsn: str, opts: ScanOptions) -> psycopg.Connection:
    cx = psycopg.connect(dsn, autocommit=True)
    cx.execute("SET default_transaction_read_only = on")
    cx.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(int(opts.statement_timeout_ms))))
    return cx


def _semantic_type(name: str, typ: str, is_key: bool, values: list | None) -> str:
    t = typ.lower()
    if is_key:
        return "identifier"
    if t == "boolean":
        return "flag"
    if t == "date":
        return "date"
    if t.startswith("timestamp"):
        return "timestamp"
    if (t in INTEGER_TYPES or t.startswith(NUMERIC_PREFIXES)) and MONEY.search(name):
        return "money"
    if t in INTEGER_TYPES or t.startswith(NUMERIC_PREFIXES):
        return "number"
    if values is not None:
        return "category"
    if name.endswith("_id") or name == "id":
        return "identifier"
    return "text"


def _term_from_comment(comment: str | None) -> str | None:
    if not comment:
        return None
    head = re.split(r"[（(。、:：,]", comment.strip(), maxsplit=1)[0].strip()
    return head if 0 < len(head) <= 20 else None


def introspect(cx: psycopg.Connection, opts: ScanOptions) -> dict[str, Any]:
    kinds = ["r", "p"] + (["v", "m"] if opts.include_views else [])
    if opts.schemas:
        where, params = "n.nspname = ANY(%s)", [opts.schemas]
    else:
        where, params = "n.nspname <> ALL(%s) AND n.nspname NOT LIKE 'pg_temp%%' AND n.nspname NOT LIKE 'pg_toast%%'", [list(SYSTEM_SCHEMAS)]
    rels = cx.execute(f"""
        SELECT c.oid, n.nspname, c.relname, c.relkind, obj_description(c.oid, 'pg_class'), c.reltuples::bigint
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = ANY(%s) AND NOT c.relispartition AND {where}
        ORDER BY n.nspname, c.relname""", [kinds, *params]).fetchall()
    oids = [r[0] for r in rels]
    info: dict[int, dict] = {}
    for oid, nsp, name, kind, comment, tuples in rels:
        info[oid] = {"schema": nsp, "name": name, "qualified": f"{nsp}.{name}",
                     "kind": {"r": "table", "p": "table", "v": "view", "m": "materialized_view"}[kind],
                     "comment": comment, "row_estimate": int(tuples) if tuples is not None and tuples >= 0 else None,
                     "columns": {}, "attnum": {}, "pk": [], "unique": [], "fks": [], "checks": {}}
    for relid, attnum, attname, typ, notnull, comment, typcat in cx.execute("""
            SELECT a.attrelid, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                   col_description(a.attrelid, a.attnum), t.typcategory
            FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
            WHERE a.attrelid = ANY(%s) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attrelid, a.attnum""", [oids]).fetchall():
        info[relid]["columns"][attname] = {"type": typ, "nullable": not notnull, "comment": comment, "typcategory": typcat}
        info[relid]["attnum"][attnum] = attname
    enum_values = {}
    for typname_ns, labels in cx.execute("""
            SELECT format_type(t.oid, NULL), array_agg(e.enumlabel ORDER BY e.enumsortorder)
            FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid GROUP BY t.oid""").fetchall():
        enum_values[typname_ns] = list(labels)
    cons = cx.execute("""
        SELECT con.conrelid, con.contype, con.conkey, con.confrelid, con.confkey, pg_get_constraintdef(con.oid),
               fn.nspname, fc.relname
        FROM pg_constraint con
        LEFT JOIN pg_class fc ON fc.oid = con.confrelid
        LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace
        WHERE con.conrelid = ANY(%s) AND con.contype IN ('p', 'u', 'f', 'c')
        ORDER BY con.conrelid, con.contype, con.conname""", [oids]).fetchall()
    foreign_cols: dict[int, dict[int, str]] = {}
    fk_targets = {c[3] for c in cons if c[1] == "f"}
    if fk_targets:
        for relid, attnum, attname in cx.execute(
                "SELECT attrelid, attnum, attname FROM pg_attribute WHERE attrelid = ANY(%s) AND attnum > 0",
                [list(fk_targets)]).fetchall():
            foreign_cols.setdefault(relid, {})[attnum] = attname
    for relid, ctype, conkey, confrelid, confkey, condef, fnsp, frel in cons:
        r = info[relid]
        cols = [r["attnum"][k] for k in (conkey or []) if k in r["attnum"]]
        if ctype == "p":
            r["pk"] = cols
        elif ctype == "u":
            r["unique"].append(cols)
        elif ctype == "f":
            r["fks"].append({"columns": cols, "target": f"{fnsp}.{frel}",
                             "target_columns": [foreign_cols.get(confrelid, {}).get(k, "?") for k in (confkey or [])]})
        elif ctype == "c":
            m = CHECK_IN.search(condef)
            if m and m.group(1) in r["columns"]:
                r["checks"][m.group(1)] = [v.replace("''", "'") for v in CHECK_LITERAL.findall(m.group(2))]
    for r in info.values():
        for cname, c in r["columns"].items():
            if c["type"] in enum_values:
                r["checks"].setdefault(cname, enum_values[c["type"]])
    return {"relations": info, "server": {
        "version": cx.execute("SHOW server_version").fetchone()[0],
        "timezone": cx.execute("SHOW TimeZone").fetchone()[0],
        "database": cx.execute("SELECT current_database()").fetchone()[0]}}


def _sample(cx: psycopg.Connection, rel: dict, col: str, opts: ScanOptions) -> list | None:
    q = sql.SQL("SELECT v::text, count(*) FROM (SELECT {c} AS v FROM {s}.{t} LIMIT {n}) x "
                "WHERE v IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {k}").format(
        c=sql.Identifier(col), s=sql.Identifier(rel["schema"]), t=sql.Identifier(rel["name"]),
        n=sql.Literal(opts.sample_rows), k=sql.Literal(opts.max_distinct + 1))
    try:
        rows = cx.execute(q).fetchall()
    except psycopg.Error:
        return None
    if not rows or len(rows) > opts.max_distinct:
        return None
    total = sum(n for _, n in rows)
    if len(rows) > 1 and len(rows) >= total:          # every value distinct -> not a category
        return None
    if any(len(v) > opts.max_value_length for v, _ in rows):
        return None
    return sorted(v for v, _ in rows)


def _samplable(cname: str, col: dict, keyish: set[str]) -> bool:
    return col["typcategory"] in ("S", "B", "E") and cname not in keyish and not PII_OR_ID.search(cname) \
        and not FREE_TEXT.search(cname)


def build_draft(meta: dict, cx: psycopg.Connection | None, opts: ScanOptions) -> dict:
    rels_out: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    terms: dict[str, Any] = {}
    currency_guess = None
    for r in meta["relations"].values():
        q = r["qualified"]
        keyish = set(r["pk"]) | {c for u in r["unique"] for c in u if len(u) == 1} | {c for fk in r["fks"] for c in fk["columns"]}
        columns, column_info, date_fields, aggregatable = {}, {}, [], []
        for cname, c in r["columns"].items():
            columns[cname] = c["type"]
            ci: dict[str, Any] = {"type": c["type"], "nullable": c["nullable"]}
            if c["comment"]:
                ci["description"] = c["comment"]
            if cname in r["pk"]:
                ci["primary_key"] = True
            refs = [f"{fk['target']}.{fk['target_columns'][fk['columns'].index(cname)]}" for fk in r["fks"] if cname in fk["columns"]]
            if refs:
                ci["references"] = refs[0] if len(refs) == 1 else refs
            if cname in r["checks"]:
                ci["allowed_values"] = r["checks"][cname]
            values = None
            if opts.sample_values and cx is not None and cname not in r["checks"] and _samplable(cname, c, keyish):
                values = _sample(cx, r, cname, opts)
                if values is not None:
                    ci["sample_values"] = values
            st = _semantic_type(cname, c["type"], cname in keyish, values if values is not None else r["checks"].get(cname))
            ci["semantic_type"] = st
            if st in ("date", "timestamp"):
                date_fields.append(cname)
            if (st == "money" or (st == "number" and not NOT_A_MEASURE.search(cname))) and cname not in keyish:
                aggregatable.append(cname)
            if st == "money" and cname.lower().endswith("_yen"):
                currency_guess = currency_guess or "JPY"
            column_info[cname] = ci
            term = _term_from_comment(c["comment"])
            if term:
                terms.setdefault(term, {"maps_to": f"{q}.{cname}", "candidates": []})["candidates"].append(f"{q}.{cname}")
            if st == "money" and r["kind"] == "table":
                mname = f"{r['name']}__{cname}__sum"
                metrics[mname] = {
                    "definition": f"DRAFT: sum of {q}.{cname}" + (f" ({c['comment']})" if c["comment"] else ""),
                    "source": q, "grain": "relation total (filters to be defined)", "measure": f"sum({cname})",
                    "review": {"status": "needs_review", "inferred": ["measure", "grain", "unit"],
                               "note": "Add the business filter (status, cutoff) before use."}}
        joins: dict[str, Any] = {}
        for fk in r["fks"]:
            same = fk["columns"] == fk["target_columns"]
            val = ", ".join(fk["columns"]) if same else ", ".join(f"{a} = {b}" for a, b in zip(fk["columns"], fk["target_columns"]))
            if fk["target"] in joins:
                prev = joins[fk["target"]]
                joins[fk["target"]] = (prev if isinstance(prev, list) else [prev]) + [val]
            else:
                joins[fk["target"]] = val
        rel: dict[str, Any] = {"kind": r["kind"], "description": r["comment"],
                               "grain": f"one row per {', '.join(r['pk'])}" if r["pk"] else "unknown (no primary key)",
                               "key": r["pk"], "columns": columns}
        if date_fields:
            rel["date_fields"] = date_fields
        rel["aggregatable"] = aggregatable
        if joins:
            rel["joins"] = joins
        if r["row_estimate"] is not None:
            rel["row_estimate"] = r["row_estimate"]
        rel["column_info"] = column_info
        rel["review"] = {"status": "needs_review", "inferred": ["grain", "aggregatable", "date_fields", "semantic_type"]
                         + (["joins"] if joins else [])}
        rels_out[q] = rel
        tterm = _term_from_comment(r["comment"])
        if tterm:
            terms.setdefault(tterm, {"maps_to": q, "candidates": []})["candidates"].append(q)
    for t in terms.values():
        if len(t["candidates"]) <= 1:
            t.pop("candidates")
        t["review"] = "needs_review"
    tz = opts.timezone or meta["server"]["timezone"]
    generated_at = opts.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    doc: dict[str, Any] = {
        "semantic_version": f"draft-{generated_at[:10]}",
        "data_label": f"Draft generated by lexigate scan from database {meta['server']['database']}",
        "timezone": tz,
    }
    if opts.currency or currency_guess:
        doc["currency"] = opts.currency or currency_guess
    doc["review"] = {"status": "draft", "note": (
        f"Generated by lexigate scan {__version__} at {generated_at} (PostgreSQL {meta['server']['version']}; "
        f"schemas: {', '.join(opts.schemas) if opts.schemas else 'all non-system'}). Every item marked needs_review "
        "was inferred from metadata. Confirm grains, metrics and terms; write intents and synonyms; set permissions; "
        "choose sql_guard.allowed_relations; then change status to reviewed.")}
    doc["time"] = {"cutoff_rule": "TODO: define the as-of / cutoff convention (see spec/semantic_map.md#time).",
                   "as_of_modes": {}, "base_date_source": "server"}
    doc["intents"] = {}
    doc["terms"] = dict(sorted(terms.items()))
    doc["metrics"] = dict(sorted(metrics.items()))
    doc["relations"] = rels_out
    doc["sql_guard"] = {"statement": "single SELECT only", "allowed_relations": [],
                        "allowed_functions": DEFAULT_FUNCTIONS, "parameters_required": [],
                        "forbidden": DEFAULT_FORBIDDEN,
                        "review": {"status": "needs_review",
                                   "note": "Empty on purpose: list only the relations a generated query may read."}}
    return doc


def scan(dsn: str, opts: ScanOptions | None = None) -> dict:
    opts = opts or ScanOptions()
    with _connect(dsn, opts) as cx:
        meta = introspect(cx, opts)
        if not meta["relations"]:
            raise SystemExit("No tables or views found" + (f" in schemas {opts.schemas}" if opts.schemas else ""))
        doc = build_draft(meta, cx, opts)
    errs = validation_errors(doc)
    if errs:   # a bug in the scanner, never silently written
        raise RuntimeError("draft does not validate against semantic_map v0:\n  " + "\n  ".join(errs))
    return doc


HEADER = """# semantic_map v0 DRAFT, generated by `lexigate scan`. NOT reviewed.
# Items with `review: needs_review` were inferred from database metadata (types, keys, comments) and,
# where enabled, sampled values of low-cardinality columns. A person must review this file before use.
# Spec: https://github.com/lexigate/lexigate-core/blob/main/spec/semantic_map.md
"""


def to_yaml(doc: dict) -> str:
    import yaml

    class _Dumper(yaml.SafeDumper):
        pass

    _Dumper.add_representer(type(None), lambda d, _: d.represent_scalar("tag:yaml.org,2002:null", "null"))
    return HEADER + yaml.dump(doc, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=120, default_flow_style=False)
