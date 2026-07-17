import csv
import json

import pytest

from scripts import generate_sku_dossiers as generator


def _write_catalog(path, rows):
    columns = [
        "sku", "descricao", "nome", "produto_bling", "categoria", "marca", "ncm", "cest",
        "m3 individual", "loja_sync", "updated_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_generator_creates_one_traceable_json_per_sku(tmp_path, monkeypatch):
    tenant = tmp_path / "info" / "000002"
    tenant.mkdir(parents=True)
    _write_catalog(
        tenant / "cadastro_produtos.csv",
        [
            {
                "sku": "001",
                "descricao": (
                    "Interruptor termico do radiador. Responsavel por acionar a ventoinha. "
                    "Aplicacao: Honda CB 500 1998 a 2005. Codigo original: 37760-MT2-003. "
                    "Rosca M16 e temperatura de 85 graus. Instalacao por profissional qualificado."
                ),
                "produto_bling": "Cebolao do radiador Honda CB500",
                "ncm": "85365090",
                "loja_sync": "JK Pecas",
            },
            {
                "sku": "ABC-2",
                "descricao": "Acessorio sem medidas ou instrucao tecnica confirmada.",
                "nome": "Acessorio de teste",
            },
            {
                "sku": "395",
                "descricao": "SKU excluído dos dossiês publicados.",
                "nome": "Produto que permanece somente no cadastro comercial",
            },
        ],
    )
    (tenant / "mlb_sku_vinculos.csv").write_text(
        "sku,mlb_principal,mlb_ids,qtd_anuncios_mlb\n001,MLB123,MLB123,1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generator, "_load_supplier_spreadsheet", lambda *_args: {"001": [], "ABC-2": []})
    monkeypatch.setattr(generator, "_load_listing_export", lambda *_args: {"001": [], "ABC-2": []})

    output = tmp_path / "output"
    output.mkdir()
    (output / "395.json").write_text('{"sku":"395"}', encoding="utf-8")
    result = generator.main([
        "--client-id", "000002",
        "--source-dir", str(tenant),
        "--output-dir", str(output),
    ])

    assert result == 0
    assert {path.name for path in output.glob("*.json")} == {"001.json", "ABC-2.json", "_INDICE.json"}
    index = json.loads((output / "_INDICE.json").read_text(encoding="utf-8"))
    assert index["total_skus_cadastro"] == 2
    assert index["arquivos_sku_gerados"] == 2
    assert index["skus_excluidos"] == ["395"]
    dossier = json.loads((output / "001.json").read_text(encoding="utf-8"))
    assert dossier["sku"] == "001"
    assert dossier["produto"]["o_que_e"]["texto"] == "Cebolao do radiador Honda CB500"
    assert dossier["produto"]["oem"]["codigos"][0]["codigo"] == "37760-MT2-003"
    assert dossier["produto"]["veiculos_compativeis"]["status"] == "documentado"
    assert dossier["fontes_consultadas"][0]["referencia"] == "info/000002/cadastro_produtos.csv"

    sparse = json.loads((output / "ABC-2.json").read_text(encoding="utf-8"))
    assert sparse["produto"]["oem"] == {
        "status": "",
        "codigos": [],
        "observacao": "",
    }
    for field in (
        "caracteristicas_tecnicas",
        "medidas",
        "modo_de_funcionamento",
        "instalacao",
        "veiculos_compativeis",
        "equipamentos_ou_aplicacoes_compativeis",
    ):
        section = sparse["produto"][field]
        if not section["itens"]:
            assert section["status"] == ""
            assert section["observacao"] == ""
    assert "instalacao" in sparse["qualidade"]["lacunas"]


def test_generator_rejects_source_dir_from_another_tenant(tmp_path):
    tenant = tmp_path / "info" / "000016"
    tenant.mkdir(parents=True)
    _write_catalog(tenant / "cadastro_produtos.csv", [{"sku": "001", "descricao": "Teste"}])

    with pytest.raises(SystemExit, match="corresponder ao client-id 000002"):
        generator.main(["--client-id", "000002", "--source-dir", str(tenant)])


def test_legacy_catalog_never_reads_another_tenant(tmp_path):
    info = tmp_path / "info"
    tenant = info / "000002"
    other = info / "000016"
    tenant.mkdir(parents=True)
    other.mkdir(parents=True)
    _write_catalog(other / "cadastro_produtos.csv", [{"sku": "001", "descricao": "Outro tenant"}])

    assert generator._load_legacy_catalog(tenant, {"001"}) == {}


def test_empty_raw_section_keeps_schema_without_placeholder_text():
    section = generator._section(
        [{"texto": "Medidas: nao informadas.", "fontes": ["cadastro:descricao"]}],
        "Informacao nao localizada.",
    )

    assert section == {"status": "", "itens": [], "observacao": ""}


def test_catalog_sku_resolution_only_uses_unique_compact_match():
    catalog = {"15-1", "151", "309.1"}
    assert generator._resolve_catalog_sku("309-1", catalog) == "309.1"
    assert generator._resolve_catalog_sku("151", catalog) == "151"
    assert generator._resolve_catalog_sku("15 1", catalog) == "151"


def test_oem_validation_rejects_vehicle_year_ranges():
    assert generator._valid_oem_code("37760-MT2-003", "001", "85365090")
    assert not generator._valid_oem_code("2007-2011", "001", "85365090")
    assert not generator._valid_oem_code("2015/2022", "001", "85365090")
    assert not generator._valid_oem_code("7895141653740", "521", "")
    assert not generator._valid_oem_code("7891234000224", "001", "")
    assert not generator._valid_oem_code("10PCS", "150", "")
    assert not generator._valid_oem_code("ISO9001", "245", "")
    assert not generator._valid_oem_code("2573685687", "214", "")
    assert not generator._valid_oem_code("55574685", "225", "")
    assert not generator._valid_oem_code("55116901AA", "372", "")
    assert not generator._valid_oem_code("5058482AD", "372", "")
    assert not generator._valid_oem_code("5058394AG", "372", "")
    assert not generator._valid_oem_code("5058446AH", "372", "")
    assert not generator._valid_oem_code("5058482AH", "372", "")


def test_ml_variation_attributes_extend_and_override_parent_attributes():
    item = {
        "attributes": [
            {"id": "BRAND", "name": "Marca", "value_name": "Marca pai"},
            {"id": "OEM", "name": "OEM", "value_name": "ABC123"},
        ]
    }
    variation = {
        "attributes": [
            {"id": "BRAND", "name": "Marca", "value_name": "Marca da variacao"},
        ]
    }

    assert generator._merged_ml_attributes(item, variation) == [
        {"nome": "Marca", "valor": "Marca da variacao"},
        {"nome": "OEM", "valor": "ABC123"},
    ]


def test_oem_extraction_requires_an_explicit_oem_label():
    fragments = [
        {"texto": "Scanner ELM327 para veiculos 2007-2011", "fontes": ["cadastro:descricao"]},
        {"texto": "Codigo original: 37760-MT2-003; aplicacao CB500 1998-2005", "fontes": ["anuncio:MLB1"]},
    ]

    assert generator._extract_oem(fragments, "001", "85365090") == [
        {"codigo": "37760-MT2-003", "fontes": ["anuncio:MLB1"]},
    ]


def test_oem_extraction_ignores_bling_legacy_and_supplier_only_codes():
    fragments = [
        {"texto": "Código OEM: 93110-2D000", "fontes": ["cadastro:produto_bling"]},
        {"texto": "Código OEM: 52029195AK", "fontes": ["fornecedor_planilha:1"]},
        {"texto": "Código OEM: 254000015R", "fontes": ["cadastro_legado:pista"]},
        {"texto": "Número de peça: 93110-3S000", "fontes": ["anuncio:MLB1"]},
        {"texto": "Código de referência: 93110-3S000", "fontes": ["cadastro:descricao"]},
    ]

    assert generator._extract_oem(fragments, "214", "") == [
        {
            "codigo": "93110-3S000",
            "fontes": ["anuncio:MLB1", "cadastro:descricao"],
        }
    ]


def test_oem_extraction_recognizes_accented_mercado_livre_part_number():
    fragments = [
        {
            "texto": "Número de peça: 0280142431 06E906517A",
            "fontes": ["anuncio:MLB4084179970"],
        }
    ]

    assert generator._extract_oem(fragments, "225", "") == [
        {"codigo": "0280142431", "fontes": ["anuncio:MLB4084179970"]},
        {"codigo": "06E906517A", "fontes": ["anuncio:MLB4084179970"]},
    ]


def test_legacy_insecure_flag_only_disables_public_web_ssl():
    args = generator._parse_args(["--insecure-ssl", "--live-ml"])
    assert args.insecure_web_ssl is True


def test_web_research_overrides_are_traceable_and_catalog_scoped(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps({
            "001": {
                "consulta": "cebolao CB500 OEM",
                "consultado_em": "2026-07-16T12:00:00+00:00",
                "resultados": [{
                    "titulo": "Catalogo tecnico",
                    "url": "https://example.com/peca",
                    "trecho": "Codigo OEM 37760-MT2-003 para CB500.",
                }],
            },
            "fora-do-cadastro": {"resultados": []},
        }),
        encoding="utf-8",
    )

    overrides = generator._load_web_research_overrides(path, {"001"})

    assert set(overrides) == {"001"}
    assert overrides["001"]["status"] == "pesquisado_com_resultado"
    assert overrides["001"]["resultados"][0]["url"] == "https://example.com/peca"


def test_product_name_falls_back_from_marketing_fragment_to_bling_name():
    name, source = generator._select_product_name(
        {
            "sku": "181",
            "nome": "Nossa taxa de respostas no site e uma das melhores",
            "produto_bling": "Par Pisca Seta Espelho Retrovisor Mondeo",
        },
        [],
        [],
    )

    assert name == "Par Pisca Seta Espelho Retrovisor Mondeo"
    assert source == "cadastro:produto_bling"


def test_product_name_uses_live_listing_when_catalog_names_are_missing():
    name, source = generator._select_product_name(
        {"sku": "412-55", "descricao": "Produto disponivel em pronta entrega."},
        [{"item_id": "MLB1", "titulo": "Enxada Rotativa Para Rocadeira"}],
        [],
    )

    assert name == "Enxada Rotativa Para Rocadeira"
    assert source == "anuncio:MLB1"
