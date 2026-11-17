"""Purchase planning invariants using synthetic sales and receipt calendars."""

from __future__ import annotations

import json
import math

import pytest

from medias_compras import calcular_reposicao_periodo


REFERENCIA = "2026-11-01"
MESES_FECHADOS = (
    "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
    "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10",
)


def historico(valores=None, *, mes_atual=5000):
    valores = [100] * 12 if valores is None else valores
    meses = MESES_FECHADOS[-len(valores):] if valores else ()
    return {**dict(zip(meses, valores)), "2026-11": mes_atual}


def planejar(*, vendas=None, saldo=700, transito=0, entradas=None, **kwargs):
    return calcular_reposicao_periodo(
        total_vendido_periodo=600,
        saldo_atual=saldo,
        estoque_em_transito=transito,
        periodo_meses=6,
        vendas_mensais=historico() if vendas is None else vendas,
        data_referencia=REFERENCIA,
        entradas_previstas=[] if entradas is None else entradas,
        **kwargs,
    )


def exigir_json_finito(resultado):
    # API/cache serialization must never receive NaN/Infinity from source data.
    json.dumps(resultado, allow_nan=False)
    for chave in (
        "media_mensal_prevista", "estoque_previsto_antes_chegada",
        "estoque_previsto_apos_chegada", "estoque_alvo_apos_chegada",
        "reserva_antes_chegada_quantidade", "falta_antes_chegada_quantidade",
        "transito_sem_previsao_quantidade",
    ):
        assert math.isfinite(resultado[chave]), chave
        assert resultado[chave] >= 0, chave
    assert isinstance(resultado["compra_sugerida"], int)
    assert resultado["compra_sugerida"] >= 0


def test_constant_sales_preserve_one_month_before_and_seven_total_after_receipt():
    resultado = planejar()

    assert resultado["planejamento_versao"] == 1
    assert resultado["data_chegada_prevista"] == "2027-05-01"
    assert resultado["media_mensal_prevista"] == pytest.approx(100)
    assert resultado["tendencia_vendas"] == "estavel"
    assert resultado["estoque_previsto_antes_chegada"] == pytest.approx(100)
    assert resultado["reserva_antes_chegada_quantidade"] == pytest.approx(100)
    assert resultado["reserva_antes_chegada_atendida"] is True
    assert resultado["risco_ruptura_antes_chegada"] is False
    assert resultado["compra_sugerida"] == 600
    assert resultado["estoque_alvo_apos_chegada"] == pytest.approx(700)
    assert resultado["estoque_previsto_apos_chegada"] == pytest.approx(700)


def test_lost_sales_before_arrival_do_not_inflate_new_order():
    resultado = planejar(saldo=200)

    assert resultado["estoque_previsto_antes_chegada"] == 0
    assert resultado["falta_antes_chegada_quantidade"] == pytest.approx(400)
    assert resultado["reserva_antes_chegada_atendida"] is False
    assert resultado["risco_ruptura_antes_chegada"] is True
    assert resultado["compra_sugerida"] == 700
    assert resultado["estoque_previsto_apos_chegada"] == pytest.approx(700)


def test_zero_at_arrival_is_below_reserve_even_without_prearrival_lost_sales():
    resultado = planejar(saldo=600)

    assert resultado["estoque_previsto_antes_chegada"] == 0
    assert resultado["falta_antes_chegada_quantidade"] == 0
    assert resultado["reserva_antes_chegada_atendida"] is False
    assert resultado["compra_sugerida"] == 700


def test_large_current_stock_never_creates_an_unnecessary_purchase():
    resultado = planejar(saldo=1600)

    assert resultado["compra_sugerida"] == 0
    assert resultado["estoque_previsto_antes_chegada"] == pytest.approx(1000)
    assert resultado["estoque_previsto_apos_chegada"] == pytest.approx(1000)


def test_current_partial_month_does_not_change_trend_or_purchase():
    pequeno = planejar(vendas=historico(mes_atual=1))
    grande = planejar(vendas=historico(mes_atual=1_000_000))

    for chave in (
        "media_mensal_prevista", "tendencia_vendas", "variacao_vendas_percentual",
        "compra_sugerida", "estoque_previsto_antes_chegada", "estoque_alvo_apos_chegada",
    ):
        assert pequeno[chave] == grande[chave], chave


def test_sales_window_selection_does_not_remove_the_trend_history():
    resultados = [
        calcular_reposicao_periodo(
            total_vendido_periodo=100 * periodo, saldo_atual=700,
            periodo_meses=periodo, vendas_mensais=historico(),
            data_referencia=REFERENCIA, entradas_previstas=[],
        )
        for periodo in (3, 6, 12)
    ]

    assert [r["compra_sugerida"] for r in resultados] == [600, 600, 600]
    assert all(r["media_mensal_prevista"] == pytest.approx(100) for r in resultados)


def test_product_trend_changes_demand_and_purchase_in_both_directions():
    estavel = planejar()
    alta = planejar(vendas=historico([100] * 6 + [80] * 3 + [120] * 3))
    queda = planejar(vendas=historico([100] * 6 + [120] * 3 + [80] * 3))

    assert alta["tendencia_vendas"] == "alta"
    assert alta["variacao_vendas_percentual"] > 0
    assert queda["tendencia_vendas"] == "queda"
    assert queda["variacao_vendas_percentual"] < 0
    assert queda["media_mensal_prevista"] < estavel["media_mensal_prevista"] < alta["media_mensal_prevista"]
    assert queda["compra_sugerida"] < estavel["compra_sugerida"] < alta["compra_sugerida"]


def test_decline_with_zero_current_stock_is_flagged_as_ambiguous():
    resultado = planejar(vendas=historico([100] * 6 + [120] * 3 + [80] * 3), saldo=0)

    assert resultado["tendencia_vendas"] == "indeterminada"
    assert resultado["planejamento_requer_revisao"] is True
    assert resultado["media_mensal_prevista"] >= 80


def test_recent_decline_is_not_stable_when_quarterly_averages_offset():
    # Synthetic six-month construction: similar quarter averages hide 72 -> 41 -> 22.
    resultado = planejar(vendas=historico([14, 49, 71, 72, 41, 22]), saldo=400)

    assert resultado["tendencia_vendas"] == "queda"
    assert resultado["variacao_vendas_percentual"] < 0
    assert resultado["media_mensal_prevista"] < 45
    exigir_json_finito(resultado)


def test_recent_decline_with_zero_stock_requires_review_instead_of_demand_reduction():
    resultado = planejar(vendas=historico([14, 49, 71, 72, 41, 22]), saldo=0)

    assert resultado["tendencia_vendas"] == "indeterminada"
    assert resultado["variacao_vendas_percentual"] < 0
    assert resultado["planejamento_requer_revisao"] is True
    assert resultado["media_mensal_prevista"] == pytest.approx(resultado["media_mensal"])
    assert "falta" in resultado["tendencia_motivo"].casefold()


def test_recent_rise_is_not_stable_when_quarterly_averages_offset():
    resultado = planejar(vendas=historico([86, 51, 29, 28, 59, 78]), saldo=400)

    assert resultado["tendencia_vendas"] == "alta"
    assert resultado["variacao_vendas_percentual"] > 0
    assert resultado["media_mensal_prevista"] > 55
    exigir_json_finito(resultado)


@pytest.mark.parametrize("valores", [
    [80, 80, 80, 120, 110, 100],
    [120, 120, 120, 80, 90, 100],
])
def test_opposing_quarterly_and_recent_signals_require_review(valores):
    resultado = planejar(vendas=historico(valores), saldo=400)

    assert resultado["tendencia_vendas"] == "indeterminada"
    assert resultado["planejamento_requer_revisao"] is True
    assert resultado["media_mensal_prevista"] == pytest.approx(resultado["media_mensal"])
    assert "diverg" in resultado["tendencia_motivo"].casefold()


def test_partial_current_month_does_not_change_the_recent_decline_signal():
    valores = [14, 49, 71, 72, 41, 22]
    parcial_zerado = planejar(vendas=historico(valores, mes_atual=0), saldo=400)
    parcial_alto = planejar(vendas=historico(valores, mes_atual=100_000), saldo=400)

    for chave in (
        "tendencia_vendas", "variacao_vendas_percentual", "media_mensal_prevista",
        "compra_sugerida", "estoque_previsto_antes_chegada",
    ):
        assert parcial_zerado[chave] == parcial_alto[chave], chave
    assert parcial_zerado["tendencia_vendas"] == "queda"


@pytest.mark.parametrize("recente,tendencia", [(110, "alta"), (90, "queda")])
def test_ten_percent_consistent_change_is_classified_symmetrically(recente, tendencia):
    resultado = planejar(vendas=historico([100] * 9 + [recente] * 3))

    assert resultado["tendencia_vendas"] == tendencia
    assert abs(resultado["variacao_vendas_percentual"]) == pytest.approx(10)


def test_isolated_sales_spike_requires_review_instead_of_becoming_permanent_growth():
    resultado = planejar(vendas=historico([100] * 11 + [1000]))

    assert resultado["tendencia_vendas"] == "indeterminada"
    assert resultado["planejamento_requer_revisao"] is True
    assert resultado["media_mensal_prevista"] == pytest.approx(100)
    assert resultado["compra_sugerida"] == 600


def test_months_without_sales_require_confirmation_of_product_availability():
    resultado = planejar(vendas=historico([100] * 6 + [100, 100, 0, 0, 100, 100]))

    assert resultado["tendencia_vendas"] == "indeterminada"
    assert resultado["planejamento_requer_revisao"] is True
    assert "disponibilidade" in resultado["tendencia_motivo"]
    exigir_json_finito(resultado)


@pytest.mark.parametrize("valores", [[100], [100, 110], [80, 90, 100, 110, 120]])
def test_short_history_is_identified_instead_of_claiming_a_reliable_trend(valores):
    resultado = planejar(vendas=historico(valores), saldo=0)

    assert resultado["tendencia_vendas"] == "insuficiente"
    exigir_json_finito(resultado)


def test_zero_sales_are_finite_and_do_not_generate_a_purchase():
    resultado = planejar(vendas=historico([0] * 12, mes_atual=0), saldo=0)

    assert resultado["compra_sugerida"] == 0
    assert resultado["media_mensal_prevista"] == 0
    assert resultado["tendencia_vendas"] in {"insuficiente", "indeterminada", "estavel"}
    exigir_json_finito(resultado)


def test_zero_comparison_period_does_not_generate_infinite_growth():
    resultado = planejar(vendas=historico([0] * 9 + [100] * 3), saldo=0)

    assert resultado["tendencia_vendas"] in {"insuficiente", "indeterminada"}
    assert resultado["variacao_vendas_percentual"] is None or math.isfinite(resultado["variacao_vendas_percentual"])
    exigir_json_finito(resultado)


def test_first_sale_date_prevents_diluting_a_new_product_with_prelaunch_zeros():
    resultado = planejar(
        vendas=historico([0] * 11 + [100]), saldo=0, primeira_venda="2026-10-01",
    )

    assert resultado["tendencia_vendas"] == "insuficiente"
    assert resultado["media_mensal_prevista"] == pytest.approx(100)
    assert resultado["compra_sugerida"] == 700
    assert resultado["planejamento_requer_revisao"] is True


def test_early_confirmed_receipt_can_preserve_prearrival_reserve():
    resultado = planejar(
        saldo=200, transito=500,
        entradas=[{"quantidade": 500, "data_prevista": "2026-12-01", "data_estimada": False}],
    )

    assert resultado["estoque_previsto_antes_chegada"] == pytest.approx(100)
    assert resultado["falta_antes_chegada_quantidade"] == 0
    assert resultado["reserva_antes_chegada_atendida"] is True
    assert resultado["compra_sugerida"] == 600
    assert resultado["estoque_previsto_apos_chegada"] == pytest.approx(700)


def test_late_receipt_never_hides_an_earlier_stockout():
    resultado = planejar(
        saldo=200, transito=500,
        entradas=[{"quantidade": 500, "data_prevista": "2027-04-01", "data_estimada": False}],
    )

    # The product runs out in January. An April delivery cannot undo those lost sales.
    assert resultado["risco_ruptura_antes_chegada"] is True
    assert resultado["falta_antes_chegada_quantidade"] > 0
    assert resultado["estoque_previsto_antes_chegada"] > 0


def test_receipt_after_the_new_delivery_does_not_cover_the_prearrival_period():
    resultado = planejar(
        saldo=200, transito=500,
        entradas=[{"quantidade": 500, "data_prevista": "2027-06-01", "data_estimada": False}],
    )

    assert resultado["estoque_previsto_antes_chegada"] == 0
    assert resultado["falta_antes_chegada_quantidade"] == pytest.approx(400)
    assert resultado["reserva_antes_chegada_atendida"] is False
    assert resultado["risco_ruptura_antes_chegada"] is True
    assert resultado["compra_sugerida"] == 700


def test_same_day_receipt_completes_target_but_is_not_old_stock_reserve():
    resultado = planejar(
        transito=500,
        entradas=[{"quantidade": 500, "data_prevista": "2027-05-01", "data_estimada": False}],
    )

    assert resultado["estoque_previsto_antes_chegada"] == pytest.approx(100)
    assert resultado["reserva_antes_chegada_atendida"] is True
    assert resultado["compra_sugerida"] == 100
    assert resultado["estoque_previsto_apos_chegada"] == pytest.approx(700)


def test_receipts_without_a_date_reduce_planned_purchase_but_require_review():
    sem_pedido = planejar()
    sem_data = planejar(
        transito=500,
        entradas=[{"quantidade": 500, "data_prevista": None, "data_estimada": True}],
    )

    assert sem_data["estoque_previsto_antes_chegada"] == pytest.approx(100)
    assert sem_data["transito_sem_previsao_quantidade"] == pytest.approx(500)
    assert sem_data["planejamento_requer_revisao"] is True
    assert sem_data["compra_sugerida"] < sem_pedido["compra_sugerida"]


def test_unknown_receipt_does_not_claim_to_prevent_a_stockout():
    resultado = planejar(
        saldo=200, transito=500,
        entradas=[{"quantidade": 500, "data_prevista": None, "data_estimada": True}],
    )

    assert resultado["reserva_antes_chegada_atendida"] is False
    assert resultado["risco_ruptura_antes_chegada"] is True
    assert resultado["falta_antes_chegada_quantidade"] == pytest.approx(400)
    assert resultado["planejamento_requer_revisao"] is True


def test_purchase_rounds_up_to_five_without_adding_an_extra_month_of_reserve():
    resultado = planejar(saldo=699)

    assert resultado["compra_sugerida"] == 605
    assert resultado["estoque_alvo_apos_chegada"] == pytest.approx(700)
    assert resultado["estoque_previsto_apos_chegada"] == pytest.approx(704)
    assert resultado["estoque_alvo_apos_chegada"] <= resultado["estoque_previsto_apos_chegada"] < resultado["estoque_alvo_apos_chegada"] + 5


@pytest.mark.parametrize("quantidade", ["1.5", "1,5"])
def test_decimal_sales_strings_do_not_become_fifteen_units(quantidade):
    resultado = planejar(vendas=historico([quantidade] * 12), saldo=0)

    assert resultado["media_mensal_prevista"] == pytest.approx(1.5)
    assert resultado["estoque_alvo_apos_chegada"] == pytest.approx(10.5)
    assert resultado["compra_sugerida"] == 15


@pytest.mark.parametrize("invalido", [-10, float("nan"), float("inf"), float("-inf")])
def test_invalid_source_quantities_never_escape_as_nonfinite_or_negative(invalido):
    vendas = historico()
    vendas["2026-08"] = invalido
    resultado = planejar(
        vendas=vendas, saldo=invalido, transito=invalido,
        entradas=[{"quantidade": invalido, "data_prevista": "2026-12-01", "data_estimada": False}],
    )

    exigir_json_finito(resultado)


def test_manual_growth_scales_the_forecast_instead_of_disabling_product_trend():
    vendas = historico([100] * 6 + [80] * 3 + [120] * 3)
    base = planejar(vendas=vendas, saldo=0)
    acrescimo = planejar(vendas=vendas, saldo=0, fator_crescimento=1.2)

    assert acrescimo["tendencia_vendas"] == base["tendencia_vendas"] == "alta"
    assert acrescimo["media_mensal_prevista"] == pytest.approx(base["media_mensal_prevista"] * 1.2, abs=0.02)
    assert acrescimo["compra_sugerida"] > base["compra_sugerida"]


def test_calls_without_monthly_history_preserve_the_legacy_calculation():
    resultado = calcular_reposicao_periodo(
        total_vendido_periodo=600, saldo_atual=700, estoque_em_transito=0, periodo_meses=6,
    )

    assert resultado["media_mensal"] == 100
    assert resultado["meses_cobertura"] == 10
    assert resultado["compra_sugerida"] == 300
