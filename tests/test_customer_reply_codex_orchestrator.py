from __future__ import annotations

import inspect
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.schemas.perguntas_pos_venda import PerguntasGerarRespostaRequest, PosVendaGerarRespostaRequest
from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_endpoints as endpoints


def _runtime(tmp_path):
    return SimpleNamespace(PASTA_INFO=str(tmp_path))


def _official_context() -> dict:
    return {
        "intencao_atendimento": {
            "subperguntas": [
                {
                    "intent": "compatibility",
                    "question": "O produto é compatível?",
                    "required_evidence": "evidência técnica confiável",
                },
                {
                    "intent": "shipping",
                    "question": "Qual é o prazo de entrega?",
                    "required_evidence": "prazo retornado pelo Mercado Livre",
                },
            ],
        },
        "evidence_envelope": {
            "schema_version": "evidence-envelope-v2",
            "status": "completed",
            "records": [{
                "field": "envio",
                "value": {"estimated_delivery": "prazo exibido pelo Mercado Livre"},
                "store": "Uai Mineirinho",
                "source": "mercado_livre_shipping",
                "coverage": "confirmed",
                "authority": "confirmed",
            }],
            "sources": ["mercado_livre_shipping"],
            "gaps": [],
            "confidence": "high",
            "evidence_sufficient": True,
            "coverage_complete": True,
        },
        "diagnostico_ia": [{
            "result": {
                "validation_ok": True,
                "confidence": 0.91,
                "codex_thread_id": "thread-operacional-1",
                "compatibility_analysis": {
                    "decision": "conditional",
                    "confidence": 0.91,
                    "sources": ["https://fabricante.example/manual.pdf"],
                    "evidence": {
                        "product": [{
                            "authority": "official_document",
                            "url": "https://fabricante.example/manual.pdf",
                            "reference": "Aplicacao confirmada pelo fabricante.",
                        }],
                        "target_vehicle": [{
                            "authority": "official_document",
                            "url": "https://fabricante.example/manual.pdf",
                            "reference": "Motor e versao confirmados.",
                        }],
                        "equivalence": [{
                            "authority": "derived",
                            "reference": "Mesma interface tecnica.",
                        }],
                    },
                },
            },
        }],
    }


def test_request_schemas_accept_async_alias():
    question = PerguntasGerarRespostaRequest.model_validate({
        "loja": "Uai Mineirinho",
        "pergunta": {"id": "Q1", "text": "Serve?"},
        "async": True,
    })
    post_sale = PosVendaGerarRespostaRequest.model_validate({
        "loja": "Uai Mineirinho",
        "pack_id": "P1",
        "async": True,
    })
    assert question.async_mode is True
    assert post_sale.async_mode is True


def test_ai_multi_intent_context_is_the_only_source_of_public_subquestions():
    subquestions = orchestrator._ai_subquestions(_official_context())

    assert [item["intent"] for item in subquestions] == ["compatibility", "shipping"]
    assert [item["status"] for item in subquestions] == ["pending", "pending"]
    assert orchestrator._initial_subquestions("question") == []
    assert orchestrator._ai_subquestions({
        "intencao_atendimento": {"subperguntas": [{"intencao": "compatibilidade"}]},
    }) == []


def test_post_sale_keeps_only_its_fixed_operational_subquestion():
    subquestions = orchestrator._initial_subquestions("post_sale")

    assert [item["intent"] for item in subquestions] == ["post_sale"]
    assert subquestions[0]["question"] == "Atendimento pós-venda"


def test_job_storage_claim_cancel_and_payload_roundtrip(tmp_path):
    payload = {
        "job_id": "job-storage-1",
        "profile": orchestrator.PROFILE,
        "task_type": "question",
        "subject_key": "Q1",
        "store": "Loja",
        "status": "queued",
        "agent_state": "entendendo",
        "idempotency_key": "idem-storage-1",
    }
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", payload)
    assert saved["job_id"] == "job-storage-1"
    claimed = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", "job-storage-1", owner="worker-test", lease_seconds=30
    )
    assert claimed["status"] == "running"
    assert claimed["lease_owner"] == "worker-test"
    cancelled = codex_assistant_storage.codex_assistant_customer_reply_job_request_cancel(
        str(tmp_path), "cliente", "job-storage-1"
    )
    assert cancelled["cancel_requested"] is True


def test_job_heartbeat_renews_lease_without_resurrecting_completed_job(tmp_path):
    payload = {
        "job_id": "job-heartbeat-1",
        "profile": orchestrator.PROFILE,
        "task_type": "question",
        "subject_key": "Q-HB",
        "store": "Loja",
        "status": "queued",
        "agent_state": "entendendo",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", payload)
    claimed = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", "job-heartbeat-1", owner="worker-test", lease_seconds=10
    )
    renewed = codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
        str(tmp_path), "cliente", "job-heartbeat-1", owner="worker-test", lease_seconds=60
    )
    assert renewed["heartbeat_at"]
    assert renewed["lease_expires_ts"] > claimed["lease_expires_ts"]

    renewed.update({"status": "completed", "agent_state": "aguardando_aprovacao"})
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", renewed)
    assert codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
        str(tmp_path), "cliente", "job-heartbeat-1", owner="worker-test", lease_seconds=60
    ) is None
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", "job-heartbeat-1"
    )
    assert stored["status"] == "completed"


def test_job_runs_to_versioned_approval_and_reuses_subject_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        created = orchestrator.create_job(
            client_id="cliente",
            task_type="question",
            store="Uai Mineirinho",
            subject_key="Q307K",
            request={
                "pergunta": {
                    "id": "Q307K",
                    "text": "Essa peca e do motor turbo correto? Consegue entregar antes da data prevista?",
                },
                "question_text": "Essa peca e do motor turbo correto? Consegue entregar antes da data prevista?",
            },
        )
    assert created["subquestions"] == []
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=("Serve no motor turbo informado. A entrega segue a previsao do Mercado Livre.", _official_context()),
    ):
        orchestrator._run_job("cliente", created["job_id"])

    completed = orchestrator.get_job("cliente", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["agent_state"] == "aguardando_aprovacao"
    assert completed["data_sufficient"] is True
    assert completed["proposal_version"] == 1
    assert completed["result"]["publish_attempted"] is False
    assert [item["intent"] for item in completed["subquestions"]] == ["compatibility", "shipping"]

    revised = orchestrator.approve_or_refresh_proposal(
        client_id="cliente",
        proposal_id=created["job_id"],
        proposal_version=1,
        proposal_hash=completed["proposal_hash"],
        answer="Serve no motor turbo informado. O prazo permanece o exibido pelo Mercado Livre.",
        store="Uai Mineirinho",
        subject_key="Q307K",
    )
    assert revised["proposal_version"] == 2
    assert revised["proposal_hash"] != completed["proposal_hash"]
    verified = orchestrator.mark_verified(
        client_id="cliente", job_id=created["job_id"], success=True, evidence={"question_id": "Q307K"}
    )
    assert verified["agent_state"] == "concluido"

    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", created["job_id"]
    )
    stored["thread_id"] = "thread-operacional-1"
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", stored)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        revision = orchestrator.create_job(
            client_id="cliente",
            task_type="question",
            store="Uai Mineirinho",
            subject_key="Q307K",
            request={
                "pergunta": {"id": "Q307K", "text": "Refaca de forma mais curta."},
                "question_text": "Refaca de forma mais curta.",
                "resposta_atual": "Serve no motor turbo informado.",
                "orientacao_usuario": "Seja mais curto.",
            },
        )
    persisted_revision = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", revision["job_id"]
    )
    assert persisted_revision["thread_id"] == "thread-operacional-1"
    assert persisted_revision["proposal_version"] == 3
    assert persisted_revision["subquestions"] == []


def test_missing_ai_subquestions_fails_safe_without_assuming_general(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda job: None)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        created = orchestrator.create_job(
            client_id="cliente",
            task_type="question",
            store="Uai Mineirinho",
            subject_key="Q-NO-CLASSIFICATION",
            request={
                "pergunta": {"id": "Q-NO-CLASSIFICATION", "text": "Tem e chega amanhã?"},
                "question_text": "Tem e chega amanhã?",
            },
        )

    context_without_classification = _official_context()
    context_without_classification.pop("intencao_atendimento")
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=("Há estoque e o prazo está no anúncio.", context_without_classification),
    ):
        orchestrator._run_job("cliente", created["job_id"])

    waiting = orchestrator.get_job("cliente", created["job_id"])
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", created["job_id"]
    )
    partial = stored["last_partial_result"]
    assert waiting["status"] == "waiting_retry"
    assert waiting["subquestions"] == []
    assert waiting["data_sufficient"] is False
    assert partial["evidence_status"] == []
    assert partial["data_sufficient"] is False
    assert partial["publish_attempted"] is False
    assert any("revisão humana" in warning for warning in partial["warnings"])

    stored["deadline_at_epoch"] = time.time() - 1
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", stored
    )
    blocked = orchestrator.get_job("cliente", created["job_id"])
    assert blocked["status"] == "completed"
    assert blocked["success"] is False
    assert blocked["agent_state"] == "revisao_humana"
    assert blocked["blocked_without_draft"] is True
    assert blocked["result"]["resposta"] == ""
    assert blocked["result"]["requires_approval"] is False
    assert blocked["proposal_hash"] == ""
    assert orchestrator.resume_incomplete_job(
        "cliente", created["job_id"]
    )["status"] == "completed"
    with pytest.raises(ValueError, match="nao possui proposta"):
        orchestrator.approve_or_refresh_proposal(
            client_id="cliente",
            proposal_id=created["job_id"],
            proposal_version=0,
            proposal_hash="",
            answer="Resposta manual sem classificacao.",
            store="Uai Mineirinho",
            subject_key="Q-NO-CLASSIFICATION",
        )


def test_two_independent_sources_are_sufficient_without_official_authority():
    analysis = {
        "decision": "yes",
        "sources": [
            "https://catalogo-a.example/produto",
            "https://manual-b.example/aplicacao",
        ],
        "evidence": {
            "product": [{"authority": "technical_catalog", "url": "https://catalogo-a.example/produto"}],
            "target_vehicle": [{"authority": "technical_catalog", "url": "https://manual-b.example/aplicacao"}],
            "equivalence": [{"authority": "derived", "reference": "mesma interface"}],
        },
    }
    sufficient, warnings = orchestrator._compatibility_evidence(analysis)
    assert sufficient is True
    assert warnings == []


def test_automation_has_no_direct_auto_publish_branch():
    questions_source = inspect.getsource(endpoints.ml_perguntas_automacao_poll)
    post_sale_source = inspect.getsource(endpoints.ml_pos_venda_automacao_poll)
    assert '"sent_auto"' not in questions_source
    assert '"sent_auto_pos_venda"' not in post_sale_source
    assert "_perguntas_ia_enviar_resposta_ml" not in questions_source
    assert "_ml_pos_venda_enviar_resposta_ml" not in post_sale_source
    assert "codex_job_id" in questions_source
    assert "codex_job_id" in post_sale_source


def test_frontend_uses_async_job_polling():
    question_js = inspect.getsource(endpoints.ml_perguntas_gerar_resposta_manual)
    post_sale_js = inspect.getsource(endpoints.ml_pos_venda_gerar_resposta_conversa)
    assert "req.async_mode" in question_js
    assert "req.async_mode" in post_sale_js


def test_insufficient_question_research_is_bounded_until_evidence_is_sufficient(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda job: None)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        created = orchestrator.create_job(
            client_id="cliente",
            task_type="question",
            store="JK Pecas",
            subject_key="Q-EVOQUE",
            request={
                "pergunta": {
                    "id": "Q-EVOQUE",
                    "text": "Serve na Evoque SE 2.0 gasolina 2017 e quantos bar de pressao?",
                },
                "question_text": "Serve na Evoque SE 2.0 gasolina 2017 e quantos bar de pressao?",
                "sku": "254-1",
            },
        )

    insufficient_context = {
        "diagnostico_ia": [{
            "result": {
                "validation_ok": False,
                "confidence": 0.49,
                "compatibility_analysis": {
                    "decision": "insufficient",
                    "confidence": 0.49,
                    "missing_fields": ["authoritative_technical_evidence", "pressure"],
                    "queries": [{"query": "bomba Evoque 2017 pressao"}],
                    "sources": ["https://catalogo-comercial.example/bomba"],
                    "evidence": {},
                },
            },
        }],
    }
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=("Para confirmar, informe o tipo de rosca.", insufficient_context),
    ):
        orchestrator._run_job("cliente", created["job_id"])

    waiting = orchestrator.get_job("cliente", created["job_id"])
    assert waiting["status"] == "waiting_retry"
    assert waiting["queued"] is True
    assert waiting["data_sufficient"] is False
    assert waiting["retry_policy"] == "bounded"
    assert waiting["deadline_seconds"] == 180
    assert waiting["deadline_at_epoch"] > time.time()
    assert waiting["retry_count"] == 1
    assert waiting["research_history"][0]["missing_fields"] == [
        "authoritative_technical_evidence",
        "pressure",
    ]
    assert waiting["result"] == {}

    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", created["job_id"]
    )
    stored.update({"status": "queued", "next_retry_at_epoch": 0.0})
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", stored)
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=("Essa bomba e compativel com a Evoque SE 2.0 gasolina 2017 e trabalha a 3 bar.", _official_context()),
    ):
        orchestrator._run_job("cliente", created["job_id"])

    completed = orchestrator.get_job("cliente", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["data_sufficient"] is True
    assert "3 bar" in completed["result"]["resposta"]
    assert completed["result"]["publish_attempted"] is False


def test_transient_customer_reply_failure_is_retried_instead_of_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda job: None)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        created = orchestrator.create_job(
            client_id="cliente",
            task_type="question",
            store="JK Pecas",
            subject_key="Q-RETRY",
            request={"pergunta": {"id": "Q-RETRY", "text": "Serve?"}, "question_text": "Serve?"},
        )
    with patch.object(orchestrator, "_load_question_context", side_effect=RuntimeError("HTTP 429")):
        orchestrator._run_job("cliente", created["job_id"])

    waiting = orchestrator.get_job("cliente", created["job_id"])
    assert waiting["status"] == "waiting_retry"
    assert waiting["retry_count"] == 1
    assert "HTTP 429" in waiting["retry_reason"]


def test_legacy_completed_job_without_current_contract_is_quarantined(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda job: None)
    legacy = {
        "job_id": "job-legacy-incomplete",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "question",
        "subject_key": "Q-LEGACY",
        "store": "JK Pecas",
        "status": "completed",
        "agent_state": "aguardando_aprovacao",
        "retry_count": 0,
        "cancel_requested": False,
        "result": {
            "resposta": "Fallback antigo.",
            "contexto": {},
            "evidence_status": [{"intent": "compatibility", "status": "partial"}],
            "data_sufficient": False,
            "warnings": ["Compatibilidade sem conclusao tecnica segura."],
        },
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", legacy)

    resumed = orchestrator.resume_incomplete_job("cliente", "job-legacy-incomplete")

    assert resumed["status"] == "cancelled"
    assert resumed["queued"] is False
    assert resumed["contract_quarantined"] is True
    assert resumed["blocked_without_draft"] is True
    assert resumed["result"]["resposta"] == ""
    assert resumed["result"]["requires_approval"] is False


def test_expired_research_returns_best_partial_draft_for_review(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda job: None)
    with patch.object(orchestrator, "_schedule", return_value=True), patch.object(
        orchestrator.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        created = orchestrator.create_job(
            client_id="cliente",
            task_type="question",
            store="Uai Mineirinho",
            subject_key="Q-ONIX-2017",
            request={
                "pergunta": {"id": "Q-ONIX-2017", "text": "2017"},
                "question_text": "Serve no Onix LT 1.0 2017?",
                "sku": "46",
            },
        )

    partial_answer = (
        "Para o Onix LT 1.0 2017, confirme o codigo do sensor instalado para compararmos com este produto."
    )
    deadline_checks = iter((False, True))
    monkeypatch.setattr(
        orchestrator,
        "_job_deadline_expired",
        lambda job: next(deadline_checks, True),
    )
    with patch.object(
        orchestrator,
        "_load_question_context",
        return_value=(partial_answer, {
            "model": "codex:gpt-5.6-sol",
            "intencao_atendimento": {
                "subperguntas": [{
                    "intent": "compatibility",
                    "question": "Serve no Onix LT 1.0 2017?",
                    "required_evidence": "codigo e aplicacao comprovados",
                }],
            },
            "diagnostico_ia": [{
                "result": {
                    "validation_ok": False,
                    "confidence": 0.4,
                    "compatibility_analysis": {
                        "decision": "insufficient",
                        "confidence": 0.4,
                        "missing_fields": ["codigo_do_sensor"],
                        "evidence": {},
                    },
                },
            }],
        }),
    ):
        orchestrator._run_job("cliente", created["job_id"])

    completed = orchestrator.get_job("cliente", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["queued"] is False
    assert completed["data_sufficient"] is False
    assert completed["completed_with_partial"] is True
    assert completed["deadline_reached"] is True
    assert completed["result"]["resposta"] == partial_answer
    assert completed["result"]["publish_attempted"] is False
    assert any("3 minutos" in warning for warning in completed["warnings"])


def test_polling_expired_waiting_job_surfaces_stored_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    job = {
        "job_id": "job-expired-partial",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "question",
        "subject_key": "Q-EXPIRED",
        "store": "Uai Mineirinho",
        "status": "waiting_retry",
        "agent_state": "pesquisando",
        "current_step": "consultar",
        "deadline_at_epoch": time.time() - 1,
        "deadline_seconds": 180,
        "retry_count": 12,
        "retry_policy": "bounded",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "subquestions": [{
            "id": "sq_1",
            "intent": "compatibility",
            "question": "Serve?",
            "required_evidence": "evidencia tecnica",
        }],
        "last_partial_result": {
            "resposta": "Rascunho conservador com os dados disponiveis.",
            "contexto": {"model": "codex:gpt-5.6-sol"},
            "evidence_status": [{"intent": "compatibility", "status": "partial"}],
            "data_sufficient": False,
            "warnings": ["Compatibilidade sem conclusao tecnica segura."],
        },
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)

    completed = orchestrator.get_job("cliente", "job-expired-partial")

    assert completed["status"] == "completed"
    assert completed["result"]["resposta"] == "Rascunho conservador com os dados disponiveis."
    assert completed["completed_with_partial"] is True


def test_expired_job_without_model_draft_never_creates_local_customer_text(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    job = {
        "job_id": "job-expired-without-draft",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "question",
        "subject_key": "Q-NO-DRAFT",
        "store": "Uai Mineirinho",
        "status": "waiting_retry",
        "agent_state": "pesquisando",
        "current_step": "consultar",
        "deadline_at_epoch": time.time() - 1,
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "subquestions": [{
            "id": "sq_1",
            "intent": "product_feature",
            "question": "Tem lado especifico?",
            "required_evidence": "caracteristica confirmada",
        }],
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)

    completed = orchestrator.get_job("cliente", job["job_id"])

    assert completed["status"] == "completed"
    assert completed["agent_state"] == "revisao_humana"
    assert completed["result"]["resposta"] == ""
    assert completed["result"]["requires_approval"] is False
    assert completed["result"]["completion_reason"] == "ai_response_unavailable"
    assert completed["blocked_without_draft"] is True


def test_legacy_jobs_are_hidden_from_latest_recovery_and_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    scheduled = []
    monkeypatch.setattr(orchestrator, "_schedule", lambda job: scheduled.append(job["job_id"]))
    monkeypatch.setattr(orchestrator, "_known_clients", lambda _info_base: ["cliente"])

    request = {
        "pergunta": {"id": "Q-OLD", "item_id": "MLB-1", "from": {"id": "B-1"}},
    }
    legacy = {
        "job_id": "job-old-contract",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "event_subject_key": "Q-OLD",
        "subject_key": "item:MLB-1|buyer:B-1",
        "store": "Loja",
        "status": "waiting_retry",
        "agent_state": "pesquisando",
        "prompt_version": "jk_ml_customer_reply_codex_v3",
        "prompt_hash": "old-hash",
        "schema_version": "3.0",
        "result": {
            "resposta": "Resposta de contrato antigo.",
            "proposal_version": 1,
            "proposal_hash": "old-proposal",
            "data_sufficient": False,
        },
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", legacy
    )

    assert orchestrator.latest_job_for_request(
        client_id="cliente",
        task_type="public_question",
        store="Loja",
        subject_key="Q-OLD",
        request=request,
    ) is None
    recovery_job = {
        **legacy,
        "job_id": "job-old-recovery",
        "event_subject_key": "Q-RECOVERY",
        "subject_key": "question:Q-RECOVERY",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", recovery_job
    )
    orchestrator.recover_pending_jobs()
    assert scheduled == []

    recovered = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", "job-old-recovery"
    )
    assert recovered["status"] == "cancelled"
    assert recovered["contract_quarantined"] is True

    quarantined = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", "job-old-contract"
    )
    assert quarantined["status"] == "cancelled"
    assert quarantined["contract_quarantined"] is True
    assert quarantined["thread_id"] == ""
    assert quarantined["result"]["resposta"] == ""

    with pytest.raises(ValueError, match="contrato de IA anterior"):
        orchestrator.approve_or_refresh_proposal(
            client_id="cliente",
            proposal_id="job-old-contract",
            proposal_version=1,
            proposal_hash="old-proposal",
            answer="Resposta de contrato antigo.",
            store="Loja",
            subject_key="Q-OLD",
        )
