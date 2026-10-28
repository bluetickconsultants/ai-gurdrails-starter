"""L6 Observe. One trace per handled message, with an event from every layer.

Traces are how you tell a false block from a real attack, and how a production incident
becomes a test in evals/incidents/. The sample writes JSON lines to a local file; point
`flush()` at your logging or tracing backend instead.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class Trace:
    def __init__(self, session_id: str = "local"):
        self.id = uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.started = time.time()
        self.events: list[dict] = []

    def event(self, layer: str, **data) -> None:
        self.events.append({"layer": layer, "t_ms": round((time.time() - self.started) * 1000, 1), **data})

    def as_dict(self) -> dict:
        return {"trace_id": self.id, "session_id": self.session_id, "ts": self.started, "events": self.events}

    def flush(self, path: Path | None) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.as_dict(), default=str) + "\n")
