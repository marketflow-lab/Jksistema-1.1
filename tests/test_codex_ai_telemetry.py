from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.services.codex_ai_telemetry import CodexAITelemetry
from backend.services.codex.console import runtime_policy as console_runtime_policy
from backend.services.codex.console import telemetry as console_telemetry


def _rows(path, table):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


def test_event_is_idempotent_hmac_scoped_and_strictly_content_free(tmp_path):
    telemetry = CodexAITelemetry(tmp_path / "info", hmac_key=b"t" * 32)
    try:
        payload = dict(
            event_id="event-1",
            trace_id="trace-1",
            span_id="span-1",
            event_type="inference",
            status="ok",
            requested_model="gpt-5.5",
            effective_model="gpt-5.6-terra",
            provider="openai",
            provider_path="responses",
            model_rerouted=True,
            duration_ms=123.5,
            input_tokens=10,
            output_tokens=20,
            cached_tokens=4,
            cache_status="hit",
            tool_codes=["product_data", "bad tool argument"],
            context_generation="generation-real-name",
            user_id="usuario@example.com",
            store_id="Loja Muito Secreta",
            dimensions={
                "surface": "sidebar",
                "prompt_version": "prompt-v3",
                "phone": "31999999999",
            },
            prompt="SEGREDO NO PROMPT",
            response="SEGREDO NA RESPOSTA",
            stack_trace="C:\\private\\runtime.py",
            tool_arguments={"sku": "SECRETO"},
        )
        assert telemetry.record_event("000002", **payload) is True
        assert telemetry.record_event("000002", **payload) is True
        assert telemetry.flush()

        path = tmp_path / "info" / "000002" / "codex_ai" / "telemetry.sqlite3"
        rows = _rows(path, "telemetry_events")
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"].startswith("hmac-sha256:")
        assert row["trace_id"].startswith("hmac-sha256:")
        assert row["user_hmac"].startswith("hmac-sha256:")
        assert json.loads(row["tool_codes_json"]) == ["product_data"]
        assert json.loads(row["dimensions_json"]) == {
            "prompt_version": "prompt-v3",
            "surface": "sidebar",
        }
        assert telemetry.diagnostics()["duplicates"] == 1

        conn = sqlite3.connect(path)
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            dump = "\n".join(conn.iterdump())
        finally:
            conn.close()
        for forbidden in (
            "SEGREDO NO PROMPT",
            "SEGREDO NA RESPOSTA",
            "usuario@example.com",
            "Loja Muito Secreta",
            "31999999999",
            "C:\\private",
            "generation-real-name",
        ):
            assert forbidden not in dump
    finally:
        telemetry.close()


def test_audio_dimensions_are_allowlisted_without_raw_content(tmp_path):
    telemetry = CodexAITelemetry(tmp_path / "info", hmac_key=b"a" * 32, start_worker=False)
    try:
        assert telemetry.record_event(
            "tenant-a",
            event_id="audio-event",
            trace_id="audio-trace",
            event_type="audio_processing",
            status="ok",
            dimensions={
                "surface": "whatsapp",
                "category": "audio",
                "audio_stage": "cleanup_succeeded",
                "audio_attempt": "1",
                "audio_retries": "0",
                "audio_mime_bucket": "audio/ogg",
                "audio_size_bucket": "lt_1mb",
                "audio_duration_bucket": "lt_30s",
                "audio_cleanup_state": "removed",
                "transcription": "conteudo-secreto",
                "path": "C:/private/voice.ogg",
            },
        ) is True
        row = _rows(telemetry.telemetry_db_path("tenant-a"), "telemetry_events")[0]
        dimensions = json.loads(row["dimensions_json"])
        assert dimensions == {
            "surface": "whatsapp",
            "category": "audio",
            "audio_stage": "cleanup_succeeded",
            "audio_attempt": "1",
            "audio_retries": "0",
            "audio_mime_bucket": "audio/ogg",
            "audio_size_bucket": "lt_1mb",
            "audio_duration_bucket": "lt_30s",
            "audio_cleanup_state": "removed",
        }
        serialized = json.dumps(row, ensure_ascii=False)
        assert "conteudo-secreto" not in serialized
        assert "C:/private" not in serialized
    finally:
        telemetry.close()


def test_traces_spans_summary_timeseries_evaluation_and_feedback(tmp_path):
    telemetry = CodexAITelemetry(tmp_path / "info", hmac_key=b"x" * 32)
    try:
        assert telemetry.start_trace(
            "client-a",
            trace_id="trace-a",
            surface="whatsapp",
            category="commerce_query",
            requested_model="gpt-5.5",
            expected_spans=["selection"],
        )
        assert telemetry.start_span(
            "client-a", trace_id="trace-a", span_id="span-a", stage="selection"
        )
        assert telemetry.finish_span(
            "client-a",
            trace_id="trace-a",
            span_id="span-a",
            stage="selection",
            status="ok",
            duration_ms=25,
        )
        assert telemetry.finish_trace(
            "client-a",
            trace_id="trace-a",
            status="ok",
            effective_model="gpt-5.6-terra",
            provider="openai",
            duration_ms=100,
        )
        assert telemetry.record_event(
            "client-a",
            event_id="event-a",
            trace_id="trace-a",
            status="ok",
            effective_model="gpt-5.6-terra",
            provider="openai",
            duration_ms=100,
            input_tokens=5,
            output_tokens=7,
            tool_codes=["sales_ranking"],
        )
        assert telemetry.record_evaluation_run(
            "client-a",
            run_id="run-a",
            dataset_version="dataset-v1",
            status="completed",
            model="gpt-5.6-terra",
            total_cases=1,
            passed_cases=1,
            mean_score=96,
        )
        assert telemetry.record_evaluation_case(
            "client-a",
            run_id="run-a",
            case_id="case-real-id",
            category="sales",
            status="passed",
            model="gpt-5.6-terra",
            score=96,
        )
        assert telemetry.record_feedback(
            "client-a",
            feedback_id="feedback-a",
            trace_id="trace-a",
            rating=1,
            label="helpful",
            comment="raw feedback must not be stored",
        )
        assert telemetry.flush()

        summary = telemetry.summary("client-a")
        assert summary["events"] == 1
        assert summary["success_rate"] == 1.0
        assert summary["tokens"] == 12
        assert summary["tool_calls"] == 1
        assert summary["complete_spans_rate"] == 1.0
        assert summary["by_effective_model"] == {"gpt-5.6-terra": 1}

        series = telemetry.timeseries("client-a")
        assert len(series) == 1
        assert series[0]["events"] == 1

        run = telemetry.evaluation_run("client-a", "run-a")
        assert run["run"]["dataset_version"] == "dataset-v1"
        assert run["cases"][0]["case_id"].startswith("hmac-sha256:")
        assert telemetry.feedback_summary("client-a") == {
            "total": 1,
            "positive": 1,
            "negative": 0,
            "neutral": 0,
            "by_label": {"helpful": 1},
            "by_source": {"app": 1},
        }

        path = telemetry.telemetry_db_path("client-a")
        spans = _rows(path, "telemetry_spans")
        assert spans[0]["status"] == "ok"
        assert spans[0]["duration_ms"] == 25
    finally:
        telemetry.close()


def test_retention_rolls_old_detail_into_daily_aggregate(tmp_path):
    telemetry = CodexAITelemetry(tmp_path / "info", hmac_key=b"r" * 32)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=1)
    try:
        telemetry.record_event(
            "000002",
            event_id="old-event",
            created_at=old,
            status="ok",
            effective_model="gpt-5.5",
            provider="openai",
            duration_ms=10,
            input_tokens=2,
            output_tokens=3,
        )
        telemetry.record_event(
            "000002",
            event_id="recent-event",
            created_at=recent,
            status="ok",
            effective_model="gpt-5.5",
            provider="openai",
        )
        telemetry.flush()
        deleted = telemetry.run_retention("000002", now=now)

        path = telemetry.telemetry_db_path("000002")
        assert deleted["events"] == 1
        assert len(_rows(path, "telemetry_events")) == 1
        aggregates = _rows(path, "telemetry_daily_aggregates")
        assert len(aggregates) == 1
        assert aggregates[0]["events"] == 1
        assert aggregates[0]["total_tokens"] == 5
        summary = telemetry.summary(
            "000002",
            from_at=old - timedelta(days=1),
            to_at=now,
        )
        assert summary["events"] == 2
        assert summary["aggregated_events"] == 1
        daily = telemetry.timeseries(
            "000002",
            from_at=old - timedelta(days=1),
            to_at=now,
            bucket="day",
        )
        assert sum(row["events"] for row in daily) == 2
    finally:
        telemetry.close()


def test_write_failure_is_reported_without_raising_or_blocking(monkeypatch, tmp_path):
    telemetry = CodexAITelemetry(
        tmp_path / "info",
        hmac_key=b"f" * 32,
        start_worker=False,
    )

    def fail_write(_job):
        raise sqlite3.OperationalError("storage unavailable")

    monkeypatch.setattr(telemetry, "_write_job", fail_write)
    try:
        assert telemetry.record_event(
            "000002",
            event_id="failure-event",
            status="error",
            error_code="telemetry_storage_unavailable",
            prompt="must never be logged",
        ) is False
        assert telemetry.diagnostics()["write_errors"] == 1
    finally:
        telemetry.close()


def test_direct_provider_wrapper_records_only_content_free_dimensions(monkeypatch):
    from backend.services import codex_console, ia_providers

    calls = []

    class FakeTelemetry:
        def __getattr__(self, name):
            def capture(*args, **kwargs):
                calls.append((name, args, kwargs))
                return True

            return capture

    monkeypatch.setattr(console_telemetry, "instance", lambda: FakeTelemetry())
    wrapped = ia_providers._telemetried_ia_provider("provider-test", "path-test")(
        lambda _payload, _client_id: "resposta-secreta"
    )
    payload = SimpleNamespace(
        model="model-test",
        message="pergunta-secreta",
        context={"surface": "public_questions", "store": "store-a"},
    )

    assert wrapped(payload, "client-a") == "resposta-secreta"
    serialized = repr(calls)
    assert "pergunta-secreta" not in serialized
    assert "resposta-secreta" not in serialized
    assert any(
        name == "record_event" and kwargs["provider_path"] == "path-test"
        for name, _args, kwargs in calls
    )
    assert any(
        name == "finish_trace" and kwargs["status"] == "completed"
        for name, _args, kwargs in calls
    )


def test_summary_and_timeseries_filter_surface_model_provider_and_status(tmp_path):
    telemetry = CodexAITelemetry(tmp_path / "info", hmac_key=b"q" * 32)
    try:
        telemetry.record_event(
            "client-a",
            event_id="public-ok",
            status="completed",
            requested_model="gpt-5.6-terra",
            effective_model="gpt-5.6-terra",
            provider="openai",
            duration_ms=20,
            dimensions={"surface": "public_questions"},
        )
        telemetry.record_event(
            "client-a",
            event_id="post-failed",
            status="failed",
            requested_model="gpt-5.5",
            effective_model="gpt-5.5",
            provider="codex",
            duration_ms=40,
            dimensions={"surface": "post_sale"},
        )
        telemetry.flush()

        public = telemetry.summary("client-a", surface="public_questions")
        assert public["events"] == 1
        assert public["by_effective_model"] == {"gpt-5.6-terra": 1}
        assert public["filters"] == {"surface": "public_questions"}
        failed = telemetry.timeseries(
            "client-a",
            surface="post_sale",
            model="gpt-5.5",
            provider="codex",
            status="failed",
        )
        assert len(failed) == 1
        assert failed[0]["events"] == 1
        assert failed[0]["successes"] == 0
    finally:
        telemetry.close()


def test_provider_surface_inference_recognizes_public_and_post_sale_context():
    from backend.services import ia_providers

    assert ia_providers._ia_telemetry_surface(
        {"origem_ia": "mercado_livre_perguntas_v2_contexto_sequencial"}
    ) == "public_questions"
    assert ia_providers._ia_telemetry_surface(
        {"tipo_treinamento": "pos_venda"}
    ) == "post_sale"
