"""Single owner for mutable WhatsApp bridge runtime state."""

from __future__ import annotations

import concurrent.futures
import itertools
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional


def _runtime_diagnostics() -> dict[str, Any]:
    return {
        "running": False,
        "last_error": "",
        "last_processing_at": "",
        "last_worker_ok_at": "",
        "typing_last_error": "",
        "typing_last_sent_at": "",
        "progress_last_error": "",
        "progress_last_sent_at": "",
        "whisper_download_status": "idle",
        "whisper_download_error": "",
        "whisper_download_started_at": "",
    }


def _latency_samples() -> dict[str, deque[float]]:
    return {
        "gateway_to_luna": deque(maxlen=500),
        "claimed_to_luna": deque(maxlen=500),
        "luna_duration": deque(maxlen=500),
        "sol_duration": deque(maxlen=500),
        "manager_duration": deque(maxlen=500),
        "manager_tools_duration": deque(maxlen=500),
        "completed_to_sent": deque(maxlen=500),
    }


@dataclass(slots=True)
class BridgeRuntimeState:
    """Own locks, queues, executors, threads, caches and diagnostics."""

    dual_agent_state_lock: threading.RLock = field(default_factory=threading.RLock)
    bridge_state_lock: threading.RLock = field(default_factory=threading.RLock)
    bridge_shared_state: Optional[dict[str, Any]] = None
    bridge_store: Any = None
    config_lock: threading.RLock = field(default_factory=threading.RLock)
    stop_event: threading.Event = field(default_factory=threading.Event)
    bridge_thread: Optional[threading.Thread] = None
    download_thread: Optional[threading.Thread] = None
    typing_pulses_lock: threading.RLock = field(default_factory=threading.RLock)
    typing_pulses: dict[str, threading.Event] = field(default_factory=dict)
    progress_pulses_lock: threading.RLock = field(default_factory=threading.RLock)
    progress_pulses: dict[str, threading.Event] = field(default_factory=dict)
    runtime_diagnostics: dict[str, Any] = field(default_factory=_runtime_diagnostics)
    model_validation_cache: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    phone_dispatch_lock: threading.RLock = field(default_factory=threading.RLock)
    phone_dispatch_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
    phone_dispatch_executor_workers: int = 0
    phone_dispatch_old_executors: list[concurrent.futures.ThreadPoolExecutor] = field(default_factory=list)
    phone_dispatch_queues: dict[str, list[tuple[int, int, dict[str, Any]]]] = field(default_factory=dict)
    phone_dispatch_active: set[str] = field(default_factory=set)
    phone_dispatch_inflight: int = 0
    phone_dispatch_event_ids: set[str] = field(default_factory=set)
    phone_dispatch_tick_ids: set[str] = field(default_factory=set)
    phone_dispatch_futures: set[concurrent.futures.Future[Any]] = field(default_factory=set)
    phone_dispatch_sequence: Any = field(default_factory=itertools.count)
    phone_dispatch_accepting: bool = True
    function_manager_lock: threading.RLock = field(default_factory=threading.RLock)
    function_manager_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
    function_manager_executor_workers: int = 0
    function_manager_futures: dict[str, concurrent.futures.Future[Any]] = field(default_factory=dict)
    web_fallback_lock: threading.RLock = field(default_factory=threading.RLock)
    web_fallback_circuit: dict[str, Any] = field(
        default_factory=lambda: {"failures": 0, "opened_until_epoch": 0.0, "last_error": ""}
    )
    phone_latency_samples: dict[str, deque[float]] = field(default_factory=_latency_samples)


__all__ = ["BridgeRuntimeState"]
