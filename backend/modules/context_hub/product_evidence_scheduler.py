"""One shared in-process scheduler for durable product-evidence TTL wakeups."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from backend.modules.context_hub.contracts import ContextHubPaths
from backend.modules.context_hub.runtime import _utc_now
from backend.modules.context_hub.state import CONTEXT_HUB_STATE


@dataclass(frozen=True)
class _TransitionWakeup:
    key: str
    due_at: str
    callback: Callable[[], object]
    attempts: int = 0


_RETRY_BASE_SECONDS = 1.0
_RETRY_MAX_SECONDS = 300.0


def _as_utc(value: object) -> datetime:
    normalized = str(value or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _scheduler_key(paths: ContextHubPaths) -> str:
    return str(paths.internal_dir).casefold()


def _retain_failed_wakeup(entry: _TransitionWakeup, now: datetime) -> None:
    delay = min(_RETRY_MAX_SECONDS, _RETRY_BASE_SECONDS * (2 ** min(entry.attempts, 8)))
    retry = _TransitionWakeup(
        key=entry.key,
        due_at=(now + timedelta(seconds=delay)).isoformat(timespec="microseconds"),
        callback=entry.callback,
        attempts=entry.attempts + 1,
    )
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        if entry.key not in CONTEXT_HUB_STATE.product_evidence_transition_schedule:
            CONTEXT_HUB_STATE.product_evidence_transition_schedule[entry.key] = retry


def _due_wakeups(now: datetime) -> tuple[list[_TransitionWakeup], float | None]:
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        entries = list(CONTEXT_HUB_STATE.product_evidence_transition_schedule.items())
        due_keys = [key for key, entry in entries if _as_utc(entry.due_at) <= now]
        due = [
            CONTEXT_HUB_STATE.product_evidence_transition_schedule.pop(key)
            for key in due_keys
            if key in CONTEXT_HUB_STATE.product_evidence_transition_schedule
        ]
        remaining = list(CONTEXT_HUB_STATE.product_evidence_transition_schedule.values())
    if due:
        return due, 0.0
    if not remaining:
        return [], None
    delay = min((_as_utc(entry.due_at) - now).total_seconds() for entry in remaining)
    return [], max(0.0, delay)


def _scheduler_entry(wake_event: threading.Event, stop_event: threading.Event) -> None:
    current = threading.current_thread()
    while not stop_event.is_set():
        due, delay = _due_wakeups(_as_utc(_utc_now()))
        for entry in due:
            try:
                result = entry.callback()
                if result is False or (isinstance(result, Mapping) and result.get("success") is False):
                    raise RuntimeError("transition_callback_failed")
            except Exception:
                _retain_failed_wakeup(entry, _as_utc(_utc_now()))
        if due:
            continue
        if delay is None:
            with CONTEXT_HUB_STATE.product_evidence_sync_guard:
                registered = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
                if registered and registered[0] is current:
                    CONTEXT_HUB_STATE.product_evidence_transition_scheduler = None
            return
        wake_event.wait(min(delay, 86_400.0))
        wake_event.clear()
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        registered = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
        if registered and registered[0] is current:
            CONTEXT_HUB_STATE.product_evidence_transition_scheduler = None


def schedule_product_evidence_transition(
    paths: ContextHubPaths,
    due_at: object,
    callback: Callable[[], object],
) -> bool:
    normalized_due = str(due_at or "").strip()
    if not normalized_due or _as_utc(normalized_due) <= _as_utc(_utc_now()):
        return False
    key = _scheduler_key(paths)
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        CONTEXT_HUB_STATE.product_evidence_transition_schedule[key] = _TransitionWakeup(
            key=key,
            due_at=normalized_due,
            callback=callback,
        )
        registered = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
        if registered and registered[0].is_alive():
            registered[1].set()
            return True
        wake_event = threading.Event()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_scheduler_entry,
            args=(wake_event, stop_event),
            name="context-hub-evidence-ttl-scheduler",
            daemon=True,
        )
        CONTEXT_HUB_STATE.product_evidence_transition_scheduler = (
            thread,
            wake_event,
            stop_event,
        )
        thread.start()
    return True


def cancel_product_evidence_transition(paths: ContextHubPaths) -> None:
    key = _scheduler_key(paths)
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        CONTEXT_HUB_STATE.product_evidence_transition_schedule.pop(key, None)
        registered = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
        if registered:
            registered[1].set()


def stop_product_evidence_transition_scheduler() -> None:
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        registered = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
        CONTEXT_HUB_STATE.product_evidence_transition_scheduler = None
        CONTEXT_HUB_STATE.product_evidence_transition_schedule.clear()
    if not registered:
        return
    thread, wake_event, stop_event = registered
    stop_event.set()
    wake_event.set()
    thread.join(timeout=1.0)


__all__ = [
    "cancel_product_evidence_transition",
    "schedule_product_evidence_transition",
    "stop_product_evidence_transition_scheduler",
]
