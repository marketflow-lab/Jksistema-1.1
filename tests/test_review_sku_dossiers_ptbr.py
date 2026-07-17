import csv
import json

import pytest

from scripts import review_sku_dossiers_ptbr as reviewer


def test_clean_product_name_prioritizes_ptbr_override():
    row = {
        "nome": "Engine Speed Sensor",
        "produto_bling": "Sensor de velocidade do motor",
    }
    overrides = {
        "ABC-1": {
            "nome_produto": "Sensor de rotação do motor",
        }
    }

    name = reviewer._clean_product_name("ABC-1", row, overrides)

    assert name.casefold() == "sensor de rotação do motor"
    assert "engine" not in name.casefold()


def test_clean_product_name_prefers_portuguese_catalog_name_over_english_name():
    row = {
        "nome": "Engine Speed Sensor",
        "produto_bling": "Sensor de rotação do motor",
    }

    name = reviewer._clean_product_name("ABC-2", row, {})

    assert name.casefold() == "sensor de rotação do motor"
    assert "engine" not in name.casefold()


def test_clean_measures_removes_packaging_vendor_and_one_mm_sentinel():
    raw_items = [
        {"texto": "Altura da embalagem: 3,7 cm", "fontes": ["anuncio:MLB1"]},
        {"texto": "Comprimento da embalagem do vendor: 6 cm"},
        {"texto": "Comprimento do cablagem: 1 mm"},
        {"texto": "Rosca: M16"},
        {"texto": "Dimensões do produto: 204 x 119 mm"},
    ]

    measures = reviewer._clean_measures(raw_items)

    assert isinstance(measures, list)
    assert all(isinstance(item, str) for item in measures)
    assert any("M16" in item for item in measures)
    assert any("204 x 119 mm" in item for item in measures)
    assert not any("embalagem" in item.casefold() for item in measures)
    assert not any("vendor" in item.casefold() for item in measures)
    assert not any("cablagem: 1 mm" in item.casefold() for item in measures)


def test_parse_catalog_vehicle_name_separates_vehicle_from_version_details():
    vehicle = reviewer._parse_catalog_vehicle_name(
        "Ford Focus 2010 2.0 Titanium Flex Powershift 5p BR"
    )

    assert vehicle["marca"] == "Ford"
    assert vehicle["modelo"] == "Focus"
    assert str(vehicle["ano"]) == "2010"


def test_compress_years_collapses_contiguous_years_into_range():
    years = [2009, 2010, 2011, 2012, 2013]

    assert reviewer._compress_years(years) == ["2009 a 2013"]


@pytest.mark.parametrize(
    "payload",
    [
        {"produto": {"fontes": ["cadastro:descricao"]}},
        {"blocos": [{"fontes_consultadas": [{"tipo": "cadastro"}]}]},
        {"produto": {"imagem": {"url": "https://example.com/item"}}},
    ],
)
def test_contains_source_fields_finds_forbidden_keys_recursively(payload):
    assert reviewer._contains_source_fields(payload) is True


def test_contains_source_fields_ignores_words_in_values_and_accepts_clean_payload():
    payload = {
        "sku": "ABC-1",
        "nome_produto": "Sensor de rotação do motor",
        "observacoes": ["A URL do fabricante não foi incluída no arquivo final."],
        "veiculos_compativeis": [
            {"marca": "Ford", "modelo": "Focus", "anos": ["2009 a 2013"]}
        ],
    }

    assert reviewer._contains_source_fields(payload) is False


def test_empty_clean_section_keeps_schema_without_placeholder_text():
    section = reviewer._section(
        ["Material: não informado.", "Medidas de embalagem foram desconsideradas."],
        "Não informado.",
    )

    assert section == {"status": "", "itens": [], "observação": ""}


def test_vehicle_without_year_uses_empty_year_list_and_require_year_rejects_it():
    vehicle = reviewer._parse_vehicle_text("Ford Focus")

    assert vehicle == {"marca": "Ford", "modelo": "Focus", "anos": []}
    assert reviewer._vehicle_rows_from_text("Ford Focus", require_year=True) == []


def test_merge_vehicle_rows_removes_legacy_missing_year_marker():
    vehicles = reviewer._merge_vehicle_rows([
        {"marca": "Ford", "modelo": "Focus", "anos": ["não informado"]},
    ])

    assert vehicles == [{"marca": "Ford", "modelo": "Focus", "anos": []}]


@pytest.mark.parametrize(
    "model",
    [
        "Sorento",
        "Renegade",
        "Compass",
        "Frontier",
        "Sprinter",
        "Captiva",
        "R1200GS",
        "R1250GS Adventure",
    ],
)
def test_clean_vehicle_model_keeps_alphabetic_model_names(model):
    assert reviewer._clean_vehicle_model(model) == model


def test_long_technical_block_is_segmented_and_bad_marketplace_fields_are_removed():
    items = [{"texto": (
        "Marca: Gptrend. Material: alumínio. Quantidade de chaves: 1111. "
        "Tensão: 12 V. Grau de proteção: IP66. " + ("texto auxiliar " * 20)
    )}]

    technical = reviewer._technical_items(items, product_name="Painel elétrico 12 V")

    assert any("Material: alumínio" in item for item in technical)
    assert any("Tensão: 12 V" in item for item in technical)
    assert not any("Gptrend" in item for item in technical)
    assert not any("1111" in item for item in technical)


def test_conflicting_technical_values_are_removed_instead_of_guessed():
    technical = reviewer._resolve_technical_conflicts([
        "Material: alumínio.",
        "Material: plástico.",
        "Potência: 35 W.",
        "Potência: 35W.",
        "Pressão de trabalho: 2 a 5 psi.",
        "Pressão de trabalho: 0,15 a 0,35 bar.",
    ])

    assert not any(item.startswith("Material:") for item in technical)
    assert sum(item.startswith("Potência:") for item in technical) == 1
    assert sum(item.startswith("Pressão de trabalho:") for item in technical) == 2


def test_marketplace_attribute_blocks_do_not_survive_technical_cleanup():
    technical = reviewer._resolve_technical_conflicts([
        "Tensão: 24 V.",
        "Material: Gptrend.",
        "Voltagem: 24 V Velocidade: 82 RPM Corrente: 5 A.",
        "Diâmetro: 20,32 cm (aprox.).",
    ])

    assert technical == ["Tensão: 24 V."]


def test_research_result_is_normalized_without_copying_sources():
    override = reviewer._research_result_to_override({
        "sku": "X1",
        "nome_produto_pt_br": "Sensor de teste",
        "status_identidade": "confirmada_com_referência_original",
        "tipo_correto": "veículo pesado",
        "oem_referencias_confirmadas": [{"codigo": "ABC-123", "natureza": "fabricante"}],
        "veiculos_compativeis": [{
            "marca": "Ford", "modelo": "Cargo", "ano_inicio": 2012, "ano_fim": 2018,
            "restricoes": "confirmar pelo chassi",
        }],
        "medidas_tecnicas": [
            {"descricao": "comprimento", "valor": 57, "unidade": "mm"},
            {"descricao": "profundidade", "valor": 18, "unidade": "mm"},
            {"descricao": "pressão de trabalho", "valor": "2 a 5", "unidade": "bar"},
        ],
        "caracteristicas_confirmadas": ["conector de 3 terminais"],
        "fontes": [{"url": "https://example.com"}],
    })

    assert override["tipo"] == "automóvel"
    assert override["_identidade_confirmada"] is True
    assert override["oem"] == ["ABC-123"]
    assert override["veículos_compatíveis"] == [
        {
            "marca": "Ford",
            "modelo": "Cargo",
            "anos": ["2012 a 2018"],
            "restrições": ["confirmar pelo chassi"],
        }
    ]
    assert override["medidas_do_produto"] == ["Comprimento: 57 mm", "Profundidade: 18 mm"]
    assert any("Pressão de trabalho: 2 a 5 bar" in item for item in override["características_técnicas"])
    assert reviewer._contains_source_fields(override) is False


def test_research_result_maps_nautical_models_to_equipment_applications():
    override = reviewer._research_result_to_override({
        "sku": "N1",
        "nome_produto_pt_br": "Bomba mecânica de combustível para motor de popa",
        "tipo_correto": "equipamento náutico",
        "veiculos_compativeis": [{
            "marca": "Johnson",
            "modelo": "Motor de popa 15 hp",
            "ano_inicio": 1974,
            "ano_fim": 1992,
            "restricoes": "confirmar o número do motor",
        }],
    })

    assert override["tipo"] == "náutica"
    assert override["veículos_compatíveis"] == []
    assert override["equipamentos_ou_aplicações_compatíveis"] == [
        "Johnson Motor de popa 15 hp — 1974 a 1992 — confirmar o número do motor."
    ]


@pytest.mark.parametrize(
    ("research_type", "expected"),
    [
        ("automotivo", "automóvel"),
        ("caminhão", "automóvel"),
        ("utilitário leve", "automóvel"),
        ("utilitário 4x4", "automóvel"),
        ("acessório para motocicleta", "motocicleta"),
        ("acessório automotivo universal", "universal veicular"),
        ("automotivo_universal_por_especificacao", "universal veicular"),
        ("produto para animais", "não veicular"),
    ],
)
def test_research_type_variants_are_normalized(research_type, expected):
    override = reviewer._research_result_to_override({"tipo_correto": research_type})

    assert override["tipo"] == expected


def test_research_empty_oem_and_measures_clear_weak_existing_values():
    research = reviewer._research_result_to_override({
        "oem_referencias_confirmadas": [],
        "medidas_tecnicas": [],
    })
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "001",
            "produto_bling": "Sensor térmico do radiador Honda",
            "descricao": "",
            "categoria": "Autopeças",
        },
        {
            "produto": {
                "oem": {"codigos": [{"codigo": "CODIGO-ANTIGO"}]},
                "medidas": {"itens": [{"texto": "Rosca: M16"}]},
            }
        },
        {"001": research},
        [],
    )

    assert dossier["oem"]["códigos"] == []
    assert dossier["medidas_do_produto"]["itens"] == []
    assert dossier["revisão"]["anos_exibidos_em_cada_modelo"] is True


def test_content_completion_merges_only_supported_sections(tmp_path):
    report = tmp_path / "completion.json"
    report.write_text(json.dumps({
        "X1": {
            "para_que_serve": ["Executa uma função confirmada."],
            "modo_de_funcionamento": ["Atua mecanicamente."],
            "instalação": ["Fixe no ponto compatível."],
            "fontes": [{"url": "https://example.com"}],
        }
    }, ensure_ascii=False), encoding="utf-8")

    merged = reviewer._merge_content_completions({}, [report])

    assert set(merged["X1"]) == {
        "para_que_serve", "modo_de_funcionamento", "instalação"
    }
    assert reviewer._contains_source_fields(merged) is False


@pytest.mark.parametrize(
    ("name", "expected_purpose"),
    [
        (
            "Amortecedor a gás para tampa de caçamba",
            "Auxilia a abertura e controla o movimento da tampa ou caçamba.",
        ),
        (
            "Painel elétrico universal 12 V com interruptores e voltímetro",
            "Centraliza o comando e a alimentação de acessórios elétricos.",
        ),
        (
            "Filtro eliminador de água com regulador para compressor",
            "Separa condensado e partículas do ar comprimido e ajusta a pressão de saída.",
        ),
        (
            "Módulo de controle do eletroventilador do radiador",
            "Controla a velocidade ou o acionamento elétrico da ventoinha.",
        ),
        (
            "Tampa do radiador pressurizada",
            "Fecha e veda o reservatório do sistema de arrefecimento.",
        ),
        (
            "Atuador a vácuo do eixo dianteiro 4x4",
            "Acopla ou desacopla o eixo dianteiro do sistema de tração 4x4 por comando a vácuo.",
        ),
        (
            "Cilindro atuador hidráulico da embreagem",
            "Converte a pressão hidráulica em movimento para acionar a embreagem.",
        ),
        (
            "Olho de gato refletor do para-choque",
            "Sinaliza a posição do veículo ao refletir a luz que incide sobre a peça.",
        ),
    ],
)
def test_specific_knowledge_rules_run_before_generic_product_words(name, expected_purpose):
    assert reviewer._knowledge_content(name)["para_que_serve"] == [expected_purpose]


def test_raw_equipment_ad_copy_is_discarded_but_curated_application_is_kept():
    row = {
        "sku": "EQ-1",
        "produto_bling": "Componente de teste",
        "descricao": "",
        "categoria": "",
    }
    raw = {
        "produto": {
            "equipamentos_ou_aplicacoes_compativeis": {
                "itens": [{"texto": "Descrição do anúncio com garantia de 3 meses."}]
            }
        }
    }

    uncurated = reviewer._build_clean_dossier(row, raw, {}, [])
    curated = reviewer._build_clean_dossier(
        row,
        raw,
        {"EQ-1": {"equipamentos_ou_aplicações_compatíveis": ["Motor de teste 12 V."]}},
        [],
    )

    assert uncurated["aplicação"]["equipamentos_ou_aplicações_compatíveis"]["itens"] == []
    assert curated["aplicação"]["equipamentos_ou_aplicações_compatíveis"]["itens"] == [
        "Motor de teste 12 V."
    ]


def test_known_cross_listing_vehicle_contamination_is_removed():
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "506",
            "produto_bling": "Manopla de câmbio automático Chevrolet",
            "descricao": "",
            "categoria": "Autopeças",
        },
        {"produto": {}},
        {
            "506": {
                "tipo": "automóvel",
                "veículos_compatíveis": [
                    {"marca": "Chevrolet", "modelo": "Spin", "anos": ["2013 a 2023"]},
                    {"marca": "Peugeot", "modelo": "2008", "anos": ["2025"]},
                ],
            }
        },
        [],
    )

    assert dossier["aplicação"]["veículos_compatíveis"]["itens"] == [
        {"marca": "Chevrolet", "modelo": "Spin", "anos": ["2013 a 2023"]}
    ]


def test_sparse_clean_dossier_has_blank_fields_and_no_missing_data_phrases():
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "ABC-2",
            "produto_bling": "Acessório de teste",
            "descricao": "",
            "categoria": "",
        },
        {"produto": {}},
        {},
        [],
    )

    assert dossier["medidas_do_produto"] == {
        "status": "",
        "itens": [],
        "observação": "",
    }
    assert dossier["oem"] == {"status": "", "códigos": [], "observação": ""}
    assert dossier["aplicação"]["veículos_compatíveis"] == {
        "status": "",
        "itens": [],
        "observação": "",
    }
    serialized = json.dumps(dossier, ensure_ascii=False).casefold()
    assert "não informado" not in serialized
    assert "não se aplica" not in serialized
    assert "medidas de embalagem foram desconsideradas" not in serialized


def test_final_measure_override_keeps_all_confirmed_product_dimensions():
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "312",
            "produto_bling": "Esteira do câmbio automático AL4",
            "descricao": "",
            "categoria": "Autopeças",
        },
        {"produto": {}},
        {
            "312": {
                "medidas_do_produto": ["Comprimento: 210 mm", "Largura: 47,5 mm"],
                "_medidas_pesquisa_autoritativas": True,
            }
        },
        [],
    )

    assert dossier["medidas_do_produto"]["itens"] == [
        "Comprimento: 210 mm",
        "Largura: 47,5 mm",
        "Abertura: 19 × 43 mm",
    ]


def test_universal_product_does_not_create_artificial_vehicle_or_year_text():
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "UNIV-1",
            "produto_bling": "Suporte universal automotivo para celular",
            "descricao": "",
            "categoria": "Acessórios automotivos",
        },
        {"produto": {}},
        {},
        [],
    )

    assert dossier["aplicação"]["tipo"] == "universal veicular"
    assert dossier["aplicação"]["veículos_compatíveis"] == {
        "status": "",
        "itens": [],
        "observação": "",
    }


def test_main_writes_clean_ptbr_dossier_without_sources_or_package_measures(tmp_path):
    source = tmp_path / "info" / "000002"
    raw_dir = tmp_path / "raw"
    output = tmp_path / "SKU"
    source.mkdir(parents=True)
    raw_dir.mkdir()
    with (source / "cadastro_produtos.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "produto_bling", "nome", "descricao", "categoria"])
        writer.writeheader()
        writer.writerow({
            "sku": "001",
            "produto_bling": "Sensor térmico do radiador Honda",
            "nome": "Radiator Fan Switch",
            "descricao": "Sensor com rosca M16. Compatível com Honda CB 500 1998 a 2005.",
            "categoria": "Autopeças",
        })
        writer.writerow({
            "sku": "395",
            "produto_bling": "Produto que permanece somente no cadastro comercial",
            "nome": "Produto excluído dos dossiês",
            "descricao": "Não deve gerar arquivo final.",
            "categoria": "Autopeças",
        })
    (raw_dir / "001.json").write_text(json.dumps({
        "sku": "001",
        "produto": {
            "medidas": {"itens": [
                {"texto": "Altura da embalagem: 4 cm", "fontes": ["anuncio:MLB1"]},
                {"texto": "Rosca: M16", "fontes": ["cadastro:descricao"]},
            ]},
            "caracteristicas_tecnicas": {"itens": []},
            "veiculos_compativeis": {"itens": [{
                "texto": "Honda CB 500 1998 a 2005",
                "fontes": ["cadastro:descricao"],
            }]},
        },
        "anuncios": {"ativos": []},
        "fontes_consultadas": [{"url": "https://example.com"}],
    }, ensure_ascii=False), encoding="utf-8")
    cache = tmp_path / "compat.json"
    cache.write_text(json.dumps({"anuncios": {}}, ensure_ascii=False), encoding="utf-8")
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"001": "Sensor térmico do radiador Honda"}, ensure_ascii=False), encoding="utf-8")
    output.mkdir()
    (output / "395.json").write_text('{"sku":"395"}', encoding="utf-8")

    result = reviewer.main([
        "--source-dir", str(source),
        "--raw-dir", str(raw_dir),
        "--compatibility-cache", str(cache),
        "--output-dir", str(output),
        "--overrides", str(overrides),
    ])

    assert result == 0
    assert not (output / "395.json").exists()
    index = json.loads((output / "_INDICE.json").read_text(encoding="utf-8"))
    assert index["total_skus"] == 1
    assert index["arquivos_revisados"] == 1
    assert index["skus_excluídos"] == ["395"]
    dossier = json.loads((output / "001.json").read_text(encoding="utf-8"))
    assert dossier["nome_produto"] == (
        "Interruptor térmico da ventoinha do radiador Honda, OEM 37760-MT2-003"
    )
    assert dossier["medidas_do_produto"]["itens"] == ["Rosca: M16 x 1,5", "Sextavado: 22 mm"]
    assert dossier["aplicação"]["veículos_compatíveis"]["itens"] == [
        {"marca": "Honda", "modelo": "CB 500", "anos": ["1997 a 2004"]},
        {"marca": "Honda", "modelo": "CB 600F Hornet", "anos": ["2004 a 2009"]},
        {"marca": "Honda", "modelo": "GL1500 Gold Wing", "anos": ["1988 a 1990", "1993 a 1994", "1996 a 2000"]},
        {"marca": "Honda", "modelo": "VT 600 C Shadow", "anos": ["1997 a 2006"]},
        {"marca": "Suzuki", "modelo": "GSX-R 1100 W", "anos": ["1994 a 1998"]},
    ]
    assert reviewer._contains_source_fields(dossier) is False


@pytest.mark.parametrize(
    ("raw_model", "expected"),
    [
        ("City – Anos-modelo", "City"),
        ("A class W176 - ANO", "A class W176"),
        ("C3 (", "C3"),
        ("* C180", "C180"),
        ("CB 500 de 94 a", "CB 500"),
        ("C250 C350 C63 AMG, Para", "C250 C350 C63 AMG"),
        ("Série 3 F30/F31/F34 com motor N55", "Série 3 F30/F31/F34"),
        ("C250, C350 e C63 AMG com farol D3S/D3R", "C250, C350 e C63 AMG"),
    ],
)
def test_vehicle_model_editorial_suffixes_are_removed(raw_model, expected):
    assert reviewer._clean_vehicle_model(raw_model) == expected


def test_year_cleanup_removes_covered_singles_without_inventing_a_larger_range():
    assert reviewer._normalize_year_labels([
        "2015", "2022", "2015 a 2022", "2016 a 2023",
    ]) == ["2015 a 2022", "2016 a 2023"]


def test_output_text_removes_research_language_and_embedded_absence_phrase():
    assert reviewer._sanitize_output_text(
        "Diâmetro da barra do adaptador do cadastro: 12 mm"
    ) == "Diâmetro da barra do adaptador: 12 mm"
    assert reviewer._sanitize_output_text(
        "Largura: 2 cm. Não há instruções incluídas neste kit."
    ) == "Largura: 2 cm"


def test_what_it_is_is_substantive_instead_of_repeating_the_title():
    description = reviewer._what_it_is(
        "Medidor de massa de ar MAF Continental VDO",
        "automóvel",
    )

    assert "sensor eletrônico" in description
    assert not description.startswith("Produto identificado como")


def test_clean_oem_rejects_codes_whose_only_origin_is_bling():
    raw = {
        "produto": {
            "oem": {
                "codigos": [
                    {"codigo": "93110-2D000", "fontes": ["cadastro:produto_bling"]},
                    {"codigo": "93110-3S000", "fontes": ["anuncio:MLB1"]},
                ]
            }
        }
    }

    assert reviewer._clean_oem(raw) == ["93110-3S000"]


def test_authoritative_research_can_replace_an_older_final_oem_override():
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "225",
            "produto_bling": "Código antigo do Bling",
            "descricao": "",
            "categoria": "Autopeças",
        },
        {"produto": {}},
        {
            "225": {
                "_oem_pesquisa_autoritativo": True,
                "oem": ["06E906517A", "0280142431"],
            }
        },
        [],
    )

    assert dossier["oem"]["códigos"] == ["06E906517A", "0280142431"]


@pytest.mark.parametrize(
    ("sku", "forbidden_codes", "required_codes", "first_vehicle"),
    [
        (
            "214",
            {"93110-2D000", "2573685687"},
            {"93110-3S000", "93110-0U000", "DNI2617", "LCG1272", "IM41995"},
            ("Hyundai", "HB20 1.0 e 1.6 com chave mecânica", "2012 em diante"),
        ),
        (
            "225",
            {"06H906517B", "0280142459", "1024709650", "47372790658"},
            {"06E906517A", "0280142431", "EVP0024", "911-800"},
            ("Volkswagen", "Bora", "2005 a 2010"),
        ),
    ],
)
def test_researched_skus_use_non_bling_codes_and_show_vehicle_years(
    sku, forbidden_codes, required_codes, first_vehicle
):
    dossier = reviewer._build_clean_dossier(
        {
            "sku": sku,
            "produto_bling": "Produto Bling com código incorreto",
            "descricao": "",
            "categoria": "Autopeças",
        },
        {"produto": {"oem": {"codigos": []}}},
        {},
        [],
    )

    codes = set(dossier["oem"]["códigos"])
    assert required_codes <= codes
    assert not (forbidden_codes & codes)
    vehicles = dossier["aplicação"]["veículos_compatíveis"]["itens"]
    assert vehicles
    assert all(vehicle.get("anos") for vehicle in vehicles)
    assert first_vehicle in {
        (vehicle["marca"], vehicle["modelo"], year)
        for vehicle in vehicles
        for year in vehicle["anos"]
    }
    assert dossier["revisão"]["pendências"] == []


def test_sku_372_publishes_only_safe_connector_details_and_keeps_oem_blank():
    dossier = reviewer._build_clean_dossier(
        {
            "sku": "372",
            "produto_bling": "Tubo Conexão Mangueira 55116901AA",
            "descricao": "Códigos de tampa, mangueira e conector misturados.",
            "categoria": "Autopeças",
        },
        {
            "produto": {
                "oem": {
                    "codigos": [
                        {"codigo": "53366451", "fontes": ["anuncio:MLB2162495400"]},
                        {"codigo": "55116901AA", "fontes": ["anuncio:MLB2162495400"]},
                        {"codigo": "5058482AD", "fontes": ["anuncio:MLB2162495400"]},
                        {"codigo": "5058446AH", "fontes": ["internet:1"]},
                    ]
                }
            }
        },
        {},
        [],
    )

    assert dossier["nome_produto"] == (
        "Conector com gargalo de enchimento da mangueira superior do radiador, sem tampa"
    )
    assert dossier["o_que_é"]
    assert dossier["características_técnicas"]["itens"] == [
        "Tipo: conector com gargalo de enchimento da mangueira superior do radiador.",
        "Material: plástico preto.",
        "Posição: mangueira superior do sistema de arrefecimento.",
        "Configuração: duas conexões principais estriadas para mangueira.",
        "Possui gargalo lateral para tampa pressurizada, bico fino de retorno ao reservatório de expansão e pino moldado de posicionamento.",
        "Conteúdo identificado: conector/gargalo sem tampa e sem abraçadeiras.",
        "Referência comercial compatível: 53366451.",
        "Referência comercial da peça plástica: CL9952.",
        "Alternativa de reparo em alumínio: DM-0192; conferir o desenho e os diâmetros antes da substituição.",
    ]
    assert dossier["medidas_do_produto"]["itens"] == []
    assert dossier["oem"]["status"] == ""
    assert dossier["oem"]["códigos"] == []
    assert dossier["aplicação"]["veículos_compatíveis"]["itens"] == [
        {
            "marca": "Chrysler",
            "modelo": "300C 3.6 V6",
            "anos": ["2011 a 2016"],
            "restrições": [
                "confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"
            ],
        },
        {
            "marca": "Dodge",
            "modelo": "Durango 3.6 V6",
            "anos": ["2011 a 2016"],
            "restrições": [
                "confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"
            ],
        },
        {
            "marca": "Dodge",
            "modelo": "Journey 2.7 V6",
            "anos": ["2009 a 2012"],
            "restrições": [
                "confirmar os códigos 53366451 ou CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"
            ],
        },
        {
            "marca": "Dodge",
            "modelo": "Journey 3.6 V6",
            "anos": ["2011 a 2018"],
            "restrições": [
                "confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"
            ],
        },
        {
            "marca": "Fiat",
            "modelo": "Freemont 2.4 16V",
            "anos": ["2011 a 2017"],
            "restrições": [
                "confirmar os códigos 53366451 ou CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"
            ],
        },
        {
            "marca": "Jeep",
            "modelo": "Grand Cherokee 3.6 V6",
            "anos": ["2010 a 2017"],
            "restrições": [
                "confirmar a referência CL9952, o desenho do gargalo e os diâmetros das conexões da peça instalada"
            ],
        },
    ]
    assert dossier["revisão"]["pendências"] == []
    serialized = json.dumps(dossier, ensure_ascii=False)
    for rejected_code in (
        "55116901AA", "5058482AD", "5058446AH", "MG1695", "902-305"
    ):
        assert rejected_code not in serialized
