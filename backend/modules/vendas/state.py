"""Owned synchronization state for the Vendas domain."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VendasSyncState:
    cancel_flags: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    logs: dict[str, list] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    day_context: dict[str, Any] = field(default_factory=dict)
    active: dict[str, Any] = field(default_factory=dict)
    max_active_sync: int = 2
    active_lock: threading.RLock = field(default_factory=threading.RLock)
    persistence_lock: threading.RLock = field(default_factory=threading.RLock)
    thread_context: threading.local = field(default_factory=threading.local)


DEFAULT_SYNC_STATE = VendasSyncState()

# Explicit compatibility aliases. The state object remains the sole owner.
SYNC_CANCEL_FLAGS = DEFAULT_SYNC_STATE.cancel_flags
SYNC_PROGRESS = DEFAULT_SYNC_STATE.progress
SYNC_LOGS = DEFAULT_SYNC_STATE.logs
SYNC_META = DEFAULT_SYNC_STATE.metadata
SYNC_DAY_CONTEXT = DEFAULT_SYNC_STATE.day_context
SYNC_ACTIVE = DEFAULT_SYNC_STATE.active
SYNC_MAX_ACTIVE_VENDAS = DEFAULT_SYNC_STATE.max_active_sync
SYNC_ACTIVE_LOCK = DEFAULT_SYNC_STATE.active_lock
SYNC_STATE_LOCK = DEFAULT_SYNC_STATE.persistence_lock
SYNC_THREAD_CONTEXT = DEFAULT_SYNC_STATE.thread_context


__all__ = [
    "VendasSyncState",
    "DEFAULT_SYNC_STATE",
    "SYNC_CANCEL_FLAGS",
    "SYNC_PROGRESS",
    "SYNC_LOGS",
    "SYNC_META",
    "SYNC_DAY_CONTEXT",
    "SYNC_ACTIVE",
    "SYNC_MAX_ACTIVE_VENDAS",
    "SYNC_ACTIVE_LOCK",
    "SYNC_STATE_LOCK",
    "SYNC_THREAD_CONTEXT",
]
