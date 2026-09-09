from __future__ import annotations

import json
import sqlite3

from backend.services import codex_assistant_storage
from backend.services.codex.storage import customer_reply_state


def _completed_job(job_id: str, answer: str) -> dict:
    return {
        "job_id": job_id,
        "profile": "mercado_livre_customer_reply",
        "task_type": "public_question",
        "subject_key": "item:MLB1|buyer:10",
        "event_subject_key": "Q1",
        "store": "Loja A",
        "store_id": "store-a",
        "seller_id": "seller-a",
        "site_id": "MLB",
        "status": "completed",
        "agent_state": "aguardando_aprovacao",
        "proposal_hash": "proposal-hash",
        "result": {
            "resposta": answer,
            "model": "fixture-model",
            "proposal_id": job_id,
            "proposal_version": 1,
            "proposal_hash": "proposal-hash",
            "requires_approval": True,
            "publish_attempted": False,
            "data_sufficient": True,
        },
    }


def _raw_payload(info_base, tenant: str, job_id: str) -> tuple[str, dict]:
    db_path = codex_assistant_storage.codex_assistant_state_db_path(str(info_base), tenant)
    with sqlite3.connect(db_path) as conn:
        raw = conn.execute(
            "SELECT payload_json FROM assistant_customer_reply_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
    return raw, json.loads(raw)


def test_completed_draft_survives_restart_without_plaintext_on_disk(tmp_path, monkeypatch):
    answer = "  CANARY_DRAFT_SEALED linha 1.  \n\nLinha 2 literal.  "
    key = b"A" * 32
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(
        customer_reply_state,
        "_customer_reply_result_key",
        lambda _client_id, *, create: key,
    )

    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-a", _completed_job("sealed-job", answer)
    )
    raw, payload = _raw_payload(tmp_path, "tenant-a", saved["job_id"])

    assert answer not in raw
    assert "CANARY_DRAFT_SEALED" not in raw
    assert "result" not in payload
    assert payload["sealed_result_v1"]["algorithm"] == "AES-256-GCM"
    for path in (tmp_path / "tenant-a").rglob("*"):
        if path.is_file():
            assert b"CANARY_DRAFT_SEALED" not in path.read_bytes()
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()

    recovered = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant-a", saved["job_id"]
    )

    assert recovered["result"]["resposta"] == answer
    assert "sealed_result_v1" not in recovered
    assert codex_assistant_storage.codex_assistant_customer_reply_job_has_transient(
        str(tmp_path), "tenant-a", saved["job_id"]
    )


def test_sealed_draft_fails_closed_for_wrong_key_and_tenant_replay(tmp_path, monkeypatch):
    active_key = {"value": b"A" * 32}
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(
        customer_reply_state,
        "_customer_reply_result_key",
        lambda _client_id, *, create: active_key["value"],
    )
    job = _completed_job("sealed-isolation", "CANARY_TENANT_ONLY")
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "tenant-a", job)
    raw, _payload = _raw_payload(tmp_path, "tenant-a", job["job_id"])

    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path),
        "tenant-b",
        {**job, "status": "queued", "agent_state": "entendendo", "result": {}},
    )
    tenant_b_db = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-b")
    with sqlite3.connect(tenant_b_db) as conn:
        conn.execute(
            "UPDATE assistant_customer_reply_jobs SET payload_json = ? WHERE job_id = ?",
            (raw, job["job_id"]),
        )
        conn.commit()
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()

    replayed = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant-b", job["job_id"]
    )
    assert "result" not in replayed
    assert "sealed_result_v1" not in replayed

    active_key["value"] = b"B" * 32
    wrong_key = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant-a", job["job_id"]
    )
    assert "result" not in wrong_key


def test_sealed_draft_cannot_be_replayed_into_another_job(tmp_path, monkeypatch):
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(
        customer_reply_state,
        "_customer_reply_result_key",
        lambda _client_id, *, create: b"A" * 32,
    )
    source = _completed_job("sealed-source", "CANARY_JOB_ONLY")
    target = _completed_job("sealed-target", "TARGET_DRAFT")
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "tenant-a", source)
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "tenant-a", target)
    source_raw, _payload = _raw_payload(tmp_path, "tenant-a", source["job_id"])
    db_path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-a")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE assistant_customer_reply_jobs SET payload_json = ? WHERE job_id = ?",
            (source_raw, target["job_id"]),
        )
        conn.commit()
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()

    replayed = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant-a", target["job_id"]
    )

    assert "result" not in replayed
    assert "sealed_result_v1" not in replayed


def test_cancelled_job_does_not_keep_sealed_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(
        customer_reply_state,
        "_customer_reply_result_key",
        lambda _client_id, *, create: b"A" * 32,
    )
    job = _completed_job("sealed-cancelled", "CANARY_CANCELLED")
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "tenant-a", job)
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path),
        "tenant-a",
        {**job, "status": "cancelled", "agent_state": "cancelado", "cancel_requested": True},
    )

    raw, payload = _raw_payload(tmp_path, "tenant-a", job["job_id"])
    assert "sealed_result_v1" not in payload
    assert "CANARY_CANCELLED" not in raw


def test_expired_sealed_draft_is_not_recovered(tmp_path, monkeypatch):
    now = {"value": 1_000_000.0}
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(customer_reply_state.time, "time", lambda: now["value"])
    monkeypatch.setattr(
        customer_reply_state,
        "_customer_reply_result_key",
        lambda _client_id, *, create: b"A" * 32,
    )
    job = _completed_job("sealed-expired", "CANARY_EXPIRED")
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "tenant-a", job)
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    now["value"] += customer_reply_state._CUSTOMER_REPLY_SEALED_RESULT_TTL_SECONDS + 1

    recovered = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant-a", job["job_id"]
    )

    assert "result" not in recovered
    assert "sealed_result_v1" not in recovered
