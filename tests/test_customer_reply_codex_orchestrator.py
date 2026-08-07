from __future__ import annotations

import inspect
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.modules.perguntas_pos_venda.endpoints import question_automation
from backend.schemas.perguntas_pos_venda import PerguntasGerarRespostaRequest, PosVendaGerarRespostaRequest
from backend.services import codex_assistant_storage
from backend.services import ia_providers
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_endpoints as endpoints
from backend.services import perguntas_pos_venda_perguntas_ml as perguntas_ml
from backend.services import perguntas_pos_venda_state as perguntas_state


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
        str(tmp_path), "cliente", "job-heartbeat-1", owner="worker-test",
        lease_generation=claimed["lease_generation"], lease_seconds=60
    )
    assert renewed["heartbeat_at"]
    assert renewed["lease_expires_ts"] > claimed["lease_expires_ts"]

    renewed.update({"status": "completed", "agent_state": "aguardando_aprovacao"})
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", renewed)
    assert codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
        str(tmp_path), "cliente", "job-heartbeat-1", owner="worker-test",
        lease_generation=claimed["lease_generation"], lease_seconds=60
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
    assert not any("revisão humana" in warning for warning in partial["warnings"])

    stored["deadline_at_epoch"] = time.time() - 1
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", stored
    )
    completed = orchestrator.get_job("cliente", created["job_id"])
    assert completed["status"] == "completed"
    assert completed["success"] is True
    assert completed["blocked_without_draft"] is False
    assert completed["result"]["resposta"]
    assert completed["result"]["requires_approval"] is True
    assert completed["review_required"] is False
    assert completed["completion_reason"] == "ai_classification_unavailable"
    assert completed["deadline_seconds"] == orchestrator.PUBLIC_RESEARCH_DEADLINE_SECONDS
    assert completed["can_cancel"] is False
    assert completed["proposal_hash"]
    assert orchestrator.resume_incomplete_job(
        "cliente", created["job_id"]
    )["status"] == "completed"


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
    question_execution_source = inspect.getsource(question_automation._question_poll_process_candidate)
    post_sale_source = inspect.getsource(endpoints.ml_pos_venda_automacao_poll)
    assert '"sent_auto"' not in questions_source
    assert '"sent_auto_pos_venda"' not in post_sale_source
    assert "_perguntas_ia_enviar_resposta_ml" not in questions_source
    assert "_ml_pos_venda_enviar_resposta_ml" not in post_sale_source
    assert "codex_job_id" in question_execution_source
    assert '"disabled": True' in post_sale_source
    assert '"motivo": "pos_venda_somente_manual"' in post_sale_source


def test_frontend_uses_async_job_polling():
    question_js = inspect.getsource(endpoints.ml_perguntas_gerar_resposta_manual)
    post_sale_js = inspect.getsource(endpoints.ml_pos_venda_gerar_resposta_conversa)
    assert "req.async_mode" in question_js
    assert "req.async_mode" in post_sale_js


def test_insufficient_question_research_retries_once_then_can_be_sufficient(tmp_path, monkeypatch):
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
    assert waiting["deadline_seconds"] == orchestrator.PUBLIC_RESEARCH_DEADLINE_SECONDS
    assert waiting["deadline_at_epoch"] > time.time()
    assert waiting["attempt_count"] == 1
    assert waiting["evidence_attempt_count"] == 1
    assert waiting["can_cancel"] is True
    assert waiting["status_message"]
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


def test_elapsed_public_research_keeps_same_job_and_partial_for_next_retry(tmp_path, monkeypatch):
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

    waiting = orchestrator.get_job("cliente", created["job_id"])
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", created["job_id"]
    )
    assert waiting["job_id"] == created["job_id"]
    assert waiting["status"] == "waiting_retry"
    assert waiting["queued"] is True
    assert waiting["data_sufficient"] is False
    assert waiting["deadline_reached"] is False
    assert waiting["deadline_at_epoch"] > time.time()
    assert stored["last_partial_result"]["resposta"] == partial_answer
    assert stored["last_partial_result"]["publish_attempted"] is False


def test_elapsed_current_job_completes_with_safe_partial_draft(tmp_path, monkeypatch):
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
        "deadline_seconds": orchestrator.PUBLIC_RESEARCH_DEADLINE_SECONDS,
        "retry_count": 1,
        "retry_kind": "evidence",
        "retry_policy": "bounded",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "queue_policy_version": orchestrator.QUEUE_POLICY_VERSION,
        "queue_origin": orchestrator.QUEUE_ORIGIN_MANUAL,
        "queue_priority": orchestrator.QUEUE_PRIORITY_MANUAL,
        "subquestions": [{
            "id": "sq_1",
            "intent": "compatibility",
            "question": "Serve?",
            "required_evidence": "evidencia tecnica",
        }],
        "last_partial_result": {
            "resposta": "Para confirmar a aplicacao, informe o codigo da peca instalada.",
            "contexto": {"model": "codex:gpt-5.6-sol"},
            "evidence_status": [{"intent": "compatibility", "status": "partial"}],
            "data_sufficient": False,
            "warnings": ["Compatibilidade sem conclusao tecnica segura."],
        },
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)

    completed = orchestrator.get_job("cliente", "job-expired-partial")

    assert completed["status"] == "completed"
    assert completed["result"]["data_sufficient"] is False
    assert completed["result"]["requires_approval"] is True
    assert completed["completion_reason"] == "evidence_insufficient_after_retry_limit"
    assert completed["can_cancel"] is False


def test_elapsed_job_without_model_draft_returns_neutral_available_draft(tmp_path, monkeypatch):
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
        "retry_kind": "evidence",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "queue_policy_version": orchestrator.QUEUE_POLICY_VERSION,
        "queue_origin": orchestrator.QUEUE_ORIGIN_MANUAL,
        "queue_priority": orchestrator.QUEUE_PRIORITY_MANUAL,
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
    assert completed["result"]["resposta"]
    assert completed["blocked_without_draft"] is False
    assert completed["review_required"] is False
    assert completed["completion_reason"] == "ai_response_unavailable"
    assert completed["can_cancel"] is False


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
    assert "result" not in quarantined
    assert quarantined["blocked_without_draft"] is True

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


@pytest.mark.parametrize(
    ("length", "expected_length", "truncated"),
    [(1999, 1999, False), (2000, 2000, False), (2001, 2000, True)],
)
def test_public_answer_limit_accepts_up_to_2000_characters(length, expected_length, truncated):
    answer = perguntas_state._perguntas_ia_limpar_resposta("x" * length)

    assert len(answer) == expected_length
    assert answer.endswith("...") is truncated


def test_post_sale_draft_remains_bounded_to_340_chars_and_three_sentences(monkeypatch):
    monkeypatch.setattr(perguntas_ml, "ML_POS_VENDA_DEFAULT_MAX_CHARS", 350, raising=False)
    monkeypatch.setattr(perguntas_ml, "ML_POS_VENDA_LIMITE_SEGURO", 340, raising=False)
    monkeypatch.setattr(
        perguntas_ml,
        "_perguntas_ia_assinatura_loja",
        lambda loja: f"Equipe {loja} agradece o seu contato." if loja else "Equipe da loja agradece o seu contato.",
        raising=False,
    )
    monkeypatch.setattr(perguntas_ml, "_perguntas_ia_remover_apresentacao_sistema", lambda texto: texto, raising=False)
    answer = perguntas_ml._pos_venda_ia_resposta_final_loja(
        "Primeira frase. Segunda frase. Terceira frase que nao deve entrar. Quarta frase.",
        "JK Pecas",
        350,
    )

    sentences = [item for item in answer.replace("\n", " ").split(". ") if item.strip()]
    assert len(answer) <= 340
    assert len(sentences) <= 3
    assert "Terceira frase" not in answer
    assert answer.endswith("Equipe JK Pecas agradece o seu contato.")


@pytest.mark.parametrize(
    ("retry_count", "expected"),
    [(1, 5), (2, 15), (3, 30), (4, 30), (5, 30), (6, 30), (7, 30), (20, 30)],
)
def test_public_retry_backoff_uses_bounded_operational_schedule(retry_count, expected):
    assert orchestrator._retry_delay_seconds(retry_count, "same-job") == expected


def test_active_request_reuses_same_job_id_and_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(orchestrator.codex_agent_runtime, "resolve_guidance", lambda *_args, **_kwargs: [])
    request = {
        "pergunta": {"id": "Q-SAME", "item_id": "MLB-1", "from": {"id": "BUYER-1"}, "text": "Serve?"},
        "question_text": "Serve?",
    }

    first = orchestrator.create_job(
        client_id="cliente", task_type="question", store="JK Pecas", subject_key="Q-SAME", request=request
    )
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", first["job_id"]
    )
    stored["thread_id"] = "thread-same"
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", stored)
    resumed = orchestrator.create_job(
        client_id="cliente", task_type="question", store="JK Pecas", subject_key="Q-SAME", request=request
    )

    assert resumed["job_id"] == first["job_id"]
    assert resumed["thread_id"] == "thread-same"
    assert resumed["deadline_at_epoch"] == 0


def test_cancel_active_job_is_terminal_and_rejects_late_result(tmp_path):
    running = {
        "job_id": "job-cancel-active",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-CANCEL",
        "store": "JK Pecas",
        "status": "running",
        "agent_state": "consultando",
        "lease_owner": "worker-old",
        "lease_expires_ts": time.time() + 60,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", running)

    cancelled = codex_assistant_storage.codex_assistant_customer_reply_job_request_cancel(
        str(tmp_path), "cliente", running["job_id"]
    )
    late = {**running, "status": "completed", "agent_state": "aguardando_aprovacao", "result": {"resposta": "tardia"}}
    persisted = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", late
    )
    persisted_with_cancel_flag = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", {**late, "cancel_requested": True}
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert persisted["status"] == "cancelled"
    assert persisted.get("result") is None
    assert persisted_with_cancel_flag["status"] == "cancelled"
    assert persisted_with_cancel_flag.get("result") is None


def test_cancel_waiting_job_stops_timer_and_active_codex_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    job = {
        "job_id": "job-cancel-waiting",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-WAIT",
        "store": "JK Pecas",
        "status": "waiting_retry",
        "conversation_id": "conversation-wait",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)
    cancelled_timers: list[str] = []
    interrupted: list[str] = []
    monkeypatch.setattr(orchestrator, "_cancel_retry_timer", lambda job_id: cancelled_timers.append(job_id))
    monkeypatch.setattr(ia_providers, "cancel_codex_persistent_turn", lambda key: interrupted.append(key) or True)

    cancelled = orchestrator.cancel_job("cliente", job["job_id"])

    assert cancelled["status"] == "cancelled"
    assert cancelled["can_cancel"] is False
    assert cancelled_timers == [job["job_id"]]
    assert interrupted == [job["job_id"]]


def test_restart_recovers_same_job_after_expired_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_known_clients", lambda _base: ["cliente"])
    scheduled: list[str] = []
    monkeypatch.setattr(orchestrator, "_schedule", lambda job: scheduled.append(job["job_id"]) or True)
    job = {
        "job_id": "job-restart",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-RESTART",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": "dead-worker",
        "lease_expires_ts": time.time() - 1,
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "queue_policy_version": orchestrator.QUEUE_POLICY_VERSION,
        "queue_origin": orchestrator.QUEUE_ORIGIN_MANUAL,
        "queue_priority": orchestrator.QUEUE_PRIORITY_MANUAL,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)

    orchestrator.recover_pending_jobs()

    assert scheduled == [job["job_id"]]
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", job["job_id"]
    )
    assert stored["job_id"] == job["job_id"]


def test_cancel_is_isolated_by_tenant_and_store(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    base = {
        "job_id": "same-id",
        "profile": orchestrator.PROFILE,
        "task_type": "public_question",
        "subject_key": "Q-1",
        "status": "waiting_retry",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-a", {**base, "client_id": "tenant-a", "store": "Loja A"}
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "tenant-b", {**base, "client_id": "tenant-b", "store": "Loja B"}
    )

    orchestrator.cancel_job("tenant-a", "same-id")

    tenant_a = codex_assistant_storage.codex_assistant_customer_reply_job_get(str(tmp_path), "tenant-a", "same-id")
    tenant_b = codex_assistant_storage.codex_assistant_customer_reply_job_get(str(tmp_path), "tenant-b", "same-id")
    assert tenant_a["status"] == "cancelled"
    assert tenant_b["status"] == "waiting_retry"
    assert tenant_b["store"] == "Loja B"


def test_report_settings_save_is_unchanged_by_job_terminal_guard(tmp_path):
    saved = codex_assistant_storage.codex_assistant_report_settings_save(
        str(tmp_path),
        "cliente",
        {"enabled": True, "schedule": "08:00"},
        updated_by="operador",
    )
    loaded = codex_assistant_storage.codex_assistant_report_settings_get(str(tmp_path), "cliente")

    assert saved["enabled"] is True
    assert loaded["schedule"] == "08:00"
    assert loaded["updated_by"] == "operador"


def test_retry_policy_and_deadline_are_separated_by_task_type():
    assert orchestrator._task_retry_policy("public_question") == "bounded"
    assert orchestrator._task_deadline_seconds("public_question") == 900
    assert orchestrator._task_retry_policy("question") == "bounded"
    assert orchestrator._task_deadline_seconds("question") == 900
    assert orchestrator._task_retry_policy("post_sale") == "bounded"
    assert orchestrator._task_deadline_seconds("post_sale") == 180


def test_two_jobs_in_same_conversation_have_isolated_active_turn_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    interrupted: list[str] = []
    monkeypatch.setattr(ia_providers, "cancel_codex_persistent_turn", lambda key: interrupted.append(key) or True)
    base = {
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "item:MLB-1|buyer:B-1",
        "store": "JK Pecas",
        "status": "waiting_retry",
        "conversation_id": "same-conversation",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", {**base, "job_id": "job-old"}
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", {**base, "job_id": "job-new"}
    )

    orchestrator.cancel_job("cliente", "job-old")

    newer = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", "job-new"
    )
    assert interrupted == ["job-old"]
    assert newer["status"] == "waiting_retry"


def test_active_turn_registry_interrupts_only_exact_job_key():
    class FakeTurn:
        def __init__(self):
            self.interruptions = 0

        def interrupt(self):
            self.interruptions += 1

    old_turn = FakeTurn()
    new_turn = FakeTurn()
    with ia_providers._CODEX_PERSISTENT_TURNS_LOCK:
        ia_providers._CODEX_PERSISTENT_TURNS.update(
            {"job-old": old_turn, "job-new": new_turn}
        )
    try:
        assert ia_providers.cancel_codex_persistent_turn("job-old") is True
        assert old_turn.interruptions == 1
        assert new_turn.interruptions == 0
    finally:
        with ia_providers._CODEX_PERSISTENT_TURNS_LOCK:
            ia_providers._CODEX_PERSISTENT_TURNS.pop("job-old", None)
            ia_providers._CODEX_PERSISTENT_TURNS.pop("job-new", None)


def test_transient_retry_preserves_previous_context_sources_and_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda _job: None)
    job = {
        "job_id": "job-preserve-partial",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-PARTIAL",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": orchestrator._WORKER_ID,
        "last_partial_result": {
            "resposta": "Rascunho comprovado ate aqui.",
            "contexto": {"sources": ["manual-oficial"]},
            "evidence_status": [{"intent": "compatibility", "status": "partial"}],
            "warnings": ["Falta confirmar a medida."],
        },
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)

    retried = orchestrator._persist_retry(
        job,
        error="timeout temporario",
        warnings=["Servico temporariamente indisponivel."],
    )

    assert retried["status"] == "waiting_retry"
    assert retried["last_partial_result"]["resposta"] == "Rascunho comprovado ate aqui."
    assert retried["last_partial_result"]["contexto"]["sources"] == ["manual-oficial"]
    assert len(retried["last_partial_result"]["warnings"]) == 2


def test_expired_worker_cannot_adopt_new_lease_or_cancel_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    current = {
        "job_id": "job-lease-owner",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-LEASE",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": "new-worker",
        "lease_expires_ts": time.time() + 60,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", current)
    stale = {**current, "lease_owner": "old-worker"}

    with pytest.raises(orchestrator._LeaseLost):
        orchestrator._save_step(stale, "consultando", "consultar", "resultado antigo")

    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", current["job_id"]
    )
    assert stored["status"] == "running"
    assert stored["lease_owner"] == "new-worker"
    assert stored.get("cancel_requested") is not True


def test_empty_owner_stale_result_cannot_overwrite_active_lease(tmp_path):
    current = {
        "job_id": "job-empty-owner-stale",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-LEASE-EMPTY",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": "new-worker",
        "lease_expires_ts": time.time() + 60,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", current)

    persisted = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path),
        "cliente",
        {**current, "status": "completed", "lease_owner": "", "result": {"resposta": "stale"}},
    )

    assert persisted["status"] == "running"
    assert persisted["lease_owner"] == "new-worker"
    assert "result" not in persisted


def test_current_worker_can_release_its_lease_with_expected_owner(tmp_path):
    current = {
        "job_id": "job-current-owner-release",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-LEASE-CURRENT",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": "current-worker",
        "lease_expires_ts": time.time() + 60,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", current)

    persisted = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path),
        "cliente",
        {**current, "status": "waiting_retry", "lease_owner": "", "lease_expires_ts": 0},
        expected_lease_owner="current-worker",
    )

    assert persisted["status"] == "waiting_retry"
    assert persisted["lease_owner"] == ""


def test_retry_merges_confirmed_intents_sources_and_only_keeps_remaining_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_schedule_retry_timer", lambda _job: None)
    base = {
        "job_id": "job-merge-evidence",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-MERGE",
        "store": "JK Pecas",
        "status": "running",
        "lease_owner": orchestrator._WORKER_ID,
        "attempt_count": 1,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", base)
    first = orchestrator._persist_retry(
        base,
        context={"evidence_envelope": {
            "records": [{"field": "product_feature", "value": "confirmado", "source": "fonte-A", "coverage": "confirmed"}],
            "sources": ["fonte-A"],
            "gaps": ["shipping", "invoice"],
        }},
        matrix=[
            {"id": "sq-product", "intent": "product_feature", "status": "confirmed", "required_evidence": "product_feature"},
            {"id": "sq-shipping", "intent": "shipping", "status": "pending", "required_evidence": "shipping"},
            {"id": "sq-invoice", "intent": "invoice", "status": "pending", "required_evidence": "invoice"},
        ],
        error="faltam shipping e invoice",
    )
    running_second = {**first, "status": "running", "lease_owner": orchestrator._WORKER_ID, "attempt_count": 2}
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", running_second)
    second = orchestrator._persist_retry(
        running_second,
        context={"evidence_envelope": {
            "records": [{"field": "shipping", "value": "confirmado", "source": "fonte-B", "coverage": "confirmed"}],
            "sources": ["fonte-B"],
            "gaps": ["invoice"],
        }},
        matrix=[
            {"id": "sq-shipping", "intent": "shipping", "status": "confirmed", "required_evidence": "shipping"},
            {"id": "sq-invoice", "intent": "invoice", "status": "pending", "required_evidence": "invoice"},
        ],
        error="falta invoice",
    )

    partial = second["last_partial_result"]
    assert {row["intent"]: row["status"] for row in partial["evidence_status"]} == {
        "product_feature": "confirmed",
        "shipping": "confirmed",
        "invoice": "pending",
    }
    assert partial["contexto"]["evidence_envelope"]["sources"] == ["fonte-A", "fonte-B"]
    assert second["research_history"][-1]["sources"] == ["fonte-A", "fonte-B"]
    assert orchestrator._pending_research_gaps(second) == ["invoice"]


def test_cancelled_retry_does_not_transition_plan_back_to_research(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_heartbeat_loop", lambda *_args: None)
    transitions = []
    job = {
        "job_id": "job-cancel-race-plan",
        "profile": orchestrator.PROFILE,
        "client_id": "cliente",
        "task_type": "public_question",
        "subject_key": "Q-CANCEL-PLAN",
        "store": "JK Pecas",
        "status": "queued",
        "plan_id": "plan-cancelled",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)
    monkeypatch.setattr(orchestrator, "_refresh_thread_from_previous_job", lambda claimed: claimed)
    monkeypatch.setattr(orchestrator, "_save_step", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("turn interrupted")))
    monkeypatch.setattr(
        orchestrator,
        "_persist_retry",
        lambda *_args, **_kwargs: {**job, "status": "cancelled", "cancel_requested": True},
    )
    monkeypatch.setattr(orchestrator.codex_agent_runtime, "transition_plan", lambda *args, **kwargs: transitions.append((args, kwargs)))

    orchestrator._run_job("cliente", job["job_id"])

    assert transitions == []


def test_status_payload_keeps_user_updated_during_retry():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    public = orchestrator._public_job(
        {
            "job_id": "job-progress",
            "task_type": "public_question",
            "status": "waiting_retry",
            "agent_state": "pesquisando",
            "current_step": "consultar",
            "attempt_count": 4,
            "retry_reason": "evidencia_tecnica_insuficiente",
            "next_retry_at_epoch": time.time() + 30,
            "updated_at": now,
        }
    )

    assert public["attempt_count"] == 4
    assert 1 <= public["next_retry_in_seconds"] <= 30
    assert public["last_activity_at"] == now
    assert public["can_cancel"] is True
    assert "Nova tentativa" in public["status_message"]


def test_lease_generation_fences_worker_after_new_owner_releases_lease(tmp_path):
    base = {
        "job_id": "job-fencing-generation",
        "profile": orchestrator.PROFILE,
        "task_type": "public_question",
        "subject_key": "Q-FENCE",
        "store": "Loja",
        "status": "queued",
        "agent_state": "entendendo",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", base)
    old = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", base["job_id"], owner="worker-old", lease_seconds=10
    )
    assert old["lease_generation"] == 1
    expired = {**old, "lease_expires_ts": 0.0}
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", expired,
        expected_lease_owner="worker-old", expected_lease_generation=1,
    )
    current = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", base["job_id"], owner="worker-new", lease_seconds=30
    )
    assert current["lease_generation"] == 2
    released = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente",
        {**current, "status": "waiting_retry", "lease_owner": "", "lease_expires_ts": 0.0},
        expected_lease_owner="worker-new", expected_lease_generation=2,
    )
    stale = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente",
        {**old, "status": "completed", "lease_owner": "", "result": {"resposta": "CANARY_STALE"}},
        expected_lease_owner="worker-old", expected_lease_generation=1,
    )
    assert released["status"] == stale["status"] == "waiting_retry"
    assert stale["lease_generation"] == 2
    assert "result" not in stale
    queued = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", {**released, "status": "queued"}, expected_lease_generation=2
    )
    newest = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", base["job_id"], owner="worker-third", lease_seconds=30
    )
    assert queued["status"] == "queued"
    assert newest["lease_generation"] == 3
    assert codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
        str(tmp_path), "cliente", base["job_id"], owner="worker-third",
        lease_generation=2, lease_seconds=30,
    ) is None
    assert codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
        str(tmp_path), "cliente", base["job_id"], owner="worker-third",
        lease_generation=3, lease_seconds=30,
    )["lease_generation"] == 3


def test_stale_generation_cannot_complete_new_retry_when_classification_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_cancel_retry_timer", lambda _job_id: None)
    transitions = []
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "transition_plan",
        lambda *_args, **kwargs: transitions.append(kwargs),
    )
    base = {
        "job_id": "job-stale-no-classification", "profile": orchestrator.PROFILE,
        "client_id": "cliente", "task_type": "public_question", "subject_key": "Q-NO-CLASS",
        "event_subject_key": "Q-NO-CLASS", "store": "Loja", "status": "queued",
        "agent_state": "entendendo", "subquestions": [], "plan_id": "plan-stale-no-class",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", base)
    old = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", base["job_id"], owner=orchestrator._WORKER_ID, lease_seconds=10
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente", {**old, "lease_expires_ts": 0.0},
        expected_lease_owner=orchestrator._WORKER_ID, expected_lease_generation=1,
    )
    current = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", base["job_id"], owner="worker-new", lease_seconds=30
    )
    current = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente",
        {**current, "status": "waiting_retry", "lease_owner": "", "lease_expires_ts": 0.0},
        expected_lease_owner="worker-new", expected_lease_generation=2,
    )

    returned = orchestrator._complete_with_best_available(old)
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", base["job_id"]
    )
    assert returned["lease_generation"] == stored["lease_generation"] == 2
    assert returned["status"] == stored["status"] == current["status"] == "waiting_retry"
    assert stored.get("blocked_without_draft") is not True
    assert transitions == []


def test_begin_immediate_prevents_cross_connection_claim_race(tmp_path):
    payload = {
        "job_id": "job-cross-process",
        "profile": orchestrator.PROFILE,
        "task_type": "public_question",
        "subject_key": "Q-CROSS",
        "store": "Loja",
        "status": "queued",
        "agent_state": "entendendo",
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", payload)
    db_path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "cliente")
    blocker = sqlite3.connect(db_path, timeout=5)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute(
        "UPDATE assistant_customer_reply_jobs SET cancel_requested = 1, status = 'cancelled' WHERE job_id = ?",
        (payload["job_id"],),
    )
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            codex_assistant_storage.codex_assistant_customer_reply_job_claim(
                str(tmp_path), "cliente", payload["job_id"], owner="racer", lease_seconds=30
            )
        )
    )
    worker.start()
    time.sleep(0.1)
    assert worker.is_alive()
    blocker.commit()
    blocker.close()
    worker.join(timeout=5)
    assert result == [None]


def test_v3_migration_scrubs_customer_job_and_linked_plan_plaintext(tmp_path):
    canary = "CANARY_PII buyer@example.com +55-11999999999"
    db_path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "cliente")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE assistant_customer_reply_jobs (
            job_id TEXT PRIMARY KEY, profile TEXT NOT NULL, task_type TEXT NOT NULL,
            subject_key TEXT NOT NULL, store TEXT, status TEXT NOT NULL,
            agent_state TEXT NOT NULL, idempotency_key TEXT, thread_id TEXT,
            lease_owner TEXT, lease_expires_ts REAL, cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE assistant_agent_plans (
            plan_id TEXT PRIMARY KEY, task_id TEXT, conversation_id TEXT,
            conversation_generation INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL,
            idempotency_key TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )
    legacy_job = {
        "job_id": "legacy-job", "profile": orchestrator.PROFILE,
        "task_type": "public_question", "subject_key": "Q-LEGACY", "store": "Loja",
        "status": "queued", "agent_state": "entendendo", "plan_id": "legacy-plan",
        "question_id": "Q-LEGACY", "thread_id": "thread-legacy",
        "request": {"pergunta": {"text": canary}},
        "research_history": [{"query": canary, "tool_output": canary}],
        "result": {"resposta": canary, "contexto": {"email": canary}},
    }
    legacy_plan = {
        "plan_id": "legacy-plan", "task_id": "legacy-job", "conversation_id": "conv",
        "conversation_generation": 1, "agent_state": "entendendo", "current_step": "entender",
        "idempotency_key": "legacy-idem", "request_preview": canary,
        "steps": [{"step_id": "entender", "status": "in_progress", "details": {"text": canary}}],
        "proposal": {"proposal_hash": "hash-only", "answer": canary},
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
    }
    conn.execute(
        "INSERT INTO assistant_customer_reply_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-job", orchestrator.PROFILE, "public_question", "Q-LEGACY", "Loja", "queued",
         "entendendo", "legacy-idem", "thread-legacy", "", 0.0, 0,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00", json.dumps(legacy_job)),
    )
    conn.execute(
        "INSERT INTO assistant_agent_plans VALUES (?,?,?,?,?,?,?,?,?)",
        ("legacy-plan", "legacy-job", "conv", 1, "entendendo", "legacy-idem",
         "2026-01-01T00:00:00", "2026-01-01T00:00:00", json.dumps(legacy_plan)),
    )
    conn.commit()
    conn.close()

    migrated = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", "legacy-job"
    )
    assert migrated["lease_generation"] == 0
    claimed = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        str(tmp_path), "cliente", "legacy-job", owner="worker", lease_seconds=30
    )
    assert claimed["lease_generation"] == 1
    conn = sqlite3.connect(db_path)
    raw_job = conn.execute(
        "SELECT payload_json FROM assistant_customer_reply_jobs WHERE job_id='legacy-job'"
    ).fetchone()[0]
    raw_plan = conn.execute(
        "SELECT payload_json FROM assistant_agent_plans WHERE plan_id='legacy-plan'"
    ).fetchone()[0]
    columns = [row[1] for row in conn.execute("PRAGMA table_info(assistant_customer_reply_jobs)")]
    conn.close()
    assert "lease_generation" in columns
    assert canary not in raw_job and canary not in raw_plan
    assert "request_preview" not in json.loads(raw_plan)
    assert "request" not in json.loads(raw_job)
    assert "result" not in json.loads(raw_job)


def test_new_customer_job_and_plan_never_persist_request_or_guidance_plaintext(tmp_path, monkeypatch):
    canary = "CANARY_NEW_PII customer@example.com tool-secret"
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_RECOVERY_STARTED", True)
    monkeypatch.setattr(orchestrator, "_schedule", lambda _job: True)
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "resolve_guidance",
        lambda *_args, **_kwargs: [{"guidance_id": "g", "instruction": canary}],
    )
    created = orchestrator.create_job(
        client_id="cliente", task_type="public_question", store="Loja", subject_key="Q-NEW-PII",
        request={
            "pergunta": {"id": "Q-NEW-PII", "item_id": "MLB-1", "text": canary},
            "question_text": canary,
            "tool_output": {"raw": canary},
        },
    )
    db_path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "cliente")
    conn = sqlite3.connect(db_path)
    raw_job = conn.execute(
        "SELECT payload_json FROM assistant_customer_reply_jobs WHERE job_id = ?",
        (created["job_id"],),
    ).fetchone()[0]
    raw_plan = conn.execute(
        "SELECT payload_json FROM assistant_agent_plans WHERE task_id = ?",
        (created["job_id"],),
    ).fetchone()[0]
    conn.close()
    assert canary not in raw_job and canary not in raw_plan
    assert "request" not in json.loads(raw_job)
    assert json.loads(raw_plan).get("guidance_applied") == []


def test_restart_keeps_completed_job_unblocked_and_rehydrates_available_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_known_clients", lambda _base: ["cliente"])
    scheduled = []
    monkeypatch.setattr(orchestrator, "_schedule", lambda job: scheduled.append(dict(job)) or True)
    completed = {
        "job_id": "job-restart-proposal", "profile": orchestrator.PROFILE,
        "client_id": "cliente", "task_type": "public_question", "subject_key": "Q-RESTART",
        "event_subject_key": "Q-RESTART", "question_id": "Q-RESTART", "item_id": "MLB-1",
        "store": "Loja", "status": "completed", "agent_state": "aguardando_aprovacao",
        "thread_id": "thread-same", "prompt_version": orchestrator.PROMPT_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH, "schema_version": orchestrator.SCHEMA_VERSION,
        "queue_policy_version": orchestrator.QUEUE_POLICY_VERSION,
        "queue_origin": orchestrator.QUEUE_ORIGIN_MANUAL,
        "queue_priority": orchestrator.QUEUE_PRIORITY_MANUAL,
        "proposal_version": 1, "result": {"resposta": "CANARY_DRAFT", "proposal_hash": "hash"},
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", completed)
    with codex_assistant_storage._CUSTOMER_REPLY_TRANSIENT_LOCK:
        codex_assistant_storage._CUSTOMER_REPLY_TRANSIENT.clear()
    orchestrator.recover_pending_jobs()
    assert scheduled == []
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", completed["job_id"]
    )
    assert stored["status"] == "completed"
    assert stored["agent_state"] == "concluido"
    assert stored["blocked_without_draft"] is False
    assert stored["review_required"] is False
    assert stored["completion_reason"] == "draft_expired"

    rehydrated = orchestrator.get_job("cliente", completed["job_id"])
    assert rehydrated["status"] == "completed"
    assert rehydrated["success"] is True
    assert rehydrated["result"]["resposta"]
    assert rehydrated["blocked_without_draft"] is False
    assert rehydrated["review_required"] is False
    assert rehydrated["completion_reason"] == "draft_expired_available_fallback"


def test_canonical_question_item_and_history_are_reloaded_by_ids(tmp_path, monkeypatch):
    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Runtime:
        PASTA_INFO = str(tmp_path)

        @staticmethod
        def _obter_cfg_ml(_client, _store):
            return {"user_id": "SELLER-1"}

        @staticmethod
        def _ml_api_request(_client, _store, cfg, _method, url, **_kwargs):
            if "/questions/" in url:
                return Response({"id": "Q-CANON", "item_id": "MLB-CANON", "from": {"id": "BUYER-1"}, "text": "Texto canonico"}), cfg
            return Response({"id": "MLB-CANON", "title": "Item canonico", "seller_custom_field": "SKU-1"}), cfg

        @staticmethod
        def _ml_api_item_com_oauth_tenant(*_args):
            return {}

        @staticmethod
        def _ml_api_item(*_args):
            return {}

        @staticmethod
        def _ml_extrair_sku(_item):
            return "SKU-1"

        @staticmethod
        def _ml_perguntas_normalizar(question, *_args):
            return dict(question)

        @staticmethod
        def _ml_perguntas_anexar_historico_comprador(_client, _store, cfg, _seller, questions):
            questions[0]["history"] = [{"id": "Q-OLD", "text": "Historico canonico"}]
            return questions, cfg

        @staticmethod
        def _perguntas_ia_gerar_resposta(_client, _store, cfg, question, item):
            assert question["text"] == "Texto canonico"
            assert question["history"][0]["text"] == "Historico canonico"
            assert item["title"] == "Item canonico"
            return "Resposta", cfg, {}

    monkeypatch.setattr(orchestrator, "_RUNTIME", Runtime())
    answer, context = orchestrator._load_question_context(
        {
            "job_id": "job-canon", "client_id": "cliente", "store": "Loja",
            "question_id": "Q-CANON", "item_id": "MLB-CANON", "request": {},
            "task_type": "public_question", "lease_generation": 1,
        }
    )
    assert answer == "Resposta"
    assert context["pergunta"]["id"] == "Q-CANON"


def test_rejected_final_cas_does_not_transition_plan_to_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_RUNTIME", _runtime(tmp_path))
    monkeypatch.setattr(orchestrator, "_heartbeat_loop", lambda *_args: None)
    transitions = []
    monkeypatch.setattr(
        orchestrator.codex_agent_runtime,
        "transition_plan",
        lambda *_args, **kwargs: transitions.append(kwargs),
    )
    job = {
        "job_id": "job-final-cas", "profile": orchestrator.PROFILE, "client_id": "cliente",
        "task_type": "public_question", "subject_key": "Q-CAS", "event_subject_key": "Q-CAS",
        "question_id": "Q-CAS", "store": "Loja", "status": "queued", "agent_state": "entendendo",
        "plan_id": "plan-final-cas", "request": {"pergunta": {"id": "Q-CAS", "text": "Serve?"}},
        "prompt_version": orchestrator.PROMPT_VERSION, "prompt_hash": orchestrator.PROMPT_HASH,
        "schema_version": orchestrator.SCHEMA_VERSION, "proposal_version": 1,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", job)
    monkeypatch.setattr(
        orchestrator,
        "_load_question_context",
        lambda _job: ("Resposta comprovada.", _official_context()),
    )
    real_save = codex_assistant_storage.codex_assistant_customer_reply_job_save

    def reject_final(info_base, client_id, payload, **kwargs):
        if str(payload.get("status") or "") == "completed":
            return {
                **payload,
                "status": "waiting_retry",
                "agent_state": "pesquisando",
                "lease_generation": orchestrator._lease_generation(payload) + 1,
                "proposal_hash": "",
                "result": {},
            }
        return real_save(info_base, client_id, payload, **kwargs)

    monkeypatch.setattr(codex_assistant_storage, "codex_assistant_customer_reply_job_save", reject_final)
    orchestrator._run_job("cliente", job["job_id"])
    assert not any(item.get("current_step") == "aprovar" for item in transitions)


def test_terminal_row_ttl_removes_row_and_ephemeral_payload(tmp_path):
    old = {
        "job_id": "job-old-terminal", "profile": orchestrator.PROFILE,
        "task_type": "public_question", "subject_key": "Q-OLD-TTL", "store": "Loja",
        "status": "completed", "agent_state": "aguardando_aprovacao",
        "result": {"resposta": "CANARY_TTL", "proposal_hash": "hash"},
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", old)
    db_path = codex_assistant_storage.codex_assistant_state_db_path(str(tmp_path), "cliente")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE assistant_customer_reply_jobs SET updated_at = datetime('now', '-8 days') WHERE job_id = ?",
        (old["job_id"],),
    )
    conn.commit()
    conn.close()
    codex_assistant_storage.codex_assistant_customer_reply_job_save(
        str(tmp_path), "cliente",
        {
            "job_id": "job-cleanup-trigger", "profile": orchestrator.PROFILE,
            "task_type": "public_question", "subject_key": "Q-NEW", "store": "Loja",
            "status": "queued", "agent_state": "entendendo",
        },
    )
    assert codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", old["job_id"]
    ) is None
    assert not codex_assistant_storage.codex_assistant_customer_reply_job_has_transient(
        str(tmp_path), "cliente", old["job_id"]
    )
