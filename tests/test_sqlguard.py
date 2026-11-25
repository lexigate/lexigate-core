# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
import pytest

from lexigate.sqlguard import SQLRejected, check

P = {"cutoff": ("2026-03-01T00:00:00+09:00", "timestamptz")}


@pytest.fixture(scope="module")
def guard(semantic_map):
    return semantic_map["sql_guard"]


@pytest.mark.parametrize("sql", [
    "SELECT attributed_department_ja, sum(balance_yen) FROM sem.claim_balance_at(:cutoff) WHERE balance_yen > 0 GROUP BY 1",
    "WITH b AS (SELECT * FROM sem.claim_balance_at(:cutoff)) SELECT count(*) FROM b",
    "SELECT c.claim_id, i.reason_code FROM sem.claim_balance_at(:cutoff) c JOIN sem.attendance_issues_at(:cutoff) i ON i.claim_id = c.claim_id",
    "SELECT date_trunc('month', trip_start), CASE WHEN count(*) > 1 THEN 'a' END, CAST(sum(balance_yen) AS bigint) FROM sem.claim_balance_at(:cutoff) GROUP BY 1",
    'SELECT sum(balance_yen) FROM "sem"."claim_balance_at"(:cutoff);',
])
def test_accepts(guard, sql):
    r = check(sql, guard, P, max_rows=50)
    assert r.sql.startswith("SELECT * FROM (") and r.sql.endswith("LIMIT 51")
    assert ":cutoff" not in r.sql and "CAST('2026-03-01T00:00:00+09:00' AS TIMESTAMPTZ)" in r.sql
    assert set(r.relations) <= set(guard["allowed_relations"])


@pytest.mark.parametrize("sql, why", [
    ("SELECT * FROM demo.employees", "relation"),
    ("SELECT 1; DROP TABLE demo.employees", "one statement"),
    ("DELETE FROM demo.expense_lines", "only SELECT"),
    ("UPDATE demo.payment_entries SET amount_yen = 0", "only SELECT"),
    ("WITH d AS (DELETE FROM demo.x RETURNING *) SELECT * FROM d", "DELETE"),
    ("SELECT * FROM sem.claim_balance_at(:cutoff) UNION SELECT * FROM demo.employees", "relation"),
    ("SELECT * FROM sem.claim_balance_at(:cutoff) WHERE claim_id IN (SELECT claim_id FROM demo.expense_claims)", "relation"),
    ("SELECT * FROM sem.claim_balance_at(:cutoff) b, LATERAL (SELECT * FROM demo.employees) e", "relation"),
    ("SELECT * FROM sem.claim_balance_at(:cutoff) FOR UPDATE", "LOCK"),
    ("SELECT * INTO t FROM sem.claim_balance_at(:cutoff)", "INTO"),
    ("COPY (SELECT 1) TO '/tmp/x'", "only SELECT"),
    ("SELECT pg_sleep(10)", "function"),
    ("SELECT set_config('role', 'postgres', false)", "function"),
    ("SELECT current_setting('is_superuser')", "function"),
    ("SELECT 1 FROM sem.claim_balance_at(pg_sleep(5)::text::timestamptz)", "function"),
    ("SELECT * FROM pg_catalog.pg_authid", "relation"),
    ("SELECT (SELECT usename FROM pg_user)", "relation"),
    ("SELECT * FROM x.y.z", "three-part"),
    ("SELECT * FROM sem.claim_balance_at(:other)", "parameter"),
    ("SELECT * FROM sem.claim_balance_at($1)", "parameter"),
    ("", "empty"),
    ("SELEC broken", ""),
])
def test_rejects(guard, sql, why):
    with pytest.raises(SQLRejected) as e:
        check(sql, guard, P)
    assert why in str(e.value)


def test_empty_allow_list_rejects_everything():
    with pytest.raises(SQLRejected):
        check("SELECT 1", {"allowed_relations": []}, P)
