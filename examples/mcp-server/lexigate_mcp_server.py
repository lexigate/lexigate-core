#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""LexiGate demo MCP server: thin wrapper around `lexigate mcp` (src/lexigate/mcp_server.py).

    pip install -e ".[mcp]"          # from the repository root
    lexigate mcp                     # stdio; or: lexigate mcp --transport http --port 8090
    python3 examples/mcp-server/lexigate_mcp_server.py --list-tools

The server connects only to the local quickstart demo database (see README).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description="LexiGate demo MCP server (wrapper for `lexigate mcp`)")
    p.add_argument("--list-tools", action="store_true", help="print the tool list from tools.json and exit")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p.add_argument("--port", type=int, default=8090)
    a = p.parse_args()
    if a.list_tools:
        print(json.dumps(json.loads(Path(__file__).with_name("tools.json").read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
        return
    from lexigate.mcp_server import run
    run(a.transport, port=a.port)


if __name__ == "__main__":
    main()
