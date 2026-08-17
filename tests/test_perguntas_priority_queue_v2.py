from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas.perguntas_pos_venda import PerguntasGerarRespostaRequest
from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_endpoints as endpoints
from backend.services import perguntas_pos_venda_state as perguntas_state


def _runtime(tmp_path):
    return SimpleNamespace(PASTA_INFO=str(tmp_path))


def _job(
    job_id: str,
    *,
    store: str,
    origin: str = orchestrator.QUEUE_ORIGIN_AUTOMATION,
    priority: int = orchestrator.QUEUE_PRIORITY_AUTOMATION,
    created_at: str = "2026-07-28T10:00:00",
    policy: str = orchestrator.QUEUE_POLICY_VERSION,
) -> dict:
    return {
        "job_id": job_id,
        "profile": orchestrator.PROFILE,
        "client_id": "tenant",
        "task_type": orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        "subject_key": f"question:{job_id}",
        "event_subject_key": job_id,
        "store": store,
        "status": "queued",
        "agent_state": "entendendo",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "queue_policy_version": policy,
        "queue_origin": origin,
        "queue_priority": priority,
        "created_at": created_at,
    }


def _insufficient_context() -> dict:
    return {
        "model": "codex:test",
        "intencao_atendimento": {
            "subperguntas": [
                {
                    "intent": "compatibility",
                    "question": "Serve no veiculo informado?",
                    "required_evidence": "codigo e interface tecnicos",
                }
            ]
        },
        "diagnostico_ia": [
            {
                "result": {
                    "validation_ok": False,
                    "confidence": 0.3,
                    "compatibility_analysis": {
                        "decision": "insufficient",
                        "confidence": 0.3,
                        "missing_fields": ["codigo_da_peca", "tipo_de_conector"],
                        "evidence": {},
                    },
                }
            }
        ],
    }


def _queue_retry_now(info_base: str, client_id: str, job_id: str) -> dict:
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, job_id
    )
    stored.update(
        {
            "status": "queued",
            "agent_state": "pesquisando",
            "current_step": "consultar",
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "next_retry_at_epoch": 0.0,
        }
    )
    return codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base, client_id, stored
    )


def test_priority_claim_is_manual_first_fifo_and_respects_global_and_store_limits(tmp_path):
    info_base = str(tmp_path)
    jobs = [
        _job("auto-old", store="Loja A", created_at="2026-07-28T10:00:00"),
        _job("auto-later", store="Loja B", created_at="2026-07-28T10:00:01"),
        _job("auto-same-store", store="Loja A", created_at="2026-07-28T10:00:02"),
        _job(
            "manual-new",
            store="Loja C",
            origin=orchestrator.QUEUE_ORIGIN_MANUAL,
            priority=orchestrator.QUEUE_PRIORITY_MANUAL,
            created_at="2026-07-28T10:00:03",
        ),
    ]
    for job in jobs:
        codex_assistant_storage.codex_assistant_customer_reply_job_save(
            info_base, "tenant", job
        )

    ordered = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
        info_base,
        "tenant",
        statuses=["queued"],
        priority_order=True,
        limit=20,
    )
    assert [item["job_id"] for item in ordered] == [
        "manual-new",
        "auto-old",
        "auto-later",
        "auto-same-store",
    ]
    assert codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base,
        "tenant",
        "auto-old",
        owner="worker-a",
        max_running=2,
        enforce_priority=True,
        queue_policy_version=orchestrator.QUEUE_POLICY_VERSION,
    ) is None

    manual = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base,
        "tenant",
        "manual-new",
        owner="worker-a",
        max_running=2,
        enforce_priority=True,
        queue_policy_version=orchestrator.QUEUE_POLICY_VERSION,
    )
    first_auto = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base,
        "tenant",
        "auto-old",
        owner="worker-b",
        max_running=2,
        enforce_priority=True,
        queue_policy_version=orchestrator.QUEUE_POLICY_VERSION,
    )
    assert manual["status"] == first_auto["status"] == "running"
    assert codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base,
        "tenant",
        "auto-later",
        owner="worker-c",
        max_running=2,
        enforce_priority=True,
        queue_policy_version=orchestrator.QUEUE_POLICY_VERSION,
    ) is None

    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        "tenant",
        {**manual, "status": "completed", "lease_owner": "", "lease_expires_ts": 0.0},
        expected_lease_owner="worker-a",
        expected_lease_generation=manual["lease_generation"],
    )
    assert codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base,
        "tenant",
        "auto-same-store",
        owner="worker-c",
        max_running=2,
        queue_policy_version=orchestrator.QUEUE_POLICY_VERSION,
    ) is None
    assert codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base,
        "tenant",
        "auto-later",
        owner="worker-c",
        max_running=2,
        enforce_priority=True,
        queue_policy_version=orchestrator.QUEUE_POLICY_VERSION,
    )["status"] == "running"


def test_automatic_backpressure_is_transactional_at_3_per_store_and_12_per_tenant(tmp_path):
    info_base = str(tmp_path)

    def admit(job: dict, tenant: str = "tenant-store") -> dict:
        return codex_assistant_storage.codex_assistant_customer_reply_job_save(
            info_base,
            tenant,
            job,
            max_origin_active=orchestrator.AUTOMATION_QUEUE_TOTAL_LIMIT,
            max_origin_active_store=orchestrator.AUTOMATION_QUEUE_PER_STORE_LIMIT,
        )

    store_results = [admit(_job(f"store-{index}", store="Loja A")) for index in range(4)]
    assert [item["status"] for item in store_results] == ["queued", "queued", "queued", "deferred"]
    assert codex_assistant_storage.codex_assistant_customer_reply_queue_metrics(
        info_base,
        "tenant-store",
        origin=orchestrator.QUEUE_ORIGIN_AUTOMATION,
        store="Loja A",
    )["active"] == 3

    manual = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        "tenant-store",
        _job(
            "manual-over-limit",
            store="Loja A",
            origin=orchestrator.QUEUE_ORIGIN_MANUAL,
            priority=orchestrator.QUEUE_PRIORITY_MANUAL,
        ),
    )
    assert manual["status"] == "queued"

    with ThreadPoolExecutor(max_workers=13) as pool:
        futures = [
            pool.submit(
                admit,
                _job(f"tenant-{index}", store=f"Loja {index}"),
                "tenant-total",
            )
            for index in range(13)
        ]
    tenant_results = [future.result() for future in futures]
    assert sum(item["status"] == "queued" for item in tenant_results) == 12
    assert sum(item["status"] == "deferred" for item in tenant_results) == 1
    assert codex_assistant_storage.codex_assistant_customer_reply_queue_metrics(
        info_base,
        "tenant-total",
        origin=orchestrator.QUEUE_ORIGIN_AUTOMATION,
    )["active"] == 12


def test_manual_click_promotes_existing_automatic_job_without_reset_or_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    scheduled: list[str] = []
    monkeypatch.setattr(
        orchestrator, "_schedule", lambda job: scheduled.append(job["job_id"]) or True
    )
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [],
    )
    request = {
        "pergunta": {"id": "Q-PROMOTE", "text": "Serve?"},
        "question_text": "Serve?",
    }
    automatic = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-PROMOTE",
        request=request,
        created_by="perguntas_automacao",
    )
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant", automatic["job_id"]
    )
    stored.update({"attempt_count": 2, "evidence_attempt_count": 1, "operational_failure_count": 1})
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant", stored
    )

    promoted = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-PROMOTE",
        request=request,
        created_by="module_user",
    )
    active = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
        str(tmp_path), "tenant", statuses=list(orchestrator.ACTIVE_STATUSES), limit=20
    )
    assert promoted["job_id"] == automatic["job_id"]
    assert promoted["queue_origin"] == orchestrator.QUEUE_ORIGIN_MANUAL
    assert promoted["queue_priority"] == orchestrator.QUEUE_PRIORITY_MANUAL
    assert promoted["attempt_count"] == 2
    assert promoted["evidence_attempt_count"] == 1
    assert promoted["operational_failure_count"] == 1
    assert [item["job_id"] for item in active] == [automatic["job_id"]]
    assert scheduled == [automatic["job_id"], automatic["job_id"]]


def test_manual_click_creates_new_flow_after_automatic_terminal_review(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [],
    )
    request = {
        "pergunta": {"id": "Q-REVIEW", "text": "Serve?"},
        "question_text": "Serve?",
    }
    automatic = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-REVIEW",
        request=request,
        created_by="perguntas_automacao",
    )
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant", automatic["job_id"]
    )
    stored.update(
        {
            "status": "completed",
            "agent_state": "revisao_humana",
            "blocked_without_draft": True,
            "review_required": True,
            "completion_reason": "unsafe_insufficient_draft",
            "result": {
                "resposta": "",
                "blocked_without_draft": True,
                "completion_reason": "unsafe_insufficient_draft",
            },
        }
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant", stored
    )

    manual = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-REVIEW",
        request=request,
        created_by="module_user",
    )

    assert manual["job_id"] != automatic["job_id"]
    assert manual["status"] == "queued"
    assert manual["queue_origin"] == orchestrator.QUEUE_ORIGIN_MANUAL


def test_two_insufficient_cycles_finish_with_available_draft_without_review_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda _job: None)
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [],
    )
    created = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-SAFE",
        request={"pergunta": {"id": "Q-SAFE", "text": "Serve?"}, "question_text": "Serve?"},
    )
    answer = "Para confirmar a aplicacao, informe o codigo da peca e o tipo de conector."
    with patch.object(
        orchestrator, "_load_question_context", return_value=(answer, _insufficient_context())
    ):
        orchestrator._run_job("tenant", created["job_id"])
        first = orchestrator.get_job("tenant", created["job_id"])
        assert first["status"] == "waiting_retry"
        assert first["evidence_attempt_count"] == 1
        _queue_retry_now(str(tmp_path), "tenant", created["job_id"])
        orchestrator._run_job("tenant", created["job_id"])

    completed = orchestrator.get_job("tenant", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["result"]["resposta"] == answer
    assert completed["result"]["data_sufficient"] is False
    assert completed["result"]["requires_approval"] is True
    assert completed["review_required"] is False
    assert completed["completion_reason"] == "evidence_insufficient_after_retry_limit"


def test_post_sale_classification_in_public_surface_keeps_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [],
    )
    created = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-POST-SALE",
        request={
            "pergunta": {"id": "Q-POST-SALE", "text": "Preciso de atendimento."},
            "question_text": "Preciso de atendimento.",
        },
    )
    context = {
        "intencao_atendimento": {
            "categoria": "post_sale",
            "subperguntas": [{
                "intent": "post_sale",
                "question": "Qual orientacao pode ser dada com os dados atuais?",
                "required_evidence": "mensagem e regras de atendimento disponiveis",
            }],
        },
        "diagnostico_ia": [{
            "result": {
                "category": "post_sale",
                "validation_ok": True,
                "validation_issues": [],
                "confidence": 0.65,
            },
        }],
    }
    answer = "Sentimos pelo ocorrido. Vamos orientar o atendimento com os dados ja disponiveis."
    with patch.object(orchestrator, "_load_question_context", return_value=(answer, context)):
        orchestrator._run_job("tenant", created["job_id"])

    completed = orchestrator.get_job("tenant", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["success"] is True
    assert completed["result"]["resposta"] == answer
    assert completed["data_sufficient"] is True
    assert completed["blocked_without_draft"] is False
    assert completed["review_required"] is False


def test_unsafe_insufficient_draft_is_replaced_by_neutral_available_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda _job: None)
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [],
    )
    created = orchestrator.create_job(
        client_id="tenant",
        task_type="question",
        store="Loja A",
        subject_key="Q-UNSAFE",
        request={"pergunta": {"id": "Q-UNSAFE", "text": "Serve?"}, "question_text": "Serve?"},
    )
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=("Sim, serve perfeitamente. Envie uma foto e confirme com o mecanico.", _insufficient_context()),
    ):
        orchestrator._run_job("tenant", created["job_id"])
        _queue_retry_now(str(tmp_path), "tenant", created["job_id"])
        orchestrator._run_job("tenant", created["job_id"])

    completed = orchestrator.get_job("tenant", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["blocked_without_draft"] is False
    assert completed["result"]["resposta"]
    assert "nao esta confirmada" in orchestrator._normal(completed["result"]["resposta"])
    assert completed["result"]["requires_approval"] is True
    assert completed["review_required"] is False
    assert completed["completion_reason"] == "available_information_fallback"


def test_operational_failures_stop_on_third_and_success_resets_consecutive_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda _job: None)
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [],
    )

    def create(subject: str) -> dict:
        return orchestrator.create_job(
            client_id="tenant",
            task_type="question",
            store="Loja A",
            subject_key=subject,
            request={"pergunta": {"id": subject, "text": "Serve?"}, "question_text": "Serve?"},
        )

    exhausted = create("Q-OPS-3")
    provider_failure = perguntas_state.PerguntasIAProviderIndisponivel(
        "Provedor temporariamente indisponivel.",
        reason="provider_http_429",
    )
    with patch.object(
        orchestrator, "_load_question_context", side_effect=provider_failure
    ):
        for attempt in range(3):
            orchestrator._run_job("tenant", exhausted["job_id"])
            if attempt < 2:
                waiting = orchestrator.get_job("tenant", exhausted["job_id"])
                assert waiting["status"] == "waiting_retry"
                assert waiting["operational_failure_count"] == attempt + 1
                _queue_retry_now(str(tmp_path), "tenant", exhausted["job_id"])

    terminal = orchestrator.get_job("tenant", exhausted["job_id"])
    assert terminal["status"] == "completed"
    assert terminal["blocked_without_draft"] is False
    assert terminal["result"]["resposta"]
    assert terminal["review_required"] is False
    assert terminal["operational_failure_count"] == 3
    assert terminal["completion_reason"] == "operational_retry_exhausted"

    reset = create("Q-OPS-RESET")
    with patch.object(
        orchestrator, "_load_question_context", side_effect=provider_failure
    ):
        orchestrator._run_job("tenant", reset["job_id"])
    _queue_retry_now(str(tmp_path), "tenant", reset["job_id"])
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=(
            "Para confirmar a aplicacao, informe o codigo da peca.",
            _insufficient_context(),
        ),
    ):
        orchestrator._run_job("tenant", reset["job_id"])
    after_success = orchestrator.get_job("tenant", reset["job_id"])
    assert after_success["status"] == "waiting_retry"
    assert after_success["operational_failure_count"] == 0
    assert after_success["evidence_attempt_count"] == 1


def test_recovery_paginates_and_quarantines_more_than_500_outdated_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_known_clients", lambda _base: ["tenant"])
    monkeypatch.setattr(orchestrator, "_cancel_retry_timer", lambda _job_id: None)
    scheduled: list[str] = []
    monkeypatch.setattr(
        orchestrator, "_schedule", lambda job: scheduled.append(job["job_id"]) or True
    )
    for index in range(501):
        codex_assistant_storage.codex_assistant_customer_reply_job_save(
            str(tmp_path),
            "tenant",
            _job(f"old-{index:03d}", store=f"Loja {index % 4}", policy="jk_ppv_queue_v1"),
        )
    current = _job(
        "current-v2",
        store="Loja atual",
        origin=orchestrator.QUEUE_ORIGIN_MANUAL,
        priority=orchestrator.QUEUE_PRIORITY_MANUAL,
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant", current
    )

    orchestrator.recover_pending_jobs()

    quarantined = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
        str(tmp_path), "tenant", statuses=["cancelled"], limit=1000
    )
    assert len(quarantined) == 501
    assert all(item["contract_quarantined"] is True for item in quarantined)
    assert all(item["completion_reason"] == "queue_policy_outdated" for item in quarantined)
    assert scheduled == ["current-v2"]


def test_safe_insufficient_draft_allows_two_text_fields_and_rejects_unsafe_requests():
    context = _insufficient_context()
    assert orchestrator._safe_insufficient_draft(
        "Para confirmar, informe o codigo da peca e o tipo de conector.", context
    ) is True
    assert orchestrator._safe_insufficient_draft(
        "Sim, serve perfeitamente. Informe o codigo.", context
    ) is False
    assert orchestrator._safe_insufficient_draft(
        "Envie uma foto e o chassi para confirmar com o mecanico.", context
    ) is False
    assert orchestrator._safe_insufficient_draft(
        "Qual o codigo? Qual o conector? Qual a medida?", context
    ) is False
    assert orchestrator._safe_insufficient_draft(
        "Para confirmar, informe o codigo, o conector e a medida.", context
    ) is False


def test_automation_terminal_blocker_requires_new_manual_flow_for_current_review_jobs():
    blocked = {
        "status": "completed",
        "blocked_without_draft": True,
        "completion_reason": "unsafe_insufficient_draft",
    }
    assert orchestrator.automation_terminal_blocker(blocked) == "terminal_review_required"
    assert orchestrator.automation_terminal_blocker(
        {**blocked, "completion_reason": "draft_expired"}
    ) == "terminal_review_required"
    assert orchestrator.automation_terminal_blocker(
        {**blocked, "completion_reason": "queue_policy_outdated", "contract_quarantined": True}
    ) == ""
    assert orchestrator.automation_terminal_blocker(
        {"status": "completed", "completed_with_partial": True, "blocked_without_draft": False}
    ) == ""


def test_terminal_safe_partial_is_never_reactivated(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    scheduled: list[str] = []
    monkeypatch.setattr(
        orchestrator, "_schedule", lambda job: scheduled.append(job["job_id"]) or True
    )
    completed = {
        **_job(
            "terminal-partial",
            store="Loja A",
            origin=orchestrator.QUEUE_ORIGIN_MANUAL,
            priority=orchestrator.QUEUE_PRIORITY_MANUAL,
        ),
        "status": "completed",
        "agent_state": "aguardando_aprovacao",
        "completed_with_partial": True,
        "review_required": True,
        "completion_reason": "evidence_insufficient_after_retry_limit",
        "result": {
            "resposta": "Para confirmar, informe o codigo da peca.",
            "data_sufficient": False,
            "completed_with_partial": True,
            "requires_approval": True,
        },
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant", completed
    )

    returned = orchestrator.resume_incomplete_job("tenant", completed["job_id"])
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "tenant", completed["job_id"]
    )
    assert returned["status"] == stored["status"] == "completed"
    assert returned["completed_with_partial"] is True
    assert scheduled == []


def test_manual_endpoint_never_reports_blocked_completion_as_success(monkeypatch):
    blocked = {
        "job_id": "blocked-job",
        "status": "completed",
        "success": False,
        "queued": False,
        "blocked_without_draft": True,
        "review_required": True,
        "completion_reason": "operational_retry_exhausted",
        "result": {
            "resposta": "",
            "requires_approval": False,
            "blocked_without_draft": True,
        },
    }
    monkeypatch.setattr(orchestrator, "enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "create_job", lambda **_kwargs: dict(blocked))
    request = PerguntasGerarRespostaRequest.model_validate(
        {
            "loja": "Loja A",
            "pergunta": {"id": "Q-BLOCKED", "text": "Serve?"},
            "async": False,
        }
    )

    result = endpoints.ml_perguntas_gerar_resposta_manual(request, "tenant")
    assert result["status"] == "completed"
    assert result["success"] is False
    assert result["blocked_without_draft"] is True
    assert result["resposta"] == ""
