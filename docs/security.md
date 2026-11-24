# LexiGate security principles

This is the public security document. It states principles and trust boundaries, not exploit details and not the source of enterprise enforcement code.

Please report vulnerabilities privately, as described in [SECURITY.md](../SECURITY.md).

## Trust boundaries

1. **The customer VPC is the data plane.** Raw rows of the business database do not leave it. Results that travel to Teams, Slack, a CLI, or an MCP client are aggregated or masked.
2. **Identity is never taken from model-generated text.** The caller is established by a trusted channel (for example a verified tenant and object ID, a documented demo principal, or a signed local configuration) and is applied to the database session *before* a query runs.
3. **The model is not a database user.** It may propose an intent, parameters, or (on path 2) a `SELECT`. It has no credentials, no write tools, and no right to change session variables.
4. **LexiGate, in Mode A, is not in the data path as a processor.** Mode B, if used, must be TLS passthrough without decryption so that the operator does not become an unexpected processor of personal data.
5. **Database content is never fed back to a model as instructions.** Values such as comment fields are data, not prompts.

## Six layers (principles)

These layers are cumulative. The database layer must still protect the data if an upper layer fails.

| Layer | Principle |
|---|---|
| L1 Database | Connect to a **read replica** (or an equivalent read-only copy). Use a dedicated account that can `SELECT` only, with a statement timeout and a connection cap. Writes must be impossible at this layer. |
| L2 Statement | A generated statement, when allowed at all, is a single `SELECT`. Dangerous functions and multiple statements are rejected. A `LIMIT` is mandatory. Path 1 uses fixed parameterized SQL and does not require a generator. |
| L3 Authorization | Apply the caller’s scope **before aggregation**. Prefer native row-level security with a session context (`SET LOCAL` or equivalent) over asking a model to embed filters in SQL. Column-level rules hide amounts or identifiers the role must not see. A request outside scope is denied without revealing whether a row exists. |
| L4 Cost | If `EXPLAIN` (or an equivalent estimate) exceeds a configured row or cost threshold, refuse and ask the user to narrow the question. |
| L5 Result | Cap the number of rows returned. Mask PII columns by default. Aggregates that would identify a single person should be withheld or broadened. |
| L6 Audit | Record the question, the path (fixed vs generated), the statement or metric id, the principal, and the row count, in a store the customer controls. Default to *not* storing result payloads that would themselves become a personal-data store. |

Path 2 (model-generated SQL) must pass L2, L3, and L4 before execution. Path 1 still runs under L1, L3, L5, and L6.

## Network

- **Mode A (default):** the customer publishes an endpoint they control. Inbound rules should be as tight as their environment allows (for a Teams bot, typically the Microsoft Bot service IP ranges). No LexiGate-operated relay is required.
- **Mode B (optional):** the gateway makes an outbound TLS connection only. A relay must not terminate TLS in a way that inspects payloads. This mode exists for networks that cannot accept inbound connections; it is **not** a license check and must not be required for the software to start.
- Enterprise licensing is an **offline signed license file**.

## Models (BYOM)

When a language model is used (NLU or path-2 SQL):

- It runs in the customer’s account and region (for a Japan deployment, keep prompts and completions in-region; do not use a global cross-region route that would send them out of the country).
- Send catalog excerpts, synonyms, and the question — not raw sampled rows — unless a documented, locally masked sample is strictly required for a future scanner feature, in which case masking happens *before* any model call.
- Token spend is the customer’s, not part of the LexiGate subscription.

## Semantic map and least surprise

The `semantic_map` is a customer-readable artifact. Permissions declared in it must match what the database layer actually enforces; the map is not the only control. `sql_guard` is an allow list, not a documentation comment.

The denied-access message used in the Japanese demo, 「この情報へのアクセス権限がありません」, is intentionally the same whether a row exists or not.

## What we do not claim in this revision

This repository does not yet ship a hardened runtime. The principles above are the design the Phase 1 code and the enterprise edition are expected to follow. Please judge the running month-end demo (private) and future releases against this document, and open a private report if a shipped artifact falls short.
