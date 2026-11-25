# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Test fixtures. Database tests use $LEXIGATE_TEST_DSN, or start a throwaway PostgreSQL cluster with initdb
(found on PATH or under /usr/lib/postgresql/*/bin) on a free port. If neither is available they are skipped.
The database named by LEXIGATE_TEST_DSN is overwritten (schemas demo and sem)."""
from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "monthend" / "data"
MAP = ROOT / "examples" / "monthend" / "semantic_map.yaml"


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    hits = sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"))
    return hits[-1] if hits else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg_dsn():
    env = os.environ.get("LEXIGATE_TEST_DSN")
    if env:
        yield env
        return
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("no PostgreSQL available (set LEXIGATE_TEST_DSN or install postgresql)")
    tmp = tempfile.mkdtemp(prefix="lexigate-test-pg-")
    data, port = os.path.join(tmp, "data"), _free_port()
    subprocess.run([initdb, "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C.UTF-8"],
                   check=True, capture_output=True)
    subprocess.run([pg_ctl, "start", "-D", data, "-l", os.path.join(tmp, "log"), "-w", "-o",
                    f"-p {port} -k {tmp} -c listen_addresses=127.0.0.1 -c fsync=off -c TimeZone=Asia/Tokyo"],
                   check=True, capture_output=True)
    try:
        dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        import psycopg
        for _ in range(50):
            try:
                psycopg.connect(dsn).close()
                break
            except psycopg.OperationalError:
                time.sleep(0.1)
        yield dsn
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def demo_db(pg_dsn):
    from lexigate.demo.load import load
    info = load(pg_dsn, DATA)
    return {"dsn": pg_dsn, **info}


@pytest.fixture(scope="session")
def semantic_map():
    from lexigate import semantic_map as sm
    return sm.load(MAP)


@pytest.fixture(scope="session")
def expected():
    import json
    return json.loads((DATA / "expected.json").read_text(encoding="utf-8"))
