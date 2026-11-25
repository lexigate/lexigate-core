# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Small FastAPI ask service for the quickstart. Local demo only: no authentication, no row-level security."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from lexigate import __version__, config
from lexigate import semantic_map as sm
from lexigate.engine import Engine


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="LexiGate Core ask service (demo)", version=__version__)
    state: dict = {"engine": engine}

    def get_engine() -> Engine:
        if state["engine"] is None:
            m = sm.load(config.semantic_map_path())
            state["engine"] = Engine(m, config.dsn(), config.base_date(m.get("timezone", "Asia/Tokyo")))
        return state["engine"]

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
        return get_engine().ask(req.question).to_dict()

    return app


app = create_app()
