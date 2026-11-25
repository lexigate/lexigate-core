# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""`lexigate demo load`: (re)create schemas `demo` and `sem` and load examples/monthend/data/*.csv.

Idempotent: the schemas are dropped and recreated in one transaction. File hashes and row counts are checked
against manifest.json before anything is committed. Intended for a local demo database only.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql

SQL_DIR = Path(__file__).resolve().parent / "sql"
TABLE_ORDER = ["departments", "employees", "employment_assignments", "close_periods", "shift_types", "shift_assignments",
               "clock_events", "attendance_days", "attendance_corrections", "leave_requests", "policy_versions",
               "trip_requests", "expense_claims", "expense_lines", "claim_approval_events", "payment_entries"]


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_files(data_dir: Path) -> dict:
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    bad = [f for f, h in manifest.get("file_hashes", {}).items() if (data_dir / f).exists() and _sha256(data_dir / f) != h]
    missing = [t for t in TABLE_ORDER if not (data_dir / f"{t}.csv").exists()]
    if bad or missing:
        raise SystemExit(f"demo data check failed: hash mismatch {bad}, missing {missing}")
    return manifest


def load(dsn: str, data_dir: Path) -> dict:
    manifest = check_files(data_dir)
    with psycopg.connect(dsn) as cx:
        cx.execute("SET client_min_messages = warning")
        cx.execute("DROP SCHEMA IF EXISTS sem CASCADE")
        cx.execute("DROP SCHEMA IF EXISTS demo CASCADE")
        cx.execute((SQL_DIR / "10_tables.sql").read_text(encoding="utf-8"))
        cx.execute((SQL_DIR / "15_comments.sql").read_text(encoding="utf-8"))
        cx.execute("SET search_path = public")
        counts = {}
        for t in TABLE_ORDER:
            path = data_dir / f"{t}.csv"
            with path.open(encoding="utf-8", newline="") as f:
                cols = next(csv.reader(f))
            stmt = sql.SQL("COPY demo.{t} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')").format(
                t=sql.Identifier(t), cols=sql.SQL(", ").join(map(sql.Identifier, cols)))
            with cx.cursor().copy(stmt) as cp, path.open("rb") as f:
                while chunk := f.read(1 << 16):
                    cp.write(chunk)
            counts[t] = cx.execute(sql.SQL("SELECT count(*) FROM demo.{}").format(sql.Identifier(t))).fetchone()[0]
        wrong = {t: (n, manifest["row_counts"][t]) for t, n in counts.items() if n != manifest["row_counts"].get(t)}
        if wrong:
            cx.rollback()
            raise SystemExit(f"row counts differ from manifest: {wrong}")
        cx.execute((SQL_DIR / "40_semantic.sql").read_text(encoding="utf-8"))
        cx.execute(sql.SQL("COMMENT ON SCHEMA demo IS {}").format(sql.Literal(
                       f"LexiGate quickstart demo data {manifest['data_version']} — {manifest['label']}")))
        cx.execute("ANALYZE")
        cx.commit()
    return {"data_version": manifest["data_version"], "rows": sum(counts.values()), "tables": len(counts)}
