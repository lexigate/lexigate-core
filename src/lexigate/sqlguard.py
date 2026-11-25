# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""SQL guard for model-generated SQL (path 2).

A generated statement is accepted only if, after parsing with sqlglot (PostgreSQL dialect):

* it is exactly one statement, and that statement is a SELECT (or UNION / INTERSECT / EXCEPT of SELECTs);
* it contains no data-changing, DDL, locking, COPY, SELECT INTO, SET or transaction nodes;
* every relation it reads is in ``sql_guard.allowed_relations`` (CTE names excepted), and none matches
  ``sql_guard.forbidden``;
* every function it calls is in ``sql_guard.allowed_functions`` (or is one of the allowed relations used
  as a table function);
* the only placeholders are known parameters (``:cutoff``), which are replaced by typed literals here.

The accepted SQL is wrapped as ``SELECT * FROM (...) LIMIT n``. Execution (read-only transaction, low-privilege
role, statement_timeout, EXPLAIN cost check) is done by `lexigate.generated`.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

ALWAYS_ALLOWED = {"cast", "trycast", "case", "if", "nullif", "extract", "distinct", "paren", "interval", "currenttimestamp"}
_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable", "Command",
    "Copy", "Into", "Lock", "Set", "SetItem", "Transaction", "Commit", "Rollback", "Use", "Grant", "Pragma",
    "LoadData", "Kill") if hasattr(exp, n))
_SET_OPS = tuple(getattr(exp, n) for n in ("Union", "Intersect", "Except") if hasattr(exp, n))


class SQLRejected(ValueError):
    """The generated SQL does not satisfy the guard. The message is safe to log (no data)."""


@dataclass
class GuardResult:
    sql: str                                   # final SQL to execute (literals substituted, LIMIT wrapper)
    relations: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)


_CALL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\(")


def _fn_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    m = _CALL.match(node.sql(dialect="postgres"))
    return m.group(1).lower() if m else ""


def _is_call(node: exp.Expression) -> bool:
    """sqlglot models operators (AND, =, ...) as Func subclasses too; only real calls `name(...)` count."""
    return isinstance(node, exp.Anonymous) or bool(_CALL.match(node.sql(dialect="postgres")))


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p.lower()) for p in patterns)


def check(sql: str, guard: dict, params: dict[str, tuple[str, str]], max_rows: int = 200) -> GuardResult:
    """Validate and rewrite. `params` maps placeholder name -> (literal text, PostgreSQL type)."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SQLRejected("empty SQL")
    try:
        stmts = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLRejected(f"cannot parse: {str(e).splitlines()[0][:120]}") from None
    if len(stmts) != 1:
        raise SQLRejected("exactly one statement is allowed")
    root = stmts[0]
    if not isinstance(root, (exp.Select, *_SET_OPS)):
        raise SQLRejected(f"only SELECT is allowed (got {root.key.upper()})")
    allowed_rel = {r.lower() for r in guard.get("allowed_relations") or []}
    allowed_fn = {f.lower() for f in guard.get("allowed_functions") or []} | ALWAYS_ALLOWED
    forbidden = [f.lower() for f in guard.get("forbidden") or []]
    if not allowed_rel:
        raise SQLRejected("sql_guard.allowed_relations is empty")
    ctes = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    relations, functions, table_fns = [], [], set()
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SQLRejected(f"{node.key.upper()} is not allowed")
        if isinstance(node, exp.Table):
            if node.args.get("catalog"):
                raise SQLRejected("three-part names are not allowed")
            inner = node.this
            schema = node.args.get("db")
            if isinstance(inner, exp.Func):
                name = f"{schema.name}.{_fn_name(inner)}" if schema else _fn_name(inner)
                table_fns.add(id(inner))
            else:
                name = f"{schema.name}.{node.name}".lower() if schema else str(node.name).lower()
                if not schema and name in ctes:
                    continue
            name = name.lower()
            if name not in allowed_rel or _matches(name, forbidden):
                raise SQLRejected(f"relation {name} is not in sql_guard.allowed_relations")
            relations.append(name)
    for node in root.walk():
        if isinstance(node, exp.Func) and id(node) not in table_fns and _is_call(node):
            name = _fn_name(node)
            if name not in allowed_fn or _matches(name, forbidden):
                raise SQLRejected(f"function {name} is not allowed")
            functions.append(name)
    for ph in list(root.find_all(exp.Placeholder)):
        key = str(ph.name or "").lower()
        if key not in params:
            raise SQLRejected(f"unknown parameter :{key}" if key else "positional parameters are not allowed")
        literal, typ = params[key]
        ph.replace(exp.cast(exp.Literal.string(literal), typ))
    if root.find(exp.Parameter):
        raise SQLRejected("parameters are not allowed")
    inner_sql = root.sql(dialect="postgres", normalize_functions="lower")
    final = f"SELECT * FROM ({inner_sql}) AS lexigate_q LIMIT {int(max_rows) + 1}"
    return GuardResult(final, sorted(set(relations)), sorted(set(functions)))
