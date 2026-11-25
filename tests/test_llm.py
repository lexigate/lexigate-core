# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Provider request building, with the HTTP call replaced (no network, dummy keys only)."""
import json

import pytest

from lexigate import llm

DUMMY = "dummy-not-a-key"


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_post(url, body, headers, timeout):
        calls.append({"url": url, "body": body, "headers": headers})
        if "generativelanguage" in url:
            return {"candidates": [{"content": {"parts": [{"text": '{"answerable": true, "sql": "SELECT 1"}'}]}}]}
        return {"choices": [{"message": {"content": '```json\n{"answerable": true, "sql": "SELECT 1"}\n```'}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    return calls


SCHEMA = {"type": "object", "properties": {"sql": {"type": ["string", "null"]}, "cols": {"type": "array", "items": {"type": "string"}}},
          "required": ["sql"]}


def test_gemini_request(captured):
    p = llm.GeminiProvider(DUMMY, model="m1")
    assert p.generate_json("sys", "q", SCHEMA)["sql"] == "SELECT 1"
    c = captured[0]
    assert c["url"].endswith("/models/m1:generateContent") and DUMMY not in c["url"]      # key only in a header
    assert c["headers"]["x-goog-api-key"] == DUMMY
    gs = c["body"]["generationConfig"]["responseSchema"]
    assert gs["properties"]["sql"] == {"type": "STRING", "nullable": True} and gs["properties"]["cols"]["items"]["type"] == "STRING"
    assert DUMMY not in repr(p)


def test_gemini_falls_back_on_503(monkeypatch):
    seen = []

    def flaky(url, body, headers, timeout):
        seen.append(url)
        if "m1" in url:
            raise llm.LLMError("HTTP 503 from provider", 503)
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(llm, "_post", flaky)
    assert llm.GeminiProvider(DUMMY, model="m1", fallback_model="m2").generate_json("s", "u", SCHEMA) == {}
    assert [u.split("/models/")[1] for u in seen] == ["m1:generateContent", "m2:generateContent"]



def test_gemini_retries_primary_after_both_fail(monkeypatch):
    seen = []

    def flaky(url, body, headers, timeout):
        seen.append(url.split("/models/")[1])
        if len(seen) < 3:
            raise llm.LLMError("HTTP 503 from provider", 503)
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

    monkeypatch.setattr(llm, "_post", flaky)
    p = llm.GeminiProvider(DUMMY, model="m1", fallback_model="m2")
    p.retry_delay = 0
    assert p.generate_json("s", "u", SCHEMA) == {"ok": True}
    assert seen == ["m1:generateContent", "m2:generateContent", "m1:generateContent"]


def test_gemini_does_not_retry_client_errors(monkeypatch):
    seen = []

    def bad(url, body, headers, timeout):
        seen.append(url)
        raise llm.LLMError("HTTP 400 from provider", 400)

    monkeypatch.setattr(llm, "_post", bad)
    with pytest.raises(llm.LLMError):
        llm.GeminiProvider(DUMMY, model="m1", fallback_model="m2").generate_json("s", "u", SCHEMA)
    assert len(seen) == 1

def test_openai_compatible_request(captured):
    p = llm.OpenAICompatibleProvider(DUMMY, model="m", base_url="http://127.0.0.1:9/v1/")
    assert p.generate_json("sys", "q", SCHEMA)["sql"] == "SELECT 1"          # code fences stripped
    c = captured[0]
    assert c["url"] == "http://127.0.0.1:9/v1/chat/completions"
    assert c["headers"]["Authorization"] == f"Bearer {DUMMY}"
    assert c["body"]["response_format"] == {"type": "json_object"} and DUMMY not in json.dumps(c["body"])


def test_provider_selection():
    assert llm.provider_from_env({}) is None
    assert llm.provider_from_env({"GEMINI_API_KEY": DUMMY}).name == "gemini"
    assert llm.provider_from_env({"OPENAI_API_KEY": DUMMY}).name == "openai"
    assert llm.provider_from_env({"GEMINI_API_KEY": DUMMY, "OPENAI_API_KEY": DUMMY, "LEXIGATE_LLM_PROVIDER": "openai"}).name == "openai"
    assert llm.provider_from_env({"GEMINI_API_KEY": DUMMY, "LEXIGATE_LLM_PROVIDER": "none"}) is None


def test_errors_never_contain_key(monkeypatch):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(llm.LLMError) as e:
        llm.OpenAICompatibleProvider(DUMMY, base_url="http://127.0.0.1:9/v1").generate_json("s", "u", SCHEMA)
    assert DUMMY not in str(e.value) and e.value.status == 401
