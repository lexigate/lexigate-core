# LexiGate MCP tool contract (v0)

This is the public MCP (Model Context Protocol) interface for LexiGate. The goal is that a client (Claude, Cursor, a future Dify tool, or the sample server) can query **catalog-constrained** metrics without sending raw SQL and without receiving raw business rows it did not ask for as an aggregate.

**Status (0.2):** implemented by `lexigate mcp` (`src/lexigate/mcp_server.py`, official Python `mcp` SDK; stdio by default, streamable HTTP with `--transport http`). Install with `pip install -e ".[mcp]"`; client configuration is in the README.

The sample server binds **only** to the local quickstart demo database: it refuses a database host that is not local (override list: `LEXIGATE_MCP_ALLOWED_HOSTS`, default `localhost,127.0.0.1,::1,db`) and any database whose schema `demo` lacks the marker comment written by `lexigate demo load`. Path-2 SQL runs as the NOLOGIN role `lexigate_query`, which can only execute the `sem.*` functions.

## Tools

All tools take JSON arguments and return JSON. Dates use ISO 8601. Money is an integer in the unit declared by the semantic map (for the month-end example, integer yen).

### Implemented tools at a glance

| Tool | Purpose |
|---|---|
| `ask` | `{question, session_id?}` → the same answer object as `query_metric`, plus `answer_ja`. Recommended entry point for chat clients. |
| `list_questions` | Alias of `list_metrics`, with example questions per intent and whether path 2 is enabled (`generated_sql`). |
| `list_metrics` | See below. |
| `get_semantic_map` | See below. |
| `resolve_intent` | See below. |
| `query_metric` | See below. Path 2 runs only when the server has a model configured (`GEMINI_API_KEY` or `OPENAI_API_KEY`). |
| `get_evidence` | See below. `session_id` selects the conversation; with no `query_id`, the session’s last answer is used. |
| `claim_evidence` | `{claim_id, as_of?}` → lines, approval history and settled payments of one claim (demo evidence, no RLS). |

Answers are Japanese (`answer_ja`); a client or host model may translate them. Each answer has one or two `hints`.

### `list_metrics`

List metrics, intents, and as-of modes from the loaded `semantic_map`.

- Arguments: none required. Optional `locale` (`ja` / `en` / `zh`).
- Result: `{ "semantic_version": "...", "intents": [...], "metrics": [...], "as_of_modes": [...] }`

### `resolve_intent`

Map a natural-language question to an intent and parameters, or to a clarification.

- Arguments: `{ "question": string, "locale"?: string }`
- Result (ready): `{ "status": "ok", "intent": string, "params": object, "path": "fixed" | "generated_sql" }`
- Result (need input): `{ "status": "clarify", "message": string, "choices"?: string[] }`
- Result (denied / cannot): `{ "status": "denied" | "unsupported", "message": string }`

`path` is a hint for the client. The server, not the host model, decides which path will be used when `query_metric` runs.

### `query_metric`

Run a catalog-covered metric (path 1) or, if explicitly allowed by server configuration, a guarded generated `SELECT` (path 2).

- Arguments: `{ "intent"?: string, "params"?: object, "question"?: string, "locale"?: string }`
- At least one of `intent` or `question` is required.
- Result:
  ```json
  {
    "status": "ok",
    "path": "fixed",
    "semantic_version": "sem-2026.11.1",
    "rule_version": "close-rules-v1",
    "result": { },
    "disclaimer": null,
    "hints": ["先月末時点の未払い合計は？"],
    "sql_available": false
  }
  ```
- For path 2, `disclaimer` is the soft footer text (Japanese and/or the client locale), `sql_available` is `true`, and `hints` includes 「確認：さっきの SQL と根拠は？」. The SQL itself is **not** in this payload.
- `status` may also be `clarify`, `denied`, or `error`.

### `get_evidence`

Return evidence for a previous answer (line items, approval history, or, for path 2, the SQL and rationale).

- Arguments: `{ "query_id": string, "evidence_id": string }` or `{ "query_id": string, "kind": "sql_and_rationale" }`
- `kind: "sql_and_rationale"` is valid only for a path-2 `query_id`. For path 1 it returns `status: "not_applicable"`.
- Evidence payloads must still respect the caller’s authorization.

### `get_semantic_map`

Return the **model-visible** subset of the loaded map (intents, terms, metric definitions, `sql_guard`). It must not include secrets or connection data.

## Out of scope

- A tool that accepts arbitrary SQL from the client.
- A tool that changes data, session users, or the semantic map.
- A production authentication flow. The sample binds to the local demo only.

Clients should display `disclaimer` in small red type, show `hints` as follow-up chips, and call `get_evidence` only when the user asks for SQL or for a listed evidence id.
