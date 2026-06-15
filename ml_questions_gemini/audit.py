from __future__ import annotations

import datetime as dt
from typing import Any


class AuditLogger:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def log(self, action: str, **payload: Any) -> dict[str, Any]:
        event = {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "action": action,
            **payload,
        }
        self.events.append(event)
        return event
