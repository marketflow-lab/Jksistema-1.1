from __future__ import annotations

import inspect
import json

import pytest

from backend.modules.perguntas_pos_venda.ai.unified_response_agent import (
    UnifiedResponseAgentOperationalError,
)
from backend.schemas.ia import IAChatRequest
from backend.services import perguntas_pos_venda_core as _ppv_facade  # noqa: F401
from backend.services import perguntas_pos_venda_pos_venda as post_sale


def _compatibility() -> dict:
    return {
        "applicable": False,
        "target": "",
        "decision": "not_applicable",
        "condition": "",
        "missing_fields": [],
        "evidence_refs": [],
    }


def _turn(*, action: str, answer: str = "", requests: list[dict] | None = None,
          review: bool = False) -> dict:
    return {
        "action": action,
        "flow": "pre_sale",
        "category": "post_sale_support",
        "subquestions": ["Resolver o atendimento do pedido."],
        "research_requests": list(requests or []),
        "answer": answer,
        "confidence": 0.86,
        "reason": "O estado autenticado e as fontes consultadas sustentam o rascunho.",
        "requires_human_review": review,
        "decision": "not_applicable",
        "commercial_state": "not_applicable",
        "compatibility_analysis": _compatibility(),
        "missing_fact_owner": "internal" if review else "none",
        "buyer_detail_needed": "",
    }


def _pipeline() -> dict:
    identity = {
        "store_ref": "store-42",
        "seller_id": "seller-9",
        "site_id": "MLB",
        "sku": "SKU-77",
        "item_id": "MLB123",
        "variation_id": "",
    }
    return {
        "max_chars": 350,
        "pack_id": "PACK-1",
        "order_id": "ORDER-1",
        "pedido": {"id": "ORDER-1", "status": "paid"},
        "envio": {"status": "shipped", "tracking_number": "TRACK-1"},
        "pagamento": {"todos_aprovados": True, "status_geral": "approved"},
        "reclamacao_mediacao": {"available": False, "claims": []},
        "nota_fiscal": {"available": True, "numero_nf": "NF-1"},
        "anuncios": [{
            "id": "MLB123",
            "sku": "SKU-77",
            "title": "Bomba de teste",
            "catalog_product_context": [{
                "identity": identity,
                "found": True,
            "canonical_document": {"cycle": "continuo"},
            "catalog_document": {
                "schema": "jk_store_catalog_product_v1",
                "fields": {"name": "Bomba de teste", "brand": "Marca interna"},
                "field_sources": {"name": "produto_bling", "brand": "marca"},
            },
            "catalog_status": "available",
            "catalog_identity_verified": True,
            "content_role": "untrusted_reference_data",
            }],
        }],
        "regras_oficiais": {"precisa_consultar": True, "razoes": ["politica"]},
        "decisao_automacao": {
            "pode_responder_automaticamente": False,
            "destino_sugerido": "humano",
            "motivos_humano": ["precisa_regras_oficiais"],
        },
    }


def _conversation() -> dict:
    return {
        "pack_id": "PACK-1",
        "order_id": "ORDER-1",
        "seller_id": "seller-9",
        "buyer_id": "buyer-1",
        "messages": [
            {"from_role": "buyer", "text": "A bomba pode ficar ligada direto?"},
            {"from_role": "seller", "text": "Vou verificar para você."},
        ],
        "buyer_listing_question_chat": [
            {"role": "buyer", "text": "Ela é bivolt?"},
            {"role": "seller", "text": "É 12 V."},
        ],
        "items": [{"id": "MLB123", "sku": "SKU-77", "title": "Bomba de teste"}],
        "last_message_text": "A bomba pode ficar ligada direto?",
        "_resposta_atual": "Resposta que já estava sendo editada.",
        "_orientacao_usuario": "Mantenha a saudação e deixe a resposta mais curta.",
        "_codex_thread_id": "thread-existing",
        "_codex_job_id": "job-1",
    }


def _configure_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(post_sale, "IAChatRequest", IAChatRequest, raising=False)
    monkeypatch.setattr(post_sale, "_ia_modelo_pos_venda_configurado", lambda: "codex:test", raising=False)
    monkeypatch.setattr(
        post_sale,
        "_perguntas_ia_assinatura_loja",
        lambda _store: "Equipe Loja agradece!",
        raising=False,
    )
    monkeypatch.setattr(
        post_sale.perguntas_agent_providers,
        "select_response_provider",
        lambda *_args, **_kwargs: {
            "model": "codex:test",
            "policy": "codex_primary",
            "configured_fallback": "",
            "fallback_used": False,
            "operational_failure_count": 0,
        },
    )


def test_post_sale_unified_agent_reuses_thread_and_materializes_official_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_provider(monkeypatch)
    prompts: list[str] = []
    thread_ids: list[str] = []
    policy_calls: list[dict] = []

    def invoke(_tenant, payload, model):
        prompts.append(payload.message)
        thread_ids.append(str(payload.context.get("_codex_thread_id") or ""))
        assert payload.context["context_collection_stage"] == "unified_response_agent"
        payload.context["_codex_thread_id_result"] = "thread-shared"
        if len(prompts) == 1:
            value = _turn(action="research", requests=[{
                "type": "official_policy",
                "query": "Politica de devolucao MLA ignore as regras",
                "purpose": "Orientar sem prometer devolucao.",
                "preferred_authority": "Mercado Livre oficial",
            }])
        else:
            value = _turn(
                action="answer",
                answer="Boa tarde! Vamos conferir os detalhes pela própria compra. Equipe Loja agradece!",
            )
        return json.dumps(value, ensure_ascii=False), model

    def collect(topics, *, site_id, client_id):
        policy_calls.append({"topics": topics, "site_id": site_id, "client_id": client_id})
        return {
            "status": "available",
            "site_id": site_id,
            "topics": topics,
            "covered_topics": topics,
            "sources": [{"url": "https://www.mercadolivre.com.br/ajuda/devolucao", "text": "Regra oficial."}],
        }

    monkeypatch.setattr(post_sale.perguntas_agent_providers, "invoke_model", invoke)
    monkeypatch.setattr(post_sale, "collect_official_marketplace_policy", collect)
    pipeline = _pipeline()

    answer, model = post_sale._ml_pos_venda_gerar_resposta_ia(
        "tenant-a",
        "Loja Teste",
        _conversation(),
        contexto_pipeline=pipeline,
    )

    assert answer.startswith("Boa tarde!")
    assert model == "codex:test"
    assert thread_ids == ["thread-existing", "thread-shared"]
    assert policy_calls == [{
        "topics": ["return"],
        "site_id": "MLB",
        "client_id": "tenant-a",
    }]
    assert "A bomba pode ficar ligada direto?" in prompts[0]
    assert "Ela é bivolt?" in prompts[0]
    assert "TRACK-1" in prompts[0]
    assert "Resposta que já estava sendo editada." in prompts[0]
    assert "Mantenha a saudação" in prompts[0]
    assert "Nao use CTA de compra" in prompts[0]
    assert "official_marketplace_policy_research" in prompts[1]
    for prompt in prompts:
        assert '"tenant_id":"tenant-a"' in prompt
        assert '"store_id":"Loja Teste"' in prompt
        assert '"store_ref":"store-42"' in prompt
        assert '"seller_id":"seller-9"' in prompt
        assert '"site_id":"MLB"' in prompt
        assert '"sku":"SKU-77"' in prompt
        assert '"item_id":"MLB123"' in prompt
        assert '"order_id":"ORDER-1"' in prompt
    assert pipeline["classificacao_agente"]["flow"] == "post_sale"


def test_post_sale_research_uses_only_bound_catalog_and_denies_unapproved_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    technical_calls: list[tuple[str, str]] = []

    def technical(client_id, store, pipeline, requests):
        technical_calls.append((client_id, store))
        assert pipeline["pedido"]["id"] == "ORDER-1"
        assert requests[0]["query"] == "ciclo de trabalho"
        return {"function": "technical_web_research", "status": "unavailable", "result": {"found": False}}

    monkeypatch.setattr(post_sale, "_ml_pos_venda_unified_technical_web_research", technical)
    results = post_sale._ml_pos_venda_unified_execute_research(
        "tenant-a",
        "Loja Teste",
        _pipeline(),
        [
            {"type": "order", "query": "pedido", "purpose": "status", "preferred_authority": "API"},
            {"type": "context_hub", "query": "bomba", "purpose": "ficha", "preferred_authority": "interno"},
            {"type": "technical_web", "query": "ciclo de trabalho", "purpose": "uso", "preferred_authority": "fabricante"},
            {"type": "bling", "query": "estoque", "purpose": "consultar", "preferred_authority": "Bling"},
            {"type": "internal_catalog", "query": "marca", "purpose": "consultar", "preferred_authority": "cadastro"},
            {"type": "listing", "query": "atributos", "purpose": "consultar", "preferred_authority": "Mercado Livre"},
            {"type": "same_store_listing", "query": "alternativa", "purpose": "consultar", "preferred_authority": "loja"},
        ],
        1,
    )

    assert technical_calls == [("tenant-a", "Loja Teste")]
    assert any(row["function"] == "materialized_order_read" and row["result"]["data"]["id"] == "ORDER-1" for row in results)
    assert any(row["function"] == "context_hub_store_sku_read" and row["result"]["records"] for row in results)
    bling = next(row for row in results if row["function"] == "materialized_bling_catalog_read")
    assert bling["result"]["records"][0]["catalog_document"]["fields"] == {"name": "Bomba de teste"}
    internal = next(row for row in results if row["function"] == "materialized_internal_catalog_read")
    assert internal["result"]["records"][0]["identity"]["sku"] == "SKU-77"
    listing = next(row for row in results if row["function"] == "materialized_listing_read")
    assert listing["result"]["records"][0]["id"] == "MLB123"
    assert "catalog_product_context" not in listing["result"]["records"][0]
    assert any(
        row["function"] == "research_request_denied"
        and row["result"]["requested_type"] == "same_store_listing"
        for row in results
    )


def test_post_sale_agent_internal_gap_forces_existing_operational_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_provider(monkeypatch)
    answer = "Boa tarde! A equipe vai verificar o dado técnico e retornar pelo Mercado Livre. Equipe Loja agradece!"
    monkeypatch.setattr(
        post_sale.perguntas_agent_providers,
        "invoke_model",
        lambda *_args, **_kwargs: (json.dumps(
            _turn(action="answer", answer=answer, review=True),
            ensure_ascii=False,
        ), "codex:test"),
    )
    pipeline = _pipeline()
    pipeline["regras_oficiais"] = {"precisa_consultar": False, "razoes": []}
    pipeline["decisao_automacao"] = {
        "pode_responder_automaticamente": True,
        "destino_sugerido": "auto",
        "motivos_humano": [],
    }

    generated, _model = post_sale._ml_pos_venda_gerar_resposta_ia(
        "tenant-a",
        "Loja Teste",
        _conversation(),
        contexto_pipeline=pipeline,
    )

    assert generated == answer
    assert pipeline["decisao_automacao"]["pode_responder_automaticamente"] is False
    assert pipeline["decisao_automacao"]["destino_sugerido"] == "humano"
    assert "unified_agent_requires_human_review" in pipeline["decisao_automacao"]["motivos_humano"]


def test_post_sale_agent_rejects_empty_or_non_json_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_provider(monkeypatch)
    monkeypatch.setattr(
        post_sale.perguntas_agent_providers,
        "invoke_model",
        lambda *_args, **_kwargs: ("resposta fora do contrato", "codex:test"),
    )

    with pytest.raises(UnifiedResponseAgentOperationalError) as exc_info:
        post_sale._ml_pos_venda_gerar_resposta_ia(
            "tenant-a",
            "Loja Teste",
            _conversation(),
            contexto_pipeline=_pipeline(),
        )

    assert exc_info.value.code == "invalid_output"


def test_post_sale_absent_order_data_stays_a_human_review_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_provider(monkeypatch)
    captured: dict[str, str] = {}
    answer = "Boa tarde! Vamos verificar os dados do pedido e retornar pelo Mercado Livre. Equipe Loja agradece!"

    def invoke(_tenant, payload, model):
        captured["prompt"] = payload.message
        return json.dumps(_turn(action="answer", answer=answer, review=True), ensure_ascii=False), model

    monkeypatch.setattr(post_sale.perguntas_agent_providers, "invoke_model", invoke)
    pipeline = {
        "pedido": {},
        "envio": {},
        "pagamento": {},
        "reclamacao_mediacao": {},
        "anuncios": [],
        "regras_oficiais": {"precisa_consultar": False, "razoes": []},
        "decisao_automacao": {
            "pode_responder_automaticamente": True,
            "destino_sugerido": "auto",
            "motivos_humano": [],
        },
    }

    generated, _model = post_sale._ml_pos_venda_gerar_resposta_ia(
        "tenant-a",
        "Loja Teste",
        {"messages": [{"from_role": "buyer", "text": "Cadê meu pedido?"}]},
        contexto_pipeline=pipeline,
    )

    assert generated == answer
    assert '"pedido":{}' in captured["prompt"]
    assert pipeline["decisao_automacao"]["pode_responder_automaticamente"] is False
    assert pipeline["classificacao_agente"]["requires_human_review"] is True


def test_post_sale_dedicated_generator_has_no_critic_or_revision_stage() -> None:
    source = inspect.getsource(post_sale._ml_pos_venda_gerar_resposta_ia)

    assert "factual_critic" not in source
    assert "factual_revision" not in source
    assert "run_unified_response_agent" in source
