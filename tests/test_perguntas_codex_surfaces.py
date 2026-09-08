from __future__ import annotations

import inspect
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from backend.modules.perguntas_pos_venda.ai import inputs as agent_inputs
from backend.modules.perguntas_pos_venda.ai.contracts import PerguntasIARespostaPoliticaInvalida
from backend.services import codex_assistant_storage
from backend.services import perguntas_pos_venda_codex as codex_surface
from backend.services import perguntas_pos_venda_state as perguntas_state
from backend.services.favoritos_ranking_ia import _favoritos_normalizar_sem_acentos


def _structured_intent(
    category: str,
    *,
    web: bool = False,
    mercado_livre: bool = False,
    bling: bool = False,
) -> dict:
    compatibility = category == "compatibility"
    return {
        "intencao": "compatibilidade" if compatibility else "duvida_produto",
        "categoria": category,
        "categorias": [category],
        "fluxo": "perguntas_anuncio",
        "confianca": 0.95,
        "flags": {
            "usar_busca_web": web,
            "usar_mercado_livre_anuncio": mercado_livre,
            "usar_bling": bling,
        },
        "subperguntas": [{
            "intent": category if category in {"compatibility", "product_feature"} else "general",
            "question": "Responder ao ponto classificado pela IA.",
            "required_evidence": "Dados confirmados do anuncio ou fonte tecnica.",
        }],
        "compatibilidade": {
            "aplicavel": compatibility,
            "target_item": "BMW R1300GS" if compatibility else "",
            "target_type": "vehicle" if compatibility else "",
            "compatibility_profile": "vehicle_fitment" if compatibility else "",
            "technical_focus": "interface base conector" if compatibility else "",
            "missing_fields": ["ano", "versao"] if compatibility else [],
            "decisive_fields": ["base original"] if compatibility else [],
        },
    }


def test_public_conversation_scope_uses_item_and_buyer_with_question_fallback():
    subject, verifiers = codex_surface._conversation_subject_key(
        "question",
        "Q-1",
        {"pergunta": {"id": "Q-1", "item_id": "MLB-10", "from": {"id": "B-20"}}},
    )
    assert subject == "item:MLB-10|buyer:B-20"
    assert verifiers == {"question_id": "Q-1", "item_id": "MLB-10", "buyer_id": "B-20"}

    fallback, _ = codex_surface._conversation_subject_key(
        "public_question", "Q-2", {"pergunta": {"id": "Q-2"}}
    )
    assert fallback == "question:Q-2"


def test_post_sale_conversation_scope_is_pack_with_order_and_buyer_verifiers():
    subject, verifiers = codex_surface._conversation_subject_key(
        "post_sale",
        "P-1",
        {"pack_id": "P-1", "order_id": "O-2", "buyer_id": "B-3"},
    )
    assert subject == "pack:P-1"
    assert verifiers == {"pack_id": "P-1", "order_id": "O-2", "buyer_id": "B-3"}


def test_thread_reuse_requires_current_prompt_schema_hash_and_scope():
    latest = {
        "thread_id": "thread-1",
        "prompt_version": codex_surface.PROMPT_VERSION,
        "prompt_hash": codex_surface.PROMPT_HASH,
        "schema_version": codex_surface.SCHEMA_VERSION,
        "updated_at": datetime.now().isoformat(),
        "scope_verifiers": {"pack_id": "P-1", "order_id": "O-1", "buyer_id": "B-1"},
    }
    assert codex_surface._thread_reuse_decision(
        latest,
        task_type="post_sale",
        scope_verifiers={"pack_id": "P-1", "order_id": "O-1", "buyer_id": "B-1"},
    ) == ("thread-1", "", True)

    changed = dict(latest, schema_version="1.0")
    assert codex_surface._thread_reuse_decision(
        changed,
        task_type="post_sale",
        scope_verifiers={"pack_id": "P-1", "order_id": "O-1", "buyer_id": "B-1"},
    )[1:] == ("schema_version_changed", False)

    assert codex_surface._thread_reuse_decision(
        latest,
        task_type="post_sale",
        scope_verifiers={"pack_id": "P-1", "order_id": "O-9", "buyer_id": "B-1"},
    )[1:] == ("order_id_changed", False)


def test_new_public_event_reuses_item_buyer_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_surface, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(codex_surface, "_RECOVERY_STARTED", True)
    request_one = {
        "pergunta": {"id": "Q-1", "item_id": "MLB-1", "from": {"id": "B-1"}, "text": "Serve?"}
    }
    with patch.object(codex_surface, "_schedule", return_value=True), patch.object(
        codex_surface.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        first = codex_surface.create_job(
            client_id="cliente",
            task_type="question",
            store="Loja",
            subject_key="Q-1",
            request=request_one,
        )
    stored = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        str(tmp_path), "cliente", first["job_id"]
    )
    stored.update({"status": "completed", "thread_id": "thread-item-buyer", "updated_at": datetime.now().isoformat()})
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "cliente", stored)

    request_two = {
        "pergunta": {"id": "Q-2", "item_id": "MLB-1", "from": {"id": "B-1"}, "text": "E a medida?"}
    }
    with patch.object(codex_surface, "_schedule", return_value=True), patch.object(
        codex_surface.codex_agent_runtime, "resolve_guidance", return_value=[]
    ):
        second = codex_surface.create_job(
            client_id="cliente",
            task_type="question",
            store="Loja",
            subject_key="Q-2",
            request=request_two,
        )
    assert second["task_type"] == "public_question"
    assert second["thread_id"] == "thread-item-buyer"
    assert second["thread_reused"] is True
    assert second["subject_key"] == "Q-2"
    assert second["conversation_subject_key"] == "item:MLB-1|buyer:B-1"


def test_provider_policy_is_codex_only_until_two_operational_failures(monkeypatch):
    monkeypatch.delenv("JK_PPV_RESPONSE_PROVIDER_POLICY", raising=False)
    selected = agent_inputs._perguntas_codex_provider_selection("vertex:gemini-2.5-flash", 20)
    assert selected["policy"] == "codex_only"
    assert selected["model"].startswith("codex:")
    assert selected["fallback_used"] is False

    monkeypatch.setenv("JK_PPV_RESPONSE_PROVIDER_POLICY", "codex_then_configured_fallback")
    assert agent_inputs._perguntas_codex_provider_selection("vertex:gemini-2.5-flash", 1)["model"].startswith("codex:")
    fallback = agent_inputs._perguntas_codex_provider_selection("vertex:gemini-2.5-flash", 2)
    assert fallback["model"].startswith("vertex:")
    assert fallback["fallback_used"] is True


def test_legacy_cloud_agent_preserves_nonempty_reply_previously_classified_as_fallback(monkeypatch):
    reply = "Não foi possível gerar a resposta completa agora."
    monkeypatch.setattr(
        perguntas_state,
        "_favoritos_normalizar_sem_acentos",
        _favoritos_normalizar_sem_acentos,
        raising=False,
    )
    assert perguntas_state._perguntas_ia_resposta_fallback_invalida(reply) is True

    with patch.object(agent_inputs, "_perguntas_ia_agent_input", return_value={}), patch.object(
        agent_inputs, "_ia_agent_endpoint_url_configurado", return_value="https://agent.example"
    ), patch.object(agent_inputs, "_ia_agent_endpoint_headers", return_value={}), patch.object(
        agent_inputs, "_ia_agent_http_post", return_value={"output": reply}
    ):
        response, origin = agent_inputs._perguntas_ia_chamar_agente_cloud(
            "cliente", "Loja", {}, {}, {}, "prompt"
        )

    assert response == reply
    assert origin == "agent:endpoint"


def test_only_operational_errors_unlock_provider_fallback():
    class HttpStatusError(RuntimeError):
        status_code = 503

    class NonHttpStatusError(RuntimeError):
        status_code = 600

    assert codex_surface._is_operational_failure(TimeoutError("Codex timed out")) is True
    assert codex_surface._is_operational_failure(ConnectionError("connection lost")) is True
    assert codex_surface._is_operational_failure(
        perguntas_state.PerguntasIAProviderIndisponivel(
            "Provedor indisponivel.",
            reason="provider_http_429",
        )
    ) is True
    assert codex_surface._is_operational_failure(
        perguntas_state.PerguntasIAProviderIndisponivel("Motivo generico.")
    ) is False
    assert codex_surface._is_operational_failure(RuntimeError("provider unavailable")) is False
    assert codex_surface._is_operational_failure(RuntimeError("HTTP 429")) is False
    assert codex_surface._is_operational_failure(HttpStatusError("sem texto confiavel")) is True
    assert codex_surface._is_operational_failure(NonHttpStatusError("fora da faixa HTTP")) is False
    assert codex_surface._is_operational_failure(ValueError("invalid response contract")) is False
    assert codex_surface._is_operational_failure(OSError("local context file is invalid")) is False
    assert codex_surface._is_operational_failure(
        perguntas_state.PerguntasIAClassificacaoInconclusiva("nao_entendi")
    ) is False
    policy_failure = PerguntasIARespostaPoliticaInvalida(
        "Nova IA de perguntas gerou resposta fora das orientacoes",
        ["seller_style_non_direct_opening"],
    )
    assert codex_surface._is_response_policy_failure(policy_failure) is True
    assert codex_surface._is_operational_failure(policy_failure) is False


def test_retry_persists_operational_failure_count(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_surface, "_RUNTIME", SimpleNamespace(PASTA_INFO=str(tmp_path)))
    monkeypatch.setattr(codex_surface, "_schedule_retry_timer", lambda _job: None)
    stored = {
        "job_id": "job-operational-count",
        "client_id": "tenant",
        "store": "Loja",
        "status": "running",
        "attempt_count": 1,
        "operational_failure_count": 0,
        "deadline_at_epoch": time.time() + 300,
    }
    codex_assistant_storage.codex_assistant_customer_reply_job_save(str(tmp_path), "tenant", stored)
    retry_input = {**stored, "operational_failure_count": 1}

    saved = codex_surface._persist_retry(retry_input, error="Codex timed out", immediate=True)

    assert saved["operational_failure_count"] == 1


def test_public_web_is_deferred_to_integral_envelope_except_mandatory_categories():
    with patch.object(agent_inputs, "_perguntas_ia_legacy_guidance_metadata", return_value=(False, "")):
        technical_text_without_ai_web = agent_inputs._perguntas_ia_agent_input(
            "cliente",
            "Loja",
            {"id": "Q1", "text": "Qual o conector e a medida da rosca?"},
            {"id": "MLB1", "title": "Produto"},
            {"intencao_atendimento": _structured_intent("product_feature", web=False)},
            "",
        )
        simple_text_with_ai_web = agent_inputs._perguntas_ia_agent_input(
            "cliente",
            "Loja",
            {"id": "Q2", "text": "Tem pronta entrega?"},
            {"id": "MLB1", "title": "Produto"},
            {"intencao_atendimento": _structured_intent("product_feature", web=True)},
            "",
        )
        compatibility_without_flag = agent_inputs._perguntas_ia_agent_input(
            "cliente",
            "Loja",
            {"id": "Q3", "text": "Serve?"},
            {"id": "MLB1", "title": "Produto"},
            {"intencao_atendimento": _structured_intent("compatibility", web=False)},
            "",
        )
    assert technical_text_without_ai_web["use_web_search"] is False
    assert "web_search" not in technical_text_without_ai_web["allowed_tools"]
    assert simple_text_with_ai_web["use_web_search"] is False
    assert "web_search" not in simple_text_with_ai_web["allowed_tools"]
    assert compatibility_without_flag["use_web_search"] is True
    assert "web_search" in compatibility_without_flag["allowed_tools"]


def test_structural_compaction_never_slices_json():
    compacted = agent_inputs._perguntas_codex_compact_json(
        {"question": "x" * 10_000, "history": [{"text": "y" * 2000}] * 40},
        900,
    )
    decoded = json.loads(compacted)
    assert isinstance(decoded, dict)
    assert len(compacted.encode("utf-8")) <= 900


def test_evidence_matrix_does_not_confirm_intent_without_matching_coverage():
    envelope = codex_surface._evidence_envelope(
        "public_question",
        store="Loja",
        context={"pergunta": {"text": "Chega amanha?"}, "item": {"id": "MLB1"}},
    )
    matrix, sufficient, _ = codex_surface._evidence_matrix(
        [{"id": "s1", "intent": "shipping", "status": "pending"}],
        answer="A entrega segue o prazo informado.",
        context={},
        envelope=envelope,
    )
    assert matrix[0]["status"] == "partial"
    assert sufficient is False

    misleading = {
        "schema_version": "evidence-envelope-v2",
        "status": "completed",
        "records": [
            {"field": "pergunta", "value": "Tem garantia?", "coverage": "confirmed"},
            {"field": "pedido", "value": {"id": "O1"}, "coverage": "confirmed"},
        ],
        "evidence_sufficient": True,
        "coverage_complete": True,
    }
    matrix, sufficient, _ = codex_surface._evidence_matrix(
        [
            {"id": "s1", "intent": "warranty_originality"},
            {"id": "s2", "intent": "shipping"},
            {"id": "s3", "intent": "invoice"},
        ],
        answer="Tem garantia e o envio esta confirmado.",
        context={},
        envelope=misleading,
    )
    assert [item["status"] for item in matrix] == ["partial", "partial", "partial"]
    assert sufficient is False

    exact = {
        **misleading,
        "records": [
            {"field": "garantia_anuncio", "value": "12 meses", "coverage": "confirmed"},
            {"field": "envio", "value": {"status": "ready"}, "coverage": "confirmed"},
            {"field": "nota_fiscal", "value": {"available": True}, "coverage": "confirmed"},
        ],
    }
    matrix, sufficient, _ = codex_surface._evidence_matrix(
        [
            {"id": "s1", "intent": "warranty_originality"},
            {"id": "s2", "intent": "shipping"},
            {"id": "s3", "intent": "invoice"},
        ],
        answer="Cobertura confirmada pelas fontes.",
        context={"diagnostico_ia": [{"result": {"validation_ok": True}}]},
        envelope=exact,
    )
    assert [item["status"] for item in matrix] == ["confirmed", "confirmed", "confirmed"]
    assert sufficient is True


def test_compatibility_evidence_never_bypasses_failed_answer_validation():
    context = {
        "diagnostico_ia": [{
            "result": {
                "validation_ok": False,
                "validation_issues": ["too_many_sentences"],
                "compatibility_analysis": {
                    "decision": "yes",
                    "confidence": 0.95,
                    "sources": ["https://fabricante.example/manual"],
                    "evidence": {
                        "product": [{"authority": "official_document"}],
                        "target_vehicle": [{"authority": "official_document"}],
                    },
                },
            },
        }],
    }

    matrix, sufficient, warnings = codex_surface._evidence_matrix(
        [{"id": "s1", "intent": "compatibility"}],
        answer="Resposta rejeitada pelo validador.",
        context=context,
    )

    assert matrix[0]["status"] == "partial"
    assert sufficient is False
    assert "too_many_sentences" in warnings


def test_unreviewed_public_context_never_confirms_canonical_ai_intents_by_presence_only():
    context = {
        "item": {
            "id": "MLB1",
            "description": "Corpo em aluminio e conexao de 10 mm.",
            "shipping": {"free_shipping": True, "logistic_type": "cross_docking"},
            "sale_terms": [{"id": "INVOICE", "name": "Nota fiscal", "value_name": "Emitida"}],
        },
        "busca_outra_peca": {"query": "sensor abs", "anuncios_ativos_ml": []},
        "diagnostico_ia": [{"result": {"validation_ok": True, "confidence": 0.9}}],
    }
    envelope = codex_surface._evidence_envelope(
        "public_question", store="Loja", context=context
    )

    matrix, sufficient, _warnings = codex_surface._evidence_matrix(
        [
            {"id": "s1", "intent": "shipping"},
            {"id": "s2", "intent": "invoice"},
            {"id": "s3", "intent": "product_feature"},
            {"id": "s4", "intent": "other_product"},
        ],
        answer="Resposta validada e sustentada pelos dados consultados.",
        context=context,
        envelope=envelope,
    )

    assert [item["status"] for item in matrix] == ["partial"] * 4
    assert sufficient is False


def test_evidence_matrix_requires_explicit_positive_validation():
    envelope = {
        "schema_version": "evidence-envelope-v2",
        "status": "completed",
        "records": [{
            "field": "envio",
            "value": {"estimated_delivery": "amanha"},
            "coverage": "confirmed",
        }],
        "evidence_sufficient": True,
        "coverage_complete": True,
    }

    for validation_value in (None, "", 0):
        context = {"diagnostico_ia": [{"result": {"validation_ok": validation_value}}]}
        matrix, sufficient, _warnings = codex_surface._evidence_matrix(
            [{"id": "s1", "intent": "shipping"}],
            answer="Chega amanha.",
            context=context,
            envelope=envelope,
        )
        assert matrix[0]["status"] == "partial"
        assert sufficient is False


def test_empty_other_product_search_is_not_confirmed():
    envelope = {
        "schema_version": "evidence-envelope-v2",
        "status": "completed",
        "records": [{
            "field": "other_product_search",
            "value": {"query": "sensor abs", "cadastro": [], "anuncios_ativos_ml": []},
            "coverage": "confirmed",
        }],
        "evidence_sufficient": True,
        "coverage_complete": True,
    }
    context = {"diagnostico_ia": [{"result": {"validation_ok": True}}]}

    matrix, sufficient, _warnings = codex_surface._evidence_matrix(
        [{"id": "s1", "intent": "other_product"}],
        answer="Nao localizei.",
        context=context,
        envelope=envelope,
    )

    assert matrix[0]["status"] == "partial"
    assert sufficient is False


def test_normal_flows_do_not_read_or_write_variable_sku_memory():
    from backend.services import perguntas_pos_venda_perguntas_ml, perguntas_pos_venda_pos_venda

    with patch.dict("os.environ", {}, clear=False):
        assert agent_inputs._perguntas_ia_legacy_sku_memory_reader_enabled() is False
    pipeline_source = inspect.getsource(perguntas_pos_venda_perguntas_ml._ml_pos_venda_montar_contexto_pipeline)
    generator_source = inspect.getsource(perguntas_pos_venda_pos_venda._ml_pos_venda_gerar_resposta_ia)
    assert 'contexto["memoria_sku"] = ""' in pipeline_source
    assert "_ml_pos_venda_memoria_registrar_geracao" not in generator_source
