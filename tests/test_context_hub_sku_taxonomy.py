from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.context_hub_sku_taxonomy import (
    CATEGORY_PATHS,
    PRODUCT_CATEGORY_TAXONOMY_VERSION,
    REVIEW_PATH,
    TAXONOMY_RULES,
    classify_sku_product,
    product_category_id,
    taxonomy_nodes,
    taxonomy_tree_lines,
)
from scripts.generate_sku_product_taxonomy_doc import TARGET_RELATIVE_PATH, render_document


def test_taxonomy_contract_is_unique_complete_and_versioned() -> None:
    nodes = taxonomy_nodes()
    node_ids = [str(node["id"]) for node in nodes]
    paths = [tuple(str(item) for item in node["path"]) for node in nodes]

    assert PRODUCT_CATEGORY_TAXONOMY_VERSION == "1.0.0"
    assert len(node_ids) == len(set(node_ids))
    assert len(paths) == len(set(paths))
    assert len(CATEGORY_PATHS) == len(set(CATEGORY_PATHS.values()))
    assert REVIEW_PATH in paths
    assert {path[0] for path in CATEGORY_PATHS.values()} >= {
        "Veiculares",
        "Bicicletas",
        "Jardim, agricultura e roçadeiras",
        "Ferramentas, oficina e indústria",
        "Eletrônicos e tecnologia",
        "Casa, móveis e decoração",
    }
    assert all(rule.category_key in CATEGORY_PATHS for rule in TAXONOMY_RULES)
    assert all(rule.terms for rule in TAXONOMY_RULES)


def test_motorcycle_thermal_switch_uses_approved_product_path() -> None:
    classification = classify_sku_product(
        sku="001",
        product_name="Interruptor térmico da ventoinha Honda CG",
        application_type="motocicleta",
        description="Cebolinha do radiador",
    )

    assert classification == {
        "taxonomy_version": "1.0.0",
        "category_id": "jk:sku-category:veiculares:motocicletas:arrefecimento",
        "path": ["Veiculares", "Motocicletas", "Arrefecimento"],
        "status": "classified_by_rule",
        "rule_id": "moto-cooling",
        "evidence": ["interruptor termico", "ventoinha"],
        "sku": "001",
    }


def test_unknown_product_fails_closed_into_review_queue() -> None:
    classification = classify_sku_product(
        sku="SEM-REGRA",
        product_name="Componente genérico sem interface identificada",
        application_type="automovel",
    )

    assert classification["category_id"] == product_category_id(REVIEW_PATH)
    assert classification["path"] == ["Pendentes de revisão"]
    assert classification["status"] == "pending_review"
    assert classification["rule_id"] == "no-safe-rule"
    assert classification["evidence"] == []


def test_tree_lines_keep_hierarchy_and_human_readable_accents() -> None:
    tree = "\n".join(taxonomy_tree_lines())

    assert "- Veiculares" in tree
    assert "  - Automóveis, utilitários e pesados" in tree
    assert "    - Arrefecimento e climatização" in tree
    assert "- Ferramentas, oficina e indústria" in tree
    assert "Pendentes de revisão" not in tree


def test_classification_is_deterministic() -> None:
    payload = {
        "sku": "ABC",
        "product_name": "Sensor de pressão dos pneus TPMS",
        "application_type": "automovel",
        "description": "Sensor para roda",
        "uses": ("Monitoramento da pressão",),
    }

    assert classify_sku_product(**payload) == classify_sku_product(**payload)


@pytest.mark.parametrize(
    ("sku", "product_name", "application_type", "misleading_context", "expected_category"),
    (
        (
            "154",
            "Sensor de pressão do coletor e da turbina para Mitsubishi",
            "automóvel",
            "Instalado junto ao coletor de admissão.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:motor-e-gerenciamento:admissao-e-sobrealimentacao:medidores-maf-e-map",
        ),
        (
            "223",
            "Sensor de nível de combustível do tanque para Hyundai ix35",
            "automóvel",
            "Integra o conjunto da bomba de combustível.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:motor-e-gerenciamento:combustivel-e-injecao:boias-e-sensores-de-nivel",
        ),
        (
            "225",
            "Válvula eletromagnética de purga do cânister Volkswagen",
            "automóvel",
            "Conectada ao coletor de admissão.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:motor-e-gerenciamento:emissoes-e-vacuo:evap-e-canister",
        ),
        (
            "237",
            "Reator eletrônico do farol de xenônio Mercedes-Benz",
            "automóvel",
            "Alimenta a lâmpada de xenônio.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:iluminacao-e-sinalizacao:reatores-e-modulos",
        ),
        (
            "282",
            "Atuador a vácuo do engate do eixo dianteiro 4x4 SsangYong",
            "automóvel",
            "Utiliza uma válvula PCV no circuito de vácuo.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:transmissao-embreagem-e-tracao:atuadores-4x4",
        ),
        (
            "364-1",
            "Receptor universal de carregamento sem fio por indução Qi",
            "universal veicular",
            "Opera conectado à bateria.",
            "jk:sku-category:veiculares:universais-veiculares:audio-e-conectividade",
        ),
        (
            "397",
            "Conjunto com 100 presilhas automotivas variadas",
            "automóvel",
            "Pode ser usado no para-choque.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:carroceria-e-acabamento-externo:presilhas-e-fixadores",
        ),
        (
            "408",
            "Conjunto com 415 presilhas automotivas, estojo e ferramenta extratora",
            "automóvel",
            "Pode ser usado no para-choque.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:carroceria-e-acabamento-externo:presilhas-e-fixadores",
        ),
        (
            "415",
            "Tela de toque de reposição para central multimídia Continental",
            "automóvel",
            "Substitui o display LCD.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:eletrica-e-eletronica:instrumentacao-e-telas:telas-de-centrais-multimidia",
        ),
        (
            "60",
            "Calota central de roda BMW de 68 mm",
            "automóvel",
            "Recebe o emblema da marca.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:suspensao-direcao-rodas-e-pneus:calotas-e-acabamentos-de-roda",
        ),
        (
            "60-K4",
            "Conjunto com 4 calotas centrais de roda BMW de 68 mm",
            "automóvel",
            "Recebe o emblema da marca.",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:suspensao-direcao-rodas-e-pneus:calotas-e-acabamentos-de-roda",
        ),
    ),
)
def test_product_name_evidence_precedes_secondary_context(
    sku: str,
    product_name: str,
    application_type: str,
    misleading_context: str,
    expected_category: str,
) -> None:
    classification = classify_sku_product(
        sku=sku,
        product_name=product_name,
        application_type=application_type,
        description=misleading_context,
    )

    assert classification["category_id"] == expected_category
    assert classification["status"] == "classified_by_rule"


@pytest.mark.parametrize(
    ("sku", "product_name", "application_type", "expected_category"),
    (
        (
            "18-REFIL",
            "Refil elétrico para módulo da bomba de combustível Bosch",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:motor-e-gerenciamento:combustivel-e-injecao:bombas-e-refis",
        ),
        (
            "281",
            "Válvula de retenção do vácuo do coletor de admissão Chevrolet",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:motor-e-gerenciamento:emissoes-e-vacuo:valvulas-e-atuadores-de-vacuo",
        ),
        (
            "326",
            "Difusor de ar traseiro do console central Volkswagen",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:arrefecimento-e-climatizacao:difusores-de-ar",
        ),
        (
            "422",
            "Disco Enxada Rotativa Roçadeira Capina Grama Universal",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:discos-de-capina",
        ),
        (
            "422-K",
            "Disco Enxada Rotativa Roçadeira Capina Grama Universal 2 Un",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:discos-de-capina",
        ),
        (
            "436-1",
            "Adaptador Luva Cardan Roçadeira De Enxada Rotativa Arador Cardan 9 estrias",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:adaptadores-e-luvas",
        ),
        (
            "436-2",
            "Adaptador Luva Cardan Roçadeira De Enxada Rotativa Arador Cardan 7 estrias",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:adaptadores-e-luvas",
        ),
        (
            "436-3",
            "Adaptador Luva Cardan Roçadeira De Enxada Rotativa Arador Cardan Quadrado 6mm",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:adaptadores-e-luvas",
        ),
        (
            "436-4",
            "Adaptador Luva Cardan Roçadeira De Enxada Rotativa Arador Cardan Quadrado 5,5 mm",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:adaptadores-e-luvas",
        ),
        (
            "458-2",
            "Frizo Cromado para choque 71121-T9A-T00",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:carroceria-e-acabamento-externo:frisos-e-apliques",
        ),
        (
            "458-3",
            "Emblema H da grade dianteira para Honda City",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:carroceria-e-acabamento-externo:emblemas",
        ),
        (
            "521",
            "Mangueira de retorno do reservatório de expansão do líquido de arrefecimento Audi Q5",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:arrefecimento-e-climatizacao:mangueiras-tubos-e-flanges",
        ),
        (
            "416",
            "Friso cromado da grade dianteira para Honda Civic G10",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:carroceria-e-acabamento-externo:frisos-e-apliques",
        ),
        (
            "295",
            "Bomba mecânica de vácuo do servo-freio para motores 1.6 THP",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:motor-e-gerenciamento:emissoes-e-vacuo:bombas-de-vacuo",
        ),
        (
            "250-K",
            "Tampa Parachoque Esguicho Farol Azera (Par)",
            "automóvel",
            "jk:sku-category:veiculares:automoveis-utilitarios-e-pesados:carroceria-e-acabamento-externo:lavadores-e-esguichos",
        ),
        (
            "361",
            "Tela LCD de 3,5 polegadas para localizador de satélite Satlink",
            "não veicular",
            "jk:sku-category:eletronicos-e-tecnologia:telas-e-displays:telas-para-localizadores",
        ),
        (
            "438",
            "Protetor facial transparente antiembaçante para uso com roçadeira",
            "não veicular",
            "jk:sku-category:jardim-agricultura-e-rocadeiras:protecao-para-operacao",
        ),
    ),
)
def test_primary_product_term_precedes_later_title_qualifiers(
    sku: str,
    product_name: str,
    application_type: str,
    expected_category: str,
) -> None:
    classification = classify_sku_product(
        sku=sku,
        product_name=product_name,
        application_type=application_type,
    )

    assert classification["category_id"] == expected_category
    assert classification["status"] == "classified_by_rule"


def test_generic_face_protection_is_not_shadowed_by_garden_rule() -> None:
    classification = classify_sku_product(
        sku="EPI-GENERICO",
        product_name="Protetor facial transparente para oficina",
        application_type="não veicular",
    )

    assert classification["category_id"] == (
        "jk:sku-category:ferramentas-oficina-e-industria:seguranca-e-epi:protetores-faciais"
    )


def test_versioned_taxonomy_document_is_current() -> None:
    base_dir = Path(__file__).resolve().parents[1]

    assert (base_dir / TARGET_RELATIVE_PATH).read_text(encoding="utf-8") == render_document()
