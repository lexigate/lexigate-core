# semantic_map.yaml specification (v0)

Status: **v0**. This is the first public contract, derived from the month-end demo catalog and generalized. A document may be YAML or JSON. Validate it against [`semantic_map.schema.json`](semantic_map.schema.json).

A `semantic_map` is the customer-readable source of truth for:

- which questions the gateway will try to answer (intents);
- how business terms map to metrics;
- which relations a query may touch;
- who may see which domain;
- how evidence is labeled;
- the allow list used when a model is permitted to generate SQL (`sql_guard`).

It is not a connection string, not an IAM policy, and not a substitute for database-level enforcement. Permissions in the map must match what the database session actually applies.

## Document header

| Field | Type | Required | Meaning |
|---|---|---|---|
| `semantic_version` | string | yes | Version of this map, e.g. `sem-2026.11.1`. Bump when meanings change. |
| `rule_version` | string | no | Version of the business rules cited in evidence, e.g. `close-rules-v1`. |
| `data_label` / `data_label_ja` | string | no | Human label for the dataset (“デモデータ”). |
| `timezone` | string | yes | IANA time zone used for business dates, e.g. `Asia/Tokyo`. |
| `currency` | string | no | ISO currency, e.g. `JPY`. |
| `money` | string | no | Representation. The month-end example uses `integer_yen` (never a float). |

## `time`

Describes cutoff rules and as-of modes.

```yaml
time:
  cutoff_rule_ja: 「先月末時点」＝基準日が属するJST月の月初 0:00（排他的上限）。
  as_of_modes:
    previous_month_end: { params: [], synonyms: [先月末時点, 先月末] }
    month_end:          { params: [month], synonyms: [X月末時点], example: { month: "2026-01" } }
    date:               { params: [date], synonyms: [X月X日時点], example: { date: "2026-02-10" } }
  base_date_source: server    # never taken from untrusted client input
```

Recommended as-of convention (used in the month-end example): “as of date X” is the exclusive upper bound of **00:00 in the map timezone on the day after X**. “As of last month-end” is 00:00 on the first day of the month that contains the server base date.

If a question needs an as-of value and the user did not give one, the gateway should **clarify**, not guess.

## `intents`

Named questions the NLU (or a structured API) may emit.

```yaml
intents:
  unpaid_summary:
    title_ja: 承認済み・未払いの出張精算（残高サマリー）
    metric: unpaid_trip_balance
    requires_domain: CLAIMS
    params: { as_of: required, scope: optional, page: optional }
    synonyms: [未払い, 未払残高, unpaid]
```

`params` values are `required` or `optional`. `requires_domain` (or `requires_domain_any`) ties the intent to the permission matrix.

## `terms`

Business language → semantic definition. These are the synonyms and exclusions a model or a rule parser may see.

```yaml
terms:
  承認済み: { maps_to: "last valid approval event before cutoff = FINAL_APPROVE", excludes: [段階承認のみ] }
  未払い:   { maps_to: "balance_yen > 0" }
```

## `metrics`

Each metric has a definition, a source relation, a grain, and a measure.

```yaml
metrics:
  unpaid_trip_balance:
    definition_ja: 締め時点前に最終承認済みの出張精算について、承認明細合計から確定支払を差し引き、正の残高を合計する。
    source: sem.claim_balance_at(:cutoff)
    grain: claim
    measure: sum(balance_yen)
    filter: balance_yen > 0 AND anomaly_code IS NULL
    unit: JPY
```

`source` names a relation in `relations` (often a function or view with a bound `:cutoff`). The public map may name the relation; it does not have to include the SQL body. That body may remain inside the Semantic Engine.

## `relations`

The only objects path-2 SQL may reference, and the grain / keys / join paths for path 1.

```yaml
relations:
  sem.claim_balance_at:
    grain: claim
    key: [claim_id]
    columns: { claim_id: text, balance_yen: JPY, ... }
    aggregatable: [balance_yen]
    joins: { sem.attendance_issues_at: claim_id }
```

## `permissions`

Role → domain → scope. The month-end example uses `FINANCE`, `HR`, `DEPT_MANAGER`, `EMPLOYEE`.

Rules that v0 expects implementations to honor:

- every request has an identity; a missing identity is rejected;
- permissions are applied **before** aggregation;
- an explicit request outside scope is denied, with no amount and no confirmation that a row exists;
- a broader “company-wide” question asked by a narrower role is computed on the visible scope and the response says the scope was limited;
- historical attribution (for example a trip’s department at the time) does not by itself grant current view rights.

## `evidence` and `rules`

Evidence identifiers (for example `CLM-<claim_id>:lines`, `RULE-BALANCE`) describe what a follow-up may retrieve. `rules` is the human-readable text returned as `RULE-<code>`.

## `sql_guard`

Allow / deny lists for path 2 (model-generated SQL). Path 1 does not need to go through a generator, but it should still only touch these relations.

```yaml
sql_guard:
  statement: single SELECT only
  allowed_relations: [sem.claim_balance_at, sem.attendance_issues_at]
  allowed_functions: [sum, count, min, max, coalesce, date_trunc, to_char]
  parameters_required: [cutoff]
  forbidden: [pg_catalog.*, set_config, current_setting]
```

A previous internal name for this block was `nl2sql_guard`. Validators should accept that alias in v0 and treat it as `sql_guard`.

## Versioning

- Bump `semantic_version` when a metric meaning, cutoff rule, or permission changes.
- Additive synonyms and new optional intents may keep the same version if existing answers would not change.
- Consumers should refuse a map that has no `semantic_version` or that they cannot parse.

## Example

See [`examples/monthend/semantic_map.yaml`](../examples/monthend/semantic_map.yaml) for a complete month-end close map (fictional data, Japanese terms).
