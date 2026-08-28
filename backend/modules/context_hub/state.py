"""Central concurrent state for the Context Hub domain."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ContextHubState:
    config_guard: threading.RLock = field(default_factory=threading.RLock)
    runtime_config: Optional[Any] = None
    tenant_locks_guard: threading.Lock = field(default_factory=threading.Lock)
    tenant_locks: dict[str, threading.RLock] = field(default_factory=dict)
    product_evidence_locks_guard: threading.Lock = field(default_factory=threading.Lock)
    product_evidence_locks: dict[str, threading.RLock] = field(default_factory=dict)
    curation_dashboards_refreshed: set[str] = field(default_factory=set)
    watchers_guard: threading.Lock = field(default_factory=threading.Lock)
    watchers: dict[str, tuple[threading.Thread, threading.Event]] = field(default_factory=dict)
    watch_fingerprints: dict[str, str] = field(default_factory=dict)
    product_evidence_sync_guard: threading.Lock = field(default_factory=threading.Lock)
    product_evidence_sync_workers: dict[
        str, tuple[threading.Thread, threading.Event, threading.Event]
    ] = field(default_factory=dict)
    product_evidence_transition_schedule: dict[str, Any] = field(default_factory=dict)
    product_evidence_transition_scheduler: Optional[
        tuple[threading.Thread, threading.Event, threading.Event]
    ] = None


CONTEXT_HUB_STATE = ContextHubState()
