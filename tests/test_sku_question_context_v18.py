from __future__ import annotations

import json

import pytest

from backend.modules.perguntas_pos_venda.ai.deep_research_documents import listing_document
from backend.modules.perguntas_pos_venda.ai.clients import _v18_effective_tool_results
from backend.modules.perguntas_pos_venda.ai.inputs import (
    _perguntas_ia_allowed_tools_classificadas,
    _perguntas_ia_deve_buscar_web_publica,
    _perguntas_ia_item_para_agente,
    _perguntas_ia_research_input,
)
from backend.modules.perguntas_pos_venda.ai.sku_question_context import (
    HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
    ROUTE_HIGH_RISK,
    ROUTE_SIMPLE_FACTUAL,
    ROUTE_SIMPLE_OPERATIONAL,
    SIMPLE_PUBLIC_PROMPT_MAX_CHARS,
    SKU_QUESTION_CONTEXT_MAX_CHARS,
    SKU_QUESTION_CONTEXT_SCHEMA,
    build_sku_question_context,
    with_document_references,
)
from backend.modules.perguntas_pos_venda.ai.sku_question_prompts import (
    bounded_stage_prompt,
    packet_wire_chars,
    simple_public_prompt,
    stage_transport_chars,
    validate_stage_transport,
)
from backend.modules.perguntas_pos_venda.ai.technical_planning import (
    technical_evidence_graph_prompt,
)
from backend.modules.perguntas_pos_venda.ai.technical_resolution import (
    normalize_technical_question_plan,
    technical_question_plan_prompt,
    technical_resolution_prompt,
)


def _input(category: str, question: str, *, confidence: float = 0.96) -> dict:
    return {
        "task": "mercado_livre_public_question_draft",
        "store": "JK Pecas",
        "question": {
            "text": question,
            "history": [
                {"role": "buyer", "text": "Contato comprador@example.com +55 11 99999-9999"},
                {"role": "seller", "text": "Como podemos ajudar?"},
            ],
        },
        "item": {"id": "MLB100", "seller_sku": "SKU-001", "title": "Produto"},
        "product_evidence_identity": {
            "store_ref": "b1e5a6efb16c0db69bba1836",
            "seller_id": "588182191",
            "site_id": "MLB",
            "sku": "SKU-001",
            "item_id": "MLB100",
            "variation_id": "",
        },
        "intent": {
            "categoria": category,
            "categorias": [category],
            "fluxo": "perguntas_anuncio",
            "confianca": confidence,
            "subperguntas": [{"intent": category, "question": question}],
        },
    }


def _canonical(extra: str = "") -> dict:
    return {
        "schema_version": 2,
        "sku": "SKU-001",
        "nome_produto": "Adaptador",
        "caracteristicas_tecnicas": {
            "status": "documentado",
            "itens": ["Conector USB-C", "Rosca M10"],
        },
        "aplicacao": {"tipo": "geral", "itens": ["Aplicacao documentada"]},
        "campo_integral_nao_perder": extra or "VALOR_INTEGRAL",
    }


def _hub(*, canonical: dict | None = None, conflicts: list | None = None, gaps: list | None = None) -> dict:
    document = canonical if canonical is not None else _canonical()
    return {
        "function": "context_hub_store_sku_read",
        "result": {
            "found": True,
            "generation_id": "store-generation-1",
            "generation_hash": "a" * 64,
            "generation_version": 1,
            "canonical_document": document,
            "guidance": {
                "general": {"orientacoes_perguntas": "Responder de forma objetiva."},
                "sku": {"notas": "Nao inferir aplicacao."},
            },
            "hashes": {
                "canonical_sku": "b" * 64,
                "store_guidance": "c" * 64,
                "sku_guidance": "d" * 64,
            },
            "validity": {
                "status": "active_approved_generation",
                "identity_verified": True,
                "hashes_verified": True,
                "approval_state": "approved",
            },
            "binding": {"binding_hash": "e" * 64},
            "conflicts": conflicts or [],
            "gaps": gaps or [],
        },
    }


def _internal() -> dict:
    return {
        "function": "get_mercado_livre_listing",
        "result": {
            "found": True,
            "source_authority": "official_current_listing_api",
            "matches": [{
                "id": "MLB100",
                "seller_sku": "SKU-001",
                "price": 99.90,
                "currency_id": "BRL",
                "available_quantity": 7,
                "description": "Nao deve atravessar",
                "attributes": [{"name": "Nao deve atravessar"}],
            }],
        },
    }


def _technical_listing_input(*, variation_id: str = "V1") -> dict:
    source = _input("compatibility", "Serve na BMW 120i 2005 2.0 16V gasolina?")
    source["product_evidence_identity"]["variation_id"] = variation_id
    source["context"] = {"sku": "SKU-001", "variation_id": variation_id}
    source["item"] = {
        "id": "MLB100",
        "seller_sku": "SKU-001",
        "title": "Filtro combustível BMW 120i",
        "permalink": "https://produto.mercadolivre.com.br/MLB-100",
        "description": "DESC-341. Código OEM 16127233840. Contato +55 11 99999-9999.",
        "condition": "new",
        "category_id": "MLB-FILTERS",
        "catalog_product_id": "CAT-341",
        "official_current_listing": True,
        "attributes": [
            {"id": "BRAND", "name": "Marca", "value_name": "BRAND-GLOBAL"},
            {"id": "PART_NUMBER", "name": "Número da peça", "value_name": "16127233840"},
        ],
        "sale_terms": [
            {"id": "WARRANTY", "name": "Garantia", "value_name": "WARRANTY-90"},
        ],
        "variations": [
            {
                "id": "V1",
                "seller_sku": "SKU-001",
                "attribute_combinations": [
                    {"id": "COLOR", "name": "Cor", "value_name": "COLOR-BLUE"},
                ],
                "attributes": [
                    {"id": "PART_NUMBER", "name": "Número da peça", "value_name": "16127233840"},
                ],
            },
            {
                "id": "V2",
                "seller_sku": "SIBLING-SKU",
                "attribute_combinations": [
                    {"id": "COLOR", "name": "Cor", "value_name": "SIBLING-COLOR"},
                ],
                "attributes": [
                    {"id": "PART_NUMBER", "name": "Número da peça", "value_name": "SIBLING-SECRET"},
                ],
            },
        ],
    }
    return source


def test_agent_item_preserves_variation_technical_attributes() -> None:
    raw = {
        "id": "MLB100",
        "title": "Filtro",
        "seller_sku": "PARENT-SKU",
        "description": "DESC-341",
        "_ppv_official_current_listing": True,
        "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "BRAND-GLOBAL"}],
        "sale_terms": [{"id": "WARRANTY", "name": "Garantia", "value_name": "WARRANTY-90"}],
        "variations": [{
            "id": "V1",
            "seller_sku": "SKU-001",
            "attribute_combinations": [
                {"id": "COLOR", "name": "Cor", "value_name": "COLOR-BLUE"},
            ],
            "attributes": [
                {"id": "PART_NUMBER", "name": "Número da peça", "value_name": "16127233840"},
            ],
        }],
    }

    item = _perguntas_ia_item_para_agente(raw)

    assert item["description"] == "DESC-341"
    assert item["attributes"][0]["value_name"] == "BRAND-GLOBAL"
    assert item["sale_terms"][0]["value_name"] == "WARRANTY-90"
    assert item["seller_sku"] == "PARENT-SKU"
    assert item["variations"][0]["seller_sku"] == "SKU-001"
    assert item["variations"][0]["attribute_combinations"][0]["value_name"] == "COLOR-BLUE"
    assert item["variations"][0]["attributes"][0]["value_name"] == "16127233840"


def test_agent_item_keeps_legacy_sku_sources_without_combining_variations() -> None:
    item = _perguntas_ia_item_para_agente({
        "id": "MLB100",
        "variations": [
            {
                "id": "V1",
                "attribute_combinations": [
                    {"id": "SELLER_SKU", "values": [{"name": "SKU-001"}]},
                ],
            },
            {"id": "V2", "seller_custom_field": "SKU-002"},
        ],
    })

    assert item["seller_sku"] == ""
    assert item["variations"][0]["seller_sku"] == "SKU-001"
    assert item["variations"][1]["seller_sku"] == "SKU-002"


def test_normalized_multi_variation_item_binds_exact_variation_without_aggregating_siblings() -> None:
    raw = {
        "id": "MLB100",
        "title": "Filtro",
        "seller_sku": "PARENT-SKU",
        "permalink": "https://produto.mercadolivre.com.br/MLB-100",
        "_ppv_official_current_listing": True,
        "variations": [
            {
                "id": "V1",
                "seller_sku": "SKU-001",
                "attributes": [
                    {"id": "PART_NUMBER", "name": "Número da peça", "value_name": "16127233840"},
                ],
            },
            {
                "id": "V2",
                "seller_sku": "SIBLING-SKU",
                "attributes": [
                    {"id": "PART_NUMBER", "name": "Número da peça", "value_name": "SIBLING-SECRET"},
                ],
            },
        ],
    }
    agent_input = _technical_listing_input()
    agent_input["item"] = _perguntas_ia_item_para_agente(raw)

    packet, _metrics = build_sku_question_context(
        agent_input,
        {"category": "compatibility"},
        context_hub=_hub(),
        force_high_risk=True,
    )

    encoded = json.dumps(packet, ensure_ascii=False)
    assert agent_input["item"]["seller_sku"] == "PARENT-SKU"
    assert packet["listing_facts"]["selected_variation"]["seller_sku"] == "SKU-001"
    assert "SIBLING-SKU" not in encoded
    assert "SIBLING-SECRET" not in encoded


def test_exact_variation_with_different_sku_blocks_listing_facts() -> None:
    agent_input = _technical_listing_input()
    agent_input["item"]["seller_sku"] = "SKU-001"
    agent_input["item"]["variations"][0]["seller_sku"] = "DIFFERENT-SKU"

    packet, _metrics = build_sku_question_context(
        agent_input,
        {"category": "compatibility"},
        context_hub=_hub(),
        force_high_risk=True,
    )

    assert packet.get("listing_facts") is None
    assert "identity_mismatch" in packet["route_reasons"]


def test_high_risk_packet_and_research_receive_exact_listing_variation() -> None:
    agent_input = _technical_listing_input()

    packet, _metrics = build_sku_question_context(
        agent_input,
        {"category": "compatibility"},
        context_hub=_hub(),
        force_high_risk=True,
    )

    facts = packet["listing_facts"]
    encoded = json.dumps(facts, ensure_ascii=False)
    assert packet["route"] == ROUTE_HIGH_RISK
    assert facts["scope"] == "listing_global"
    assert facts["identity_scope"] == "exact_item_and_variation"
    assert facts["variation_selection_state"] == "exact"
    assert facts["description"].startswith("DESC-341")
    assert "Código OEM 16127233840" in facts["description"]
    assert "+55 11 99999-9999" not in facts["description"]
    assert facts["attributes"][1]["value_name"] == "16127233840"
    assert facts["sale_terms"][0]["value_name"] == "WARRANTY-90"
    assert facts["selected_variation"]["id"] == "V1"
    assert facts["selected_variation"]["scope"] == "selected_variation"
    assert facts["selected_variation"]["attributes"][0]["value_name"] == "16127233840"
    assert "SIBLING-SECRET" not in encoded
    assert "SIBLING-COLOR" not in encoded
    assert '"V2"' not in encoded

    agent_input["sku_question_context"] = packet
    research_input = _perguntas_ia_research_input(agent_input)
    research_item = research_input["item"]
    research_encoded = json.dumps(research_item, ensure_ascii=False)
    assert research_item["description"].startswith("DESC-341")
    assert research_item["official_current_listing"] is True
    assert research_item["permalink"].endswith("MLB-100")
    assert research_item["attributes"][1]["value_name"] == "16127233840"
    assert research_item["variations"][0]["id"] == "V1"
    assert "SIBLING-SECRET" not in research_encoded
    assert '"V2"' not in research_encoded
    listing_evidence = listing_document(research_input)
    assert listing_evidence is not None
    assert "16127233840" in listing_evidence.text
    assert "SIBLING-SECRET" not in listing_evidence.text
    assert "+55 11 99999-9999" not in listing_evidence.text

    transported = _v18_effective_tool_results(packet, [_internal()])
    transported_encoded = json.dumps(transported, ensure_ascii=False)
    assert transported[0]["function"] == "store_sku_question_context"
    assert "16127233840" in transported_encoded
    assert "SIBLING-SECRET" not in transported_encoded
    assert all(result.get("function") != "get_mercado_livre_listing" for result in transported)


def test_high_risk_packet_never_exposes_sibling_without_exact_variation() -> None:
    agent_input = _technical_listing_input(variation_id="")

    packet, _metrics = build_sku_question_context(
        agent_input,
        {"category": "compatibility"},
        context_hub=_hub(),
        force_high_risk=True,
    )

    facts = packet["listing_facts"]
    encoded = json.dumps(facts, ensure_ascii=False)
    assert facts["scope"] == "listing_global"
    assert facts["variation_selection_state"] == "unresolved"
    assert "selected_variation" not in facts
    assert "SIBLING-SECRET" not in encoded
    assert "SIBLING-COLOR" not in encoded


def test_operational_route_uses_only_requested_live_fields():
    packet, metrics = build_sku_question_context(
        _input("stock", "Tem em estoque?"),
        {"category": "stock"},
        internal_sources=[_internal()],
        context_hub=_hub(),
    )
    encoded = json.dumps(packet, ensure_ascii=False)
    assert packet["schema"] == SKU_QUESTION_CONTEXT_SCHEMA
    assert packet["route"] == ROUTE_SIMPLE_OPERATIONAL
    assert packet["web"] == {"required": False, "reason": "operational_live_data_sufficient"}
    assert "available_quantity" in encoded
    assert '"price"' not in encoded
    assert "Nao deve atravessar" not in encoded
    assert "comprador@example.com" not in encoded
    assert metrics["packet_chars"] <= SKU_QUESTION_CONTEXT_MAX_CHARS


def test_operational_projection_keeps_exact_sku_only_and_never_uses_registry_price():
    agent_input = _input("price", "Qual o preco? token=SEGREDO-123")
    agent_input["product_evidence_identity"]["seller_id"] = "1275608482"
    listing = _internal()
    listing["result"]["matches"].append({
        "id": "MLB999",
        "seller_sku": "OUTRO-SKU",
        "price": 1.23,
        "currency_id": "BRL",
    })
    registry = {
        "function": "get_product_data",
        "result": {
            "found": True,
            "canonical_sku": "SKU-001",
            "matches": [{"sku": "SKU-001", "price": 777.77, "ncm": "0000.00.00"}],
        },
    }
    packet, _ = build_sku_question_context(
        agent_input,
        {"category": "price"},
        internal_sources=[listing, registry],
        context_hub=_hub(),
    )
    encoded = json.dumps(packet, ensure_ascii=False)
    assert packet["identity"]["seller_id"] == "1275608482"
    assert packet.get("automation_blocked") is not True
    assert "99.9" in encoded
    assert "1.23" not in encoded
    assert "777.77" not in encoded
    assert "SEGREDO-123" not in encoded


def test_variation_operational_projection_never_leaks_sibling_variation():
    agent_input = _input("stock", "Tem estoque desta variacao?")
    agent_input["product_evidence_identity"]["variation_id"] = "V1"
    agent_input["context"] = {"variation_id": "V1", "sku": "SKU-001"}
    source = _internal()
    source["result"]["matches"][0]["variations"] = [
        {"id": "V1", "available_quantity": 3},
        {"id": "V2", "available_quantity": 99, "marker": "SIBLING-SECRET"},
    ]
    packet, _ = build_sku_question_context(
        agent_input,
        {"category": "stock"},
        internal_sources=[source],
        context_hub=_hub(),
    )
    encoded = json.dumps(packet, ensure_ascii=False)
    assert '"V1"' in encoded
    assert '"V2"' not in encoded
    assert "SIBLING-SECRET" not in encoded


def test_factual_route_receives_every_original_canonical_field_and_guidance():
    canonical = _canonical("CAMPO_RARO_APROVADO")
    packet, metrics = build_sku_question_context(
        _input("product_feature", "Qual conector acompanha?"),
        {"category": "product_feature"},
        context_hub=_hub(canonical=canonical),
    )
    assert packet["route"] == ROUTE_SIMPLE_FACTUAL
    assert packet["canonical_document"] == canonical
    assert packet["guidance"]["general"]["orientacoes_perguntas"]
    assert packet["guidance"]["sku"]["notas"]
    assert packet["validity"] == _hub()["result"]["validity"]
    assert metrics["canonical_document_count"] == 1
    assert "stable_facts" not in packet
    assert "compatibility_coverage" not in packet


@pytest.mark.parametrize("question", [
    "Eh usado?",
    "E novo?",
    "O produto e novo ou usado?",
    "Nunca foi usado?",
    "Qual a condicao do produto?",
    "Ele e recondicionado?",
])
def test_store_product_condition_is_new_and_answers_used_question_without_web(question):
    packet, metrics = build_sku_question_context(
        _input("product_feature", question),
        {"category": "product_feature"},
        context_hub={"function": "context_hub_store_sku_read", "result": {"found": False}},
    )
    assert packet["route"] == ROUTE_SIMPLE_FACTUAL
    assert packet["route_reasons"] == ["store_product_condition_new"]
    assert packet["store_facts"] == {"product_condition": "new"}
    assert packet["web"] == {"required": False, "reason": "no_research_needed"}
    assert metrics["web_required"] is False
    prompt = simple_public_prompt(packet)
    assert "Todos os produtos vendidos pela loja sao novos" in prompt
    assert "nao peca confirmacao" in prompt


def test_public_prompts_receive_the_server_store_signature_exactly_once():
    agent_input = _input("product_feature", "Qual conector acompanha?")
    signature = "A equipe Loja Autenticada agradece o contato. Se precisar, estamos à disposição!"
    agent_input["context"] = {"assinatura_obrigatoria": signature}
    packet, _ = build_sku_question_context(
        agent_input,
        {"category": "product_feature"},
        context_hub=_hub(),
    )
    simple = simple_public_prompt(packet)
    resolution = technical_resolution_prompt(
        normalize_technical_question_plan({}, fallback_questions=["Qual conector acompanha?"]),
        packet,
        round_number=1,
        final=False,
    )

    assert packet["response_signature"] == signature
    assert simple.count(signature) == 1
    assert resolution.count(signature) == 1
    assert "terminar exatamente uma vez" in simple
    assert "Termine esse corpo exatamente uma vez" in resolution


def test_public_signature_uses_bound_store_and_has_a_safe_empty_store_fallback():
    packet, _ = build_sku_question_context(
        _input("product_feature", "Qual conector acompanha?"),
        {"category": "product_feature"},
        context_hub=_hub(),
    )
    assert packet["response_signature"] == (
        "A equipe JK Pecas agradece o contato. Se precisar, estamos à disposição!"
    )

    without_store = _input("product_feature", "Qual conector acompanha?")
    without_store.pop("store")
    fallback, _ = build_sku_question_context(
        without_store,
        {"category": "product_feature"},
        context_hub=_hub(),
    )
    assert fallback["response_signature"] == (
        "A equipe da loja agradece o contato. Se precisar, estamos à disposição!"
    )


def test_compatibility_originality_conflict_and_missing_evidence_are_high_risk():
    compatibility, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"},
        context_hub=_hub(),
    )
    mixed = _input("stock", "A tampa e genuina e tem o aditivo em estoque?")
    mixed["intent"]["subperguntas"] = [
        {"intent": "warranty_originality", "question": "A tampa e genuina?"},
        {"intent": "stock", "question": "Tem o aditivo em estoque?"},
    ]
    originality, _ = build_sku_question_context(
        mixed, {"category": "stock"}, internal_sources=[_internal()], context_hub=_hub(),
    )
    conflict, _ = build_sku_question_context(
        _input("product_feature", "Qual a medida?"),
        {"category": "product_feature"},
        context_hub=_hub(conflicts=["canonical_listing_conflict"]),
    )
    missing, _ = build_sku_question_context(
        _input("product_feature", "Qual a medida?"),
        {"category": "product_feature"},
        context_hub={"function": "context_hub_store_sku_read", "result": {"found": False}},
    )
    assert "compatibility_required" in compatibility["route_reasons"]
    assert "originality_required" in originality["route_reasons"]
    assert "available_quantity" in json.dumps(originality)
    assert "evidence_conflict" in conflict["route_reasons"]
    assert "decisive_fact_missing" in missing["route_reasons"]
    assert all(value["route"] == ROUTE_HIGH_RISK for value in (compatibility, originality, conflict, missing))
    assert all(value["web"]["required"] for value in (compatibility, originality, conflict, missing))


def test_integral_core_is_immutable_across_research_rebinds():
    first, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"},
        context_hub=_hub(),
    )
    second, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"},
        context_hub=_hub(),
        external_sources=[{"function": "web_search_question_context", "result": {"found": True}}],
        force_high_risk=True,
    )
    assert first["integral_core_hash"] == second["integral_core_hash"]
    assert first["canonical_document"] == second["canonical_document"]
    assert first["guidance"] == second["guidance"]
    forced, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"},
        context_hub=_hub(),
        force_high_risk=True,
    )
    assert forced == first


def test_incomplete_exact_identity_can_never_take_a_simple_route():
    agent_input = _input("stock", "Tem estoque?")
    agent_input["product_evidence_identity"].pop("seller_id")
    packet, _ = build_sku_question_context(
        agent_input,
        {"category": "stock"},
        internal_sources=[_internal()],
        context_hub={"function": "context_hub_store_sku_read", "result": {"found": False}},
    )
    assert packet["route"] == ROUTE_HIGH_RISK
    assert "identity_incomplete" in packet["route_reasons"]
    assert "exact_identity_incomplete" in packet["gaps"]


def test_large_canonical_document_reaches_model_packet_without_budget_fallback():
    large = _canonical("x" * 25_000)
    packet, metrics = build_sku_question_context(
        _input("product_feature", "Qual o campo?"),
        {"category": "product_feature"},
        context_hub=_hub(canonical=large),
    )
    assert packet.get("automation_blocked") is not True
    assert "canonical_document_too_large" not in (packet.get("block_reasons") or [])
    assert "x" * 25_000 in json.dumps(packet)
    assert metrics.get("context_budget_exceeded") is not True


def test_stage_prompts_preserve_large_input_and_integral_evidence():
    packet, _ = build_sku_question_context(
        _input("product_feature", "Qual conector?"),
        {"category": "product_feature"},
        context_hub=_hub(),
    )
    simple = simple_public_prompt(packet, {"legacy": "nao usar"})
    assert "CAMPO_RARO" not in simple
    assert SKU_QUESTION_CONTEXT_SCHEMA in simple
    unchanged, replaced = bounded_stage_prompt(
        "instrucao tecnica", packet,
        stage="technical_resolution_final", limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
    )
    assert unchanged == "instrucao tecnica"
    assert replaced is False
    large = "x" * (HIGH_RISK_STAGE_PROMPT_MAX_CHARS + 1)
    unchanged, replaced = bounded_stage_prompt(
        large, packet, stage="technical_resolution_final", limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
    )
    assert unchanged == large
    assert replaced is False


def test_complete_stage_transport_preserves_oversized_research_and_envelope():
    packet, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"},
        context_hub=_hub(),
    )
    tool_results = [
        {
            "function": "store_sku_question_context",
            "arguments": {"schema": packet["schema"]},
            "result": packet,
        },
        {
            "function": "web_search_question_context",
            "arguments": {},
            "result": {"context": "x" * HIGH_RISK_STAGE_PROMPT_MAX_CHARS},
        },
    ]
    before = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    assert stage_transport_chars("resolver", tool_results) > HIGH_RISK_STAGE_PROMPT_MAX_CHARS
    assert validate_stage_transport(
        "resolver", tool_results, stage="technical_resolution_final", limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
    ) == stage_transport_chars("resolver", tool_results)
    assert json.dumps(packet, ensure_ascii=False, sort_keys=True) == before


def test_technical_prompts_reference_one_typed_integral_envelope_without_copying_it():
    packet, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"},
        context_hub=_hub(canonical=_canonical("CAMPO_RARO_NAO_DUPLICAR")),
    )
    plan = normalize_technical_question_plan({}, fallback_questions=["Serve na Honda ADV 160?"])
    prompts = [
        technical_question_plan_prompt(packet),
        technical_evidence_graph_prompt(plan, packet),
        technical_resolution_prompt(plan, packet, round_number=1, final=False),
    ]
    assert all("store_sku_question_context" in prompt for prompt in prompts)
    assert all("CAMPO_RARO_NAO_DUPLICAR" not in prompt for prompt in prompts)


def test_missing_automotive_feature_uses_original_standard_only_with_proven_equivalence():
    packet, _ = build_sku_question_context(
        _input("product_feature", "A luz dos botoes e branca?"),
        {"category": "product_feature"}, context_hub=_hub(),
    )
    simple = simple_public_prompt(packet)
    plan = technical_question_plan_prompt(packet)
    resolution = technical_resolution_prompt(
        normalize_technical_question_plan({}, fallback_questions=["A luz dos botoes e branca?"]),
        packet, round_number=1, final=False,
    )
    assert "missing_specific_product_fact" in simple
    assert "peca original" in plan
    assert "sem pressupor" in plan
    assert "somente com equivalencia comprovada de codigo, funcao e variacao, atribua a ela a caracteristica confirmada" in resolution
    assert "todos os produtos vendidos sao novos" in resolution


def test_broad_target_prompts_require_complete_variant_universe_before_unconditional_fit():
    packet, _ = build_sku_question_context(
        _input("compatibility", "Serve na Range Rover Sport 2014?"),
        {"category": "compatibility"}, context_hub=_hub(),
    )
    technical_plan = normalize_technical_question_plan(
        {}, fallback_questions=["Serve na Range Rover Sport 2014?"],
    )

    plan_prompt = technical_question_plan_prompt(packet)
    graph_prompt = technical_evidence_graph_prompt(technical_plan, packet)
    resolution_prompt = technical_resolution_prompt(
        technical_plan, packet, round_number=1, final=False,
    )

    assert "target_variant_universe_official" in plan_prompt
    assert "enumerar o universo completo de versoes" in plan_prompt
    assert "cada versao relevante" in graph_prompt
    assert "unresolved_requirement_ids" in graph_prompt
    assert "cobrir 100% das versoes" in resolution_prompt
    assert "decida conditional" in resolution_prompt
    assert "decida insufficient" in resolution_prompt


def test_reference_metadata_is_path_free_and_never_replaces_integral_core():
    packet, _ = build_sku_question_context(
        _input("compatibility", "Serve na Honda ADV 160?"),
        {"category": "compatibility"}, context_hub=_hub(),
    )
    projected = with_document_references(packet, [{
        "attachment_name": "C:/private/manual.png",
        "source_url": "https://example.invalid/manual.pdf",
        "content_hash": "f" * 64,
        "page": 2,
        "generation_id": "generation-1",
    }])
    encoded = json.dumps(projected)
    assert projected["integral_core_hash"] == packet["integral_core_hash"]
    assert "C:/private" not in encoded
    assert "source_url" not in encoded
    assert projected["technical_references"][0]["reference_id"] == f"{'f' * 16}:p2"


def test_web_policy_remains_adaptive():
    stock = _input("stock", "Tem estoque?")
    compatibility = _input("compatibility", "Serve na Honda ADV 160?")
    missing = _input("product_feature", "Qual a temperatura?")
    packet, _ = build_sku_question_context(
        missing, {"category": "product_feature"}, context_hub={},
    )
    missing["sku_question_context"] = packet
    assert _perguntas_ia_deve_buscar_web_publica(stock) is False
    assert not any(tool.startswith("web_search") for tool in _perguntas_ia_allowed_tools_classificadas(stock))
    assert _perguntas_ia_deve_buscar_web_publica(compatibility) is True
    assert _perguntas_ia_deve_buscar_web_publica(missing) is True


def test_low_confidence_never_downgrades_route_but_commercial_data_stays_off_web():
    packet, _ = build_sku_question_context(
        _input("price", "Qual o preco?", confidence=0.4),
        {"category": "price"}, internal_sources=[_internal()], context_hub=_hub(),
    )
    assert packet["route"] == ROUTE_HIGH_RISK
    assert "low_classification_confidence" in packet["route_reasons"]
    assert packet["web"]["required"] is False
