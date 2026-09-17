from __future__ import annotations

import json
import inspect
from types import SimpleNamespace

import pytest

from backend.modules.perguntas_pos_venda.ai import clients
from backend.modules.perguntas_pos_venda.ai import execution
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
            return {"sku_question_context": {"identity": {"sku": "SKU-7"}}}

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
        "tenant_id": "tenant-2",
        "store": "Loja Exata",
        "store_id": "store-9",
        "seller_id": "seller-8",
        "site_id": "MLB",
        "sku": "SKU-7",
        "item_id": "MLB1",
        "variation_id": "var-6",
        "order_id": "",
    }
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

    with pytest.raises(PerguntasIARespostaIndisponivel, match="invalid_output"):
        execution._perguntas_ia_execucao_orquestrar(_context())


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
