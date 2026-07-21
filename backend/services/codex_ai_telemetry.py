"""Content-free, per-client telemetry for the JK Sistema internal Codex.

The hot path only sanitizes a small allowlist and submits a bounded queue item.
SQLite writes happen on a daemon worker and never block an IA response.  Raw
prompts, answers, tool payloads, personal data and absolute paths have no
columns in this schema and are ignored even when supplied by a caller.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import queue
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional


TELEMETRY_SCHEMA_VERSION = "1"
SANITIZER_VERSION = "1"
HMAC_KEY_VERSION = "v1"
HMAC_CREDENTIAL_TARGET = f"codex-ai/telemetry-hmac/{HMAC_KEY_VERSION}"

_CODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,119}\Z")
_SAFE_CLIENT_RE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_ALLOWED_DIMENSIONS = frozenset(
    {
        "surface",
        "category",
        "service_tier",
        "prompt_version",
        "tool_schema_version",
        "model_policy_version",
        "model_reason_code",
        "model_reroute_reason",
        "cache_policy",
        "cache_bypass_reason",
        "performance_schema_version",
        "audio_stage",
        "audio_attempt",
        "audio_retries",
        "audio_mime_bucket",
        "audio_size_bucket",
        "audio_duration_bucket",
        "audio_cleanup_state",
    }
)


def secure_hmac_identifier(
    value: object,
    *,
    namespace: str = "id",
    key_version: str = HMAC_KEY_VERSION,
) -> str:
    """Pseudonymize a low-entropy identifier with the Windows-backed telemetry key."""

    raw = str(value or "")
    if not raw:
        return ""
    key, _source = _resolve_hmac_key(None)
    safe_namespace = _safe_code(namespace, "id")
    digest = hmac.new(key, f"{safe_namespace}\0{raw}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{_safe_code(key_version, HMAC_KEY_VERSION)}:{digest}"


@dataclass(frozen=True)
class TelemetryRetentionPolicy:
    technical_logs_days: int = 14
    detailed_telemetry_days: int = 30
    daily_aggregates_days: int = 90
    evaluations_days: int = 180
    feedback_days: int = 180


class CodexAITelemetry:
    """Asynchronous telemetry writer and read-only aggregate query service."""

    def __init__(
        self,
        info_root: str | Path,
        *,
        hmac_key: Optional[bytes] = None,
        key_version: str = HMAC_KEY_VERSION,
        queue_maxsize: int = 2048,
        retention: Optional[TelemetryRetentionPolicy] = None,
        start_worker: bool = True,
    ) -> None:
        self.info_root = Path(info_root).resolve()
        self.key_version = _safe_code(key_version, HMAC_KEY_VERSION)
        self.retention = retention or TelemetryRetentionPolicy()
        self._hmac_key, self._key_source = _resolve_hmac_key(hmac_key)
        if len(self._hmac_key) < 16:
            raise ValueError("telemetry_hmac_key_too_short")
        self._queue: queue.Queue[Optional[dict[str, Any]]] = queue.Queue(
            maxsize=max(1, int(queue_maxsize or 1))
        )
        self._lock = threading.RLock()
        self._db_lock = threading.RLock()
        self._accepted = 0
        self._dropped = 0
        self._write_errors = 0
        self._duplicates = 0
        self._closed = False
        self._retention_next_epoch: dict[str, float] = {}
        self._maintenance_threads: set[threading.Thread] = set()
        self._worker: Optional[threading.Thread] = None
        if start_worker:
            candidate = threading.Thread(
                target=self._worker_loop,
                name="codex-ai-telemetry",
                daemon=True,
            )
            if callable(getattr(candidate, "is_alive", None)):
                self._worker = candidate
                self._worker.start()

    def telemetry_db_path(self, client_id: object) -> Path:
        safe_client = self._safe_client_path(client_id)
        target = (self.info_root / safe_client / "codex_ai" / "telemetry.sqlite3").resolve()
        target.relative_to(self.info_root)
        return target

    def pseudonymize(self, value: object, *, namespace: str = "id") -> str:
        raw = str(value or "")
        if not raw:
            return ""
        material = f"{_safe_code(namespace, 'id')}\0{raw}".encode("utf-8")
        digest = hmac.new(self._hmac_key, material, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{self.key_version}:{digest}"

    def record_event(
        self,
        client_id: object,
        *,
        event_id: object = "",
        trace_id: object = "",
        span_id: object = "",
        event_type: object = "inference",
        created_at: object = "",
        status: object = "unknown",
        requested_model: object = "",
        effective_model: object = "",
        provider: object = "",
        provider_path: object = "",
        model_rerouted: bool = False,
        duration_ms: object = 0,
        input_tokens: object = 0,
        output_tokens: object = 0,
        cached_tokens: object = 0,
        cache_status: object = "",
        tool_codes: Optional[Iterable[object]] = None,
        context_generation: object = "",
        error_code: object = "",
        user_id: object = "",
        store_id: object = "",
        dimensions: Optional[Mapping[str, Any]] = None,
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        """Queue one allowlisted event; unknown fields are intentionally dropped."""

        try:
            raw_event_id = str(event_id or uuid.uuid4())
            tools = _safe_code_list(tool_codes or (), limit=64)
            payload = {
                "event_id": self.pseudonymize(raw_event_id, namespace="event"),
                "trace_id": self.pseudonymize(trace_id, namespace="trace"),
                "span_id": self.pseudonymize(span_id, namespace="span"),
                "event_type": _safe_code(event_type, "inference"),
                "created_at": _safe_timestamp(created_at),
                "status": _safe_code(status, "unknown"),
                "requested_model": _safe_code(requested_model),
                "effective_model": _safe_code(effective_model),
                "provider": _safe_code(provider),
                "provider_path": _safe_code(provider_path),
                "model_rerouted": 1 if model_rerouted else 0,
                "duration_ms": _bounded_number(duration_ms, 86_400_000.0),
                "input_tokens": _bounded_int(input_tokens, 2_000_000_000),
                "output_tokens": _bounded_int(output_tokens, 2_000_000_000),
                "cached_tokens": _bounded_int(cached_tokens, 2_000_000_000),
                "cache_status": _safe_code(cache_status),
                "tool_count": len(tools),
                "tool_codes_json": _json_dumps(tools),
                "context_generation_hash": self.pseudonymize(
                    context_generation, namespace="context_generation"
                ),
                "error_code": _safe_code(error_code),
                "client_hmac": self.pseudonymize(client_id, namespace="client"),
                "user_hmac": self.pseudonymize(user_id, namespace="user"),
                "store_hmac": self.pseudonymize(store_id, namespace="store"),
                "dimensions_json": _json_dumps(_safe_dimensions(dimensions)),
                "sanitizer_version": SANITIZER_VERSION,
            }
            return self._submit(client_id, "event", payload)
        except Exception:
            self._mark_drop()
            return False

    def start_trace(
        self,
        client_id: object,
        *,
        trace_id: object,
        surface: object = "",
        category: object = "",
        requested_model: object = "",
        started_at: object = "",
        user_id: object = "",
        store_id: object = "",
        expected_spans: Optional[Iterable[object]] = None,
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        try:
            payload = {
                "trace_id": self.pseudonymize(trace_id, namespace="trace"),
                "client_hmac": self.pseudonymize(client_id, namespace="client"),
                "user_hmac": self.pseudonymize(user_id, namespace="user"),
                "store_hmac": self.pseudonymize(store_id, namespace="store"),
                "surface": _safe_code(surface),
                "category": _safe_code(category),
                "requested_model": _safe_code(requested_model),
                "expected_spans_json": _json_dumps(
                    _safe_code_list(expected_spans or (), limit=32)
                ),
                "effective_model": "",
                "provider": "",
                "started_at": _safe_timestamp(started_at),
                "finished_at": "",
                "duration_ms": 0.0,
                "status": "started",
                "error_code": "",
                "sanitizer_version": SANITIZER_VERSION,
            }
            if not payload["trace_id"]:
                return False
            return self._submit(client_id, "trace", payload)
        except Exception:
            self._mark_drop()
            return False

    def finish_trace(
        self,
        client_id: object,
        *,
        trace_id: object,
        status: object,
        effective_model: object = "",
        provider: object = "",
        duration_ms: object = 0,
        error_code: object = "",
        finished_at: object = "",
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        try:
            payload = {
                "trace_id": self.pseudonymize(trace_id, namespace="trace"),
                "finished_at": _safe_timestamp(finished_at),
                "status": _safe_code(status, "unknown"),
                "effective_model": _safe_code(effective_model),
                "provider": _safe_code(provider),
                "duration_ms": _bounded_number(duration_ms, 86_400_000.0),
                "error_code": _safe_code(error_code),
            }
            if not payload["trace_id"]:
                return False
            return self._submit(client_id, "trace_finish", payload)
        except Exception:
            self._mark_drop()
            return False

    def start_span(
        self,
        client_id: object,
        *,
        trace_id: object,
        span_id: object,
        stage: object,
        started_at: object = "",
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        return self._record_span(
            client_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            started_at=started_at,
            status="started",
        )

    def finish_span(
        self,
        client_id: object,
        *,
        trace_id: object,
        span_id: object,
        stage: object,
        status: object,
        duration_ms: object = 0,
        error_code: object = "",
        finished_at: object = "",
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        return self._record_span(
            client_id,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            finished_at=finished_at,
            status=status,
            duration_ms=duration_ms,
            error_code=error_code,
        )

    def record_evaluation_run(
        self,
        client_id: object,
        *,
        run_id: object,
        dataset_version: object,
        status: object,
        model: object = "",
        total_cases: object = 0,
        passed_cases: object = 0,
        critical_failures: object = 0,
        mean_score: object = 0,
        created_at: object = "",
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        try:
            payload = {
                "run_id": self.pseudonymize(run_id, namespace="evaluation_run"),
                "dataset_version": _safe_code(dataset_version),
                "model": _safe_code(model),
                "status": _safe_code(status, "unknown"),
                "total_cases": _bounded_int(total_cases, 1_000_000),
                "passed_cases": _bounded_int(passed_cases, 1_000_000),
                "critical_failures": _bounded_int(critical_failures, 1_000_000),
                "mean_score": _bounded_number(mean_score, 100.0),
                "created_at": _safe_timestamp(created_at),
                "sanitizer_version": SANITIZER_VERSION,
            }
            if not payload["run_id"]:
                return False
            return self._submit(client_id, "evaluation_run", payload)
        except Exception:
            self._mark_drop()
            return False

    def record_evaluation_case(
        self,
        client_id: object,
        *,
        run_id: object,
        case_id: object,
        category: object,
        status: object,
        model: object,
        score: object = 0,
        critical_failure: bool = False,
        duration_ms: object = 0,
        total_tokens: object = 0,
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        try:
            payload = {
                "run_id": self.pseudonymize(run_id, namespace="evaluation_run"),
                "case_id": self.pseudonymize(case_id, namespace="evaluation_case"),
                "category": _safe_code(category),
                "status": _safe_code(status, "unknown"),
                "model": _safe_code(model),
                "score": _bounded_number(score, 100.0),
                "critical_failure": 1 if critical_failure else 0,
                "duration_ms": _bounded_number(duration_ms, 86_400_000.0),
                "total_tokens": _bounded_int(total_tokens, 2_000_000_000),
                "created_at": _utc_now(),
                "sanitizer_version": SANITIZER_VERSION,
            }
            if not payload["run_id"] or not payload["case_id"]:
                return False
            return self._submit(client_id, "evaluation_case", payload)
        except Exception:
            self._mark_drop()
            return False

    def record_feedback(
        self,
        client_id: object,
        *,
        feedback_id: object,
        trace_id: object,
        rating: object,
        label: object = "",
        source: object = "app",
        created_at: object = "",
        user_id: object = "",
        **_discarded_sensitive_fields: Any,
    ) -> bool:
        try:
            payload = {
                "feedback_id": self.pseudonymize(feedback_id, namespace="feedback"),
                "trace_id": self.pseudonymize(trace_id, namespace="trace"),
                "user_hmac": self.pseudonymize(user_id, namespace="user"),
                "rating": max(-1, min(1, int(rating or 0))),
                "label": _safe_code(label),
                "source": _safe_code(source, "app"),
                "created_at": _safe_timestamp(created_at),
                "sanitizer_version": SANITIZER_VERSION,
            }
            if not payload["feedback_id"]:
                return False
            return self._submit(client_id, "feedback", payload)
        except Exception:
            self._mark_drop()
            return False

    # Compatibility-friendly name for integrations that do not distinguish the
    # evaluation run aggregate from case-level results yet.
    record_evaluation = record_evaluation_run

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.flush(timeout)
        worker_alive = getattr(self._worker, "is_alive", None)
        if self._worker and callable(worker_alive) and worker_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                return
            self._worker.join(max(0.0, float(timeout or 0.0)))
        with self._lock:
            maintenance_threads = list(self._maintenance_threads)
        for maintenance in maintenance_threads:
            join = getattr(maintenance, "join", None)
            if callable(join):
                join(max(0.0, float(timeout or 0.0)))
        with self._lock:
            for maintenance in maintenance_threads:
                is_alive = getattr(maintenance, "is_alive", None)
                if not callable(is_alive) or not is_alive():
                    self._maintenance_threads.discard(maintenance)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "sanitizer_version": SANITIZER_VERSION,
                "hmac_key_version": self.key_version,
                "hmac_key_source": self._key_source,
                "queue_size": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "accepted": self._accepted,
                "dropped": self._dropped,
                "write_errors": self._write_errors,
                "duplicates": self._duplicates,
                "worker_alive": bool(
                    self._worker
                    and callable(getattr(self._worker, "is_alive", None))
                    and self._worker.is_alive()
                ),
            }

    def summary(
        self,
        client_id: object,
        *,
        from_at: object = "",
        to_at: object = "",
        surface: object = "",
        model: object = "",
        provider: object = "",
        status: object = "",
    ) -> dict[str, Any]:
        self.flush(2.0)
        start, end = _query_window(from_at, to_at)
        with self._read_connection(client_id) as conn:
            rows = conn.execute(
                """
                SELECT status, requested_model, effective_model, provider,
                       duration_ms, input_tokens, output_tokens, cached_tokens,
                       cache_status, tool_count, error_code, dimensions_json
                FROM telemetry_events
                WHERE created_at >= ? AND created_at <= ?
                """,
                (start, end),
            ).fetchall()
            trace_rows = conn.execute(
                """
                SELECT trace_id, expected_spans_json, surface, requested_model,
                       effective_model, provider, status
                FROM telemetry_traces
                WHERE started_at >= ? AND started_at <= ?
                """,
                (start, end),
            ).fetchall()
            span_rows = conn.execute(
                """
                SELECT trace_id, stage, status FROM telemetry_spans
                WHERE started_at >= ? AND started_at <= ?
                """,
                (start, end),
            ).fetchall()
            aggregate_rows = conn.execute(
                """
                SELECT day, effective_model, provider, status, events,
                       duration_ms, total_tokens, tool_calls
                FROM telemetry_daily_aggregates
                WHERE day >= ? AND day <= ?
                """,
                (start[:10], end[:10]),
            ).fetchall()
        filters = _telemetry_filters(
            surface=surface,
            model=model,
            provider=provider,
            status=status,
        )
        if any(filters.values()):
            rows = [row for row in rows if _event_matches_filters(row, filters)]
            trace_rows = [row for row in trace_rows if _trace_matches_filters(row, filters)]
            selected_trace_ids = {str(row["trace_id"] or "") for row in trace_rows}
            span_rows = [row for row in span_rows if str(row["trace_id"] or "") in selected_trace_ids]
            aggregate_rows = [
                row for row in aggregate_rows if _aggregate_matches_filters(row, filters)
            ] if not filters["surface"] else []
        durations = [float(row["duration_ms"] or 0.0) for row in rows]
        total_tokens = [
            int(row["input_tokens"] or 0) + int(row["output_tokens"] or 0)
            for row in rows
        ]
        successful_stages: dict[str, set[str]] = {}
        for row in span_rows:
            if row["status"] in {"ok", "success", "completed"}:
                successful_stages.setdefault(str(row["trace_id"]), set()).add(str(row["stage"]))
        eligible_traces = 0
        complete_traces = 0
        for row in trace_rows:
            try:
                expected = set(json.loads(str(row["expected_spans_json"] or "[]")))
            except (TypeError, ValueError, json.JSONDecodeError):
                expected = set()
            if not expected:
                continue
            eligible_traces += 1
            if expected.issubset(successful_stages.get(str(row["trace_id"]), set())):
                complete_traces += 1
        aggregate_events = sum(int(row["events"] or 0) for row in aggregate_rows)
        detailed_successes = sum(
            1 for row in rows if row["status"] in {"ok", "success", "completed"}
        )
        aggregate_successes = sum(
            int(row["events"] or 0)
            for row in aggregate_rows
            if row["status"] in {"ok", "success", "completed"}
        )
        event_count = len(rows) + aggregate_events
        return {
            "from_at": start,
            "to_at": end,
            "filters": {key: value for key, value in filters.items() if value},
            "events": event_count,
            "detailed_events": len(rows),
            "aggregated_events": aggregate_events,
            "success_rate": _ratio(detailed_successes + aggregate_successes, event_count),
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
            "latency_percentiles_scope": "detailed_events_only",
            "tokens": sum(total_tokens) + sum(int(row["total_tokens"] or 0) for row in aggregate_rows),
            "cached_tokens": sum(int(row["cached_tokens"] or 0) for row in rows),
            "tool_calls": sum(int(row["tool_count"] or 0) for row in rows)
            + sum(int(row["tool_calls"] or 0) for row in aggregate_rows),
            "traces_with_expected_spans": eligible_traces,
            "traces_with_complete_spans": complete_traces,
            "complete_spans_rate": _ratio(complete_traces, eligible_traces),
            "by_effective_model": _merge_weighted_counts(
                _counts(row["effective_model"] for row in rows),
                ((row["effective_model"], row["events"]) for row in aggregate_rows),
            ),
            "by_provider": _merge_weighted_counts(
                _counts(row["provider"] for row in rows),
                ((row["provider"], row["events"]) for row in aggregate_rows),
            ),
            "by_status": _merge_weighted_counts(
                _counts(row["status"] for row in rows),
                ((row["status"], row["events"]) for row in aggregate_rows),
            ),
            "by_error_code": _counts(row["error_code"] for row in rows if row["error_code"]),
            "by_cache_status": _counts(row["cache_status"] for row in rows if row["cache_status"]),
        }

    def timeseries(
        self,
        client_id: object,
        *,
        from_at: object = "",
        to_at: object = "",
        bucket: str = "hour",
        surface: object = "",
        model: object = "",
        provider: object = "",
        status: object = "",
    ) -> list[dict[str, Any]]:
        self.flush(2.0)
        start, end = _query_window(from_at, to_at)
        bucket_code = "day" if str(bucket).lower() == "day" else "hour"
        expression = "substr(created_at, 1, 10)" if bucket_code == "day" else "substr(created_at, 1, 13)"
        with self._read_connection(client_id) as conn:
            rows = conn.execute(
                f"""
                SELECT {expression} AS bucket, status, requested_model,
                       effective_model, provider, duration_ms, input_tokens,
                       output_tokens, tool_count, dimensions_json
                FROM telemetry_events
                WHERE created_at >= ? AND created_at <= ?
                ORDER BY {expression}
                """,
                (start, end),
            ).fetchall()
            aggregate_rows = []
            if bucket_code == "day":
                aggregate_rows = conn.execute(
                    """
                    SELECT day AS bucket, effective_model, provider, status,
                           events,
                           CASE WHEN events > 0 THEN duration_ms / events ELSE 0 END AS average_ms,
                           total_tokens, tool_calls
                    FROM telemetry_daily_aggregates
                    WHERE day >= ? AND day <= ?
                    ORDER BY day
                    """,
                    (start[:10], end[:10]),
                ).fetchall()
        filters = _telemetry_filters(
            surface=surface,
            model=model,
            provider=provider,
            status=status,
        )
        if any(filters.values()):
            rows = [row for row in rows if _event_matches_filters(row, filters)]
            aggregate_rows = [
                row for row in aggregate_rows if _aggregate_matches_filters(row, filters)
            ] if not filters["surface"] else []
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket_value = str(row["bucket"] or "")
            current = result.setdefault(
                bucket_value,
                {
                    "bucket": bucket_value,
                    "events": 0,
                    "successes": 0,
                    "average_ms": 0.0,
                    "total_tokens": 0,
                    "tool_calls": 0,
                },
            )
            old_events = int(current["events"])
            new_events = old_events + 1
            duration_total = float(current["average_ms"]) * old_events
            duration_total += float(row["duration_ms"] or 0.0)
            current.update(
                {
                    "events": new_events,
                    "successes": int(current["successes"])
                    + (1 if row["status"] in {"ok", "success", "completed"} else 0),
                    "average_ms": round(duration_total / new_events, 3),
                    "total_tokens": int(current["total_tokens"])
                    + int(row["input_tokens"] or 0)
                    + int(row["output_tokens"] or 0),
                    "tool_calls": int(current["tool_calls"]) + int(row["tool_count"] or 0),
                }
            )
        for row in aggregate_rows:
            bucket_value = str(row["bucket"] or "")
            current = result.setdefault(
                bucket_value,
                {
                    "bucket": bucket_value,
                    "events": 0,
                    "successes": 0,
                    "average_ms": 0.0,
                    "total_tokens": 0,
                    "tool_calls": 0,
                },
            )
            old_events = int(current["events"])
            added_events = int(row["events"] or 0)
            combined_events = old_events + added_events
            duration_total = float(current["average_ms"]) * old_events
            duration_total += float(row["average_ms"] or 0.0) * added_events
            current.update(
                {
                    "events": combined_events,
                    "successes": int(current["successes"])
                    + (
                        added_events
                        if row["status"] in {"ok", "success", "completed"}
                        else 0
                    ),
                    "average_ms": round(duration_total / combined_events, 3) if combined_events else 0.0,
                    "total_tokens": int(current["total_tokens"]) + int(row["total_tokens"] or 0),
                    "tool_calls": int(current["tool_calls"]) + int(row["tool_calls"] or 0),
                }
            )
        return [result[key] for key in sorted(result)]

    def evaluation_runs(self, client_id: object, *, limit: int = 100) -> list[dict[str, Any]]:
        self.flush(2.0)
        with self._read_connection(client_id) as conn:
            rows = conn.execute(
                """
                SELECT run_id, dataset_version, model, status, total_cases,
                       passed_cases, critical_failures, mean_score, created_at
                FROM evaluation_runs ORDER BY created_at DESC LIMIT ?
                """,
                (max(1, min(1000, int(limit or 100))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def evaluation_run(self, client_id: object, run_id: object) -> dict[str, Any]:
        self.flush(2.0)
        supplied = str(run_id or "")
        run_hash = (
            supplied
            if supplied.startswith(f"hmac-sha256:{self.key_version}:")
            else self.pseudonymize(supplied, namespace="evaluation_run")
        )
        with self._read_connection(client_id) as conn:
            run = conn.execute("SELECT * FROM evaluation_runs WHERE run_id = ?", (run_hash,)).fetchone()
            cases = conn.execute(
                """
                SELECT case_id, category, status, model, score, critical_failure,
                       duration_ms, total_tokens, created_at
                FROM evaluation_cases WHERE run_id = ? ORDER BY case_id
                """,
                (run_hash,),
            ).fetchall()
        return {"run": dict(run) if run else {}, "cases": [dict(row) for row in cases]}

    def feedback_summary(self, client_id: object) -> dict[str, Any]:
        self.flush(2.0)
        with self._read_connection(client_id) as conn:
            rows = conn.execute(
                "SELECT rating, label, source FROM telemetry_feedback"
            ).fetchall()
        return {
            "total": len(rows),
            "positive": sum(1 for row in rows if int(row["rating"] or 0) > 0),
            "negative": sum(1 for row in rows if int(row["rating"] or 0) < 0),
            "neutral": sum(1 for row in rows if int(row["rating"] or 0) == 0),
            "by_label": _counts(row["label"] for row in rows if row["label"]),
            "by_source": _counts(row["source"] for row in rows if row["source"]),
        }

    def run_retention(self, client_id: object, *, now: Optional[datetime] = None) -> dict[str, int]:
        self.flush(5.0)
        current = now or datetime.now(timezone.utc)
        detail_cutoff = _iso(current - timedelta(days=self.retention.detailed_telemetry_days))
        log_cutoff = _iso(current - timedelta(days=self.retention.technical_logs_days))
        aggregate_cutoff = _iso(current - timedelta(days=self.retention.daily_aggregates_days))[:10]
        evaluation_cutoff = _iso(current - timedelta(days=self.retention.evaluations_days))
        feedback_cutoff = _iso(current - timedelta(days=self.retention.feedback_days))
        deleted: dict[str, int] = {}
        path = self.telemetry_db_path(client_id)
        with _connection(path, self._db_lock) as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO telemetry_daily_aggregates(
                    day, effective_model, provider, status, events, duration_ms,
                    total_tokens, tool_calls, updated_at
                )
                SELECT substr(created_at, 1, 10), effective_model, provider, status,
                       COUNT(*), SUM(duration_ms),
                       SUM(input_tokens + output_tokens), SUM(tool_count), ?
                FROM telemetry_events
                WHERE created_at < ? AND created_at >= ?
                      AND event_type != 'technical_log'
                GROUP BY substr(created_at, 1, 10), effective_model, provider, status
                ON CONFLICT(day, effective_model, provider, status) DO UPDATE SET
                    events=excluded.events,
                    duration_ms=excluded.duration_ms,
                    total_tokens=excluded.total_tokens,
                    tool_calls=excluded.tool_calls,
                    updated_at=excluded.updated_at
                """,
                (_utc_now(), detail_cutoff, f"{aggregate_cutoff}T00:00:00.000Z"),
            )
            deletions = (
                ("technical_logs", "DELETE FROM telemetry_events WHERE event_type = 'technical_log' AND created_at < ?", log_cutoff),
                ("events", "DELETE FROM telemetry_events WHERE created_at < ?", detail_cutoff),
                ("spans", "DELETE FROM telemetry_spans WHERE COALESCE(finished_at, started_at) < ?", detail_cutoff),
                ("traces", "DELETE FROM telemetry_traces WHERE COALESCE(finished_at, started_at) < ?", detail_cutoff),
                ("aggregates", "DELETE FROM telemetry_daily_aggregates WHERE day < ?", aggregate_cutoff),
                ("evaluation_cases", "DELETE FROM evaluation_cases WHERE created_at < ?", evaluation_cutoff),
                ("evaluation_runs", "DELETE FROM evaluation_runs WHERE created_at < ?", evaluation_cutoff),
                ("feedback", "DELETE FROM telemetry_feedback WHERE created_at < ?", feedback_cutoff),
            )
            for name, statement, cutoff in deletions:
                cursor = conn.execute(statement, (cutoff,))
                deleted[name] = max(0, int(cursor.rowcount or 0))
        return deleted

    def schedule_retention(self, client_id: object, *, interval_seconds: int = 86_400) -> bool:
        """Run retention out of band at most once per client and interval."""

        safe_client = self._safe_client_path(client_id)
        current = time.time()
        with self._lock:
            if self._closed or current < self._retention_next_epoch.get(safe_client, 0.0):
                return False
            self._retention_next_epoch[safe_client] = current + max(300, int(interval_seconds or 0))

        def apply() -> None:
            try:
                self.run_retention(safe_client)
            except Exception:
                with self._lock:
                    self._write_errors += 1

        worker = threading.Thread(
            target=apply,
            name=f"codex-ai-retention-{safe_client[:24]}",
            daemon=True,
        )
        if not callable(getattr(worker, "is_alive", None)):
            apply()
            return True
        with self._lock:
            self._maintenance_threads = {
                item
                for item in self._maintenance_threads
                if callable(getattr(item, "is_alive", None)) and item.is_alive()
            }
            self._maintenance_threads.add(worker)
            worker.start()
        return True

    def _record_span(
        self,
        client_id: object,
        *,
        trace_id: object,
        span_id: object,
        stage: object,
        started_at: object = "",
        finished_at: object = "",
        status: object,
        duration_ms: object = 0,
        error_code: object = "",
    ) -> bool:
        try:
            payload = {
                "span_id": self.pseudonymize(span_id, namespace="span"),
                "trace_id": self.pseudonymize(trace_id, namespace="trace"),
                "stage": _safe_code(stage, "unknown"),
                "started_at": _safe_timestamp(started_at),
                "finished_at": _safe_timestamp(finished_at) if finished_at else "",
                "duration_ms": _bounded_number(duration_ms, 86_400_000.0),
                "status": _safe_code(status, "unknown"),
                "error_code": _safe_code(error_code),
                "sanitizer_version": SANITIZER_VERSION,
            }
            if not payload["span_id"] or not payload["trace_id"]:
                return False
            return self._submit(client_id, "span", payload)
        except Exception:
            self._mark_drop()
            return False

    def _submit(self, client_id: object, operation: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if self._closed:
                self._dropped += 1
                return False
        job = {
            "client": self._safe_client_path(client_id),
            "operation": operation,
            "payload": payload,
        }
        if self._worker is None:
            try:
                self._write_job(job)
                with self._lock:
                    self._accepted += 1
                return True
            except Exception:
                with self._lock:
                    self._write_errors += 1
                return False
        try:
            self._queue.put_nowait(job)
            with self._lock:
                self._accepted += 1
            return True
        except queue.Full:
            self._mark_drop()
            return False

    def _mark_drop(self) -> None:
        with self._lock:
            self._dropped += 1

    def _safe_client_path(self, client_id: object) -> str:
        raw = str(client_id or "default").strip() or "default"
        if _SAFE_CLIENT_RE.fullmatch(raw):
            return raw
        digest = hmac.new(self._hmac_key, raw.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return f"client-{digest}"

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._write_job(job)
            except Exception:
                with self._lock:
                    self._write_errors += 1
            finally:
                self._queue.task_done()

    def _write_job(self, job: Mapping[str, Any]) -> None:
        client = str(job["client"])
        path = (self.info_root / client / "codex_ai" / "telemetry.sqlite3").resolve()
        path.relative_to(self.info_root)
        operation = str(job["operation"])
        payload = dict(job["payload"])
        with _connection(path, self._db_lock) as conn:
            _ensure_schema(conn)
            conn.execute(
                "INSERT OR REPLACE INTO telemetry_meta(key, value) VALUES ('hmac_key_version', ?)",
                (self.key_version,),
            )
            if operation == "event":
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO telemetry_events(
                        event_id, trace_id, span_id, event_type, created_at, status,
                        requested_model, effective_model, provider, provider_path,
                        model_rerouted, duration_ms, input_tokens, output_tokens,
                        cached_tokens, cache_status, tool_count, tool_codes_json,
                        context_generation_hash, error_code, client_hmac, user_hmac,
                        store_hmac, dimensions_json, sanitizer_version
                    ) VALUES (
                        :event_id, :trace_id, :span_id, :event_type, :created_at, :status,
                        :requested_model, :effective_model, :provider, :provider_path,
                        :model_rerouted, :duration_ms, :input_tokens, :output_tokens,
                        :cached_tokens, :cache_status, :tool_count, :tool_codes_json,
                        :context_generation_hash, :error_code, :client_hmac, :user_hmac,
                        :store_hmac, :dimensions_json, :sanitizer_version
                    )
                    """,
                    payload,
                )
                if cursor.rowcount == 0:
                    with self._lock:
                        self._duplicates += 1
            elif operation == "trace":
                conn.execute(
                    """
                    INSERT INTO telemetry_traces(
                        trace_id, client_hmac, user_hmac, store_hmac, surface,
                        category, requested_model, expected_spans_json,
                        effective_model, provider,
                        started_at, finished_at, duration_ms, status, error_code,
                        sanitizer_version
                    ) VALUES (
                        :trace_id, :client_hmac, :user_hmac, :store_hmac, :surface,
                        :category, :requested_model, :expected_spans_json,
                        :effective_model, :provider,
                        :started_at, :finished_at, :duration_ms, :status, :error_code,
                        :sanitizer_version
                    ) ON CONFLICT(trace_id) DO NOTHING
                    """,
                    payload,
                )
            elif operation == "trace_finish":
                conn.execute(
                    """
                    UPDATE telemetry_traces SET finished_at=:finished_at,
                        status=:status, effective_model=:effective_model,
                        provider=:provider, duration_ms=:duration_ms,
                        error_code=:error_code WHERE trace_id=:trace_id
                    """,
                    payload,
                )
            elif operation == "span":
                conn.execute(
                    """
                    INSERT INTO telemetry_spans(
                        span_id, trace_id, stage, started_at, finished_at,
                        duration_ms, status, error_code, sanitizer_version
                    ) VALUES (
                        :span_id, :trace_id, :stage, :started_at, :finished_at,
                        :duration_ms, :status, :error_code, :sanitizer_version
                    ) ON CONFLICT(span_id) DO UPDATE SET
                        finished_at=CASE WHEN excluded.finished_at != '' THEN excluded.finished_at ELSE telemetry_spans.finished_at END,
                        duration_ms=CASE WHEN excluded.duration_ms > 0 THEN excluded.duration_ms ELSE telemetry_spans.duration_ms END,
                        status=excluded.status,
                        error_code=excluded.error_code
                    """,
                    payload,
                )
            elif operation == "evaluation_run":
                conn.execute(
                    """
                    INSERT INTO evaluation_runs(
                        run_id, dataset_version, model, status, total_cases,
                        passed_cases, critical_failures, mean_score, created_at,
                        sanitizer_version
                    ) VALUES (
                        :run_id, :dataset_version, :model, :status, :total_cases,
                        :passed_cases, :critical_failures, :mean_score, :created_at,
                        :sanitizer_version
                    ) ON CONFLICT(run_id) DO UPDATE SET
                        status=excluded.status, total_cases=excluded.total_cases,
                        passed_cases=excluded.passed_cases,
                        critical_failures=excluded.critical_failures,
                        mean_score=excluded.mean_score
                    """,
                    payload,
                )
            elif operation == "evaluation_case":
                conn.execute(
                    """
                    INSERT OR REPLACE INTO evaluation_cases(
                        run_id, case_id, category, status, model, score,
                        critical_failure, duration_ms, total_tokens, created_at,
                        sanitizer_version
                    ) VALUES (
                        :run_id, :case_id, :category, :status, :model, :score,
                        :critical_failure, :duration_ms, :total_tokens, :created_at,
                        :sanitizer_version
                    )
                    """,
                    payload,
                )
            elif operation == "feedback":
                conn.execute(
                    """
                    INSERT OR IGNORE INTO telemetry_feedback(
                        feedback_id, trace_id, user_hmac, rating, label, source,
                        created_at, sanitizer_version
                    ) VALUES (
                        :feedback_id, :trace_id, :user_hmac, :rating, :label,
                        :source, :created_at, :sanitizer_version
                    )
                    """,
                    payload,
                )

    @contextmanager
    def _read_connection(self, client_id: object) -> Iterator[sqlite3.Connection]:
        path = self.telemetry_db_path(client_id)
        with _connection(path, self._db_lock) as conn:
            _ensure_schema(conn)
            yield conn


def _resolve_hmac_key(explicit: Optional[bytes]) -> tuple[bytes, str]:
    if explicit is not None:
        return bytes(explicit), "injected"
    try:
        from backend.services import secure_credentials

        encoded = secure_credentials.read_scoped_secret(HMAC_CREDENTIAL_TARGET)
        if encoded:
            return base64.urlsafe_b64decode(encoded.encode("ascii")), "windows_credential_manager"
        generated = secrets.token_bytes(32)
        encoded = base64.urlsafe_b64encode(generated).decode("ascii")
        if secure_credentials.write_scoped_secret(HMAC_CREDENTIAL_TARGET, encoded):
            return generated, "windows_credential_manager_created"
    except Exception:
        pass
    # Telemetry remains non-blocking outside Windows/tests, but hashes are not
    # comparable after process restart until the secure store is available.
    return secrets.token_bytes(32), "ephemeral"


@contextmanager
def _connection(
    path: Path,
    lock: Optional[threading.RLock] = None,
) -> Iterator[sqlite3.Connection]:
    if lock is not None:
        lock.acquire()
    conn: Optional[sqlite3.Connection] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        if conn is not None:
            conn.close()
        if lock is not None:
            lock.release()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS telemetry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telemetry_events (
            event_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_model TEXT NOT NULL,
            effective_model TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_path TEXT NOT NULL,
            model_rerouted INTEGER NOT NULL,
            duration_ms REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cached_tokens INTEGER NOT NULL,
            cache_status TEXT NOT NULL,
            tool_count INTEGER NOT NULL,
            tool_codes_json TEXT NOT NULL,
            context_generation_hash TEXT NOT NULL,
            error_code TEXT NOT NULL,
            client_hmac TEXT NOT NULL,
            user_hmac TEXT NOT NULL,
            store_hmac TEXT NOT NULL,
            dimensions_json TEXT NOT NULL,
            sanitizer_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_created ON telemetry_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_trace ON telemetry_events(trace_id);
        CREATE TABLE IF NOT EXISTS telemetry_traces (
            trace_id TEXT PRIMARY KEY,
            client_hmac TEXT NOT NULL,
            user_hmac TEXT NOT NULL,
            store_hmac TEXT NOT NULL,
            surface TEXT NOT NULL,
            category TEXT NOT NULL,
            requested_model TEXT NOT NULL,
            expected_spans_json TEXT NOT NULL,
            effective_model TEXT NOT NULL,
            provider TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT NOT NULL,
            sanitizer_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telemetry_spans (
            span_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            duration_ms REAL NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT NOT NULL,
            sanitizer_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_telemetry_spans_trace ON telemetry_spans(trace_id);
        CREATE TABLE IF NOT EXISTS telemetry_daily_aggregates (
            day TEXT NOT NULL,
            effective_model TEXT NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            events INTEGER NOT NULL,
            duration_ms REAL NOT NULL,
            total_tokens INTEGER NOT NULL,
            tool_calls INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(day, effective_model, provider, status)
        );
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            run_id TEXT PRIMARY KEY,
            dataset_version TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            total_cases INTEGER NOT NULL,
            passed_cases INTEGER NOT NULL,
            critical_failures INTEGER NOT NULL,
            mean_score REAL NOT NULL,
            created_at TEXT NOT NULL,
            sanitizer_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evaluation_cases (
            run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            model TEXT NOT NULL,
            score REAL NOT NULL,
            critical_failure INTEGER NOT NULL,
            duration_ms REAL NOT NULL,
            total_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            sanitizer_version TEXT NOT NULL,
            PRIMARY KEY(run_id, case_id)
        );
        CREATE TABLE IF NOT EXISTS telemetry_feedback (
            feedback_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            user_hmac TEXT NOT NULL,
            rating INTEGER NOT NULL,
            label TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sanitizer_version TEXT NOT NULL
        );
        """
    )
    trace_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(telemetry_traces)").fetchall()
    }
    if "expected_spans_json" not in trace_columns:
        conn.execute(
            "ALTER TABLE telemetry_traces ADD COLUMN expected_spans_json TEXT NOT NULL DEFAULT '[]'"
        )
    conn.execute(
        "INSERT OR REPLACE INTO telemetry_meta(key, value) VALUES ('schema_version', ?)",
        (TELEMETRY_SCHEMA_VERSION,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO telemetry_meta(key, value) VALUES ('sanitizer_version', ?)",
        (SANITIZER_VERSION,),
    )


def _safe_code(value: object, fallback: str = "") -> str:
    raw = str(value or "").strip()
    if raw.startswith("/") or ".." in raw or (raw.isdigit() and len(raw) >= 8):
        return fallback
    return raw if _CODE_RE.fullmatch(raw) else fallback


def _safe_code_list(values: Iterable[object], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        code = _safe_code(value)
        if code and code not in seen:
            result.append(code)
            seen.add(code)
        if len(result) >= limit:
            break
    return result


def _safe_dimensions(values: Optional[Mapping[str, Any]]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        return {}
    result: dict[str, str] = {}
    for name in _ALLOWED_DIMENSIONS:
        code = _safe_code(values.get(name))
        if code:
            result[name] = code
    return result


def _safe_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return _iso(value)
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return _iso(parsed)
        except ValueError:
            pass
    return _utc_now()


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _utc_now() -> str:
    return _iso(datetime.now(timezone.utc))


def _bounded_number(value: object, maximum: float) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(number, maximum))


def _bounded_int(value: object, maximum: int) -> int:
    return int(_bounded_number(value, float(maximum)))


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _query_window(from_at: object, to_at: object) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = _safe_timestamp(from_at) if from_at else _iso(now - timedelta(days=30))
    end = _safe_timestamp(to_at) if to_at else _iso(now)
    return (start, end) if start <= end else (end, start)


def _telemetry_filters(
    *,
    surface: object = "",
    model: object = "",
    provider: object = "",
    status: object = "",
) -> dict[str, str]:
    return {
        "surface": _safe_code(surface),
        "model": _safe_code(model),
        "provider": _safe_code(provider),
        "status": _safe_code(status),
    }


def _row_dimensions(row: Mapping[str, Any]) -> dict[str, str]:
    try:
        parsed = json.loads(str(row["dimensions_json"] or "{}"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return _safe_dimensions(parsed) if isinstance(parsed, Mapping) else {}


def _model_matches(row: Mapping[str, Any], expected: str) -> bool:
    if not expected:
        return True
    return expected in {
        _safe_code(row["requested_model"]),
        _safe_code(row["effective_model"]),
    }


def _event_matches_filters(row: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
    dimensions = _row_dimensions(row)
    return (
        (not filters["surface"] or dimensions.get("surface") == filters["surface"])
        and _model_matches(row, filters["model"])
        and (not filters["provider"] or _safe_code(row["provider"]) == filters["provider"])
        and (not filters["status"] or _safe_code(row["status"]) == filters["status"])
    )


def _trace_matches_filters(row: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
    return (
        (not filters["surface"] or _safe_code(row["surface"]) == filters["surface"])
        and _model_matches(row, filters["model"])
        and (not filters["provider"] or _safe_code(row["provider"]) == filters["provider"])
        and (not filters["status"] or _safe_code(row["status"]) == filters["status"])
    )


def _aggregate_matches_filters(row: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
    return (
        (not filters["model"] or _safe_code(row["effective_model"]) == filters["model"])
        and (not filters["provider"] or _safe_code(row["provider"]) == filters["provider"])
        and (not filters["status"] or _safe_code(row["status"]) == filters["status"])
    )


def _percentile(values: Iterable[float], fraction: float) -> float:
    items = sorted(float(value) for value in values)
    if not items:
        return 0.0
    index = max(0, min(len(items) - 1, math.ceil(len(items) * fraction) - 1))
    return round(items[index], 3)


def _counts(values: Iterable[object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = _safe_code(value, "unknown")
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def _merge_weighted_counts(
    base: Mapping[str, int], values: Iterable[tuple[object, object]]
) -> dict[str, int]:
    result = {str(key): int(value) for key, value in base.items()}
    for raw_key, raw_count in values:
        key = _safe_code(raw_key, "unknown")
        count = _bounded_int(raw_count, 2_000_000_000)
        result[key] = result.get(key, 0) + count
    return dict(sorted(result.items()))


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


__all__ = [
    "CodexAITelemetry",
    "HMAC_CREDENTIAL_TARGET",
    "HMAC_KEY_VERSION",
    "SANITIZER_VERSION",
    "TELEMETRY_SCHEMA_VERSION",
    "TelemetryRetentionPolicy",
    "secure_hmac_identifier",
]
