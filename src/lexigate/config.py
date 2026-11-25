# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Runtime settings, read from the environment (never from client requests)."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = PACKAGE_DIR.parent.parent          # src/lexigate -> repo root when running from a checkout


def _first_existing(*candidates: Path) -> Path | None:
    for c in candidates:
        if c and c.exists():
            return c
    return None


def dsn(explicit: str | None = None) -> str:
    """PostgreSQL connection string: --dsn, then LEXIGATE_DSN, then DATABASE_URL."""
    value = explicit or os.environ.get("LEXIGATE_DSN") or os.environ.get("DATABASE_URL")
    if not value:
        raise SystemExit("No database configured. Pass --dsn or set LEXIGATE_DSN (e.g. postgresql://user@host:5432/db).")
    return value


def demo_data_dir(explicit: str | None = None) -> Path:
    p = _first_existing(*(Path(x) for x in (explicit, os.environ.get("LEXIGATE_DEMO_DATA")) if x),
                        Path.cwd() / "examples/monthend/data", _REPO_ROOT / "examples/monthend/data")
    if not p:
        raise SystemExit("Demo data not found. Set LEXIGATE_DEMO_DATA to examples/monthend/data.")
    return p


def semantic_map_path(explicit: str | None = None) -> Path:
    p = _first_existing(*(Path(x) for x in (explicit, os.environ.get("LEXIGATE_SEMANTIC_MAP")) if x),
                        Path.cwd() / "examples/monthend/semantic_map.yaml", _REPO_ROOT / "examples/monthend/semantic_map.yaml")
    if not p:
        raise SystemExit("semantic_map not found. Set LEXIGATE_SEMANTIC_MAP.")
    return p


def base_date(tz: str = "Asia/Tokyo") -> date:
    """Server-side 'today'. LEXIGATE_BASE_DATE, else the demo manifest's base_date, else today in the map timezone."""
    env = os.environ.get("LEXIGATE_BASE_DATE")
    if env:
        return date.fromisoformat(env)
    try:
        manifest = demo_data_dir() / "manifest.json"
        return date.fromisoformat(json.loads(manifest.read_text(encoding="utf-8"))["base_date"])
    except (SystemExit, OSError, KeyError, ValueError):
        return datetime.now(ZoneInfo(tz)).date()
