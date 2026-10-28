"""L4 Tool gate. No prompt takes part in the decision: the model requests, this code decides.

Checks, in order: known tool, environment, customer scope, argument schema, destructive verbs,
change freeze, per-session limits, user confirmation. Wire `gate()` into your agent loop so
every tool call the model proposes passes through it before execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .common import load_yaml
from .context import Context

_TYPES = {"str": str, "int": int, "bool": bool}


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)
    verb: str | None = None


@dataclass
class GateDecision:
    decision: str                 # allow | deny | approval
    reason: str
    approver: str | None = None
    call: ToolCall | None = None  # the call to execute, with server-injected args applied


def _validate(args: dict, spec: dict, injected: list[str]) -> str | None:
    for name, type_name in spec.items():
        if name not in args:
            return f"missing argument '{name}'"
        if not isinstance(args[name], _TYPES.get(type_name, object)):
            return f"argument '{name}' should be {type_name}"
    extra = set(args) - set(spec) - set(injected)
    if extra:
        return f"unexpected arguments {sorted(extra)}"
    return None


def _verb(call: ToolCall, rule: dict) -> str | None:
    if call.verb:
        return call.verb.upper()
    arg = rule.get("verb_from_arg")
    value = call.args.get(arg) if arg else None
    if isinstance(value, str) and value.strip():
        return value.strip().split()[0].upper()
    return None


def gate(call: ToolCall, ctx: Context, permissions: dict | None = None) -> GateDecision:
    perms = permissions or load_yaml("policies/tool_permissions.yaml")
    rule = perms["tools"].get(call.name)
    if rule is None:
        return GateDecision("deny", "unknown_tool")

    if ctx.env not in rule.get("allowed_envs", []):
        return GateDecision("deny", "env_not_allowed")

    if rule.get("scope") == "own_customer_only":
        if ctx.customer_id is None:
            return GateDecision("deny", "no_customer_in_session")
        requested = call.args.get("customer_id")
        if requested is not None and requested != ctx.customer_id:
            return GateDecision("deny", "scope_violation")
        call = replace(call, args={**call.args, "customer_id": ctx.customer_id})

    problem = _validate(call.args, rule.get("args", {}), rule.get("server_injected_args", []))
    if problem:
        return GateDecision("deny", "bad_args", call=call)

    approver = rule.get("approver")
    verb = _verb(call, rule)
    if verb and verb in {v.upper() for v in rule.get("destructive_verbs", [])}:
        return GateDecision("approval", "destructive_verb", approver, call)

    if ctx.freeze_active and rule.get("mutates", True):
        return GateDecision("approval", "freeze_active", approver, call)

    per_session = rule.get("limits", {}).get("per_session")
    if per_session is not None and ctx.tool_counts.get(call.name, 0) >= per_session:
        return GateDecision("approval", "limit_exceeded", approver, call)

    if rule.get("approval") == "user_confirm":
        return GateDecision("approval", "user_confirm", approver or "user", call)

    return GateDecision("allow", "ok", call=call)
