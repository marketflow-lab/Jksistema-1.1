"""Runtime budgets, cache metadata and content-free performance metrics.

This module deliberately does not cache business payloads.  It defines the
small contracts that callers can use to enforce deadlines, scope their own
caches and export numeric stage timings to the Codex telemetry service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional


PERFORMANCE_SCHEMA_VERSION = "1"
RUNTIME_STAGES: tuple[str, ...] = (
    "queue",
    "selection",
    "tools",
    "provider_ttft",
    "provider",
    "render",
)


class DeadlineExceeded(TimeoutError):
    """Raised when a runtime stage has exhausted its caller-owned budget."""


@dataclass(frozen=True)
class DeadlineBudget:
    """Monotonic deadline shared by all stages in one execution.

    ``child`` never extends the parent deadline.  This prevents a retry or a
    tool call from accidentally resetting the end-to-end timeout.
    """

    deadline_monotonic: float
    started_monotonic: float
    label: str = "runtime"
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)

    @classmethod
    def from_timeout(
        cls,
        timeout_seconds: float,
        *,
        label: str = "runtime",
        clock: Callable[[], float] = time.monotonic,
    ) -> "DeadlineBudget":
        started = float(clock())
        timeout = max(0.0, float(timeout_seconds or 0.0))
        return cls(started + timeout, started, str(label or "runtime"), clock)

    def elapsed_seconds(self) -> float:
        return max(0.0, float(self.clock()) - self.started_monotonic)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - float(self.clock()))

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0.0

    def checkpoint(self, stage: str = "") -> float:
        remaining = self.remaining_seconds()
        if remaining <= 0.0:
            stage_code = _safe_code(stage, "deadline")
            raise DeadlineExceeded(f"deadline_exceeded:{stage_code}")
        return remaining

    def timeout_for_call(self, cap_seconds: Optional[float] = None, *, minimum: float = 0.001) -> float:
        remaining = self.checkpoint()
        if cap_seconds is not None:
            remaining = min(remaining, max(0.0, float(cap_seconds)))
        if remaining < minimum:
            raise DeadlineExceeded("deadline_exceeded:call")
        return remaining

    def child(self, cap_seconds: float, *, label: str = "stage") -> "DeadlineBudget":
        now = float(self.clock())
        deadline = min(self.deadline_monotonic, now + max(0.0, float(cap_seconds or 0.0)))
        return DeadlineBudget(deadline, now, _safe_code(label, "stage"), self.clock)


@dataclass(frozen=True)
class CachePolicy:
    """Metadata-only cache policy; business payload storage stays with callers."""

    name: str
    ttl_seconds: float
    stale_if_error_seconds: float = 0.0
    max_entries: int = 0
    policy_version: str = "1"

    def __post_init__(self) -> None:
        raw_name = str(self.name or "").strip()
        if not raw_name or len(raw_name) > 100 or not all(
            ch.isalnum() or ch in "._:/+-" for ch in raw_name
        ):
            raise ValueError("cache_policy_name_invalid")
        if self.ttl_seconds < 0 or self.stale_if_error_seconds < 0 or self.max_entries < 0:
            raise ValueError("cache_policy_limits_invalid")


DEFAULT_CACHE_POLICIES: Mapping[str, CachePolicy] = {
    "model_decision": CachePolicy("model_decision", 60.0, max_entries=2048),
    "data_selection": CachePolicy("data_selection", 30.0, max_entries=1024),
    "readonly_tool": CachePolicy("readonly_tool", 45.0, stale_if_error_seconds=30.0, max_entries=4096),
    "context_generation": CachePolicy("context_generation", 300.0, max_entries=512),
}


def build_scoped_cache_key(
    secret_key: bytes,
    *,
    namespace: str,
    client_id: object,
    store_id: object = "",
    user_id: object = "",
    source_version: object = "",
    inputs: Optional[Mapping[str, Any]] = None,
) -> str:
    """Return an HMAC key that never exposes tenant, store, user or inputs."""

    key = bytes(secret_key or b"")
    if len(key) < 16:
        raise ValueError("cache_hmac_key_too_short")
    normalized_inputs = {
        _safe_code(name, "field"): _stable_scalar(value)
        for name, value in sorted((inputs or {}).items(), key=lambda item: str(item[0]))
    }
    material = json.dumps(
        {
            "namespace": _safe_code(namespace, "cache"),
            "client": str(client_id or ""),
            "store": str(store_id or ""),
            "user": str(user_id or ""),
            "source_version": str(source_version or ""),
            "inputs": normalized_inputs,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "hmac-sha256:" + hmac.new(key, material, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class CacheEnvelope:
    """Safe cache observation suitable for telemetry and admin diagnostics."""

    key_hash: str
    policy_name: str
    state: str
    age_ms: int
    ttl_ms: int
    source_version_hash: str = ""
    bypass_reason: str = ""
    schema_version: str = PERFORMANCE_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        key_hash: str,
        policy: CachePolicy,
        created_monotonic: Optional[float],
        now_monotonic: Optional[float] = None,
        found: bool,
        source_version: object = "",
        bypass_reason: str = "",
    ) -> "CacheEnvelope":
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        age_ms = 0 if created_monotonic is None else max(0, int((now - float(created_monotonic)) * 1000))
        ttl_ms = max(0, int(float(policy.ttl_seconds) * 1000))
        bypass = _safe_code(bypass_reason, "")
        if bypass:
            state = "bypass"
        elif not found:
            state = "miss"
        elif age_ms <= ttl_ms:
            state = "hit"
        elif age_ms <= ttl_ms + max(0, int(policy.stale_if_error_seconds * 1000)):
            state = "stale"
        else:
            state = "expired"
        version_hash = ""
        if source_version:
            version_hash = hashlib.sha256(str(source_version).encode("utf-8")).hexdigest()
        safe_key = str(key_hash or "")
        if not safe_key.startswith("hmac-sha256:"):
            safe_key = ""
        return cls(
            key_hash=safe_key,
            policy_name=_safe_code(policy.name, "cache"),
            state=state,
            age_ms=age_ms,
            ttl_ms=ttl_ms,
            source_version_hash=version_hash,
            bypass_reason=bypass,
        )

    def as_telemetry(self) -> dict[str, Any]:
        return {
            "cache_status": self.state,
            "cache_policy": self.policy_name,
            "cache_age_ms": self.age_ms,
            "cache_ttl_ms": self.ttl_ms,
            "cache_key_hash": self.key_hash,
            "source_version_hash": self.source_version_hash,
            "cache_bypass_reason": self.bypass_reason,
            "performance_schema_version": self.schema_version,
        }


class RuntimePerformanceTrace:
    """Thread-safe numeric timing collector for one IA execution."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = float(clock())
        self._durations_ms: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._lock = threading.RLock()

    @contextmanager
    def measure(self, stage: str) -> Iterator[None]:
        stage_code = _runtime_stage(stage)
        started = float(self._clock())
        try:
            yield
        finally:
            self.record_duration(stage_code, (float(self._clock()) - started) * 1000.0)

    def record_duration(self, stage: str, duration_ms: float) -> None:
        stage_code = _runtime_stage(stage)
        safe_duration = max(0.0, min(float(duration_ms or 0.0), 86_400_000.0))
        with self._lock:
            self._durations_ms[stage_code] = self._durations_ms.get(stage_code, 0.0) + safe_duration
            self._counts[stage_code] = self._counts.get(stage_code, 0) + 1

    def mark_ttft(self, provider_started_monotonic: float) -> None:
        self.record_duration("provider_ttft", (float(self._clock()) - provider_started_monotonic) * 1000.0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stages = {
                name: round(self._durations_ms.get(name, 0.0), 3)
                for name in RUNTIME_STAGES
                if name in self._durations_ms
            }
            counts = {
                name: int(self._counts.get(name, 0))
                for name in RUNTIME_STAGES
                if name in self._counts
            }
        return {
            "schema_version": PERFORMANCE_SCHEMA_VERSION,
            "total_elapsed_ms": round(max(0.0, (float(self._clock()) - self._started) * 1000.0), 3),
            "stage_duration_ms": stages,
            "stage_count": counts,
        }


class PerformanceWindow:
    """Bounded in-memory window for p50/p95 operational diagnostics."""

    def __init__(self, max_samples: int = 1000) -> None:
        self._samples = {
            stage: deque(maxlen=max(1, int(max_samples or 1))) for stage in RUNTIME_STAGES
        }
        self._lock = threading.RLock()

    def add(self, snapshot: Mapping[str, Any]) -> None:
        values = snapshot.get("stage_duration_ms") if isinstance(snapshot, Mapping) else None
        if not isinstance(values, Mapping):
            return
        with self._lock:
            for stage in RUNTIME_STAGES:
                value = values.get(stage)
                if isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) >= 0:
                    self._samples[stage].append(float(value))

    def summary(self) -> dict[str, dict[str, float | int]]:
        result: dict[str, dict[str, float | int]] = {}
        with self._lock:
            for stage, samples in self._samples.items():
                if not samples:
                    continue
                values = sorted(samples)
                result[stage] = {
                    "count": len(values),
                    "p50_ms": round(_percentile(values, 0.50), 3),
                    "p95_ms": round(_percentile(values, 0.95), 3),
                    "max_ms": round(values[-1], 3),
                }
        return result


def _runtime_stage(value: object) -> str:
    stage = str(value or "").strip().lower()
    if stage not in RUNTIME_STAGES:
        raise ValueError("runtime_stage_invalid")
    return stage


def _safe_code(value: object, fallback: str = "") -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 100:
        return fallback
    if all(ch.isalnum() or ch in "._:/+-" for ch in raw):
        return raw
    return fallback


def _stable_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (list, tuple)):
        return [_stable_scalar(item) for item in value[:50]]
    if isinstance(value, Mapping):
        return {
            _safe_code(key, "field"): _stable_scalar(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))[:50]
        }
    return type(value).__name__


def _percentile(values: Iterable[float], fraction: float) -> float:
    items = sorted(float(item) for item in values)
    if not items:
        return 0.0
    index = max(0, min(len(items) - 1, math.ceil(len(items) * fraction) - 1))
    return items[index]


__all__ = [
    "CacheEnvelope",
    "CachePolicy",
    "DEFAULT_CACHE_POLICIES",
    "DeadlineBudget",
    "DeadlineExceeded",
    "PERFORMANCE_SCHEMA_VERSION",
    "PerformanceWindow",
    "RUNTIME_STAGES",
    "RuntimePerformanceTrace",
    "build_scoped_cache_key",
]
