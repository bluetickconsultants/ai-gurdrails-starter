"""Runs the layers in order for one user message.

L1 input hygiene -> L2 router -> L3 retrieval -> generate -> L5 grounding + judge -> reply
L6 records an event at every step. L4 (tool gate) sits in your agent loop; see l4_tools.py.

Every exit that isn't a generated answer uses fixed copy from responses/.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import l1_input, l2_router, l3_retrieval, l5_output
from .common import ROOT, load_bundle, load_yaml, parse_json, render
from .context import Context
from .l6_observe import Trace


@dataclass
class Response:
    text: str
    outcome: str                  # answered | refused | escalated
    reason: str | None = None     # refusal or escalation key
    path: str | None = None       # router path
    layer: str | None = None      # layer that stopped the turn
    queue: str | None = None      # escalation queue
    detail: list | str | None = None
    redactions: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)  # retrieved chunk ids dropped by L3
    trace_id: str | None = None


def load_kb(rel_path: str = "examples/sample_kb.yaml") -> list[dict]:
    return load_yaml(rel_path)["chunks"]


class Pipeline:
    def __init__(self, client, kb: list[dict] | None = None, *, tracing: bool | None = None):
        self.client = client
        self.cfg = load_bundle()
        self.kb = kb if kb is not None else load_kb()
        tcfg = self.cfg["thresholds"]["tracing"]
        enabled = tcfg.get("enabled", False) if tracing is None else tracing
        self.trace_path = ROOT / tcfg["path"] if enabled else None

    # ------------------------------------------------------------------ public

    def handle(self, message: str, ctx: Context | None = None) -> Response:
        ctx = ctx or Context()
        trace = Trace(ctx.session_id)
        try:
            response = self._handle(message, ctx, trace)
            response.trace_id = trace.id
            trace.event("reply", outcome=response.outcome, reason=response.reason)
            return response
        finally:
            trace.flush(self.trace_path)

    # ------------------------------------------------------------------ steps

    def _handle(self, message: str, ctx: Context, trace: Trace) -> Response:
        cfg, policy = self.cfg, self.cfg["policy"]
        limits = cfg["thresholds"]["limits"]

        l1 = l1_input.run(message, cfg["pii"], limits)
        trace.event("l1", redactions=l1.redactions, blocked=l1.blocked, reason=l1.reason)
        if l1.blocked:
            return self._refuse("input_rejected", layer="l1", detail=l1.reason, redactions=l1.redactions)

        route = l2_router.route(l1.text, self.client, policy, ctx.conversation_summary)
        trace.event("l2", **asdict(route))
        if route.path == "refuse":
            return self._refuse(route.response_key, layer="l2", path=route.path, redactions=l1.redactions)
        if route.path == "escalate":
            return self._escalate(route.response_key, layer="l2", path=route.path, redactions=l1.redactions)

        strict = route.path == "strict"
        retrieval = l3_retrieval.retrieve(l1.text, self.kb, cfg["trust"], strict=strict, **cfg["thresholds"]["retrieval"])
        quarantined = [c.id for c in retrieval.quarantined]
        trace.event("l3", chunks=[c.id for c in retrieval.chunks], quarantined=quarantined)
        common = {"path": route.path, "redactions": l1.redactions, "quarantined": quarantined}
        if not retrieval.chunks:
            return self._escalate("no_source", layer="l3", **common)

        sources_text = l3_retrieval.format_for_prompt(retrieval.chunks, cfg["trust"])
        feedback: list[str] | None = None
        for attempt in range(limits.get("max_regenerations", 1) + 1):
            draft = self._generate(l1.text, retrieval.chunks, sources_text, attempt, feedback)
            trace.event("generate", attempt=attempt, draft=draft)

            if draft.strip().startswith("{"):
                parsed = parse_json(draft)
                if parsed and parsed.get("action") == "escalate":
                    return self._escalate("no_source", layer="generate", detail=parsed.get("reason"), **common)

            if strict:
                grounding = l5_output.grounding_check(draft, sources_text, self.client)
                trace.event("l5_grounding", attempt=attempt, verdict=grounding.verdict, failing=grounding.failing_claims)
                if not grounding.passed:
                    feedback = grounding.failing_claims
                    continue

            judge = l5_output.output_judge(draft, policy, self.client)
            trace.event("l5_judge", block=judge.block, reasons=judge.reasons)
            if judge.block:
                return self._escalate("judge_block", layer="l5", detail=judge.reasons, **common)

            return Response(text=l5_output.with_ai_label(draft, policy), outcome="answered", **common)

        return self._escalate("grounding_fail", layer="l5", detail=feedback, **common)

    def _system_prompt(self) -> str:
        policy = self.cfg["policy"]
        return render(
            "prompts/system/assistant_v3.md",
            never_generate_topics=", ".join(k.replace("_", " ") for k in policy["never_generate"]),
            **policy["assistant"],
        )

    def _generate(self, question, chunks, sources_text, attempt, feedback) -> str:
        prompt = f"<retrieved>\n{sources_text}\n</retrieved>\n\nCustomer question: {question}"
        if feedback:
            listed = "\n".join(f"- {c}" for c in feedback)
            prompt += (
                "\n\nYour previous draft made claims the sources do not support:\n"
                f"{listed}\nRewrite using only the sources, or return the escalation JSON."
            )
        return self.client.call(
            "generator",
            prompt,
            system=self._system_prompt(),
            variables={"question": question, "chunks": chunks, "attempt": attempt},
        )

    # ------------------------------------------------------------------ fixed responses

    def _refuse(self, key: str, **kw) -> Response:
        text = l5_output.with_ai_label(self.cfg["refusals"][key], self.cfg["policy"])
        return Response(text=text, outcome="refused", reason=key, **kw)

    def _escalate(self, key: str, **kw) -> Response:
        esc = self.cfg["escalations"][key]
        text = l5_output.with_ai_label(esc["message"], self.cfg["policy"])
        return Response(text=text, outcome="escalated", reason=key, queue=esc["queue"], **kw)
