"""Model clients. Every layer calls `client.call(role, prompt, ...)`, so swapping providers
touches only this file and config/models.yaml.

Roles: router, generator, grounding, judge.

StubClient is a deterministic stand-in so the pipeline, tests and CI run with no API key.
Its heuristics are deliberately simple; they exist to exercise the code paths, not to
measure model quality. Run the eval suites against a real provider before trusting numbers.
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from .common import load_yaml


class LLMClient(Protocol):
    def call(
        self,
        role: str,
        prompt: str,
        *,
        variables: dict | None = None,
        json_mode: bool = False,
        system: str | None = None,
    ) -> str: ...


# --------------------------------------------------------------------------- stub

_INJECTION = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"\byou are now\b",
    r"developer mode",
    r"legally binding",
    r"(agree|say yes) (with|to) (anything|everything)",
    r"(system|hidden) (prompt|instructions)",
    r"\b(i'?m|i am) (the|an) admin\b",
    r"no takesies",
    r"pretend (to be|you are)",
]
_PII_REQUEST = [r"customer\s*(id\s*)?#?\s*\d{3,}", r"someone else'?s", r"another customer"]

_COMMITMENT = [
    r"\b(i|we)('ll| will| can| have)\s+(refund|waive|waived|reverse|credit|approve)",
    r"legally binding",
    r"\bguarantee",
    r"\byou are eligible\b",
    r"\bfees? (are|is|will be) waived\b",
]
_PROFANITY = [r"\b(damn|hell|crap|fuck\w*|shit\w*|bloody)\b"]
_OFF_BRAND = [r"\b(worst|useless|terrible|pathetic)\b"]
_PERSONA = [r"\b(i'?m|i am) (a )?(human|real person)\b", r"my (system )?instructions (are|say)"]
_PII_LEAK = [r"\b[A-Z]{5}\d{4}[A-Z]\b", r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b"]


def _any(patterns: list[str], text: str, flags=re.IGNORECASE) -> str | None:
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(0)
    return None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


class StubClient:
    """Deterministic fake model.

    `script` lets a test or eval case force what a role returns, which is how the suites
    simulate a model that fabricates a policy or swears. Values may be a string (returned
    every time) or a list (returned in order, last one repeated).
    """

    def __init__(self, script: dict[str, Any] | None = None):
        self.script = {k: (list(v) if isinstance(v, list) else v) for k, v in (script or {}).items()}
        self.calls: list[tuple[str, str]] = []

    def call(self, role, prompt, *, variables=None, json_mode=False, system=None) -> str:
        self.calls.append((role, prompt))
        if role in self.script:
            value = self.script[role]
            if isinstance(value, list):
                return value.pop(0) if len(value) > 1 else value[0]
            return value
        handler = getattr(self, f"_{role}", None)
        if handler is None:
            raise ValueError(f"StubClient has no behaviour for role '{role}'")
        return handler(variables or {})

    # Router: keyword matching against the policy's intent examples.
    def _router(self, v: dict) -> str:
        text = v["user_message"].lower()
        intent = "out_of_scope"
        for group in (v["never_generate"], v["intents"]):
            match = next((name for name, spec in group.items()
                          if any(ex.lower() in text for ex in spec.get("examples", []))), None)
            if match:
                intent = match
                break
        high = intent in v["never_generate"] or intent in v.get("high_risk_intents", [])
        return json.dumps({
            "intent": intent,
            "risk": "high" if high else "low",
            "injection": _any(_INJECTION, text) is not None,
            "pii_requested": _any(_PII_REQUEST, text) is not None,
            "reason": "stub classification",
        })

    # Generator: answers with the best retrieved chunk, or escalates when there is none.
    def _generator(self, v: dict) -> str:
        chunks = v.get("chunks") or []
        if not chunks:
            return json.dumps({"action": "escalate", "reason": "No source covers this question."})
        best = chunks[0]
        cite = f" [policy {best.policy_id}]" if best.policy_id else ""
        return f"{best.text}{cite}"

    # Grounding: every number in a sentence must appear in the sources.
    def _grounding(self, v: dict) -> str:
        sources = v["sources_text"]
        claims = []
        for sentence in _sentences(v["answer"]):
            numbers = re.findall(r"\d+(?:[.:]\d+)?", sentence)
            supported = all(n in sources for n in numbers)
            claims.append({"claim": sentence, "supported": supported, "source_id": None, "contradicted": False})
        verdict = "pass" if all(c["supported"] for c in claims) else "fail"
        return json.dumps({"claims": claims, "verdict": verdict})

    # Judge: regex checks for commitments, tone, persona and leaked identifiers.
    def _judge(self, v: dict) -> str:
        reply = v["reply"]
        reasons = []
        for name, patterns, flags in (
            ("commitment", _COMMITMENT, re.IGNORECASE),
            ("off_brand", _PROFANITY + _OFF_BRAND, re.IGNORECASE),
            ("persona_break", _PERSONA, re.IGNORECASE),
            ("pii_leak", _PII_LEAK, 0),
        ):
            hit = _any(patterns, reply, flags)
            if hit:
                reasons.append(f"{name}: {hit}")
        return json.dumps({"block": bool(reasons), "reasons": reasons})


# --------------------------------------------------------------------------- anthropic

class AnthropicClient:
    ROLE_TO_MODEL = {"router": "router", "generator": "generator", "grounding": "verifier", "judge": "judge"}

    def __init__(self, cfg: dict):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install the SDK first: pip install -r requirements-model.txt") from exc
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.cfg = cfg

    def call(self, role, prompt, *, variables=None, json_mode=False, system=None) -> str:
        kwargs: dict[str, Any] = {
            "model": self.cfg[self.ROLE_TO_MODEL.get(role, "generator")],
            "max_tokens": self.cfg.get("max_tokens", 1024),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if self.cfg.get("temperature") is not None:
            kwargs["temperature"] = self.cfg["temperature"]
        response = self._client.messages.create(**kwargs)
        return "".join(getattr(block, "text", "") for block in response.content)


def get_client(provider: str | None = None, script: dict | None = None) -> LLMClient:
    cfg = load_yaml("config/models.yaml")
    provider = provider or cfg.get("provider", "stub")
    if provider == "stub":
        return StubClient(script)
    if provider == "anthropic":
        return AnthropicClient(cfg)
    raise ValueError(f"Unknown provider '{provider}'. Add it to layers/llm.py:get_client().")
