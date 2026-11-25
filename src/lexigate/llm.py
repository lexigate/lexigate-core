# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Pluggable LLM providers for path 2 (model-generated SQL). Bring your own model.

Selection (environment, read by the server, never by clients):

* ``LEXIGATE_LLM_PROVIDER`` = ``auto`` (default) | ``gemini`` | ``openai`` | ``none``
* Gemini: ``GEMINI_API_KEY`` (or ``LEXIGATE_GEMINI_API_KEY``), model ``LEXIGATE_GEMINI_MODEL``
  (default ``gemini-flash-lite-latest``), tried once more with ``LEXIGATE_GEMINI_FALLBACK_MODEL``
  (default ``gemini-3.1-flash-lite``) on HTTP 429 / 5xx or a timeout
* OpenAI-compatible (OpenAI, Azure OpenAI v1 endpoints, vLLM, Ollama, LiteLLM, ...): ``OPENAI_API_KEY``
  (or ``LEXIGATE_OPENAI_API_KEY``), ``LEXIGATE_OPENAI_BASE_URL`` (default ``https://api.openai.com/v1``),
  ``LEXIGATE_OPENAI_MODEL`` (default ``gpt-4o-mini``)
* ``LEXIGATE_LLM_TIMEOUT_S`` (default 15)

``auto`` picks Gemini if its key is set, else OpenAI-compatible if its key is set, else nothing: path 2 is then
disabled. Keys are only sent in request headers and are never logged or returned. The model receives the
question and the model-visible part of the semantic_map; it never receives result rows.
"""
from __future__ import annotations

import json
import os
import time
import re
import urllib.error
import urllib.request
from typing import Protocol


class LLMError(RuntimeError):
    """Provider failure. Messages never contain the key."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        """Return one JSON object that follows `schema` (a small JSON Schema)."""


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code} from provider", e.code) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LLMError(f"provider request failed: {type(e).__name__}") from None


def parse_json_object(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        obj = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise LLMError("model did not return JSON") from None
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise LLMError("model did not return a JSON object")
    return obj


def _to_gemini_schema(s: dict) -> dict:
    t = s.get("type")
    nullable = isinstance(t, list) and "null" in t
    if isinstance(t, list):
        t = next(x for x in t if x != "null")
    out: dict = {"type": t.upper()}
    if nullable:
        out["nullable"] = True
    if "description" in s:
        out["description"] = s["description"]
    if t == "object":
        out["properties"] = {k: _to_gemini_schema(v) for k, v in s.get("properties", {}).items()}
        if s.get("required"):
            out["required"] = s["required"]
    if t == "array":
        out["items"] = _to_gemini_schema(s["items"])
    return out


class GeminiProvider:
    name = "gemini"
    API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str | None = None, timeout: float | None = None, fallback_model: str | None = None):
        self._key = api_key
        self.model = model or os.environ.get("LEXIGATE_GEMINI_MODEL", "gemini-flash-lite-latest")
        self.fallback_model = fallback_model or os.environ.get("LEXIGATE_GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
        self.timeout = timeout or float(os.environ.get("LEXIGATE_LLM_TIMEOUT_S", "15"))
        self.retry_delay = float(os.environ.get("LEXIGATE_LLM_RETRY_DELAY_S", "1.5"))

    def __repr__(self) -> str:
        return f"GeminiProvider(model={self.model!r})"

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                     "responseSchema": _to_gemini_schema(schema)}}
        # Transient 429 / 5xx / timeouts are common on the free tier: try the fallback model, then the
        # primary once more after a short pause.
        models = [self.model] + ([self.fallback_model] if self.fallback_model and self.fallback_model != self.model else [])
        models.append(self.model)
        for i, model in enumerate(models):
            if i == len(models) - 1 and self.retry_delay:
                time.sleep(self.retry_delay)
            try:
                r = _post(self.API.format(model=model), body, {"x-goog-api-key": self._key}, self.timeout)
                break
            except LLMError as e:
                retryable = e.status is None or e.status == 429 or e.status >= 500
                if not retryable or i == len(models) - 1:
                    raise
        try:
            text = r["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected Gemini response") from None
        return parse_json_object(text)


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str | None = None, base_url: str | None = None, timeout: float | None = None):
        self._key = api_key
        self.model = model or os.environ.get("LEXIGATE_OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.environ.get("LEXIGATE_OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.timeout = timeout or float(os.environ.get("LEXIGATE_LLM_TIMEOUT_S", "15"))

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(model={self.model!r}, base_url={self.base_url!r})"

    def generate_json(self, system: str, user: str, schema: dict) -> dict:
        body = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system + "\n\nReturn ONLY a JSON object matching this JSON Schema:\n"
                              + json.dumps(schema, ensure_ascii=False)},
                             {"role": "user", "content": user}]}
        r = _post(f"{self.base_url}/chat/completions", body, {"Authorization": f"Bearer {self._key}"}, self.timeout)
        try:
            text = r["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected OpenAI-compatible response") from None
        return parse_json_object(text)


def provider_from_env(env: dict | None = None) -> LLMProvider | None:
    env = os.environ if env is None else env
    choice = env.get("LEXIGATE_LLM_PROVIDER", "auto").lower()
    gkey = env.get("LEXIGATE_GEMINI_API_KEY") or env.get("GEMINI_API_KEY")
    okey = env.get("LEXIGATE_OPENAI_API_KEY") or env.get("OPENAI_API_KEY")
    if choice in ("none", "off", "disabled", ""):
        return None
    if choice in ("auto", "gemini") and gkey:
        return GeminiProvider(gkey, env.get("LEXIGATE_GEMINI_MODEL"))
    if choice in ("auto", "openai") and okey:
        return OpenAICompatibleProvider(okey, env.get("LEXIGATE_OPENAI_MODEL"), env.get("LEXIGATE_OPENAI_BASE_URL"))
    return None
