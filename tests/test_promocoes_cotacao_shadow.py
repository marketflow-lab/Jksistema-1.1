from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal

import pytest

from backend.services.promocoes_cotacao_shadow import (
    observar_cotacao_promocao,
    reset_contadores_shadow,
    snapshot_contadores_shadow,
)


D = Decimal

CLASSIFICACOES = {
    "match",
    "rounding_difference",
    "exactness_difference",
    "value_difference",
    "shadow_incomplete",
    "shadow_error",
}


@pytest.fixture(autouse=True)
def _contadores_shadow_isolados():
    reset_contadores_shadow()
    yield
    reset_contadores_shadow()


def _entrada_exata(**overrides):
    entrada = {
        "preco_efetivo": "100.00",
        "custo_produto": "30.00",
        "aliquota_imposto": "0.10",
        "tarifa_total": "10.00",
        "tarifa_exata": True,
        "tarifa_fonte": "sites/MLB/listing_prices",
        "tarifa_contexto_preco": "100.00",
        "frete_vendedor": "5.00",
        "frete_exato": True,
        "frete_fonte": "users/shipping_options/free",
        "frete_contexto_preco": "100.00",
        "promocao": {
            "type": "MARKETPLACE_CAMPAIGN",
            "benefits": [{"type": "REBATE", "value": "20.00"}],
        },
        "resultado_legado": {
            "exato": True,
            "imposto": D("10.00"),
            "valor_liquido": D("45.00"),
            "margem": D("45.000000"),
        },
    }
    entrada.update(overrides)
    return entrada


def test_classifica_resultado_financeiro_identico_como_match():
    classificacao = observar_cotacao_promocao(**_entrada_exata())

    assert classificacao == "match"
    assert snapshot_contadores_shadow()["match"] == 1


def test_classifica_apenas_diferenca_de_arredondamento_do_fluxo_legado():
    preco = D("99.99")
    imposto_legado = preco * D("0.0725")
    liquido_legado = (
        preco
        - D("31.11")
        - imposto_legado
        - D("13.27")
        - D("8.54")
    )
    margem_legada = liquido_legado * D("100") / preco
    entrada = _entrada_exata(
        preco_efetivo=preco,
        custo_produto=D("31.11"),
        aliquota_imposto=D("0.0725"),
        tarifa_total=D("13.27"),
        tarifa_contexto_preco=preco,
        frete_vendedor=D("8.54"),
        frete_contexto_preco=preco,
        resultado_legado={
            "exato": True,
            "imposto": imposto_legado,
            "valor_liquido": liquido_legado,
            "margem_percentual": margem_legada,
        },
    )

    classificacao = observar_cotacao_promocao(**entrada)

    assert classificacao == "rounding_difference"
    assert snapshot_contadores_shadow()["rounding_difference"] == 1


def test_contexto_de_preco_um_centavo_divergente_e_diferenca_de_exatidao():
    entrada = _entrada_exata(tarifa_contexto_preco="99.99")

    classificacao = observar_cotacao_promocao(**entrada)

    assert classificacao == "exactness_difference"
    assert snapshot_contadores_shadow()["exactness_difference"] == 1


def test_divergencia_financeira_material_e_classificada_como_valor():
    entrada = _entrada_exata(
        resultado_legado={
            "exato": True,
            "imposto": D("10.00"),
            "valor_liquido": D("44.99"),
            "margem": D("44.99"),
        },
    )

    classificacao = observar_cotacao_promocao(**entrada)

    assert classificacao == "value_difference"
    assert snapshot_contadores_shadow()["value_difference"] == 1


def test_resultados_incompletos_em_ambos_os_fluxos_sao_classificados_sem_valores():
    entrada = _entrada_exata(
        tarifa_exata=False,
        resultado_legado={"exato": False},
    )

    classificacao = observar_cotacao_promocao(**entrada)

    assert classificacao == "shadow_incomplete"
    assert snapshot_contadores_shadow()["shadow_incomplete"] == 1


def test_observador_captura_erro_e_incrementa_apenas_shadow_error():
    entrada = _entrada_exata(tarifa_exata="sim")

    classificacao = observar_cotacao_promocao(**entrada)

    assert classificacao == "shadow_error"
    assert snapshot_contadores_shadow() == {
        "match": 0,
        "rounding_difference": 0,
        "exactness_difference": 0,
        "value_difference": 0,
        "shadow_incomplete": 0,
        "shadow_error": 1,
    }


def test_metricas_expoem_somente_contadores_allowlisted_sem_dados_da_cotacao():
    item_id = "MLB-NAO-PERSISTIR-987654"
    seller_id = "SELLER-NAO-PERSISTIR-123456"
    fonte_tarifa = "FONTE-TARIFA-NAO-PERSISTIR"
    fonte_frete = "FONTE-FRETE-NAO-PERSISTIR"
    entrada = _entrada_exata(
        tarifa_fonte=fonte_tarifa,
        frete_fonte=fonte_frete,
        promocao={"item_id": item_id, "seller_id": seller_id},
    )

    assert observar_cotacao_promocao(**entrada) == "match"
    snapshot = snapshot_contadores_shadow()
    serializado = json.dumps(snapshot, sort_keys=True)

    assert set(snapshot) == CLASSIFICACOES
    assert all(type(contagem) is int and contagem >= 0 for contagem in snapshot.values())
    for dado_sensivel in (
        item_id,
        seller_id,
        fonte_tarifa,
        fonte_frete,
        "100.00",
        "30.00",
    ):
        assert dado_sensivel not in serializado


def test_observacao_nao_muta_estruturas_de_entrada():
    entrada = _entrada_exata(
        promocao={
            "id": "campaign-immutability",
            "benefits": [{"type": "REBATE", "value": "20.00"}],
        },
        resultado_legado={
            "exato": True,
            "imposto": "10.00",
            "valor_liquido": "45.00",
            "margem": "45.000000",
            "metadata": {"nested": ["preservar"]},
        },
    )
    antes = deepcopy(entrada)

    assert observar_cotacao_promocao(**entrada) == "match"

    assert entrada == antes


def test_contadores_agregados_sao_thread_safe():
    total = 200

    with ThreadPoolExecutor(max_workers=8) as executor:
        classifications = list(
            executor.map(
                lambda _index: observar_cotacao_promocao(**_entrada_exata()),
                range(total),
            )
        )

    assert classifications == ["match"] * total
    assert snapshot_contadores_shadow()["match"] == total
