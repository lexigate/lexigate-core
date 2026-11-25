# Example MCP server

The MCP server described in [`spec/mcp.md`](../../spec/mcp.md) is implemented in the package as `lexigate mcp` (`src/lexigate/mcp_server.py`). This directory keeps the tool list (`tools.json`) and a thin wrapper script.

It connects **only** to the local quickstart demo database (the fictional data in [`../monthend/`](../monthend/)); other database hosts and databases without the demo marker are refused. No tool accepts SQL.

```bash
pip install -e ".[mcp]"                                  # from the repository root
docker compose up -d                                     # demo database (uncomment the db port to reach it from the host)
export LEXIGATE_DSN=postgresql://lexigate@127.0.0.1:55432/lexigate_demo PGPASSWORD=lexigate-demo LEXIGATE_BASE_DATE=2026-03-10
lexigate mcp                                             # stdio
lexigate mcp --transport http --port 8090                # streamable HTTP at http://127.0.0.1:8090/mcp
python3 examples/mcp-server/lexigate_mcp_server.py --list-tools
```

Claude Desktop and generic client configuration: see the README section “MCP server”. Clients should show `disclaimer` in small red type, show `hints` as follow-up chips, and call `get_evidence` (`kind: "sql_and_rationale"`) only when the user asks for the SQL.
