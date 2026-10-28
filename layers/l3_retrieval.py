"""L3 Retrieval. Trust tiers per source, quarantine for injected text, and tagging so the
model reads retrieved content as data.

The keyword search in `search()` is a stand-in so the repo runs without a vector store.
Replace it with your retriever and keep `assign_tiers`, `apply_rules` and `format_for_prompt`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from html import escape

TIER_RANK = {"authoritative": 0, "supporting": 1, "untrusted": 2}

_STOPWORDS = set(
    "a an the is it i my me you your we our to of in on for and or are do does how what where "
    "when can at this that with be if from by any only one will there about as its was".split()
)


@dataclass
class Chunk:
    id: str
    source: str
    text: str
    policy_id: str | None = None
    tier: str = "untrusted"
    score: float = 0.0


@dataclass
class RetrievalResult:
    chunks: list[Chunk]
    quarantined: list[Chunk]


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("es") and word[:-2].endswith(("ch", "sh", "x")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def tier_for(source: str, trust: dict) -> str:
    for tier in ("authoritative", "supporting", "untrusted"):
        if any(fnmatch(source, pattern) for pattern in trust["tiers"].get(tier, [])):
            return tier
    return "untrusted"


def search(query: str, kb: list[dict], top_k: int, min_score: float) -> list[Chunk]:
    """Sample retriever: unique keyword overlap. Swap for vector or hybrid search."""
    q = tokens(query)
    scored = []
    for doc in kb:
        score = len(q & tokens(doc["text"]))
        if score >= min_score:
            scored.append(Chunk(doc["id"], doc["source"], doc["text"], doc.get("policy_id"), score=score))
    scored.sort(key=lambda c: -c.score)
    return scored[:top_k]


def assign_tiers(chunks: list[Chunk], trust: dict) -> list[Chunk]:
    for chunk in chunks:
        chunk.tier = tier_for(chunk.source, trust)
    return chunks


def apply_rules(chunks: list[Chunk], trust: dict, *, strict: bool) -> RetrievalResult:
    rules = trust.get("rules", {})
    patterns = [re.compile(p, re.IGNORECASE) for p in rules.get("quarantine_patterns", [])]
    kept, quarantined = [], []
    for chunk in chunks:
        if chunk.tier != "authoritative" and any(p.search(chunk.text) for p in patterns):
            quarantined.append(chunk)
            continue
        if strict and chunk.tier not in rules.get("high_risk_intents_require", ["authoritative"]):
            continue
        kept.append(chunk)
    kept.sort(key=lambda c: (-c.score, TIER_RANK[c.tier]))
    return RetrievalResult(kept, quarantined)


def retrieve(query: str, kb: list[dict], trust: dict, *, strict: bool, top_k: int = 3, min_score: float = 1) -> RetrievalResult:
    return apply_rules(assign_tiers(search(query, kb, top_k, min_score), trust), trust, strict=strict)


def format_for_prompt(chunks: list[Chunk], trust: dict) -> str:
    untrusted_tag = trust.get("rules", {}).get("untrusted_wrap_tag", "retrieved_untrusted")
    parts = []
    for c in chunks:
        attrs = f'id="{c.id}" tier="{c.tier}"' + (f' policy_id="{c.policy_id}"' if c.policy_id else "")
        body = f'<source {attrs}>\n{escape(c.text, quote=False)}\n</source>'
        if c.tier == "untrusted":
            body = f"<{untrusted_tag}>\n{body}\n</{untrusted_tag}>"
        parts.append(body)
    return "\n".join(parts)
