from __future__ import annotations

import json
import inspect
from types import SimpleNamespace

import pytest

from backend.modules.perguntas_pos_venda.ai import clients
from backend.modules.perguntas_pos_venda.ai import execution
from backend.modules.perguntas_pos_venda.ai.model_context import (
    compact_server_identity_for_model,
    unified_agent_input_for_model,
    unified_initial_context_for_model,
)
from backend.modules.perguntas_pos_venda.ai import unified_presale
from backend.modules.perguntas_pos_venda.ai.unified_response_agent import (
    UnifiedResponseAgentOperationalError,
    run_unified_response_agent,
)
from backend.services import perguntas_pos_venda_perguntas_ml as perguntas_ml
from backend.services.perguntas_pos_venda_state import PerguntasIARespostaIndisponivel
from ml_questions_gemini.schemas import PublishDecision, QuestionCategory, RouteAction


def _context() -> dict:
    return {
        "client_id": "tenant-2",
        "loja": "Loja Exata",
        "model_req": "codex:model-test",
        "reasoning": "medium",
        "flow": "pre_sale",
        "post_sale": False,
        "agent_input": {
            "_unified_response_flow": "pre_sale",
            "task": "mercado_livre_public_question_draft",
            "store": "Loja Exata",
            "store_id": "store-9",
            "seller_id": "seller-8",
            "site_id": "MLB",
            "question": {"id": "q-1", "text": "Pode ficar ligada?", "item_id": "MLB1"},
            "item": {"id": "MLB1", "seller_sku": "SKU-7", "title": "Bomba"},
            "context": {"sku": "SKU-7", "variation_id": "var-6"},
        },
        "settings": SimpleNamespace(
            auto_publish_enabled=False,
            min_confidence=0.78,
            max_chars=0,
            max_sentences=0,
            whitelisted_domains=[],
        ),
        "diagnostics": [{"function": "test", "result": {}}],
    }


def _typed_input(*_args, **_kwargs):
    return (
        SimpleNamespace(id="q-1", item_id="MLB1"),
        SimpleNamespace(id="MLB1", title="Bomba"),
        [SimpleNamespace(question="anterior")],
        SimpleNamespace(),
    )


def test_pending_legacy_intent_does_not_run_or_gate_on_a_separate_classifier(monkeypatch) -> None:
    monkeypatch.setattr(
        execution,
        "resolve_runtime_adapter",
        lambda group, key, default: (
            (lambda: "codex:model-test")
            if (group, key) == ("models", "public_model")
            else (lambda: "medium")
            if (group, key) == ("models", "public_reasoning")
            else default
        ),
    )
    monkeypatch.setattr(execution, "GeminiQuestionsSettings", SimpleNamespace(
        from_env=lambda: SimpleNamespace(
            auto_publish_enabled=False,
            min_confidence=0.78,
            max_chars=900,
            max_sentences=3,
            whitelisted_domains=[],
            model="codex:model-test",
        ),
    ))
    agent_input = {
        "task": "mercado_livre_public_question_draft",
        "store": "Loja Exata",
        "intent": {"status": "pending_unified_agent"},
        "question": {"id": "q-1", "text": "Pode ficar ligada?"},
    }

    configured = execution._perguntas_ia_execucao_configurar("tenant-2", agent_input, 1.0)

    assert configured["flow"] == "pre_sale"
    assert agent_input["intent"] == {"status": "pending_unified_agent"}
    assert agent_input["_unified_response_flow"] == "pre_sale"


def test_model_projection_removes_duplicate_representations_without_losing_answer_facts() -> None:
    signature = "A equipe Loja Exata agradece o contato."
    agent_input = {
        "tenant_id": "tenant-2",
        "store": "Loja Exata",
        "prompt": "PROMPT LEGADO COM DESCRICAO REPETIDA",
        "app_guidance": "POLITICA REPETIDA",
        "app_guidance_source": "policy-v1",
        "app_guidance_truth_class": "versioned_technical",
        "app_guidance_usage": "published_behavior_policy_not_product_evidence",
        "commercial_method_version": "seller-v1",
        "task": "mercado_livre_public_question_draft",
        "orchestrator_profile": "mercado_livre_customer_reply",
        "locale": "pt-BR",
        "store_id": "store-9",
        "seller_id": "seller-8",
        "site_id": "MLB",
        "order_id": "order-4",
        "legacy_guidance_available": True,
        "legacy_guidance_hash": "legacy-hash",
        "legacy_fallback_enabled": True,
        "legacy_fallback_used": False,
        "legacy_retirement_zero_use_days": 30,
        "context_collection_pipeline": [{"step": 1}],
        "allowed_tools": ["context_hub_search"],
        "tool_policy": {"usar_context_hub": True},
        "constraints": {"read_only": True},
        "use_web_search": True,
        "web_search_required": True,
        "force_external_research": True,
        "research_gap_only": True,
        "research_attempt": 2,
        "product_evidence_identity": {"sku": "SKU-7", "item_id": "MLB1"},
        "_catalog_identity_proof": "catalog-proof",
        "_codex_thread_id": "thread-1",
        "_codex_job_id": "job-1",
        "_codex_conversation_key": "conversation-1",
        "_codex_active_turn_key": "turn-1",
        "_codex_on_thread_ready": "callback",
        "_codex_operational_failure_count": 1,
        "_codex_prompt_version": "prompt-v1",
        "_codex_schema_version": "schema-v1",
        "_research_session_key": "research-1",
        "intent": {"fluxo": "pre_sale"},
        "question": {
            "id": "q-1",
            "item_id": "MLB1",
            "text": "Tem outra cor?",
            "status": "UNANSWERED",
            "buyer_id": "buyer-1",
            "buyer_name": "Nome removido",
            "history": [{"role": "buyer", "text": "Quero a maior."}],
            "history_count": 1,
            "history_source": "Mercado Livre",
        },
        "item": {
            "id": "MLB1",
            "title": "Produto Exato",
            "seller_sku": "SKU-7",
            "description": "DESCRICAO OFICIAL",
            "thumbnail": "https://example.invalid/image.jpg",
            "pictures_count": 3,
            "site_id": "MLB",
            "listing_type_id": "gold_special",
            "buying_mode": "buy_it_now",
            "permalink": "https://produto.mercadolivre.com.br/MLB1",
            "link": "https://produto.mercadolivre.com.br/MLB1",
            "url": "https://produto.mercadolivre.com.br/MLB1",
            "variations": [{"id": "var-6", "seller_sku": "SKU-7-A", "available_quantity": 4}],
        },
        "context": {
            "tenant_id": "tenant-2",
            "loja": "Loja Exata",
            "store_id": "store-9",
            "seller_id": "seller-8",
            "site_id": "MLB",
            "question_id": "q-1",
            "item_id": "MLB1",
            "variation_id": "var-6",
            "order_id": "",
            "titulo": "Produto Exato",
            "sku": "SKU-7",
            "permalink": "https://produto.mercadolivre.com.br/MLB1",
            "descricao": "DESCRICAO OFICIAL",
            "descricao_chars": 17,
            "descricao_disponivel": True,
            "pergunta": "Tem outra cor?",
            "assinatura_obrigatoria": signature,
            "intencao_atendimento": {"fluxo": "pre_sale"},
            "busca_outra_peca": {},
            "contexto_exclusivo": "MANTER ESTE FATO",
        },
        "seller_behavior_profile": {"orientacao_obsidian": "MANTER ORIENTACAO"},
        "verified_product_evidence": [{"field_name": "material", "value": "aco"}],
    }
    identity = {
        "tenant_id": "tenant-2",
        "store": "Loja Exata",
        "store_id": "store-9",
        "seller_id": "seller-8",
        "site_id": "MLB",
        "sku": "SKU-7",
        "item_id": "MLB1",
        "variation_id": "var-6",
        "order_id": "order-4",
    }

    projected = unified_agent_input_for_model(
        agent_input,
        server_identity=identity,
        response_signature=signature,
    )

    for duplicate in (
        "prompt", "app_guidance", "app_guidance_source", "app_guidance_truth_class",
        "app_guidance_usage", "commercial_method_version", "tenant_id", "store",
        "store_id", "seller_id", "site_id", "order_id", "task",
        "orchestrator_profile", "locale", "legacy_guidance_available",
        "legacy_guidance_hash", "legacy_fallback_enabled", "legacy_fallback_used",
        "legacy_retirement_zero_use_days", "context_collection_pipeline",
        "allowed_tools", "tool_policy", "constraints", "use_web_search",
        "web_search_required", "force_external_research", "research_gap_only",
        "research_attempt", "product_evidence_identity", "_catalog_identity_proof",
        "_codex_thread_id", "_codex_job_id", "_codex_conversation_key",
        "_codex_active_turn_key", "_codex_on_thread_ready",
        "_codex_operational_failure_count", "_codex_prompt_version",
        "_codex_schema_version", "_research_session_key",
    ):
        assert duplicate not in projected
    assert projected["question"] == {
        "text": "Tem outra cor?",
        "history": [{"role": "buyer", "text": "Quero a maior."}],
    }
    assert projected["item"]["description"] == "DESCRICAO OFICIAL"
    assert projected["item"]["variations"] == [{
        "seller_sku": "SKU-7-A", "available_quantity": 4,
    }]
    for removed_item_key in (
        "id", "seller_sku", "sku", "variation_id", "thumbnail",
        "pictures_count", "site_id", "listing_type_id", "buying_mode",
    ):
        assert removed_item_key not in projected["item"]
    assert "link" not in projected["item"] and "url" not in projected["item"]
    assert projected["context"] == {"contexto_exclusivo": "MANTER ESTE FATO"}
    assert projected["seller_behavior_profile"]["orientacao_obsidian"] == "MANTER ORIENTACAO"
    assert projected["verified_product_evidence"][0]["value"] == "aco"
    assert agent_input["prompt"] == "PROMPT LEGADO COM DESCRICAO REPETIDA"


def test_context_hub_projection_removes_control_metadata_and_keeps_obsidian_documents() -> None:
    initial_context = {"sku_question_context": {
        "schema": "schema-v1",
        "policy": "policy-v1",
        "route": "simple_factual",
        "route_reasons": ["canonical_evidence_sufficient"],
        "category": "product_feature",
        "question": {"text": "Pergunta duplicada"},
        "subquestions": [{"question": "Subpergunta duplicada"}],
        "identity": {"sku": "SKU-7", "item_id": "MLB1", "variation_id": "var-6"},
        "canonical_document": {
            "id": "OBSIDIAN-ID-PRESERVED",
            "seller_id": "OBSIDIAN-SELLER-PRESERVED",
            "fact": "FATO CANONICO PRESERVADO",
        },
        "catalog_document": {
            "sku": "OBSIDIAN-SKU-PRESERVED",
            "fact": "FATO DE CATALOGO PRESERVADO",
        },
        "catalog_generation": {"id": "catalog-1", "revision": 2},
        "guidance": {
            "general": {"text": "ORIENTACAO GERAL PRESERVADA"},
            "sku": {"text": "ORIENTACAO SKU PRESERVADA"},
        },
        "listing_facts": {
            "identity": {"sku": "SKU-7", "item_id": "MLB1"},
            "title": "Produto",
            "selected_variation": {
                "id": "var-6", "seller_sku": "SKU-7", "available_quantity": 4,
            },
        },
        "operational_data": [{
            "source": "get_mercado_livre_listing",
            "matches": [{
                "item_id": "MLB1",
                "seller_sku": "SKU-7",
                "available_quantity": 4,
                "variations": [{
                    "id": "var-6", "seller_sku": "SKU-7", "available_quantity": 4,
                }],
            }],
        }],
        "generation": {"id": "generation-1", "hash": "hash-1", "version": 3},
        "source_hashes": {"canonical": "hash-2"},
        "validity": {"status": "active_approved_generation"},
        "integral_core_hash": "hash-3",
        "binding_hash": "hash-4",
        "conflicts": [{"field": "material"}],
        "gaps": ["decisive_fact_missing"],
        "web": {"required": True, "reason": "decisive_fact_missing"},
        "content_role": "untrusted_reference_data",
        "instruction_policy": "controle interno",
        "response_signature": "Assinatura duplicada",
    }}

    projected = unified_initial_context_for_model(initial_context)
    packet = projected["sku_question_context"]

    for control_key in (
        "schema", "policy", "route", "route_reasons", "category", "question",
        "subquestions", "identity", "catalog_generation", "generation",
        "source_hashes", "integral_core_hash", "binding_hash", "content_role",
        "instruction_policy", "response_signature", "web",
    ):
        assert control_key not in packet
    assert packet["canonical_document"] == initial_context["sku_question_context"]["canonical_document"]
    assert packet["catalog_document"] == initial_context["sku_question_context"]["catalog_document"]
    assert packet["guidance"] == initial_context["sku_question_context"]["guidance"]
    assert packet["validity"] == {"status": "active_approved_generation"}
    assert packet["conflicts"] == [{"field": "material"}]
    assert packet["gaps"] == ["decisive_fact_missing"]
    assert packet["listing_facts"]["selected_variation"] == {"available_quantity": 4}
    assert packet["operational_data"][0]["matches"][0] == {
        "available_quantity": 4,
        "variations": [{"available_quantity": 4}],
    }
    assert initial_context["sku_question_context"]["identity"]["sku"] == "SKU-7"


def test_compact_model_identity_contains_only_sku_item_and_variation() -> None:
    assert compact_server_identity_for_model({
        "tenant_id": "tenant-2",
        "store": "Loja Exata",
        "store_id": "store-9",
        "seller_id": "seller-8",
        "site_id": "MLB",
        "sku": "SKU-7",
        "item_id": "MLB1",
        "variation_id": "var-6",
        "order_id": "order-4",
    }) == {"sku": "SKU-7", "item_id": "MLB1", "variation_id": "var-6"}


def test_presale_execution_uses_one_unified_agent_and_preserves_server_scope(monkeypatch) -> None:
    captured = {}

    class Client:
        def __init__(self, client_id, store, model, agent_input, **_kwargs):
            self.client_id = client_id
            self.loja = store
            self.model_usado = model
            self.agent_input = agent_input
            self.codex_thread_id = "thread-one"
            self.context_pipeline = []
            self.compatibility_analysis = {}
            self.commercial_state = ""
            self.manual_review_required = False
            self.unified_research_rounds = 1
            self.evidence_records = []

        def collect_unified_initial_context(self, metadata):
            captured["metadata"] = metadata
            return {"sku_question_context": {
                "schema": "internal-schema",
                "route": "simple_factual",
                "identity": {"sku": "SKU-7"},
                "canonical_document": {"obsidian_fact": "CANONICAL-PRESERVED"},
                "catalog_document": {"obsidian_catalog": "CATALOG-PRESERVED"},
                "guidance": {
                    "general": {"text": "GENERAL-PRESERVED"},
                    "sku": {"text": "SKU-GUIDANCE-PRESERVED"},
                },
            }}

        def invoke_unified_turn(self, *_args, **_kwargs):
            raise AssertionError("runner fake owns the result")

        def execute_unified_research(self, *_args, **_kwargs):
            raise AssertionError("runner fake owns the result")

    def run_unified(**kwargs):
        captured.update(kwargs)
        return {
            "action": "answer",
            "flow": "pre_sale",
            "category": "product_feature",
            "subquestions": ["ciclo de trabalho"],
            "research_requests": [],
            "answer": "Resposta comprovada. Assinatura exata.",
            "confidence": 0.91,
            "reason": "technical_source_confirmed",
            "requires_human_review": False,
            "decision": "yes",
            "commercial_state": "fits",
            "compatibility_analysis": {
                "applicable": False,
                "target": "",
                "decision": "not_applicable",
                "condition": "",
                "missing_fields": [],
                "evidence_refs": [],
            },
            "missing_fact_owner": "none",
            "buyer_detail_needed": "",
            "evidence_basis": "manufacturer_model",
            "evidence_refs": ["manual:model-exato"],
        }

    monkeypatch.setattr(execution, "_PerguntasCodexV3Client", Client)
    monkeypatch.setattr(execution, "context_from_agent_input", _typed_input)
    monkeypatch.setattr(execution, "run_unified_response_agent", run_unified)
    monkeypatch.setattr(execution, "_perguntas_ia_assinatura_loja", lambda _store: "Assinatura exata.")

    result, answer, model, client, _started = execution._perguntas_ia_execucao_orquestrar(_context())

    assert answer == "Resposta comprovada. Assinatura exata."
    assert model == "codex:model-test"
    assert result.category is QuestionCategory.PRODUCT_FEATURE
    assert result.route is RouteAction.SEARCH_AND_AI
    assert result.decision is PublishDecision.HUMAN_REVIEW
    assert result.needs_human is True
    assert result.source == "unified_response_agent"
    assert captured["flow"] == "pre_sale"
    assert captured["max_research_rounds"] == 2
    assert captured["context"]["response_signature"] == "Assinatura exata."
    assert captured["context"]["server_identity"] == {
        "sku": "SKU-7",
        "item_id": "MLB1",
        "variation_id": "var-6",
    }
    assert "response_policy_version" not in captured["context"]
    assert "seller_method_version" not in captured["context"]
    assert "approval_required" not in captured["context"]
    sku_context = captured["context"]["initial_read_only_context"]["sku_question_context"]
    assert "schema" not in sku_context
    assert "route" not in sku_context
    assert "identity" not in sku_context
    assert sku_context["canonical_document"] == {"obsidian_fact": "CANONICAL-PRESERVED"}
    assert sku_context["catalog_document"] == {"obsidian_catalog": "CATALOG-PRESERVED"}
    assert sku_context["guidance"]["general"] == {"text": "GENERAL-PRESERVED"}
    assert sku_context["guidance"]["sku"] == {"text": "SKU-GUIDANCE-PRESERVED"}
    assert captured["invoke_turn"] == client.invoke_unified_turn
    assert captured["execute_research"] == client.execute_unified_research

    diagnostic_context = _context()
    monkeypatch.setattr(execution, "_ia_agent_perguntas_log_perf", lambda *_a, **_k: None)
    execution._perguntas_ia_atualizar_diagnostico(
        diagnostic_context,
        result,
        answer,
        model,
        client,
        0.0,
    )
    diagnostics = diagnostic_context["diagnostics"][0]["result"]
    assert diagnostics["flow"] == "pre_sale"
    assert diagnostics["decision"] == "yes"
    assert diagnostics["publication_decision"] == "human_review"
    assert diagnostics["commercial_state"] == "fits"
    assert diagnostics["missing_fact_owner"] == "none"
    assert diagnostics["requires_human_review"] is False


def test_active_presale_orchestrator_has_no_legacy_graph_writer_or_critic() -> None:
    source = inspect.getsource(execution._perguntas_ia_execucao_orquestrar)

    assert "run_unified_response_agent" in source
    assert "QuestionAnswerOrchestrator" not in source
    assert "technical_question_plan" not in source
    assert "technical_evidence_graph" not in source
    assert "technical_resolution" not in source
    assert "factual_critic" not in source
    assert "factual_revision" not in source

    service_source = inspect.getsource(perguntas_ml._perguntas_ia_gerar_resposta)
    assert "_perguntas_ia_classificar_intencao" not in service_source
    assert service_source.count("generate_response(") == 1


def test_invalid_unified_output_becomes_existing_operational_error(monkeypatch) -> None:
    class Client:
        def __init__(self, *_args, **_kwargs):
            self.model_usado = "codex:model-test"
            self.context_pipeline = []
            self.compatibility_analysis = {}
            self.manual_review_required = False
            self.unified_research_rounds = 0
            self.evidence_records = []

        def collect_unified_initial_context(self, _metadata):
            return {}

        def invoke_unified_turn(self, *_args, **_kwargs):
            return {}

        def execute_unified_research(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(execution, "_PerguntasCodexV3Client", Client)
    monkeypatch.setattr(execution, "context_from_agent_input", _typed_input)
    monkeypatch.setattr(
        execution,
        "run_unified_response_agent",
        lambda **_kwargs: (_ for _ in ()).throw(
            UnifiedResponseAgentOperationalError("invalid_output", "bad contract")
        ),
    )

    with pytest.raises(PerguntasIARespostaIndisponivel, match="invalid_output") as raised:
        execution._perguntas_ia_execucao_orquestrar(_context())
    assert raised.value.unified_error_code == "invalid_output"
    assert raised.value.unified_error_round == 0
    assert raised.value.unified_repair_type == "structured_output"


def test_client_unified_turn_updates_state_without_factual_review(monkeypatch) -> None:
    monkeypatch.setattr(clients, "_ia_raciocinio_perguntas_configurado", lambda: "medium")
    client = clients._PerguntasCodexV3Client(
        "tenant-2",
        "Loja Exata",
        "codex:model-test",
        {"_unified_response_flow": "pre_sale"},
    )
    calls = []
    payload = {
        "action": "research",
        "category": "compatibility",
        "subquestions": ["aplicacao"],
        "commercial_state": "partial",
        "compatibility_analysis": {
            "applicable": True,
            "target": "alvo",
            "decision": "conditional",
            "condition": "codigo exato",
            "missing_fields": ["codigo"],
            "evidence_refs": [],
        },
    }

    def call(_prompt, metadata, *, stage, tool_results, isolated=False):
        calls.append((stage, metadata, tool_results, isolated))
        client.codex_thread_id = "same-thread"
        return payload

    monkeypatch.setattr(client, "_call_structured_model", call)

    assert client.invoke_unified_turn("prompt", [], False) is payload
    assert calls[0][0] == "unified_response_agent"
    assert calls[0][3] is False
    assert client.agent_input["intent"]["categoria"] == "compatibility"
    assert client.commercial_state == "partial"
    assert client.compatibility_analysis["decision"] == "conditional"
    assert all(step[0] not in {"factual_critic", "factual_revision"} for step in calls)


def test_presale_research_denies_post_sale_sources_and_uses_bound_alternative(monkeypatch) -> None:
    monkeypatch.setattr(clients, "_ia_raciocinio_perguntas_configurado", lambda: "medium")
    client = clients._PerguntasCodexV3Client(
        "tenant-2",
        "Loja Exata",
        "codex:model-test",
        {
            "_unified_response_flow": "pre_sale",
            "item": {"id": "MLB1", "seller_sku": "SKU-7"},
            "context": {"sku": "SKU-7"},
        },
    )
    client.sku_question_context = {"identity": {"sku": "SKU-7"}}
    client._sku_context_internal_sources = []
    client._sku_context_hub = {}
    client.compatibility_analysis = {"decision": "no", "target": "alvo"}
    captured = {}

    monkeypatch.setattr(unified_presale, "bind_client_sku_question_context", lambda *_a, **_k: {})

    def alternative(client_id, store, agent_input, analysis):
        captured.update(
            client_id=client_id,
            store=store,
            agent_input=agent_input,
            analysis=analysis,
        )
        return {"function": "find_same_store_compatible_alternative", "result": {"found": False}}

    monkeypatch.setattr(clients, "_find_same_store_compatible_alternative", alternative)
    results = client.execute_unified_research(
        [
            {"type": "same_store_listing", "query": "ignorado", "purpose": "alternativa", "preferred_authority": "store"},
            {"type": "order", "query": "pedido alheio", "purpose": "pedido", "preferred_authority": "api"},
        ],
        1,
    )

    assert captured["client_id"] == "tenant-2"
    assert captured["store"] == "Loja Exata"
    assert captured["agent_input"] is client.agent_input
    assert captured["analysis"] is client.compatibility_analysis
    assert results[1]["result"]["unavailable"] is True
    assert results[1]["result"]["reason"] == "tool_not_available_in_pre_sale_flow"


def _turn(
    *,
    action: str,
    category: str = "product_feature",
    subquestions: list[str] | None = None,
    research_requests: list[dict[str, str]] | None = None,
    answer: str = "",
    reason: str = "agent_decision",
    decision: str = "not_applicable",
    commercial_state: str = "not_applicable",
    compatibility: dict | None = None,
    evidence_basis: str = "none",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "action": action,
        "flow": "pre_sale",
        "category": category,
        "subquestions": list(subquestions or []),
        "research_requests": list(research_requests or []),
        "answer": answer,
        "confidence": 0.9,
        "reason": reason,
        "requires_human_review": False,
        "decision": decision,
        "commercial_state": commercial_state,
        "compatibility_analysis": compatibility or {
            "applicable": False,
            "target": "",
            "decision": "not_applicable",
            "condition": "",
            "missing_fields": [],
            "evidence_refs": [],
        },
        "missing_fact_owner": "none",
        "buyer_detail_needed": "",
        "evidence_basis": evidence_basis,
        "evidence_refs": list(evidence_refs or []),
    }


def test_presale_unified_turn_always_creates_a_persistent_codex_thread(monkeypatch) -> None:
    monkeypatch.setattr(clients, "_ia_raciocinio_perguntas_configurado", lambda: "medium")
    monkeypatch.setattr(
        clients,
        "policy_stage_context",
        lambda _client, prompt, results: (prompt, results),
    )
    captured = {}

    def call_model(_tenant, payload, model):
        captured["persist"] = payload.context["_codex_persist_thread"]
        captured["thread_before"] = payload.context["_codex_thread_id"]
        captured["message"] = payload.message
        captured["tool_results"] = payload.tool_results
        payload.context["_codex_thread_id_result"] = "thread-created"
        return json.dumps(_turn(action="answer", answer="Resposta comprovada.")), model

    monkeypatch.setattr(clients, "_ia_agent_perguntas_chamar_modelo", call_model)
    client = clients._PerguntasCodexV3Client(
        "tenant-2",
        "Loja Exata",
        "codex:model-test",
        {"_unified_response_flow": "pre_sale"},
    )
    client.sku_question_context = {"identity": {"sku": "SKU-7"}}
    monkeypatch.setattr(
        clients,
        "bounded_stage_prompt",
        lambda *_args, **_kwargs: pytest.fail("unified prompt must remain integral"),
    )

    result = client.invoke_unified_turn(
        "contexto-integral-em-todos-os-turnos",
        [{"evidence": "resultado-acumulado"}],
        False,
    )

    assert result["action"] == "answer"
    assert captured["persist"] is True
    assert captured["thread_before"] == ""
    assert "contexto-integral-em-todos-os-turnos" in captured["message"]
    assert captured["tool_results"] == [{"evidence": "resultado-acumulado"}]
    assert client.codex_thread_id == "thread-created"


def test_pump_cycle_gap_research_does_not_depend_on_legacy_reason_literal() -> None:
    turns = []
    research_calls = []

    def invoke(_prompt, results, force_answer):
        turns.append((list(results), force_answer))
        if len(turns) == 1:
            return _turn(
                action="research",
                reason="ciclo_de_trabalho_precisa_de_fonte",
                research_requests=[{
                    "type": "technical_web",
                    "query": "bomba modelo exato ciclo de trabalho manual fabricante",
                    "purpose": "confirmar operacao continua",
                    "preferred_authority": "fabricante",
                }],
            )
        return _turn(
            action="answer",
            answer="O manual confirma o ciclo de trabalho informado.",
            reason="manual_confirmed",
            decision="yes",
            commercial_state="fits",
        )

    def research(requests, round_number):
        research_calls.append((requests, round_number))
        return [{"function": "technical_web", "result": {"found": True, "read_only": True}}]

    result = run_unified_response_agent(
        flow="pre_sale",
        context={
            "response_signature": "Assinatura.",
            "response_policy": "policy",
            "sku_question_context": {"canonical_document": {"voltage": "12V"}},
        },
        invoke_turn=invoke,
        execute_research=research,
    )

    assert result["answer"].startswith("O manual confirma")
    assert result["reason"] == "manual_confirmed"
    assert research_calls[0][1] == 1
    assert research_calls[0][0][0]["type"] == "technical_web"
    assert len(turns) == 2


def test_missing_product_identity_runs_identity_then_technical_web_in_same_round(
    monkeypatch,
) -> None:
    events = []
    captured = {}
    identity_result = {
        "function": "web_search_product_identity",
        "arguments": {},
        "result": {
            "found": True,
            "read_only": True,
            "product_research_evidence": [
                {"brand": "Fabricante A", "model": "Modelo 32A"},
            ],
        },
    }
    technical_result = {
        "function": "web_search_question_context",
        "arguments": {},
        "result": {
            "found": True,
            "read_only": True,
            "conflicts": [
                {"source": "fabricante-a", "claim": "4 a 6 mm2"},
                {"source": "fabricante-b", "claim": "ate 10 mm2 rigido"},
            ],
        },
    }
    packet = {
        "identity": {
            "store_ref": "store-9",
            "seller_id": "seller-8",
            "site_id": "MLB",
            "sku": "632-K",
            "item_id": "MLB4992847213",
            "variation_id": "var-6",
        },
        "question": {
            "id": "q-1",
            "text": "Aceita fios de 4 mm, 6 mm ou 10 mm?",
        },
        "listing_facts": {
            "title": "Conjunto tomada industrial 2P+T 32A azul",
            "permalink": "https://produto.mercadolivre.com.br/MLB-4992847213",
            "attributes": [],
            "selected_variation": {"id": "var-6", "seller_sku": "632-K"},
        },
    }
    client = SimpleNamespace(
        client_id="tenant-2",
        loja="Loja Exata",
        agent_input={
            "tenant_id": "tenant-modelo",
            "store": "Loja do modelo",
            "item": {"id": "MLB-DO-MODELO", "seller_sku": "SKU-DO-MODELO"},
            "context": {"sku": "SKU-DO-MODELO"},
            "question": {"text": "texto original"},
        },
        sku_question_context=packet,
        unified_research_rounds=0,
        unified_research_results=[],
        context_pipeline=[],
    )

    monkeypatch.setattr(
        unified_presale,
        "bind_client_sku_question_context",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        unified_presale,
        "_mandatory_web_tool",
        lambda _name, callback: callback(),
    )

    def identity_tool(client_id, research_input, prior_results):
        events.append("identity")
        captured["identity"] = (client_id, research_input, list(prior_results))
        return identity_result

    def web_tool(client_id, research_input, prior_results):
        events.append("technical")
        captured["technical"] = (client_id, research_input, list(prior_results))
        return technical_result

    bindings = unified_presale.GeneralBindings(
        context_hub_tool=lambda *_args: {},
        web_tool=web_tool,
        product_identity_tool=identity_tool,
    )
    results = unified_presale.execute_unified_research(
        client,
        [{
            "type": "technical_web",
            "query": "bitola de fio aceita pelo borne",
            "purpose": "confirmar bitola flexivel e rigida",
            "preferred_authority": "fabricante",
            "tenant_id": "tenant-modelo",
            "store_id": "store-modelo",
            "seller_id": "seller-modelo",
            "site_id": "SITE-MODELO",
            "sku": "SKU-DO-MODELO",
            "item_id": "MLB-DO-MODELO",
            "variation_id": "var-modelo",
        }],
        1,
        bindings,
    )

    assert events == ["identity", "technical"]
    assert [result["function"] for result in results] == [
        "web_search_product_identity",
        "web_search_question_context",
    ]
    assert client.unified_research_rounds == 1
    for stage in ("identity", "technical"):
        client_id, research_input, _prior = captured[stage]
        assert client_id == "tenant-2"
        assert research_input["tenant_id"] == "tenant-2"
        assert research_input["store"] == "Loja Exata"
        assert research_input["item"]["id"] == "MLB4992847213"
        assert research_input["item"]["seller_sku"] == "632-K"
        assert research_input["item"]["variation_id"] == "var-6"
        assert research_input["context"] == {"sku": "632-K"}
        assert research_input["sku_question_context"]["identity"] == packet["identity"]
        assert "store_id" not in research_input
        assert "seller_id" not in research_input
        assert "site_id" not in research_input
    assert captured["identity"][2] == []
    assert captured["technical"][2] == [identity_result]
    assert results[1]["result"]["conflicts"] == technical_result["result"]["conflicts"]


def test_explicit_product_identity_web_uses_server_bound_scope(monkeypatch) -> None:
    packet = {
        "identity": {
            "store_ref": "store-9",
            "seller_id": "seller-8",
            "site_id": "MLB",
            "sku": "632-K",
            "item_id": "MLB4992847213",
        },
        "question": {"id": "q-1", "text": "Qual fabricante e modelo?"},
        "listing_facts": {"title": "Conjunto tomada industrial 32A"},
    }
    client = SimpleNamespace(
        client_id="tenant-2",
        loja="Loja Exata",
        agent_input={"item": {}, "context": {}, "question": {}},
        sku_question_context=packet,
        unified_research_rounds=0,
        unified_research_results=[],
        context_pipeline=[],
    )
    calls = []
    monkeypatch.setattr(
        unified_presale,
        "bind_client_sku_question_context",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        unified_presale,
        "_mandatory_web_tool",
        lambda _name, callback: callback(),
    )

    def identity_tool(client_id, research_input, prior_results):
        calls.append((client_id, research_input, prior_results))
        return {
            "function": "web_search_product_identity",
            "result": {"found": True, "read_only": True},
        }

    results = unified_presale.execute_unified_research(
        client,
        [{
            "type": "product_identity_web",
            "query": "identificar produto",
            "purpose": "achar fabricante e modelo",
            "preferred_authority": "fabricante",
        }],
        1,
        unified_presale.GeneralBindings(
            context_hub_tool=lambda *_args: {},
            web_tool=lambda *_args: pytest.fail("technical web was not requested"),
            product_identity_tool=identity_tool,
        ),
    )

    assert len(calls) == 1
    assert calls[0][0] == "tenant-2"
    assert calls[0][1]["item"]["id"] == "MLB4992847213"
    assert calls[0][1]["item"]["seller_sku"] == "632-K"
    assert [result["function"] for result in results] == ["web_search_product_identity"]


def test_complete_manufacturer_and_model_skip_automatic_identity_research(
    monkeypatch,
) -> None:
    packet = {
        "identity": {"sku": "SKU-7", "item_id": "MLB1"},
        "question": {"id": "q-1", "text": "Qual a bitola?"},
        "listing_facts": {
            "title": "Tomada industrial",
            "attributes": [
                {"id": "BRAND", "value_name": "Fabricante A"},
                {"id": "MODEL", "value_name": "Modelo 32A"},
            ],
        },
    }
    client = SimpleNamespace(
        client_id="tenant-2",
        loja="Loja Exata",
        agent_input={"item": {}, "context": {}, "question": {}},
        sku_question_context=packet,
        unified_research_rounds=0,
        unified_research_results=[],
        context_pipeline=[],
    )
    monkeypatch.setattr(
        unified_presale,
        "bind_client_sku_question_context",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        unified_presale,
        "_mandatory_web_tool",
        lambda _name, callback: callback(),
    )

    results = unified_presale.execute_unified_research(
        client,
        [{
            "type": "technical_web",
            "query": "bitola aceita",
            "purpose": "confirmar especificacao",
            "preferred_authority": "fabricante",
        }],
        1,
        unified_presale.GeneralBindings(
            context_hub_tool=lambda *_args: {},
            web_tool=lambda *_args: {
                "function": "web_search_question_context",
                "result": {"found": True, "read_only": True},
            },
            product_identity_tool=lambda *_args: pytest.fail(
                "complete identity must not be researched again"
            ),
        ),
    )

    assert [result["function"] for result in results] == ["web_search_question_context"]


def test_sku_632k_identity_then_technical_consensus_answers_wire_sizes(
    monkeypatch,
) -> None:
    packet = {
        "identity": {
            "store_ref": "store-9",
            "seller_id": "seller-8",
            "site_id": "MLB",
            "sku": "632-K",
            "item_id": "MLB4992847213",
            "variation_id": "var-6",
        },
        "question": {
            "id": "1365958367",
            "text": "Quais bitolas de fios que este conjunto aceita 4 mm, 6 mm, 10mm?",
        },
        "listing_facts": {
            "title": "Conjunto Tomada Industrial Plug Macho 2p+t 32a Azul 220-250v",
            "permalink": "https://produto.mercadolivre.com.br/MLB-4992847213",
            "attributes": [],
            "selected_variation": {"id": "var-6", "seller_sku": "632-K"},
        },
    }
    client = SimpleNamespace(
        client_id="tenant-2",
        loja="Loja Exata",
        agent_input={"item": {}, "context": {}, "question": {}},
        sku_question_context=packet,
        unified_research_rounds=0,
        unified_research_results=[],
        context_pipeline=[],
    )
    sequence: list[str] = []
    identity_result = {
        "function": "web_search_product_identity",
        "result": {
            "found": True,
            "read_only": True,
            "product_research_evidence": [{
                "ref": "fabricante:modelo-32a",
                "brand": "Fabricante identificado",
                "model": "Tomada 32 A",
            }],
        },
    }
    technical_result = {
        "function": "web_search_question_context",
        "result": {
            "found": True,
            "read_only": True,
            "sources": [
                {"ref": "manual:a", "claim": "4 e 6 mm2 para condutor flexivel"},
                {"ref": "catalogo:b", "claim": "ate 10 mm2 para condutor rigido"},
            ],
        },
    }
    monkeypatch.setattr(
        unified_presale,
        "bind_client_sku_question_context",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        unified_presale,
        "_mandatory_web_tool",
        lambda _name, callback: callback(),
    )
    bindings = unified_presale.GeneralBindings(
        context_hub_tool=lambda *_args: {},
        product_identity_tool=lambda *_args: sequence.append("identity") or identity_result,
        web_tool=lambda *_args: sequence.append("technical") or technical_result,
    )
    turns = 0

    def invoke(_prompt, results, _force_answer):
        nonlocal turns
        turns += 1
        if turns == 1:
            return _turn(
                action="research",
                research_requests=[{
                    "type": "technical_web",
                    "query": "tomada industrial 32 A bitola borne flexivel rigido",
                    "purpose": "confirmar 4, 6 e 10 mm2",
                    "preferred_authority": "fabricante e catalogos tecnicos",
                }],
            )
        assert [item["function"] for item in results] == [
            "web_search_product_identity",
            "web_search_question_context",
        ]
        return {
            **_turn(
                action="answer",
                answer=(
                    "Aceita 4 e 6 mm2 com condutor flexivel; 10 mm2 somente quando o "
                    "condutor for rigido, conforme a especificacao do borne."
                ),
                reason="consenso_tecnico_com_condicao_de_condutor",
                decision="conditional",
                commercial_state="partial",
                evidence_basis="technical_consensus",
                evidence_refs=["manual:a", "catalogo:b"],
            ),
            "requires_human_review": True,
        }

    result = run_unified_response_agent(
        flow="pre_sale",
        context={"sku_question_context": packet},
        invoke_turn=invoke,
        execute_research=lambda requests, round_number: unified_presale.execute_unified_research(
            client, requests, round_number, bindings,
        ),
    )

    assert sequence == ["identity", "technical"]
    assert client.unified_research_rounds == 1
    assert "4 e 6 mm2" in result["answer"]
    assert "10 mm2" in result["answer"]
    assert "flexivel" in result["answer"] and "rigido" in result["answer"]
    assert result["evidence_basis"] == "technical_consensus"
    assert result["requires_human_review"] is True


@pytest.mark.parametrize(
    ("category", "subquestions"),
    [
        ("warranty_originality", ["originalidade"]),
        ("compatibility", ["compatibilidade", "medida"]),
        ("product_feature", ["ciclo de trabalho", "tensao", "garantia"]),
    ],
)
def test_confirmed_fact_originality_compatibility_and_multi_subject_finish_in_one_turn(
    category,
    subquestions,
) -> None:
    calls = []

    def invoke(_prompt, results, force_answer):
        calls.append((results, force_answer))
        return _turn(
            action="answer",
            category=category,
            subquestions=subquestions,
            answer="Resposta cobre todos os fatos confirmados.",
            decision="yes",
            commercial_state="fits",
            compatibility=(
                {
                    "applicable": True,
                    "target": "modelo alvo",
                    "decision": "yes",
                    "condition": "",
                    "missing_fields": [],
                    "evidence_refs": ["catalogo"],
                }
                if category == "compatibility"
                else None
            ),
        )

    result = run_unified_response_agent(
        flow="pre_sale",
        context={"response_signature": "Assinatura.", "response_policy": "policy"},
        invoke_turn=invoke,
        execute_research=lambda *_args: pytest.fail("confirmed fact must not research"),
    )

    assert result["category"] == category
    assert result["subquestions"] == subquestions
    assert len(calls) == 1


def test_incompatible_item_can_request_bound_same_store_alternative() -> None:
    turns = []
    requests_seen = []

    def invoke(_prompt, results, _force_answer):
        turns.append(list(results))
        if not results:
            return _turn(
                action="research",
                category="compatibility",
                research_requests=[{
                    "type": "same_store_listing",
                    "query": "modelo alvo",
                    "purpose": "buscar alternativa compativel",
                    "preferred_authority": "mesma loja",
                }],
                decision="no",
                commercial_state="incompatible",
                compatibility={
                    "applicable": True,
                    "target": "modelo alvo",
                    "decision": "no",
                    "condition": "",
                    "missing_fields": [],
                    "evidence_refs": ["codigo divergente"],
                },
            )
        return _turn(
            action="answer",
            category="compatibility",
            answer="Este item nao serve; a alternativa confirmada e a do link oficial retornado.",
            decision="no",
            commercial_state="incompatible",
            compatibility={
                "applicable": True,
                "target": "modelo alvo",
                "decision": "no",
                "condition": "",
                "missing_fields": [],
                "evidence_refs": ["codigo divergente", "alternativa mesma loja"],
            },
        )

    def research(requests, _round_number):
        requests_seen.extend(requests)
        return [{
            "function": "find_same_store_compatible_alternative",
            "result": {"found": True, "store_sku_bound": True},
        }]

    result = run_unified_response_agent(
        flow="pre_sale",
        context={"response_signature": "Assinatura.", "response_policy": "policy"},
        invoke_turn=invoke,
        execute_research=research,
    )

    assert requests_seen[0]["type"] == "same_store_listing"
    assert turns[1][0]["result"]["store_sku_bound"] is True
    assert len(turns) == 2
    assert result["commercial_state"] == "incompatible"


def test_bound_listing_keeps_exact_item_scope_without_claiming_invalid_sku_binding() -> None:
    packet = {
        "identity": {"item_id": "MLB-EXACT", "sku": "SKU-358"},
        "listing_facts": {
            "title": "Caneta Stylus para iPad 9 geracao",
            "description": "Compativel com iPad 9 geracao.",
        },
        "generation": {"id": "generation-1", "hash": "a" * 64, "version": 1},
        "binding_hash": "b" * 64,
        "validity": {
            "status": "unavailable",
            "identity_verified": False,
            "hashes_verified": False,
            "approval_state": "unknown",
        },
        "gaps": ["listing_sku_binding_mismatch"],
    }

    result = unified_presale._bound_packet_result(packet, [], "listing")["result"]

    assert result["found"] is True
    assert result["source_family"] == "listing"
    assert result["listing_item_bound"] is True
    assert result["store_sku_bound"] is False
    assert result["evidence_scope"] == "exact_listing"


def test_bound_context_hub_requires_approved_generation_hashes_and_binding() -> None:
    packet = {
        "identity": {"item_id": "MLB-EXACT", "sku": "SKU-358"},
        "canonical_document": {"description": "Compativel com iPad 9 geracao."},
        "generation": {"id": "generation-1", "hash": "a" * 64, "version": 1},
        "source_hashes": {"canonical_sku": "c" * 64},
        "binding_hash": "b" * 64,
        "validity": {
            "status": "active_approved_generation",
            "identity_verified": True,
            "hashes_verified": True,
            "approval_state": "approved",
        },
        "gaps": [],
    }

    result = unified_presale._bound_packet_result(packet, [], "context_hub")["result"]

    assert result["source_family"] == "exact_sku_catalog"
    assert result["listing_item_bound"] is False
    assert result["store_sku_bound"] is True
    assert result["evidence_scope"] == "exact_store_sku"
    assert result["data"]["canonical_document"] == packet["canonical_document"]
    assert result["data"]["validity"] == packet["validity"]
    for control_key in (
        "identity", "generation", "source_hashes", "binding_hash",
    ):
        assert control_key not in result["data"]

    packet["gaps"] = ["listing_sku_binding_mismatch"]
    mismatched = unified_presale._bound_packet_result(packet, [], "context_hub")["result"]
    assert mismatched["store_sku_bound"] is False
    assert mismatched["evidence_scope"] == "reference_only"
