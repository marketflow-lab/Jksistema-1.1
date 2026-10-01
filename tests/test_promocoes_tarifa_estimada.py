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
    assert resultado["desconto"] is None
    assert resultado["desconto_fonte"] == resultado["desconto_motivo"] == ""


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
    assert estimar(raw)["desconto"] == 4
    assert estimar(raw)["desconto_fonte"] == "estimativa.seller_promotions.percentual_meli"
    assert estimar(raw)["desconto_motivo"]
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
    assert estimar(fee=fee, desconto_validado=4)["desconto"] is None


def test_dados_divergentes_e_outro_preco_nao_fornecem_beneficio():
    raw = {"promotion_type": "SMART", "original_price": 100, "meli_percentage": 4}
    assert estimar(raw, desconto_validado=4, conflito="conflito_tarifa_recebivel")["tarifa"] == 16
    resultado = estimar_tarifa_promocao(raw, 80, cotacao(), preco_raw=70, desconto_validado=4)
    assert resultado["tarifa"] == 16
    assert resultado["desconto"] is None
    assert estimar(raw, desconto_validado=4, conflito="conflito_tarifa_recebivel")["desconto"] is None


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
    desconto = 3.58 if meli is not None else None
    assert linha["Desconto ML"] == ("R$ 3,58" if desconto is not None else "Não informado pela API")
    assert linha["action_desconto_ml"] == desconto
    assert linha["_jk_desconto_ml_estimado"] is (desconto is not None)
    assert linha["_jk_desconto_ml_confiavel"] is False
    assert bool(linha["_jk_desconto_ml_fonte"]) is (desconto is not None)
    assert bool(linha["_jk_desconto_ml_estimativa_motivo"]) is (desconto is not None)
    assert linha["Margem ML"]
    assert "Estimado" not in linha["Tarifa ML"]  # Rotulo da UI nao contamina o numero.
    selecionada = dict(linha, action_tecnico_apto=True)
    selecionada.update({"Ação": "Participar", "Acao": "Participar", "Participar ou não": "Participar"})
    participacoes = flow.jobs._promo_automacao_montar_participacoes({"analises": [{
        "promo_b_id": flow.PROMO_B_ID, "promo_b_type": "SMART", "data": [selecionada],
    }]}, "Loja Teste")
    assert participacoes["promocoes"] == []


@pytest.mark.parametrize("faltante", ["frete", "custo", "imposto"])
def test_tarifa_estimada_nao_preenche_outros_componentes_ausentes(fluxo, faltante):
    opcoes = {"frete_b_data": {"shipping_cost": None}} if faltante == "frete" else {faltante if faltante == "custo" else "imposto_rate": None}
    raw = {"id": flow.PROMO_B_ID, "promotion_id": flow.PROMO_B_ID,
           "promotion_type": "SMART", "status": "candidate", "item_id": flow.ITEM_ID,
           "price": 110.79, "original_price": 149.24}
    interna, linha = fluxo._executar_fluxo(raw, **opcoes)
    assert linha["_jk_tarifa_ml_estimada"] is True
    assert linha["action_financeiro_estimado"] is False
    assert linha["Desconto ML"] == "Não informado pela API"
    assert linha["action_desconto_ml"] is None
    assert linha["_jk_desconto_ml_estimado"] is False
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
    assert linha["action_desconto_ml"] is None
    assert linha["Desconto ML"] == "Não informado pela API"
    assert linha["_jk_desconto_ml_estimado"] is False


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


@pytest.mark.parametrize("modo", ["direta", "job", "arquivos"])
def test_base_da_oferta_divergente_da_base_atual_preserva_promocao_um(fluxo, modo):
    raw = {"id": flow.PROMO_B_ID, "promotion_type": "SMART", "status": "candidate",
           "item_id": flow.ITEM_ID, "price": 96, "original_price": 120,
           "seller_percentage": 18, "meli_percentage": 2}
    opcoes = {"price_info": {"price": 100, "standard_price": 100, "original_price": 100},
              "item_overrides": {"price": 100, "original_price": 100},
              "com_arquivos": modo == "arquivos", "via_api_direta": modo == "direta",
              "preco_arquivo": 96}
    _, linha = fluxo._executar_fluxo(raw, **opcoes)
    controle = dict(raw)
    controle.pop("seller_percentage")
    controle.pop("meli_percentage")
    _, sem_beneficio = fluxo._executar_fluxo(controle, **opcoes)
    assert linha["Desconto ML"] == "R$ 2,40"
    assert linha["action_desconto_ml"] == 2.4
    assert linha["_jk_desconto_ml_confiavel"] is True
    assert linha["_jk_desconto_ml_estimado"] is False
    assert linha["action_financeiro_exato"] is True
    assert linha["action_financeiro_estimado"] is False
    assert linha["action_tarifa_ml"] == 19.7
    assert linha["action_valor_liquido_ml"] == 18.43
    for campo in ("% Fixa", "Tarifa", "Imposto", "Margem"):
        assert linha[campo] == sem_beneficio[campo]
    for campo in linha:
        if campo.endswith("o Final") or campo.endswith("quido"):
            assert linha[campo] == sem_beneficio[campo]


@pytest.mark.parametrize("base", [None, 0, -1, float("inf"), float("nan"), True, "invalido"])
def test_base_ausente_ou_invalida_usa_contingencia_sem_confirmar_beneficio(fluxo, base):
    raw = fluxo._confirmar_contexto_candidate({
        "promotion_type": "SMART", "status": "candidate", "price": 80,
        "original_price": base, "seller_percentage": 16, "meli_percentage": 4,
    }, flow.PROMO_B_ID, "SMART")
    resultado = analysis._promo_calcular_contexto_financeiro_acao(
        raw, 100, 80, 20, cotacao(),
        {"shipping_cost": 5, "shipping_exact_for_price": True, "shipping_price_context": 80},
        20, .1)
    assert resultado["tarifa"] == 12
    assert resultado["desconto"] == 4
    assert resultado["desconto_confiavel"] is False
    assert resultado["desconto_estimado"] is True
    assert resultado["exato"] is False
    assert resultado["valor_liquido"] == 35
    assert "base atual do anuncio" in resultado["desconto_estimativa_motivo"]


def test_base_da_oferta_definitiva_prevalece_sobre_base_anterior(fluxo):
    raw = fluxo._confirmar_contexto_candidate({
        "promotion_type": "SMART", "status": "candidate", "price": 80,
        "original_price": 100, "seller_percentage": 16, "meli_percentage": 4,
    }, flow.PROMO_B_ID, "SMART")
    resultado = analysis._promo_calcular_contexto_financeiro_acao(
        raw, 200, 80, 20, cotacao(ad_cost_exact_for_price=True),
        {"shipping_cost": 5, "shipping_exact_for_price": True, "shipping_price_context": 80},
        20, .1)
    assert resultado["tarifa"] == 12
    assert resultado["desconto"] == 4
    assert resultado["desconto_confiavel"] is True
    assert resultado["exato"] is True
    assert resultado["valor_liquido"] == 35


def test_conflito_nao_inventa_beneficio_a_partir_da_tarifa(fluxo):
    raw = fluxo._confirmar_contexto_candidate({
        "promotion_type": "SMART", "status": "candidate", "price": 80,
        "original_price": 100, "meli_percentage": 4,
        "sale_fee_amount": 10, "seller_receives": 60,
    }, flow.PROMO_B_ID, "SMART")
    resultado = analysis._promo_calcular_contexto_financeiro_acao(
        raw, 100, 80, 20, cotacao(),
        {"shipping_cost": 5, "shipping_exact_for_price": True, "shipping_price_context": 80},
        20, .1)
    assert resultado["tarifa"] == 16
    assert resultado["desconto"] is None
    assert resultado["desconto_estimado"] is False
    assert "divergem" in resultado["estimativa_motivo"]


@pytest.mark.parametrize("modo", ["direta", "job", "arquivos"])
@pytest.mark.parametrize("zero", [0, {"amount": 0}])
def test_zero_explicito_nao_se_confunde_com_desconto_desconhecido(fluxo, modo, zero):
    raw = {"id": flow.PROMO_B_ID, "promotion_type": "SMART", "status": "candidate",
           "item_id": flow.ITEM_ID, "price": 110.79, "original_price": 149.24,
           "sale_fee_discount_amount": zero}
    _, linha = fluxo._executar_fluxo(raw, com_arquivos=modo == "arquivos",
                                   via_api_direta=modo == "direta", preco_arquivo=110.79)
    assert linha["Desconto ML"] == "R$ 0,00"
    assert linha["action_desconto_ml"] == 0
    assert linha["_jk_desconto_ml_confiavel"] is True
    assert linha["_jk_desconto_ml_estimado"] is False
    assert linha["action_tarifa_ml"] == 18.83


@pytest.mark.parametrize("modo", ["direta", "job", "arquivos"])
@pytest.mark.parametrize("base", [None, 0, -1, float("nan"), float("inf"), True, "invalido"])
def test_contingencia_da_base_atual_e_igual_nos_tres_modos(fluxo, modo, base):
    raw = {"id": flow.PROMO_B_ID, "promotion_type": "SMART", "status": "candidate",
           "item_id": flow.ITEM_ID, "price": 80, "original_price": base,
           "seller_percentage": 16, "meli_percentage": 4}
    _, linha = fluxo._executar_fluxo(
        raw, price_info={"price": 100, "standard_price": 100, "original_price": 100},
        item_overrides={"price": 100, "original_price": 100},
        com_arquivos=modo == "arquivos", via_api_direta=modo == "direta", preco_arquivo=80)
    assert linha["Desconto ML"] == "R$ 4,00"
    assert linha["action_desconto_ml"] == 4
    assert linha["action_tarifa_ml"] == 18.1
    assert linha["_jk_desconto_ml_confiavel"] is False
    assert linha["_jk_desconto_ml_estimado"] is True
    assert linha["action_financeiro_exato"] is False
    assert linha["action_financeiro_estimado"] is True
    assert linha["_jk_desconto_ml_fonte"].endswith("base_anuncio")


def test_zero_de_oferta_em_outro_preco_nao_confirma_analise_com_arquivo(fluxo):
    raw = {"id": flow.PROMO_B_ID, "promotion_type": "SMART", "status": "candidate",
           "item_id": flow.ITEM_ID, "price": 80, "original_price": 100,
           "sale_fee_discount_amount": 0}
    _, linha = fluxo._executar_fluxo(raw, com_arquivos=True, preco_arquivo=90,
                                   price_info={"price": 100, "standard_price": 100})
    assert linha["action_financeiro_exato"] is False
    assert linha["action_financeiro_estimado"] is True
    assert linha["action_desconto_ml"] is None
    assert linha["_jk_desconto_ml_confiavel"] is False
    assert linha["Desconto ML"] == "Não informado pela API"
