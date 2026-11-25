# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LexiGate contributors
"""Load and validate semantic_map documents (YAML or JSON) against spec/semantic_map.schema.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from lexigate.config import PACKAGE_DIR

SCHEMA_PATH = PACKAGE_DIR / "spec" / "semantic_map.schema.json"


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validation_errors(doc: Any) -> list[str]:
    v = jsonschema.Draft202012Validator(schema())
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))]


def load(path: str | Path, validate: bool = True) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    doc = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: not a mapping")
    if validate:
        errs = validation_errors(doc)
        if errs:
            raise ValueError(f"{path} does not match semantic_map v0:\n  " + "\n  ".join(errs))
    if "sql_guard" not in doc and "nl2sql_guard" in doc:      # v0 alias
        doc["sql_guard"] = doc["nl2sql_guard"]
    return doc
