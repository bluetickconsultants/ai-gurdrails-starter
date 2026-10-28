"""Per-request state that guardrails read. Build it on the server from the authenticated
session and system flags, never from anything the model or the user's message says."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Context:
    customer_id: str | None = None   # from the authenticated session
    env: str = "prod"                # dev | staging | prod, from deployment config
    freeze_active: bool = False      # from your release or change-management system
    session_id: str = "local"
    tool_counts: dict[str, int] = field(default_factory=dict)  # calls made so far this session
    conversation_summary: str = ""
