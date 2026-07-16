from __future__ import annotations

import inspect
import time
from types import SimpleNamespace
from unittest.mock import patch

from backend.schemas.perguntas_pos_venda import PerguntasGerarRespostaRequest, PosVendaGerarRespostaRequest
from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_codex as orchestrator
from backend.services import perguntas_pos_venda_endpoints as endpoints
from ml_questions_gemini.classifier import QuestionClassifier
from ml_questions_gemini.schemas import ListingSnapshot, QuestionCategory, QuestionContext


def _runtime(tmp_path):
    return SimpleNamespace(PASTA_INFO=str(tmp_path))


def _official_context() -> dict:
    return {
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


def test_composite_compatibility_and_shipping_uses_technical_route():
    classifier = QuestionClassifier()
    result = classifier.classify(
        QuestionContext(
            id="Q307K",
            text="Essa peca e do motor turbo correto? Consegue entregar antes da data prevista se eu pagar o frete?",
        ),
        ListingSnapshot(id="MLB4129425225", title="Carcaca Valvula Termostatica THP 1.6"),
    )
    assert result.category == QuestionCategory.COMPATIBILITY
    assert result.reason == "multi_intent_compatibility_shipping"
    subquestions = orchestrator._subquestions(
        "Essa peca e do motor turbo correto? Consegue entregar antes da data prevista se eu pagar o frete?",
        "question",
    )
    assert [item["intent"] for item in subquestions] == ["compatibility", "shipping"]


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


def test_legacy_completed_job_without_evidence_can_be_resumed(tmp_path, monkeypatch):
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

    assert resumed["status"] == "waiting_retry"
    assert resumed["queued"] is True
    assert resumed["retry_policy"] == "bounded"
    assert resumed["retry_count"] == 1
    assert resumed["result"] == {}


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
