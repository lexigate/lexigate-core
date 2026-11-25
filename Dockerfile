# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
# LexiGate Core quickstart image: CLI + ask service + the fictional month-end demo data.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install ".[serve]"

COPY spec ./spec
COPY examples/monthend ./examples/monthend

ENV LEXIGATE_DEMO_DATA=/app/examples/monthend/data \
    LEXIGATE_SEMANTIC_MAP=/app/examples/monthend/semantic_map.yaml \
    LEXIGATE_HOST=0.0.0.0 \
    LEXIGATE_PORT=8080

RUN useradd --system --uid 10001 lexigate
USER lexigate
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"
CMD ["sh", "-c", "lexigate demo load && exec lexigate serve"]
