# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
import json

import pytest

from lexigate import semantic_map as sm
from tests.conftest import MAP, ROOT


def test_packaged_schema_matches_spec():
    assert json.loads((ROOT / "spec" / "semantic_map.schema.json").read_text()) == sm.schema()


def test_example_map_is_valid():
    doc = sm.load(MAP)
    assert doc["time"]["default_as_of"] == "previous_month_end"
    assert {"unpaid_summary", "attendance_unconfirmed", "claim_detail"} <= set(doc["intents"])


def test_invalid_map_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("timezone: Asia/Tokyo\nreview: {status: maybe}\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        sm.load(p)
    assert "semantic_version" in str(e.value)


def test_nl2sql_guard_alias(tmp_path):
    p = tmp_path / "alias.json"
    p.write_text(json.dumps({"semantic_version": "x", "timezone": "UTC", "nl2sql_guard": {"allowed_relations": ["a.b"]}}))
    assert sm.load(p)["sql_guard"]["allowed_relations"] == ["a.b"]
