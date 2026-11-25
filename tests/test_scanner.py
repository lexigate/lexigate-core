# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
import yaml

from lexigate import semantic_map as sm
from lexigate.cli import main
from lexigate.scanner import ScanOptions, scan, to_yaml


def test_scan_demo_schema(demo_db):
    doc = scan(demo_db["dsn"], ScanOptions(schemas=["demo"], generated_at="2026-01-01T00:00:00+00:00"))
    assert sm.validation_errors(doc) == []
    assert doc["review"]["status"] == "draft"
    assert doc["semantic_version"] == "draft-2026-01-01"
    assert doc["currency"] == "JPY"                      # inferred from *_yen columns
    rels = doc["relations"]
    assert len(rels) == 16 and all(k.startswith("demo.") for k in rels)
    claims = rels["demo.expense_claims"]
    assert claims["key"] == ["claim_id"]
    assert claims["grain"] == "one row per claim_id"
    assert claims["joins"]["demo.trip_requests"] == "trip_id, employee_id"     # composite FK
    assert claims["description"].startswith("経費精算書")
    assert claims["column_info"]["business_type"]["allowed_values"] == ["TRIP", "GENERAL"]   # from CHECK
    assert claims["review"]["status"] == "needs_review"
    lines = rels["demo.expense_lines"]
    assert lines["aggregatable"] == ["claimed_amount_yen", "approved_amount_yen"]
    assert lines["column_info"]["approved_amount_yen"]["semantic_type"] == "money"
    assert lines["column_info"]["claim_id"]["references"] == "demo.expense_claims.claim_id"
    pay = rels["demo.payment_entries"]
    assert pay["joins"]["demo.payment_entries"] == "reverses_entry_id = entry_id"            # self reference
    appr = rels["demo.claim_approval_events"]["column_info"]
    assert appr["actor_role"]["sample_values"] == sorted(["申請者", "部門承認者", "経理", "財務"])
    assert "sample_values" not in appr["comment_ja"]                 # free text is never sampled
    emp = rels["demo.employees"]["column_info"]
    assert "sample_values" not in emp["display_name_ja"]             # names are never sampled
    assert "expense_lines__approved_amount_yen__sum" in doc["metrics"]
    assert doc["metrics"]["expense_lines__approved_amount_yen__sum"]["review"]["status"] == "needs_review"
    assert doc["terms"]["精算書番号"]["review"] == "needs_review"
    assert doc["sql_guard"]["allowed_relations"] == []              # nothing allowed until a person decides
    assert doc["intents"] == {}


def test_scan_no_samples_and_yaml_roundtrip(demo_db):
    doc = scan(demo_db["dsn"], ScanOptions(schemas=["demo", "sem"], sample_values=False))
    text = to_yaml(doc)
    assert text.startswith("# semantic_map v0 DRAFT")
    again = yaml.safe_load(text)
    assert sm.validation_errors(again) == []
    assert not any("sample_values" in c for r in again["relations"].values() for c in r["column_info"].values())


def test_scan_cli_writes_file(demo_db, tmp_path):
    out = tmp_path / "draft.yaml"
    assert main(["scan", "--dsn", demo_db["dsn"], "--schema", "demo", "-o", str(out)]) == 0
    assert main(["validate", str(out)]) == 0
    assert "demo.trip_requests" in yaml.safe_load(out.read_text(encoding="utf-8"))["relations"]


def test_scan_session_is_read_only(demo_db):
    import psycopg
    from lexigate.scanner import _connect
    with _connect(demo_db["dsn"], ScanOptions()) as cx:
        try:
            cx.execute("CREATE TABLE demo.should_fail (x int)")
            raise AssertionError("write succeeded")
        except psycopg.errors.ReadOnlySqlTransaction:
            pass
