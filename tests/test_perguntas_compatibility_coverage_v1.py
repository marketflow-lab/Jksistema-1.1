from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backend.modules.context_hub import retrieval as hub_retrieval
from backend.modules.perguntas_pos_venda.ai import clients as agent_clients
from backend.modules.perguntas_pos_venda.ai import context as agent_context
from backend.modules.perguntas_pos_venda.ai import validation as agent_validation
from backend.services.compatibility_coverage import (
    COMPATIBILITY_COVERAGE_VERSION,
    bind_compatibility_coverage,
    build_compatibility_coverage,
    normalize_compatibility_coverage,
    select_compatibility_coverage,
)
from backend.services.context_inventory.sku_content import build_sku_content
from ml_questions_gemini import AIAnswer
from ml_questions_gemini.schemas import ListingSnapshot, QuestionCategory, QuestionContext, SellerRules
from ml_questions_gemini.validator import AnswerValidator


def _reviewed_dossier(*, items: list[str], observation: str = "") -> dict:
    return {
        "schema_version": 1,
        "sku": "TEST-001",
        "nome_produto": "Produto de teste",
        "revisao": {"status": "revisado", "pendencias": []},
        "aplicacao": {
            "tipo": "geral",
            "veiculos_compativeis": {"status": "documentado", "itens": []},
            "equipamentos_ou_aplicacoes_compativeis": {
                "status": "documentado",
                "itens": items,
                "observacao": observation,
            },
        },
        "caracteristicas_tecnicas": {"status": "documentado", "itens": []},
        "para_que_serve": {"status": "documentado", "itens": []},
    }


def _bound(dossier: dict, sku: str = "TEST-001", generation: str = "g-active") -> dict:
    coverage = build_compatibility_coverage(dossier, sku)
    return bind_compatibility_coverage(
        coverage,
        source_hash="a" * 64,
        generation_id=generation,
        truth_class="canonical",
        doc_id=f"jk:sku:{sku.lower()}",
    )


def test_reviewed_universal_rule_preserves_scope_status_and_related_condition() -> None:
    dossier = _reviewed_dossier(
        items=["O encaixe serve em qualquer celular do mercado"],
        observation="O suporte nao realiza carregamento eletrico.",
    )

    coverage = build_compatibility_coverage(dossier, "TEST-001")
    normalized = normalize_compatibility_coverage(coverage)

    assert normalized["schema_version"] == COMPATIBILITY_COVERAGE_VERSION
    assert len(normalized["rules"]) == 1
    rule = normalized["rules"][0]
    assert rule["coverage_mode"] == "universal"
    assert rule["target_type"] == "phone_computing"
    assert rule["target_kind"] == "phone"
    assert rule["scope"] == "physical_fit"
    assert rule["source_status"] == "documentado"
    assert rule["related_conditions"] == [{
        "scope": "electrical",
        "text": "O suporte nao realiza carregamento eletrico.",
    }]


def test_unreviewed_or_ambiguous_source_does_not_create_coverage() -> None:
    dossier = _reviewed_dossier(items=["Produto universal para qualquer uso"])
    dossier["revisao"]["status"] = "pendente"
    assert build_compatibility_coverage(dossier, "TEST-001")["rules"] == []

    dossier["revisao"] = {"status": "revisado", "pendencias": []}
    dossier["aplicacao"]["equipamentos_ou_aplicacoes_compativeis"]["status"] = "inferido"
    assert build_compatibility_coverage(dossier, "TEST-001")["rules"] == []


def test_binding_rejects_noncanonical_other_sku_and_missing_generation() -> None:
    raw = build_compatibility_coverage(
        _reviewed_dossier(items=["O encaixe serve em qualquer celular"]),
        "TEST-001",
    )
    common = {"source_hash": "b" * 64, "generation_id": "g1", "doc_id": "jk:sku:test-001"}

    assert not bind_compatibility_coverage(raw, truth_class="legacy_unverified", **common)
    assert not bind_compatibility_coverage(raw, truth_class="canonical", **{**common, "doc_id": "jk:sku:other"})
    assert not bind_compatibility_coverage(raw, truth_class="canonical", **{**common, "generation_id": ""})
    assert bind_compatibility_coverage(raw, truth_class="canonical", **common)


def test_closed_target_kind_and_scope_pairs_fail_closed() -> None:
    raw = build_compatibility_coverage(
        _reviewed_dossier(items=["O encaixe serve em qualquer celular"]),
        "TEST-001",
    )
    invalid_pair = json.loads(json.dumps(raw))
    invalid_pair["rules"][0]["rule_id"] = ""
    invalid_pair["rules"][0]["target_kind"] = "vehicle"
    invalid_scope = json.loads(json.dumps(raw))
    invalid_scope["rules"][0]["rule_id"] = ""
    invalid_scope["rules"][0]["scope"] = "marketing_claim"

    assert normalize_compatibility_coverage(invalid_pair)["rules"] == []
    assert normalize_compatibility_coverage(invalid_scope)["rules"] == []


def test_physical_fit_coverage_does_not_prove_charging() -> None:
    coverage = _bound(_reviewed_dossier(items=["O encaixe serve em qualquer celular do mercado"]))
    common = {
        "coverages": [coverage],
        "target_type": "phone_computing",
        "target_item": "Samsung S25",
        "product_text": "Suporte para celular",
    }

    physical = select_compatibility_coverage(
        question_text="Meu celular Samsung S25 neste suporte da certo?",
        **common,
    )
    charging = select_compatibility_coverage(
        question_text="Este suporte carrega o celular Samsung S25?",
        **common,
    )

    assert physical["research_skipped"] == "canonical_coverage_sufficient"
    assert physical["scope"] == "physical_fit"
    assert charging == {}


def test_general_universal_classes_and_conditional_dimension() -> None:
    scenarios = [
        ("O encaixe serve em qualquer celular", "phone_computing", "iPhone 15", "Meu celular iPhone 15 encaixa?"),
        ("O eixo encaixa em qualquer ferramenta", "machine_tool", "ferramenta Makita X", "Encaixa na ferramenta Makita X?"),
        ("A conexao eletrica serve em qualquer equipamento eletrico de 12 V", "electrical_electronic", "equipamento eletrico 12 V", "A conexao eletrica serve no equipamento eletrico 12 V?"),
        ("O encaixe serve em qualquer componente com tubo de 22 mm", "dimensional", "componente com tubo de 22 mm", "Encaixa no componente com tubo de 22 mm?"),
    ]
    for text, target_type, target, question in scenarios:
        coverage = _bound(_reviewed_dossier(items=[text]))
        match = select_compatibility_coverage(
            [coverage],
            target_type=target_type,
            target_item=target,
            question_text=question,
            product_text=text,
        )
        assert match, (text, target_type)

    dimensional = _bound(_reviewed_dossier(items=["O encaixe serve em componentes com tubo de 22 mm"]))
    conditional = select_compatibility_coverage(
        [dimensional],
        target_type="dimensional",
        target_item="componente com tubo de 22 mm",
        question_text="Encaixa no componente com tubo de 22 mm?",
        product_text="Adaptador de encaixe",
    )
    assert conditional["decision"] == "conditional"
    assert conditional["conditions"]


def test_enumerated_vehicle_match_rejects_documented_exclusion() -> None:
    dossier = _reviewed_dossier(items=[])
    dossier["aplicacao"]["veiculos_compativeis"] = {
        "status": "documentado",
        "itens": [{
            "marca": "Marca",
            "modelo": "Modelo X",
            "anos": ["2020-2024"],
            "restricoes": ["Exceto versao Sport"],
        }],
    }
    coverage = _bound(dossier)

    regular = select_compatibility_coverage(
        [coverage], target_type="vehicle", target_item="Modelo X 2022",
        question_text="Serve no Modelo X 2022?", product_text="Peca veicular",
    )
    excluded = select_compatibility_coverage(
        [coverage], target_type="vehicle", target_item="Modelo X Sport 2022",
        question_text="Serve no Modelo X Sport 2022?", product_text="Peca veicular",
    )

    assert regular
    assert excluded == {}


def test_context_hub_document_keeps_status_observation_restrictions_and_contract() -> None:
    dossier = _reviewed_dossier(
        items=["O encaixe serve em qualquer celular"],
        observation="Nao confirma carregamento eletrico.",
    )
    content, allowed = build_sku_content(dossier, "TEST-001", "geral:produto")

    marker = next(line for line in content.splitlines() if line.startswith("CompatibilityCoverageV1: "))
    contract = json.loads(marker.split(": ", 1)[1])
    assert "Status das aplicacoes compativeis: documentado" in content
    assert "Observacao das aplicacoes compativeis: Nao confirma carregamento eletrico." in content
    assert contract == allowed["compatibility_coverage"]
    assert allowed["compatibility_coverage_version"] == COMPATIBILITY_COVERAGE_VERSION


def test_exact_context_hub_result_exposes_bound_rules_without_snippet_dependency() -> None:
    from backend.services import context_hub

    dossier = _reviewed_dossier(items=["O encaixe serve em qualquer celular"])
    content, _ = build_sku_content(dossier, "TEST-001", "geral:produto")
    row = {
        "doc_id": "jk:sku:test-001",
        "title": "Produto de teste",
        "chunk_id": "jk:sku:test-001#0",
        "content": "Trecho sem a regra de compatibilidade.",
        "document_content": content,
        "source_refs_json": '["SKU/TEST-001.json"]',
        "relative_path": "SKU/TEST-001.json",
        "truth_class": "canonical",
        "sensitivity": "internal",
        "source_version": "1.0.116",
        "source_hash": "c" * 64,
        "content_hash": "d" * 64,
        "kind": "sku",
        "module": "cadastro",
        "surface": "checkout",
        "store_ref": "",
        "tags_text": "",
        "valid_from": "",
        "valid_to": "",
    }

    result = hub_retrieval._search_result_from_row(
        row,
        active_id="g-active",
        query_terms=["produto"],
        score=1.0,
        strategy="exact_identifier",
        reason="exact sku",
    )

    assert "CompatibilityCoverageV1" not in result["snippet"]
    assert result["compatibility_coverage"]["source_binding"] == {
        "doc_id": "jk:sku:test-001",
        "source_hash": "c" * 64,
        "generation_id": "g-active",
        "truth_class": "canonical",
    }


def test_canonical_coverage_maps_to_existing_schema_and_validator() -> None:
    import backend_api  # noqa: F401
    coverage = _bound(_reviewed_dossier(items=["O encaixe serve em qualquer celular do mercado"]))
    match = select_compatibility_coverage(
        [coverage], target_type="phone_computing", target_item="Samsung S25",
        question_text="Meu celular Samsung S25 neste suporte da certo?", product_text="Suporte de celular",
    )
    agent_input = {
        "store": "JK Pecas",
        "question": {"text": "Meu celular Samsung S25 neste suporte da certo?"},
        "item": {"title": "Suporte para celular"},
        "intent": {
            "categoria": "compatibility",
            "categorias": ["compatibility"],
            "fluxo": "perguntas_anuncio",
            "compatibilidade": {
                "aplicavel": True,
                "target_item": "Samsung S25",
                "target_type": "phone_computing",
                "compatibility_profile": "device_interface",
            },
        },
    }
    analysis = agent_context._perguntas_ia_v2_coverage_analysis(agent_input, match)
    validation = AnswerValidator().validate(
        "Sim, da certo no Samsung S25 para o encaixe do suporte.\n\nEquipe JK Pecas agradece pelo contato, Precisando estamos a disposição!",
        question=QuestionContext(id="Q1", text=agent_input["question"]["text"]),
        listing=ListingSnapshot(id="MLB1", title="Suporte para celular"),
        category=QuestionCategory.COMPATIBILITY,
        rules=SellerRules(store_name="JK Pecas", max_sentences=3, max_chars=2000),
        confidence=0.97,
        compatibility_analysis=analysis,
    )

    assert analysis["decision"] == "yes"
    assert analysis["research_skipped"] == "canonical_coverage_sufficient"
    assert validation.ok, validation.issues


def test_active_generation_mismatch_cannot_resolve_compatibility() -> None:
    import backend_api  # noqa: F401
    stale = _bound(
        _reviewed_dossier(items=["O encaixe serve em qualquer celular"]),
        generation="g-old",
    )
    agent_input = {
        "question": {"text": "Meu celular Samsung S25 neste suporte da certo?"},
        "item": {"title": "Suporte para celular"},
        "intent": {
            "intencao": "compatibilidade",
            "categoria": "compatibility",
            "categorias": ["compatibility"],
            "fluxo": "perguntas_anuncio",
            "confianca": 0.99,
            "flags": {"usar_busca_web": True, "usar_mercado_livre_anuncio": True, "usar_bling": True},
            "subperguntas": [{
                "intent": "compatibility", "question": "O suporte encaixa?",
                "required_evidence": "cobertura canonica do encaixe",
            }],
            "compatibilidade": {
                "aplicavel": True,
                "target_item": "Samsung S25",
                "target_type": "phone_computing",
                "compatibility_profile": "device_interface",
                "technical_focus": "encaixe fisico",
                "missing_fields": [],
                "decisive_fields": ["physical_fit"],
            },
        },
    }
    hub_result = {
        "result": {
            "generation_id": "g-active",
            "results": [{"compatibility_coverage": stale}],
        }
    }

    assert agent_context._perguntas_ia_v2_coverage_match(agent_input, hub_result) == {}


def test_regression_skus_use_the_general_contract_without_web_research() -> None:
    import backend_api  # noqa: F401
    dossier = _reviewed_dossier(items=["O encaixe serve em qualquer celular do mercado"])
    for sku in ("241", "241-1"):
        coverage = _bound(dossier, sku=sku)
        hub_result = {
            "function": "context_hub_search",
            "result": {
                "found": True,
                "generation_id": "g-active",
                "results": [{"compatibility_coverage": coverage}],
            },
        }
        agent_input = {
            "store": "JK Pecas",
            "question": {"text": "Meu celular Samsung S25 neste suporte da certo?"},
            "item": {"seller_sku": sku, "title": "Suporte para celular"},
            "intent": {
                "intencao": "compatibilidade",
                "categoria": "compatibility",
                "categorias": ["compatibility"],
                "fluxo": "perguntas_anuncio",
                "confianca": 0.99,
                "flags": {
                    "usar_busca_web": True,
                    "usar_mercado_livre_anuncio": True,
                    "usar_bling": True,
                },
                "subperguntas": [{
                    "intent": "compatibility",
                    "question": "O suporte encaixa no celular informado?",
                    "required_evidence": "cobertura canonica do encaixe fisico",
                }],
                "compatibilidade": {
                    "aplicavel": True,
                    "target_item": "Samsung S25",
                    "target_type": "phone_computing",
                    "compatibility_profile": "device_interface",
                    "technical_focus": "encaixe fisico do suporte",
                    "missing_fields": [],
                    "decisive_fields": ["physical_fit"],
                },
            },
        }
        client = agent_clients._PerguntasVertexGeminiV2Client(
            "tenant-a", "JK Pecas", "codex:gpt-5.5", agent_input
        )
        seller_answer = AIAnswer(
            answer="Sim, da certo no Samsung S25 para o encaixe do suporte.",
            confidence=0.97,
            requires_human_review=False,
            reason="canonical_coverage_sufficient",
        )
        with patch.object(agent_clients, "marketplace_listing_query", return_value={}), \
             patch.object(agent_clients, "_ia_tool_get_product_data", return_value={}), \
             patch.object(agent_clients, "_ia_tool_get_bling_product", return_value={}), \
             patch.object(agent_clients, "_perguntas_ia_context_hub_tool", return_value=hub_result), \
             patch.object(agent_clients, "_ia_agent_perguntas_product_identity_web_tool", side_effect=AssertionError("web identity must be skipped")), \
             patch.object(agent_clients, "_ia_agent_perguntas_web_tool", side_effect=AssertionError("web research must be skipped")), \
             patch.object(client, "_call_model", return_value=seller_answer) as model_call:
            result = client.generate(
                "prompt",
                {
                    "category": "compatibility",
                    "question_text": agent_input["question"]["text"],
                    "listing_title": agent_input["item"]["title"],
                },
            )

        assert result.answer.startswith("Sim")
        assert model_call.call_count == 1
        assert client.compatibility_analysis["research_skipped"] == "canonical_coverage_sufficient"
        assert [step["status"] for step in client.context_pipeline if step["name"] in {
            "product_interface_research", "official_technical_research"
        }] == ["skipped", "skipped"]


def test_public_seller_style_is_objective_and_signature_is_not_counted() -> None:
    validator = AnswerValidator()
    common = {
        "question": QuestionContext(id="Q1", text="Quais as caracteristicas?"),
        "listing": ListingSnapshot(id="MLB1", title="Produto", description="Fato confirmado."),
        "category": QuestionCategory.PRODUCT_FEATURE,
        "rules": SellerRules(store_name="JK Pecas", max_sentences=3, max_chars=2000),
        "confidence": 0.90,
    }
    valid = validator.validate(
        "Boa tarde! Ele mede 12.5 mm. E simples de usar. Esta pronto para instalacao.\n\nEquipe JK Pecas agradece pelo contato, Precisando estamos a disposição!",
        **common,
    )
    technical = validator.validate(
        "A analise de compatibilidade indica evidencia insuficiente.\n\nEquipe JK Pecas agradece pelo contato, Precisando estamos a disposição!",
        **common,
    )
    verbose = validator.validate(
        "Primeira. Segunda. Terceira. Quarta.\n\nEquipe JK Pecas agradece pelo contato, Precisando estamos a disposição!",
        **common,
    )

    assert "too_many_sentences" not in valid.issues
    assert "seller_non_direct_opening" not in valid.issues
    assert "seller_process_language" in technical.issues
    assert "too_many_sentences" in verbose.issues


def test_style_failure_codes_and_deterministic_compaction_are_stable() -> None:
    import backend_api  # noqa: F401
    original = (
        "Ola. A analise de compatibilidade esta pronta. Primeira informacao util. "
        "Segunda informacao util. Terceira informacao util. Quarta informacao redundante."
    )
    codes = agent_validation._perguntas_ia_seller_style_violations(original)
    compacted = agent_validation._perguntas_ia_compactar_estilo_vendedor(original, "JK Pecas")

    assert codes == [
        "seller_style_too_many_sentences",
        "seller_style_internal_process_language",
    ]
    assert agent_validation._perguntas_ia_seller_style_violations(compacted) == []
    assert compacted.startswith("Ola. Primeira informacao util.")
    assert compacted.endswith("Equipe JK Pecas agradece pelo contato, Precisando estamos a disposição!")


def _validation_classification(question: str, *, category: str) -> dict:
    compatibility = category == "compatibility"
    return {
        "intencao": "compatibilidade" if compatibility else "duvida_produto",
        "categoria": category,
        "categorias": [category],
        "fluxo": "perguntas_anuncio",
        "confianca": 0.97,
        "flags": {
            "usar_busca_web": compatibility,
            "usar_mercado_livre_anuncio": True,
            "usar_bling": True,
        },
        "subperguntas": [{
            "intent": category,
            "question": question,
            "required_evidence": "anuncio e cadastro canonico do produto",
        }],
        "compatibilidade": {
            "aplicavel": compatibility,
            "target_item": "Ford Ranger Black 2026" if compatibility else "",
            "target_type": "vehicle" if compatibility else "",
            "compatibility_profile": "vehicle_fitment" if compatibility else "",
            "technical_focus": "aplicacao, encaixe e quantidade do kit" if compatibility else "quantidade do kit",
            "missing_fields": [],
            "decisive_fields": ["aplicacao documentada"] if compatibility else [],
        },
    }


def test_seller_reply_can_answer_compatibility_and_confirmed_kit_quantity() -> None:
    import backend_api  # noqa: F401

    agent_input = {
        "store": "Uai Mineirinho",
        "question": {
            "text": "Boa tarde, serve na Ranger Black 2026? Na compra vem o par, duas unidades?",
        },
        "item": {
            "title": "Par de amortecedores para tampa da Ranger",
            "description": "Aplicacao Ford Ranger 2013 a 2019. Kit com um par.",
        },
        "intent": _validation_classification(
            "Serve na Ranger Black 2026? Na compra vem o par, duas unidades?",
            category="compatibility",
        ),
    }
    answer = (
        "Boa tarde! A aplicacao confirmada e para Ranger de 2013 a 2019, por isso nao podemos "
        "garantir o encaixe na Ranger Black 2026. A compra inclui 1 par (duas unidades).\n\n"
        "Equipe Uai Mineirinho agradece pelo contato, Precisando estamos a disposição!"
    )

    issues = agent_validation._ia_agent_perguntas_violacoes_resposta(agent_input, answer)

    assert issues == []


def test_numeric_stock_claim_remains_blocked_even_when_buyer_asks_kit_quantity() -> None:
    import backend_api  # noqa: F401

    agent_input = {
        "store": "Uai Mineirinho",
        "question": {"text": "Na compra vem o par, duas unidades?"},
        "item": {"title": "Par de amortecedores", "description": "Kit com um par."},
        "intent": _validation_classification(
            "Na compra vem o par, duas unidades?",
            category="product_feature",
        ),
    }

    issues = agent_validation._ia_agent_perguntas_violacoes_resposta(
        agent_input,
        "Boa tarde! Temos 10 unidades disponiveis em estoque.",
    )

    assert "mencionou quantidade em estoque" in issues


@pytest.mark.parametrize(
    "category",
    [category for category in QuestionCategory if category != QuestionCategory.POST_SALE],
)
def test_every_public_category_uses_the_same_three_sentence_style_gate(category: QuestionCategory) -> None:
    validation = AnswerValidator().validate(
        "Primeira. Segunda. Terceira. Quarta.\n\nEquipe JK Pecas agradece pelo contato, Precisando estamos a disposição!",
        question=QuestionContext(id="Q1", text="Pergunta publica"),
        listing=ListingSnapshot(id="MLB1", title="Produto"),
        category=category,
        rules=SellerRules(store_name="JK Pecas", max_sentences=3, max_chars=2000),
        confidence=0.90,
    )

    assert "too_many_sentences" in validation.issues


def test_production_coverage_module_has_no_regression_sku_or_device_hardcode() -> None:
    from pathlib import Path

    source = Path("backend/services/compatibility_coverage.py").read_text(encoding="utf-8").lower()
    assert "241-1" not in source
    assert "samsung s25" not in source
