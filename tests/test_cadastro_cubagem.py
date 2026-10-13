import copy
from decimal import Decimal

import pytest

from backend.services.cadastro_cubagem import (
    decimal_cubagem,
    normalizar_m3_individual,
    normalizar_sku_cubagem,
    planejar_migracao_cubagem,
    resolver_cubagem_produtos,
    validar_dados_cubagem,
)


def _produto(sku="001", store="a", valor="", **extras):
    return {"sku": sku, "store_id": store, "m3 individual": valor, **extras}


def _historico(sku="001", store="a", valor="0,002", **extras):
    return {
        "sku": sku, "store_id": store, "cg_m³ individual": valor,
        "m3_unidade": "par", "_origem": "packing-list-confirmada.xlsx", **extras,
    }


def test_lojas_e_skus_exatos_possuem_cubagem_independente():
    produtos = [
        _produto("001", "a", "0.001"), _produto("001", "b", "0.009"),
        _produto("1", "a", "0.003"), _produto("AB-1", "a", "0.004"),
        _produto("AB1", "a", "0.005"),
    ]
    mapa = resolver_cubagem_produtos(produtos, "a")
    assert mapa["001"]["m3_individual"] == Decimal("0.001")
    assert mapa["1"]["m3_individual"] == Decimal("0.003")
    assert mapa["AB-1"]["m3_individual"] != mapa["AB1"]["m3_individual"]
    assert resolver_cubagem_produtos(produtos, "b")["001"]["m3_individual"] == Decimal("0.009")
    assert normalizar_sku_cubagem(" 001 ") == "001"


def test_leitura_operacional_ignora_totais_backups_sem_escopo_e_excluidos():
    produtos = [
        {"sku": "001", "store_id": "a", "M3": "7", "CBM": "8", "cg_m³ individual": "0.001"},
        _produto("002", "", "0.009"),
        _produto("003", "a", "0.009", deleted_at_utc="2026-01-01"),
    ]
    mapa = resolver_cubagem_produtos(produtos, "a")
    assert set(mapa) == {"001"}
    assert mapa["001"]["status"] == "pendente"
    assert mapa["001"]["m3_individual"] is None
    assert resolver_cubagem_produtos(produtos, "") == {}


@pytest.mark.parametrize("texto,esperado", [
    ("0,00003125", "0.00003125"), ("3.125e-5", "0.00003125"),
    ("3,125E-5", "0.00003125"), ("0.123456789123456789123456789123456789",
     "0.123456789123456789123456789123456789"), ("1.0000", "1"),
])
def test_decimal_preserva_precisao_sem_arredondamento(texto, esperado):
    assert normalizar_m3_individual(texto) == esperado
    assert decimal_cubagem(texto) == Decimal(esperado)


@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity", "0", "-1", "abc",
                                  "1.000,5", "1 000", "1e999", "1e-999", True, float("nan")])
def test_valores_invalidos_nao_entram_no_cadastro_ou_calculo(valor):
    assert decimal_cubagem(valor) is None
    with pytest.raises(ValueError):
        normalizar_m3_individual(valor)
    assert resolver_cubagem_produtos([_produto(valor=valor)], "a")["001"]["status"] == "pendente"


def test_mutacao_consolida_apenas_aliases_individuais_iguais():
    dados = {"cg_M³ individual": "3.125E-5", "m3_individual": "0,00003125",
             "m3_unidade": "Peças", "nome": "Produto literal"}
    copia = copy.deepcopy(dados)
    normalizado = validar_dados_cubagem(dados, atualizado_em="2026-10-13T00:00:00Z")
    assert normalizado == {"nome": "Produto literal", "m3 individual": "0.00003125",
                          "m3_unidade": "peca", "m3_status": "confirmado",
                          "m3_origem": "Cadastro manual", "m3_atualizado_em": "2026-10-13T00:00:00Z"}
    assert dados == copia


@pytest.mark.parametrize("dados", [
    {"m3 individual": "0.1", "cg_m³ individual": "0.2"},
    {"m3 individual": "", "M3 individual": "0.2"},
    {"M3": "0.2"}, {"CBM": "0.2"}, {"m3_unidade": "caixa"},
])
def test_mutacao_bloqueia_ambiguidade_e_total(dados):
    with pytest.raises(ValueError):
        validar_dados_cubagem(dados)


def test_kit_e_par_sao_unidades_comerciais_explicitas():
    mapa = resolver_cubagem_produtos([
        _produto("K", valor="0.002", m3_unidade="kit", m3_origem="lista",
                 m3_atualizado_em="2026-10-13"),
        _produto("P", valor="0.003", m3_unidade="pares"),
        _produto("U", valor="0.004"),
    ], "a")
    assert mapa["K"]["unidade"] == "kit"
    assert mapa["K"]["origem"] == "lista"
    assert mapa["K"]["data"] == "2026-10-13"
    assert mapa["P"]["unidade"] == "par"
    assert mapa["U"]["unidade"] == "unidade_comercial"


def test_duplicatas_conflitantes_ou_status_pendente_nao_entram_no_total():
    mapa = resolver_cubagem_produtos([
        _produto(valor="0.1"), _produto(valor="0.2"),
        _produto("P", valor="0.3", m3_status="pendente"),
    ], "a")
    assert mapa["001"]["status"] == "conflito"
    assert mapa["001"]["m3_individual"] is None
    assert mapa["P"]["m3_individual"] is None


def test_migracao_dry_run_resolve_lojas_explicitamente_sem_cruzar_skus():
    produtos = [_produto("001", "a"), _produto("001", "b"), _produto("1", "a")]
    historicos = [
        _historico(store="", loja_sync="Loja A", valor="0.002"),
        _historico(store="", loja_sync="Loja B", valor="0.004"),
    ]
    antes = copy.deepcopy((historicos, produtos))
    plano = planejar_migracao_cubagem(historicos, produtos, lojas_por_nome={"Loja A": "a", "Loja B": "b"})
    candidatos = {(p["store_id"], p["sku"]): p["m3 individual"] for p in plano["candidatos"]}
    assert candidatos == {("a", "001"): "0.002", ("b", "001"): "0.004"}
    assert (historicos, produtos) == antes


def test_migracao_ignora_sku_alias_unidade_desconhecida_e_ausencia_de_loja():
    produtos = [_produto("1"), _produto("P"), _produto("U")]
    historicos = [
        _historico("001"), _historico("P", m3_unidade=""),
        _historico("U", store=""), _historico("U", m3_unidade="caixa"),
    ]
    plano = planejar_migracao_cubagem(historicos, produtos)
    assert not plano["candidatos"]
    assert {p["motivo"] for p in plano["pendentes"]} == {
        "sku_exato_ausente", "unidade_nao_comprovada", "loja_nao_comprovada",
    }


def test_migracao_unidade_comercial_exige_documento_e_totais_nao_servem():
    produtos = [_produto()]
    plano = planejar_migracao_cubagem([
        _historico(m3_unidade="", _unidade_comercial_documentada=True),
        {"sku": "001", "store_id": "a", "M3": "0.002", "CBM": "0.002"},
    ], produtos)
    assert plano["candidatos"][0]["m3_unidade"] == "unidade_comercial"
    assert plano["pendentes"][0]["motivo"] == "sem_campo_individual"


def test_migracao_nao_sobrescreve_atual_e_reporta_fontes_divergentes():
    produtos = [_produto(valor="0.001", m3_unidade="par")]
    plano = planejar_migracao_cubagem([_historico(valor="0.002")], produtos)
    assert not plano["candidatos"]
    assert plano["conflitos"][0]["motivo"] == "valor_atual_preservado"
    plano = planejar_migracao_cubagem([
        _historico(valor="0.002"), _historico(valor="0.003", _origem="outra-lista.xlsx"),
    ], [_produto()])
    assert plano["conflitos"][0]["motivo"] == "fontes_divergentes"


def test_migracao_preserva_exclusao_e_nao_regrava_valor_identico():
    produtos = [
        _produto(valor="0.002", m3_unidade="par"),
        _produto("DEL", valor="", deleted_at_utc="2026-01-01"),
    ]
    plano = planejar_migracao_cubagem([_historico(), _historico("DEL")], produtos)
    assert not plano["candidatos"]
    assert plano["ja_cadastrados"][0]["sku"] == "001"
    assert plano["pendentes"][0]["motivo"] == "produto_excluido"


def test_migracao_aliases_divergentes_e_valor_atual_invalido_sao_conflitos():
    plano = planejar_migracao_cubagem([
        _historico(**{"m3 individual": "0.009"}),
        _historico("BAD"),
    ], [_produto(), _produto("BAD", valor="NaN")])
    assert not plano["candidatos"]
    assert {p["motivo"] for p in plano["conflitos"]} == {
        "aliases_individuais_divergentes", "cadastro_atual_pendente",
    }

def test_status_e_data_enviados_nao_decidem_validade():
    dados = validar_dados_cubagem(
        {"m3 individual": "", "m3_status": "confirmado", "m3_atualizado_em": "forjada"},
        atualizado_em="2026-10-13T00:00:00Z",
    )
    assert dados["m3_status"] == "pendente"
    assert dados["m3_atualizado_em"] == "2026-10-13T00:00:00Z"
    dados = validar_dados_cubagem({"m3_status": "confirmado", "m3_atualizado_em": "forjada"})
    assert dados == {}
    dados = validar_dados_cubagem({"m3 individual": "0.001", "m3_status": "conflito"})
    assert dados["m3_status"] == "confirmado"


def test_duplicata_invalida_impede_escolha_arbitraria_de_valor():
    mapa = resolver_cubagem_produtos([_produto(valor="0.1"), _produto(valor="NaN")], "a")
    assert mapa["001"]["status"] == "pendente"
    assert mapa["001"]["m3_individual"] is None


def test_migracao_nao_converte_unidade_incompativel_mesmo_com_flag_documental():
    plano = planejar_migracao_cubagem([
        _historico(m3_unidade="caixa", _unidade_comercial_documentada=True),
    ], [_produto()])
    assert not plano["candidatos"]
    assert plano["pendentes"][0]["motivo"] == "unidade_nao_comprovada"


def test_alias_cbm_individual_e_compatibilidade_explicitamente_individual():
    dados = validar_dados_cubagem(
        {"CBM individual": "0.00003125"}, atualizado_em="2026-10-13T00:00:00Z",
    )
    assert dados["m3 individual"] == "0.00003125"
    assert "CBM individual" not in dados
    historico = {"sku": "001", "store_id": "a", "CBM individual": "0.002",
                 "m3_unidade": "par", "_origem": "documento-confirmado.xlsx"}
    plano = planejar_migracao_cubagem([historico], [_produto()])
    assert plano["candidatos"][0]["m3 individual"] == "0.002"


def test_ponte_de_identidade_bruta_nao_usa_sku_publico_normalizado():
    produtos = [_produto("001", valor="0.002", cubagem_sku="1")]
    mapa = resolver_cubagem_produtos(produtos, "a")
    assert set(mapa) == {"1"}
    assert mapa["1"]["m3_individual"] == Decimal("0.002")
    assert mapa["1"]["sku_cadastro"] == "001"


def test_migracao_usa_identidade_bruta_e_reporta_chave_publica_para_aplicacao():
    produtos = [_produto("001", cubagem_sku="1")]
    plano = planejar_migracao_cubagem([_historico("1"), _historico("001")], produtos)
    assert len(plano["candidatos"]) == 1
    assert plano["candidatos"][0]["sku"] == "1"
    assert plano["candidatos"][0]["sku_cadastro"] == "001"
    assert plano["pendentes"][0]["sku"] == "001"
    assert plano["pendentes"][0]["motivo"] == "sku_exato_ausente"


def test_migracao_respeita_valor_atual_e_exclusao_na_identidade_bruta():
    produtos = [
        _produto("001", valor="0.002", m3_unidade="par", cubagem_sku="1"),
        _produto("002", cubagem_sku="2", deleted_at_utc="2026-10-13"),
    ]
    plano = planejar_migracao_cubagem([_historico("1"), _historico("2")], produtos)
    assert not plano["candidatos"]
    assert plano["ja_cadastrados"][0]["sku"] == "1"
    assert plano["pendentes"][0]["motivo"] == "produto_excluido"
