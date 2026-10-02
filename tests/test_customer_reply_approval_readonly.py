from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.services import codex_assistant_storage, perguntas_pos_venda_codex as codex, perguntas_pos_venda_state
from backend.services.codex.storage import customer_replies, customer_reply_state
from backend.services.whatsapp.approvals import question_workflow


def _job(job_id="job-1", **changes):
    job = {
        "job_id": job_id, "task_type": "public_question", "profile": codex.PROFILE,
        "store": "Loja A", "store_id": "store-a", "subject_key": "question-1",
        "status": "completed", "agent_state": "aguardando_aprovacao",
        "prompt_version": codex.PROMPT_VERSION, "prompt_hash": codex.PROMPT_HASH,
        "schema_version": codex.SCHEMA_VERSION, "queue_policy_version": codex.QUEUE_POLICY_VERSION,
        "proposal_hash": "draft-hash",
        "result": {"resposta": "Resposta literal.\nSegunda linha.", "requires_approval": True,
                   "proposal_hash": "draft-hash"},
    }
    job.update(changes)
    return job


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    yield
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()


def _save(tmp_path, job, tenant="tenant-a"):
    return codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), tenant, job)


def test_eligibility_batch_reads_one_snapshot_without_schema_or_public_jobs(tmp_path, monkeypatch):
    _save(tmp_path, _job())
    calls, statements = [], []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    def unexpected(*_args, **_kwargs):
        pytest.fail("Approval check used a mutating or public lifecycle operation")

    monkeypatch.setattr(customer_replies.sqlite3, "connect", connect)
    monkeypatch.setattr(customer_replies, "_ensure_state_schema", unexpected)
    for name in ("get_job", "_public_job", "_queue_position"):
        monkeypatch.setattr(codex, name, unexpected)
    result = codex.approval_jobs_current("tenant-a", ["job-1", "job-1", "missing"])
    assert result == {"job-1": True, "missing": False}
    assert len(calls) == 1
    assert "mode=ro" in calls[0][0][0]
    assert all(sql.lstrip().upper().startswith(("PRAGMA QUERY_ONLY", "BEGIN", "SELECT")) for sql in statements)
    assert not any("assistant_action" in sql or "COUNT(" in sql for sql in statements)


@pytest.mark.parametrize("changes", [
    {"prompt_hash": "old"}, {"queue_policy_version": "old"}, {"schema_version": "old"},
    {"prompt_version": "old"}, {"status": "cancelled"}, {"agent_state": "concluido"},
    {"contract_quarantined": True}, {"blocked_without_draft": True}, {"task_type": "post_sale"},
    {"result": {"resposta": "", "requires_approval": True}},
    {"result": {"resposta": "Draft", "requires_approval": False}},
    {"result": {"resposta": "Draft", "blocked_without_draft": True}},
])
def test_ineligible_jobs_fail_closed_without_lifecycle_changes(tmp_path, changes):
    _save(tmp_path, _job(**changes))
    path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-a")
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT payload_json FROM assistant_customer_reply_jobs").fetchone()[0]
    assert codex.approval_job_current("tenant-a", "job-1") is False
    with sqlite3.connect(path) as connection:
        after = connection.execute("SELECT payload_json FROM assistant_customer_reply_jobs").fetchone()[0]
    assert after == before


def test_missing_database_and_invalid_tenant_create_nothing(tmp_path):
    missing_root = tmp_path / "missing-root"
    assert codex_assistant_storage.codex_assistant_customer_reply_jobs_readonly(
        str(missing_root), "tenant-a", ["job-1"],
    ) == {}
    assert codex.approval_job_current("../tenant-a", "job-1") is False
    assert not missing_root.exists()
    assert not (tmp_path / "tenant-a").exists()


def test_batch_chunks_keep_all_ids_and_isolate_same_job_between_tenants(tmp_path, monkeypatch):
    _save(tmp_path, _job())
    _save(tmp_path, _job(prompt_hash="old"), "tenant-b")
    statements = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(customer_replies.sqlite3, "connect", connect)
    ids = [f"missing-{index}" for index in range(520)] + ["job-1"]
    result = codex.approval_jobs_current("tenant-a", ids)
    assert len(result) == len(ids)
    assert result["job-1"] is True
    assert sum("FROM assistant_customer_reply_jobs WHERE job_id IN" in sql for sql in statements) == 3
    assert codex.approval_job_current("tenant-b", "job-1") is False


def test_sealed_draft_recovers_and_expiration_or_missing_draft_blocks(tmp_path, monkeypatch):
    key = b"R" * 32
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(customer_reply_state, "_customer_reply_result_key", lambda _client, *, create: key)
    _save(tmp_path, _job())
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    assert codex.approval_job_current("tenant-a", "job-1") is True
    path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-a")
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM assistant_customer_reply_jobs").fetchone()[0])
        payload["sealed_result_v1"]["expires_at"] = 1
        connection.execute("UPDATE assistant_customer_reply_jobs SET payload_json=?", (json.dumps(payload),))
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    assert codex.approval_job_current("tenant-a", "job-1") is False
    assert codex.job_contract_current("tenant-a", "job-1") is True


def test_current_snapshot_is_not_reused_after_cancellation(tmp_path):
    _save(tmp_path, _job())
    snapshot = codex.approval_jobs_current("tenant-a", ["job-1"])
    _save(tmp_path, _job(status="cancelled", agent_state="cancelado"))
    assert snapshot["job-1"] is True
    assert codex.approval_job_current("tenant-a", "job-1") is False


def test_valid_partial_draft_keeps_required_human_review_without_inventing_evidence(tmp_path):
    _save(tmp_path, _job(result={
        "resposta": "Ainda faltam dados tecnicos para confirmar.",
        "requires_approval": True, "data_sufficient": False, "review_required": True,
    }))
    assert codex.approval_job_current("tenant-a", "job-1") is True
    job = codex_assistant_storage.codex_assistant_customer_reply_jobs_readonly(
        str(tmp_path), "tenant-a", ["job-1"],
    )["job-1"]
    assert job["result"]["data_sufficient"] is False
    assert job["result"]["review_required"] is True


def test_pending_filter_skips_historical_disabled_and_incomplete_records_before_job_reads(monkeypatch):
    ppv_state = SimpleNamespace(
        _perguntas_loja_config_obter=lambda configs, store: configs.get(store, {}),
        _perguntas_loja_config_normalizar=lambda config: config,
    )
    base = {"id": "approval", "codex_job_id": "job-1", "status": "pending",
            "loja": "Loja A", "resposta_sugerida": "Resposta"}
    records = [{**base, "status": "answered", "codex_job_id": f"old-{i}"} for i in range(1500)]
    records.extend([{**base, "tipo": "pos-venda"}, {**base, "loja": "Disabled"},
                    {**base, "resposta_sugerida": ""}, {**base, "id": ""},
                    {**base, "codex_job_id": ""}, base])
    calls = []
    monkeypatch.setattr(codex, "approval_job_current", lambda tenant, job: calls.append((tenant, job)) or True)
    selected = question_workflow._pending_question_approval(
        ppv_state, {"Loja A": {"notificar_whatsapp_aprovacoes": True}}, records, client_id="tenant-a",
    )
    assert selected is base
    assert calls == [("tenant-a", "job-1")]


def test_wrong_schema_fails_closed(tmp_path):
    _save(tmp_path, _job())
    path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-a")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE assistant_meta SET value='unknown' WHERE key='schema_version'")
    assert codex.approval_job_current("tenant-a", "job-1") is False


def test_readonly_check_fails_closed_when_rollback_database_is_locked(tmp_path):
    _save(tmp_path, _job())
    path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-a")
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN EXCLUSIVE")
        assert codex.approval_job_current("tenant-a", "job-1") is False
    finally:
        connection.rollback()
        connection.close()


def test_batch_reuses_only_the_same_tenant_cycle_and_rechecks_before_delivery(tmp_path, monkeypatch):
    _save(tmp_path, _job())
    _save(tmp_path, _job(prompt_hash="old"), "tenant-b")
    bindings = [("tenant-a", "phone-a1"), ("tenant-a", "phone-a2"), ("tenant-b", "phone-b")]
    monkeypatch.setattr(question_workflow, "_eligible_question_bindings", lambda _cfg: [
        ({}, tenant, "operator", phone) for tenant, phone in bindings
    ])
    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_loja_configs_carregar", lambda _tenant: {
        "Loja A": {"notificar_whatsapp_aprovacoes": True}
    })
    loads = []

    def approvals(tenant):
        loads.append(tenant)
        return [{"id": "approval-1", "codex_job_id": "job-1", "status": "pending",
                 "loja": "Loja A", "resposta_sugerida": "Resposta literal."}]

    monkeypatch.setattr(perguntas_pos_venda_state, "_perguntas_ia_aprovacoes_carregar", approvals)
    monkeypatch.setattr(question_workflow, "_question_active_approval", lambda *_a, **_kw: (None, "", None), raising=False)
    monkeypatch.setattr(question_workflow, "_resolve_active_question_approval", lambda *_a, **_kw: (False, None))
    monkeypatch.setattr(question_workflow, "_save_state", lambda _state: None, raising=False)
    sent = []

    def send(_cfg, _state, _notifications, _approval, tenant, _user, phone, **_kw):
        sent.append((tenant, phone))
        # A concurrent action invalidates the job after the cycle's selection.
        _save(tmp_path, _job(status="cancelled", agent_state="cancelado"))

    monkeypatch.setattr(question_workflow, "_send_question_approval_card", send)
    question_workflow._forward_question_approvals({}, {})
    assert loads == ["tenant-a", "tenant-b"]
    assert sent == [("tenant-a", "phone-a1")]
    question_workflow._forward_question_approvals({}, {})
    assert loads == ["tenant-a", "tenant-b", "tenant-a", "tenant-b"]
    assert sent == [("tenant-a", "phone-a1")]


def test_readonly_sealed_draft_rejects_wrong_key_and_tenant_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(customer_reply_state, "_customer_reply_sealed_results_enabled", lambda: True)
    monkeypatch.setattr(customer_reply_state, "_customer_reply_result_key", lambda _tenant, *, create: b"A" * 32)
    _save(tmp_path, _job())
    _save(tmp_path, _job(), "tenant-b")
    path_a = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-a")
    path_b = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "tenant-b")
    with sqlite3.connect(path_a) as connection:
        payload_a = json.loads(connection.execute("SELECT payload_json FROM assistant_customer_reply_jobs").fetchone()[0])
    with sqlite3.connect(path_b) as connection:
        payload_b = json.loads(connection.execute("SELECT payload_json FROM assistant_customer_reply_jobs").fetchone()[0])
        payload_b["sealed_result_v1"] = payload_a["sealed_result_v1"]
        connection.execute("UPDATE assistant_customer_reply_jobs SET payload_json=?", (json.dumps(payload_b),))
    with customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK:
        customer_reply_state._CUSTOMER_REPLY_TRANSIENT.clear()
    assert codex.approval_job_current("tenant-b", "job-1") is False
    monkeypatch.setattr(customer_reply_state, "_customer_reply_result_key", lambda _tenant, *, create: b"B" * 32)
    assert codex.approval_job_current("tenant-a", "job-1") is False
