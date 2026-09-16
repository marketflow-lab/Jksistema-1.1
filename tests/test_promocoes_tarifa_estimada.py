import copy

import pytest

from backend.services.promocoes_tarifa_estimada import estimar_tarifa_promocao
from backend.services import promocoes_api_analise as analysis
import test_promocoes_desconto_ml_fluxo as flow


def cotacao(**campos):
    return {"ad_cost": 16, "ad_cost_price_context": 80,
            "ad_cost_source": "sites/MLB/listing_prices", **campos}


def estimar(raw=None, fee=None, **opcoes):
    return estimar_tarifa_promocao(raw or {}, 80, cotacao() if fee is None else fee,
                                  preco_raw=80, **opcoes)


def test_desconto_desconhecido_usa_tarifa_total_da_consulta_sem_somar_fixa():
    resultado = estimar(fee=cotacao(fixed_fee_amount=6.75))
    assert resultado["tarifa"] == 16
    assert resultado["fonte"] == "estimativa.listing_prices"
    assert "beneficio ML nao confirmado" in resultado["motivo"]


@pytest.mark.parametrize("alteracoes", [
    {"ad_cost_price_context": 100}, {"ad_cost_price_context": None},
    {"ad_cost_source": "outra_origem"}, {"ad_cost": None},
    {"ad_cost": -1}, {"ad_cost": 81}, {"ad_cost": float("nan")},
    {"ad_cost": float("inf")}, {"ad_cost": True},
])
def test_nao_inventa_tarifa_sem_dados_numericos_da_consulta_no_preco(alteracoes):
    assert estimar(fee=cotacao(**alteracoes)) is None


def test_componentes_nao_somam_parcelamento_duas_vezes():
    fee = cotacao(ad_cost=None, sale_fee_total_pct=17, meli_fee_pct=12,
                  financing_fee_pct=5, fixed_fee_amount=6)
    assert estimar(fee=fee)["tarifa"] == 19.6
    fee.pop("sale_fee_total_pct")
    assert estimar(fee=fee)["tarifa"] == 19.6
    fee.pop("financing_fee_pct")
    assert estimar(fee=fee) is None


@pytest.mark.parametrize("fixa", [None, -2, float("inf")])
def test_componentes_exigem_taxa_fixa_valida_inclusive_zero(fixa):
    assert estimar(fee=cotacao(ad_cost=None, sale_fee_total_pct=17, fixed_fee_amount=fixa)) is None
    assert estimar(fee=cotacao(ad_cost=None, sale_fee_total_pct=17, fixed_fee_amount=0))["tarifa"] == 13.6


@pytest.mark.parametrize("fonte", ["estimativa_legada", "listing_fee"])
def test_nao_reconstroi_com_taxa_por_preco_antiga_ou_taxa_de_publicacao(fonte):
    assert estimar(fee=cotacao(ad_cost=None, sale_fee_total_pct=17,
                              fixed_fee_amount=6, fixed_fee_source=fonte)) is None


def test_coparticipacao_parcial_meli_aproxima_tarifa_sem_tornar_evidencia_exata():
    raw = {"promotion_type": "SMART", "original_price": 100, "meli_percentage": 4}
    assert estimar(raw)["tarifa"] == 12
    assert estimar(raw)["fonte"].endswith("menos_percentual_meli")
    raw["seller_percentage"] = 16
    assert estimar(raw)["tarifa"] == 12
    assert estimar(raw)["fonte"].endswith("menos_coparticipacao_reconciliada")


@pytest.mark.parametrize("alteracoes", [
    {"original_price": None}, {"meli_percentage": 120},
    {"meli_percentage": 30}, {"seller_percentage": 5},
    {"seller_percentage": "invalido"}, {"promotion_type": "SELLER_CAMPAIGN"},
    {"meli_percentage": float("nan")}, {"meli_percentage": 19},
])
def test_nao_abate_percentual_incompativel_ou_beneficio_maior_que_tarifa(alteracoes):
    raw = {"promotion_type": "SMART", "original_price": 100, "meli_percentage": 4, **alteracoes}
    assert estimar(raw)["tarifa"] == 16


def test_beneficio_conhecido_e_cotacao_ja_ajustada_nao_sao_somados():
    assert estimar(desconto_validado=4)["tarifa"] == 12
    fee = cotacao(promotion_fee_discount_applied=True, promotion_fee_charged=12)
    assert estimar(fee=fee, desconto_validado=4)["tarifa"] == 12
    assert estimar(fee=fee, desconto_validado=4)["fonte"].endswith("promocao_aplicada")


def test_dados_divergentes_e_outro_preco_nao_fornecem_beneficio():
    raw = {"promotion_type": "SMART", "original_price": 100, "meli_percentage": 4}
    assert estimar(raw, desconto_validado=4, conflito="conflito_tarifa_recebivel")["tarifa"] == 16
    resultado = estimar_tarifa_promocao(raw, 80, cotacao(), preco_raw=70, desconto_validado=4)
    assert resultado["tarifa"] == 16


def test_tarifa_zero_e_entradas_preservadas():
    raw, fee = {}, cotacao(ad_cost=0)
    original = copy.deepcopy((raw, fee))
    assert estimar_tarifa_promocao(raw, 80, fee)["tarifa"] == 0
    assert (raw, fee) == original


@pytest.fixture(scope="module")
def fluxo():
    flow.PromocoesDescontoMlFluxoTests.setUpClass()
    return flow.PromocoesDescontoMlFluxoTests()


@pytest.mark.parametrize("modo", ["direta", "job", "arquivos"])
@pytest.mark.parametrize("meli,tarifa,liquido", [(None, 18.83, 30.54), (2.4, 15.25, 34.12)])
@pytest.mark.parametrize("tipo_no_payload", [True, False])
def test_estimativa_chega_ao_json_com_flags_em_todos_os_modos(fluxo, modo, meli, tarifa, liquido, tipo_no_payload):
    raw = {"id": flow.PROMO_B_ID, "promotion_id": flow.PROMO_B_ID,
           "promotion_type": "SMART", "status": "candidate", "item_id": flow.ITEM_ID,
           "price": 110.79, "original_price": 149.24}
    if not tipo_no_payload:
        raw.pop("promotion_type")
        raw = fluxo._confirmar_contexto_candidate(raw, flow.PROMO_B_ID, "SMART")
    if meli is not None:
        raw["meli_percentage"] = meli
    interna, linha = fluxo._executar_fluxo(raw, com_arquivos=modo == "arquivos",
                                         via_api_direta=modo == "direta", preco_arquivo=110.79)
    assert interna["action_tarifa_ml"] == tarifa
    assert linha["action_valor_liquido_ml"] == liquido
    assert linha["_jk_tarifa_ml_estimada"] is True
    assert linha["action_financeiro_estimado"] is True
    assert linha["action_financeiro_exato"] is False
    assert linha["_jk_tarifa_ml_estimativa_motivo"]
    assert linha["action_financeiro_estimativa_motivo"]
    assert linha["Desconto ML"] == "Não informado pela API"
    assert linha["Margem ML"]
    assert "Estimado" not in linha["Tarifa ML"]  # Rotulo da UI nao contamina o numero.


@pytest.mark.parametrize("faltante", ["frete", "custo", "imposto"])
def test_tarifa_estimada_nao_preenche_outros_componentes_ausentes(fluxo, faltante):
    opcoes = {"frete_b_data": {"shipping_cost": None}} if faltante == "frete" else {faltante if faltante == "custo" else "imposto_rate": None}
    raw = {"id": flow.PROMO_B_ID, "promotion_id": flow.PROMO_B_ID,
           "promotion_type": "SMART", "status": "candidate", "item_id": flow.ITEM_ID,
           "price": 110.79, "original_price": 149.24}
    interna, linha = fluxo._executar_fluxo(raw, **opcoes)
    assert linha["_jk_tarifa_ml_estimada"] is True
    assert linha["action_financeiro_estimado"] is False
    assert linha["action_financeiro_exato"] is False
    assert linha["Margem ML"] == ""
    assert faltante in linha["action_financeiro_motivo"]


def test_tarifa_confirmada_tem_prioridade_e_sem_estimado(fluxo):
    raw = {"id": flow.PROMO_B_ID, "promotion_id": flow.PROMO_B_ID,
           "promotion_type": "SMART", "status": "candidate", "item_id": flow.ITEM_ID,
           "price": 110.79, "original_price": 149.24, "sale_fee_amount": 10}
    _, linha = fluxo._executar_fluxo(raw)
    assert linha["action_tarifa_ml"] == 10
    assert linha["action_financeiro_exato"] is True
    assert linha["_jk_tarifa_ml_estimada"] is False
    assert linha["action_financeiro_estimado"] is False


def test_campanha_divergente_nao_recebe_estimativa(fluxo, monkeypatch):
    def consultar(*args, **kwargs):
        pytest.fail("Identidade divergente nao pode chegar a consulta de tarifas")
    monkeypatch.setattr(analysis, "_ml_obter_taxas_anuncio", consultar)
    raw = fluxo._confirmar_contexto_candidate({"price": 80, "status": "candidate"},
                                              flow.PROMO_B_ID, "SMART")
    resultado, _ = analysis._promo_obter_contexto_financeiro_acao(
        "tenant-a", "loja-a", {}, flow.ITEM_ID, {}, raw, "OUTRA-CAMPANHA",
        100, 80, 20, 10, .1, promotion_type_esperado="SMART")
    assert resultado["tarifa"] is None
    assert resultado.get("estimado", False) is False


@pytest.mark.parametrize("campo,valor", [
    ("custo", float("inf")), ("custo", -1), ("custo", float("nan")),
    ("imposto", float("inf")), ("imposto", -0.1), ("imposto", 2),
    ("frete", float("inf")), ("frete", -10), ("frete", float("nan")),
])
def test_estimativa_nao_gera_lucro_com_componentes_invalidos(fluxo, campo, valor):
    custo = valor if campo == "custo" else 20
    imposto = valor if campo == "imposto" else .2
    frete = valor if campo == "frete" else 5
    resultado = analysis._promo_calcular_contexto_financeiro_acao(
        {"price": 80, "original_price": 100}, 100, 80, 20, cotacao(),
        {"shipping_cost": frete, "shipping_exact_for_price": True, "shipping_price_context": 80},
        custo, imposto)
    assert resultado["tarifa"] == 16
    assert resultado["tarifa_estimada"] is True
    assert resultado["estimado"] is False
    assert resultado["valor_liquido"] is None
    assert resultado["margem"] is None
    assert campo in resultado["motivo"]
