# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Small FastAPI ask service for the quickstart. Local demo only: no authentication, no row-level security."""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from lexigate import __version__, config
from lexigate import semantic_map as sm
from lexigate.engine import Engine
from lexigate.render import to_adaptive_card, to_html


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")


def build_engine() -> Engine:
    m = sm.load(config.semantic_map_path())
    return Engine(m, config.dsn(), config.base_date(m.get("timezone", "Asia/Tokyo")), llm="auto")


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="LexiGate Core ask service (demo)", version=__version__)
    state: dict = {"engine": engine}

    def get_engine() -> Engine:
        if state["engine"] is None:
            state["engine"] = build_engine()
        return state["engine"]

    def _ask(req: AskRequest) -> dict:
        sid = req.session_id or uuid.uuid4().hex
        return get_engine().ask(req.question, session_id=sid).to_dict()

    @app.get("/healthz")
    def healthz():
        try:
            return get_engine().health()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"not ready: {type(e).__name__}")

    @app.get("/v1/intents")
    def intents():
        m = get_engine().ctx.semantic_map
        return {k: {"title_ja": v.get("title_ja"), "synonyms": v.get("synonyms", [])} for k, v in (m.get("intents") or {}).items()}

    @app.post("/v1/ask")
    def ask(req: AskRequest):
        """JSON answer. Pass the returned session_id back to ask 「確認：さっきの SQL と根拠は？」 about it."""
        return _ask(req)

    @app.post("/v1/ask/card")
    def ask_card(req: AskRequest):
        return to_adaptive_card(_ask(req))

    @app.post("/v1/ask/html", response_class=HTMLResponse)
    def ask_html(req: AskRequest):
        return to_html(_ask(req))

    return app


app = create_app()
