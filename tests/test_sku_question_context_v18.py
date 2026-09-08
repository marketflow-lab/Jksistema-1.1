from __future__ import annotations

import json

import pytest

from backend.modules.perguntas_pos_venda.ai.inputs import (
    _perguntas_ia_allowed_tools_classificadas,
    _perguntas_ia_deve_buscar_web_publica,
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


def test_oversize_blocks_automation_without_silent_truncation():
    large = _canonical("x" * 25_000)
    packet, metrics = build_sku_question_context(
        _input("product_feature", "Qual o campo?"),
        {"category": "product_feature"},
        context_hub=_hub(canonical=large),
    )
    assert packet["automation_blocked"] is True
    assert packet["requires_human_review"] is True
    assert "canonical_document_too_large" in packet["block_reasons"]
    assert "x" * 100 not in json.dumps(packet)
    assert metrics["context_budget_exceeded"] is True


def test_prompts_validate_limits_instead_of_replacing_evidence():
    packet, _ = build_sku_question_context(
        _input("product_feature", "Qual conector?"),
        {"category": "product_feature"},
        context_hub=_hub(),
    )
    simple = simple_public_prompt(packet, {"legacy": "nao usar"})
    assert len(simple) + packet_wire_chars(packet) <= SIMPLE_PUBLIC_PROMPT_MAX_CHARS
    assert "CAMPO_RARO" not in simple
    assert SKU_QUESTION_CONTEXT_SCHEMA in simple
    unchanged, replaced = bounded_stage_prompt(
        "instrucao tecnica", packet,
        stage="technical_resolution_final", limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
    )
    assert unchanged == "instrucao tecnica"
    assert replaced is False
    with pytest.raises(ValueError, match="prompt_budget_exceeded"):
        bounded_stage_prompt(
            "x" * (HIGH_RISK_STAGE_PROMPT_MAX_CHARS - packet_wire_chars(packet) + 1),
            packet,
            stage="technical_resolution_final",
            limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
        )
    with pytest.raises(ValueError, match="prompt_budget_exceeded"):
        bounded_stage_prompt(
            "x" * (HIGH_RISK_STAGE_PROMPT_MAX_CHARS + 1),
            packet,
            stage="technical_resolution_final",
            limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
        )


def test_complete_stage_transport_blocks_oversized_research_without_mutating_envelope():
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
    with pytest.raises(ValueError, match="transport_budget_exceeded|prompt_budget_exceeded"):
        validate_stage_transport(
            "resolver",
            tool_results,
            stage="technical_resolution_final",
            limit=HIGH_RISK_STAGE_PROMPT_MAX_CHARS,
        )
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
