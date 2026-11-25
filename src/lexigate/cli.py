# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Command line: lexigate scan | validate | demo load | serve | ask | mcp."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from lexigate import __version__, config


def _cmd_scan(a) -> int:
    from lexigate.scanner import ScanOptions, scan, to_yaml
    opts = ScanOptions(schemas=a.schema or [], sample_values=not a.no_samples, max_distinct=a.max_distinct,
                       timezone=a.timezone, currency=a.currency, include_views=not a.no_views)
    text = to_yaml(scan(config.dsn(a.dsn), opts))
    if a.output in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"wrote draft semantic_map to {a.output} (review every item marked needs_review)", file=sys.stderr)
    return 0


def _cmd_validate(a) -> int:
    from lexigate import semantic_map as sm
    import yaml
    rc = 0
    for f in a.files:
        text = Path(f).read_text(encoding="utf-8")
        doc = json.loads(text) if f.endswith(".json") else yaml.safe_load(text)
        errs = sm.validation_errors(doc)
        print(f"{f}: {'OK' if not errs else 'INVALID'}")
        for e in errs:
            print(f"  {e}")
        rc |= bool(errs)
    return rc


def _cmd_demo_load(a) -> int:
    from lexigate.demo.load import load
    r = load(config.dsn(a.dsn), config.demo_data_dir(a.data))
    print(f"loaded {r['data_version']}: {r['rows']} rows in {r['tables']} tables (schemas demo, sem)")
    return 0


def _cmd_serve(a) -> int:
    import uvicorn
    uvicorn.run("lexigate.service:app", host=a.host, port=a.port, log_level="info")
    return 0


def _cmd_mcp(a) -> int:
    from lexigate.mcp_server import run
    run("stdio" if a.transport == "stdio" else "http", host=a.host, port=a.port)
    return 0


def _print_answer(d: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print(d["answer_ja"])
    if d.get("footer_ja"):
        red = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        print("\n" + (f"\033[2;31m{d['footer_ja']}\033[0m" if red else d["footer_ja"]))
    if d.get("hints"):
        print("\n次の質問例： " + " / ".join(d["hints"]))
    label = d.get("data_label")
    if label:
        print(f"（{label}・{d.get('semantic_version')}）")


def _cmd_ask(a) -> int:
    question = " ".join(a.question)
    if a.local:
        from lexigate import semantic_map as sm
        from lexigate.engine import Engine
        m = sm.load(config.semantic_map_path(a.map))
        d = Engine(m, config.dsn(a.dsn), config.base_date(m.get("timezone", "Asia/Tokyo")), llm="auto").ask(
            question, session_id=a.session).to_dict()
    else:
        url = a.url.rstrip("/") + "/v1/ask"
        req = urllib.request.Request(url, data=json.dumps({"question": question, "session_id": a.session}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:
            print(f"cannot reach {url}: {e}. Is `docker compose up` running? (or use --local with LEXIGATE_DSN)", file=sys.stderr)
            return 2
    _print_answer(d, a.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lexigate", description="LexiGate Core command line")
    p.add_argument("--version", action="version", version=f"lexigate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="read PostgreSQL metadata and write a semantic_map v0 draft (YAML)")
    s.add_argument("--dsn", help="PostgreSQL DSN (default: $LEXIGATE_DSN or $DATABASE_URL)")
    s.add_argument("--schema", action="append", help="schema to scan (repeatable; default: all non-system schemas)")
    s.add_argument("-o", "--output", help="output file (default: stdout)")
    s.add_argument("--no-samples", action="store_true", help="do not read any row values (metadata only)")
    s.add_argument("--max-distinct", type=int, default=12, help="max distinct values to list for a category column")
    s.add_argument("--no-views", action="store_true", help="tables only")
    s.add_argument("--timezone", help="map timezone (default: the server TimeZone)")
    s.add_argument("--currency", help="ISO currency, e.g. JPY")
    s.set_defaults(fn=_cmd_scan)

    v = sub.add_parser("validate", help="validate semantic_map files against the v0 JSON Schema")
    v.add_argument("files", nargs="+")
    v.set_defaults(fn=_cmd_validate)

    d = sub.add_parser("demo", help="quickstart demo database")
    dsub = d.add_subparsers(dest="demo_cmd", required=True)
    dl = dsub.add_parser("load", help="(re)load the fictional month-end data into schemas demo and sem")
    dl.add_argument("--dsn")
    dl.add_argument("--data", help="data directory (default: examples/monthend/data)")
    dl.set_defaults(fn=_cmd_demo_load)

    sv = sub.add_parser("serve", help="run the ask service (FastAPI)")
    sv.add_argument("--host", default=os.environ.get("LEXIGATE_HOST", "127.0.0.1"))
    sv.add_argument("--port", type=int, default=int(os.environ.get("LEXIGATE_PORT", "8080")))
    sv.set_defaults(fn=_cmd_serve)

    k = sub.add_parser("ask", help="ask a question, e.g. lexigate ask \"未払い出張精算はいくら？\"")
    k.add_argument("question", nargs="+")
    k.add_argument("--url", default=os.environ.get("LEXIGATE_URL", "http://127.0.0.1:8080"))
    k.add_argument("--json", action="store_true", help="print the full JSON answer")
    k.add_argument("--local", action="store_true", help="answer in-process against $LEXIGATE_DSN instead of the service")
    k.add_argument("--dsn")
    k.add_argument("--map", help="semantic_map path (with --local)")
    k.add_argument("--session", default=os.environ.get("LEXIGATE_SESSION", "cli"),
                   help="session id; follow-ups such as 「確認：さっきの SQL と根拠は？」 refer to the last answer in it")
    k.set_defaults(fn=_cmd_ask)

    mc = sub.add_parser("mcp", help="MCP server for the local demo database (stdio or streamable HTTP)")
    mc.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("LEXIGATE_MCP_TRANSPORT", "stdio"))
    mc.add_argument("--host", default=os.environ.get("LEXIGATE_MCP_HOST", "127.0.0.1"))
    mc.add_argument("--port", type=int, default=int(os.environ.get("LEXIGATE_MCP_PORT", "8090")))
    mc.set_defaults(fn=_cmd_mcp)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
