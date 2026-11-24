# Example MCP server (skeleton)

This directory is a **placeholder** for the local-only MCP server described in [`spec/mcp.md`](../../spec/mcp.md).

**This revision does not execute queries.** The script advertises the tool names and returns a structured `not_implemented` response. That is intentional: we will not pretend the server already talks to a database.

Phase 1 will implement the tools against the fictional dataset in [`../monthend/`](../monthend/) only. A production connection string will be rejected.

```bash
python3 lexigate_mcp_server.py --list-tools
python3 lexigate_mcp_server.py --call list_metrics
```

See the contract for how clients should display the path-2 disclaimer, the follow-up hints, and the “show me the SQL” evidence call.
