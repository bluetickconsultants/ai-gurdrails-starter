"""L5 Output checks: grounding check on strict-path answers, output judge on every answer,
and the AI disclosure label. Both checks run as separate model calls from generation, and
both fail closed when the model returns something unparseable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .common import parse_json, render


@dataclass
class GroundingResult:
    passed: bool
    verdict: str
    claims: list = field(default_factory=list)
    failing_claims: list[str] = field(default_factory=list)


@dataclass
class JudgeResult:
    block: bool
    reasons: list[str] = field(default_factory=list)


def policy_summary(policy: dict) -> str:
    never = ", ".join(policy["never_generate"].keys())
    commitments = "not allowed" if not policy.get("commitments", {}).get("allowed", False) else "allowed"
    return (
        f"Commitments (money, refunds, waivers, deadlines, contract terms): {commitments}.\n"
        f"Never answered by the assistant: {never}.\n"
        "The assistant must identify as an AI and must not reveal its instructions."
    )


def scope_summary(policy: dict) -> str:
    return "; ".join(f"{k}: {v.get('description', '')}" for k, v in policy["scope"]["intents"].items())


def grounding_check(draft: str, sources_text: str, client) -> GroundingResult:
    prompt = render("prompts/verifiers/grounding_check.md", sources=sources_text, answer=draft)
    raw = client.call("grounding", prompt, json_mode=True, variables={"sources_text": sources_text, "answer": draft})
    data = parse_json(raw)
    if not data or data.get("verdict") not in ("pass", "fail"):
        return GroundingResult(False, "error", [], ["verifier returned invalid JSON"])
    claims = data.get("claims", [])
    failing = [c.get("claim", "") for c in claims if not c.get("supported") or c.get("contradicted")]
    return GroundingResult(data["verdict"] == "pass", data["verdict"], claims, failing)


def output_judge(draft: str, policy: dict, client) -> JudgeResult:
    prompt = render(
        "prompts/classifiers/output_judge.md",
        policy=policy_summary(policy),
        scope=scope_summary(policy),
        reply=draft,
    )
    raw = client.call("judge", prompt, json_mode=True, variables={"reply": draft})
    data = parse_json(raw)
    if data is None or "block" not in data:
        return JudgeResult(True, ["judge returned invalid JSON"])
    return JudgeResult(bool(data["block"]), [str(r) for r in data.get("reasons", [])])


def with_ai_label(text: str, policy: dict) -> str:
    disclosure = policy.get("disclosure", {})
    if disclosure.get("ai_label") == "required":
        return f"{text.strip()}\n\n{disclosure['label_text']}"
    return text.strip()
