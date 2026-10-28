"""L2 Router. A small model classifies intent, risk and injection in one call; code picks the path.

Paths
  refuse    fixed refusal copy (injection, another person's data, out of scope)
  escalate  fixed handoff copy (never_generate topics, router failure)
  strict    high-risk intent: grounding check on every answer
  standard  everything else

Any malformed router output fails closed to `escalate`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .common import parse_json, render


@dataclass
class Route:
    path: str
    intent: str
    risk: str
    injection: bool
    pii_requested: bool
    reason: str
    response_key: str | None = None


def intent_list(policy: dict) -> str:
    lines = []
    for name, spec in {**policy["scope"]["intents"], **policy["never_generate"]}.items():
        examples = ", ".join(spec.get("examples", []))
        lines.append(f"- {name}: {spec.get('description', '')} (e.g. {examples})")
    return "\n".join(lines)


def decide(data: dict, policy: dict) -> Route:
    """Turn the router's classification into a path. Pure code, easy to unit-test."""
    intents = policy["scope"]["intents"]
    never = policy["never_generate"]
    intent = str(data.get("intent", "out_of_scope"))
    risk = "high" if data.get("risk") == "high" else "low"
    injection = bool(data.get("injection"))
    pii_requested = bool(data.get("pii_requested"))
    reason = str(data.get("reason", ""))

    def route(path: str, key: str | None = None) -> Route:
        return Route(path, intent, risk, injection, pii_requested, reason, key)

    if injection:
        return route("refuse", "manipulation")
    if pii_requested:
        return route("refuse", "other_customer_data")
    if intent in never:
        return route("escalate", "never_generate")
    if intent not in intents:  # out_of_scope, or a label the policy doesn't know
        return route("refuse", "out_of_scope")
    if intent in policy.get("high_risk_intents", []) or risk == "high":
        return route("strict")
    return route("standard")


def route(text: str, client, policy: dict, conversation_summary: str = "") -> Route:
    prompt = render(
        "prompts/classifiers/input_router.md",
        intent_list=intent_list(policy),
        conversation_summary=conversation_summary or "(none)",
        user_message=text,
    )
    raw = client.call(
        "router",
        prompt,
        json_mode=True,
        variables={
            "user_message": text,
            "intents": policy["scope"]["intents"],
            "never_generate": policy["never_generate"],
            "high_risk_intents": policy.get("high_risk_intents", []),
        },
    )
    data = parse_json(raw)
    if data is None or "intent" not in data:
        return Route("escalate", "unknown", "high", False, False, "router returned invalid JSON", "router_error")
    return decide(data, policy)
