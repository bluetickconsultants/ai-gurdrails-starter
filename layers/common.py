"""Shared helpers: repo paths, YAML loading, prompt rendering and JSON parsing."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_HEADER = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


@lru_cache(maxsize=None)
def load_yaml(rel_path: str) -> Any:
    with open(ROOT / rel_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_bundle() -> dict:
    """Every policy, response and config file the pipeline needs, keyed by short name."""
    return {
        "policy": load_yaml("policies/product_policy.yaml"),
        "tools": load_yaml("policies/tool_permissions.yaml"),
        "trust": load_yaml("policies/source_trust.yaml"),
        "pii": load_yaml("policies/pii_rules.yaml"),
        "refusals": load_yaml("responses/refusals.yaml"),
        "escalations": load_yaml("responses/escalations.yaml"),
        "thresholds": load_yaml("config/thresholds.yaml"),
        "models": load_yaml("config/models.yaml"),
    }


def render(rel_path: str, **variables: Any) -> str:
    """Fill {{var}} placeholders in a prompt file. Missing variables raise, so a renamed
    variable fails in tests instead of silently sending a broken prompt."""
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    text = _HEADER.sub("", text, count=1)

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Prompt {rel_path} needs '{key}' but it was not supplied")
        return str(variables[key])

    return _VAR.sub(sub, text)


def parse_json(raw: str | None) -> dict | None:
    """Parse a model's JSON reply. Returns None when there is no usable object, so callers
    can fail closed."""
    if not raw:
        return None
    raw = raw.strip()
    fence = _FENCE.match(raw)
    if fence:
        raw = fence.group(1)
    for candidate in (raw, raw[raw.find("{"): raw.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else None
    return None
