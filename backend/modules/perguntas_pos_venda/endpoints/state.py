"""Central concurrent state for Perguntas/Pós-venda HTTP workflows."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PerguntasEndpointsState:
    runtime_guard: threading.RLock = field(default_factory=threading.RLock)
    runtime: Optional[Any] = None
    approval_send_lock: threading.RLock = field(default_factory=threading.RLock)
    pos_sale_sync_lock: threading.RLock = field(default_factory=threading.RLock)
    pos_sale_sync_threads: dict[str, threading.Thread] = field(default_factory=dict)
    pos_sale_sync_semaphore: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(1)
    )


ENDPOINTS_STATE = PerguntasEndpointsState()
