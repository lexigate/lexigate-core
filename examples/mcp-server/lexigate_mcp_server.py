#!/usr/bin/env python3
"""LexiGate demo MCP server (skeleton).

This revision advertises the tools defined in the public contract (spec/mcp.md)
and refuses to execute queries. A Phase 1 implementation will bind only to the
fictional month-end dataset under examples/monthend/ (or a local compose
database created from it). It will not accept a customer connection string.

No network service is started. Run:  python3 lexigate_mcp_server.py --list-tools
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

NOT_IMPLEMENTED = {
    "status": "not_implemented",
    "message": "LexiGate MCP query execution is planned for Phase 1 and is not implemented in this revision.",
}

TOOLS = json.loads((Path(__file__).with_name("tools.json")).read_text())["tools"]


def list_tools() -> dict:
    return {"status": "ok", "implementation": "skeleton", "tools": TOOLS}


def call_tool(name: str, arguments: dict | None = None) -> dict:
    known = {t["name"] for t in TOOLS}
    if name not in known:
        return {"status": "error", "message": f"unknown tool: {name}"}
    if name == "list_metrics":
        # Even list_metrics would need a loaded map + engine in Phase 1.
        return dict(NOT_IMPLEMENTED, tool=name)
    return dict(NOT_IMPLEMENTED, tool=name, arguments=arguments or {})


def main() -> None:
    p = argparse.ArgumentParser(description="LexiGate MCP server skeleton (no query execution)")
    p.add_argument("--list-tools", action="store_true")
    p.add_argument("--call", metavar="TOOL")
    args = p.parse_args()
    if args.call:
        print(json.dumps(call_tool(args.call), ensure_ascii=False, indent=2))
        return
    print(json.dumps(list_tools(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
