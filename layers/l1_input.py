"""L1 Input hygiene. Deterministic and cheap; runs on all traffic before any model call.

Size limits, a denylist and PII redaction. Redaction has to happen here: once text reaches a
hosted model it has left your network, which is what DPDP, APPI and GDPR care about.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class L1Result:
    text: str
    redactions: list[str] = field(default_factory=list)
    blocked: bool = False
    reason: str | None = None


def run(text: str, pii_rules: dict, limits: dict) -> L1Result:
    if not text or not text.strip():
        return L1Result(text="", blocked=True, reason="empty_input")

    max_chars = limits.get("max_chars", 4000)
    if len(text) > max_chars:
        return L1Result(text=text[:max_chars], blocked=True, reason="input_too_long")

    for pattern in pii_rules.get("denylist_patterns", []):
        if re.search(pattern, text, flags=re.IGNORECASE):
            return L1Result(text=text, blocked=True, reason="denylisted_content")

    redacted = text
    found: list[str] = []
    for entity in pii_rules.get("entities", []):
        if entity.get("enabled", True) is False:
            continue
        redacted, count = re.subn(entity["pattern"], f"[{entity['name']}]", redacted)
        found.extend([entity["name"]] * count)

    return L1Result(text=redacted, redactions=found)
