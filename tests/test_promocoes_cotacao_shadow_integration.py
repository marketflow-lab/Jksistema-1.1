import copy
import json
from unittest.mock import patch

import pytest

from backend.services import mercadolivre_legacy_core as mlcore
from backend.services import promocoes_api_analise as analysis
from backend.services import promocoes_core
from backend.services import promocoes_cotacao_shadow as shadow


def _configure_runtime():
    core_peers = {
        name: getattr(promocoes_core, name)
        for name in promocoes_core.__all__
        if hasattr(promocoes_core, name)
    }
    mlcore.configure_mercadolivre_legacy_core_runtime(peers=core_peers)
    peers = dict(core_peers)
    peers.update({
        name: getattr(mlcore, name)
        for name in mlcore.__all__
        if hasattr(mlcore, name)
    })
    analysis.configure_promocoes_api_analise_runtime(peers=peers)


@pytest.fixture(scope="module", autouse=True)
def _runtime_configured():
    _configure_runtime()


def _inputs(*, promotion_price=80.0, shipping_price_context=80.0):
    return (
        {
            "promotion_id": "PROMO-SHADOW-TEST",
            "offer_id": "OFFER-SHADOW-TEST",
            "price": promotion_price,
            "sale_fee_amount": 14.77,
        },
        {
            "ad_cost": 16.61,
            "ad_cost_source": "sites/MLB/listing_prices",
            "ad_cost_exact_for_price": True,
            "ad_cost_price_context": 80.0,
        },
        {
            "shipping_cost": 18.85,
            "shipping_exact_for_price": True,
            "shipping_price_context": shipping_price_context,
            "shipping_cost_retry_source": "users/shipping_options/free/contexto",
        },
    )


def _calculate(raw, fee, shipping):
    return analysis._promo_calcular_contexto_financeiro_acao(
        raw,
        100.0,
        80.0,
        20.0,
        fee,
        shipping,
        20.0,
        0.10,
    )


def _response_bytes(result):
    return json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_shadow_is_off_by_default_and_unknown_values_fail_closed(monkeypatch):
    raw, fee, shipping = _inputs()
    monkeypatch.delenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, raising=False)

    with patch.object(
        shadow,
        "observar_cotacao_promocao",
        side_effect=AssertionError("observer must remain disabled"),
    ) as observer:
        baseline = _calculate(raw, fee, shipping)
    observer.assert_not_called()

    for disabled_value in ("", "0", "false", "unexpected"):
        monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, disabled_value)
        with patch.object(
            shadow,
            "observar_cotacao_promocao",
            side_effect=AssertionError("observer must remain disabled"),
        ) as observer:
            result = _calculate(raw, fee, shipping)
        observer.assert_not_called()
        assert _response_bytes(result) == _response_bytes(baseline)


def test_shadow_on_and_off_keep_identical_result_and_inputs(monkeypatch):
    raw, fee, shipping = _inputs()
    snapshots = copy.deepcopy((raw, fee, shipping))
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "0")
    baseline = _calculate(raw, fee, shipping)

    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "1")
    with patch.object(shadow, "observar_cotacao_promocao", return_value="match") as observer:
        enabled = _calculate(raw, fee, shipping)

    assert _response_bytes(enabled) == _response_bytes(baseline)
    assert (raw, fee, shipping) == snapshots
    observer.assert_called_once()
    observed = observer.call_args.kwargs
    assert observed["preco_efetivo"] == 80.0
    assert observed["tarifa_total"] == 14.77
    assert observed["tarifa_contexto_preco"] == 80.0
    assert observed["frete_vendedor"] == 18.85
    assert observed["frete_contexto_preco"] == 80.0
    assert observed["resultado_legado"] == enabled
    assert observed["resultado_legado"] is not enabled


def test_shadow_cannot_mutate_authoritative_result_through_its_snapshots(monkeypatch):
    raw, fee, shipping = _inputs()
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "1")

    def mutate_snapshots(**observed):
        observed["promocao"].clear()
        observed["resultado_legado"].clear()
        return "shadow_error"

    with patch.object(
        shadow,
        "observar_cotacao_promocao",
        side_effect=mutate_snapshots,
    ):
        result = _calculate(raw, fee, shipping)

    assert raw["promotion_id"] == "PROMO-SHADOW-TEST"
    assert result == {
        "tarifa": 14.77,
        "valor_liquido": 18.38,
        "margem": 22.975,
        "exato": True,
        "fonte": "seller_promotions.sale_fee_amount",
    }


def test_shadow_exception_is_swallowed_and_authoritative_result_is_unchanged(monkeypatch):
    raw, fee, shipping = _inputs()
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "0")
    baseline = _calculate(raw, fee, shipping)

    shadow.reset_contadores_shadow()
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "true")
    with patch.object(
        shadow,
        "observar_cotacao_promocao",
        side_effect=RuntimeError("must not escape"),
    ):
        enabled = _calculate(raw, fee, shipping)

    assert _response_bytes(enabled) == _response_bytes(baseline)
    assert shadow.snapshot_contadores_shadow()["shadow_error"] == 1


def test_shadow_uses_existing_values_without_financial_network_calls(monkeypatch):
    raw, fee, shipping = _inputs()
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "on")

    with (
        patch.object(
            analysis,
            "_ml_obter_taxas_anuncio",
            side_effect=AssertionError("unexpected fee lookup"),
            create=True,
        ) as fee_lookup,
        patch.object(
            analysis,
            "_ml_obter_frete_detalhado",
            side_effect=AssertionError("unexpected shipping lookup"),
            create=True,
        ) as shipping_lookup,
    ):
        result = _calculate(raw, fee, shipping)

    assert result["exato"] is True
    fee_lookup.assert_not_called()
    shipping_lookup.assert_not_called()


def test_original_one_cent_context_difference_is_observed_not_rewritten(monkeypatch):
    raw, fee, shipping = _inputs(promotion_price=80.01)
    shadow.reset_contadores_shadow()
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "sim")

    result = _calculate(raw, fee, shipping)

    assert result["exato"] is True
    counters = shadow.snapshot_contadores_shadow()
    assert counters["exactness_difference"] == 1
    assert counters["match"] == 0


def test_promotion_scope_is_forwarded_only_to_ephemeral_capture(monkeypatch):
    raw, fee, shipping = _inputs()
    monkeypatch.setenv(analysis.PROMO_FINANCIAL_QUOTE_SHADOW_ENV, "1")

    with patch.object(shadow, "observar_cotacao_promocao", return_value="match") as observer:
        result = analysis._promo_calcular_contexto_financeiro_acao(
            raw,
            100.0,
            80.0,
            20.0,
            fee,
            shipping,
            20.0,
            0.10,
            client_id="tenant-a",
            loja="store-a",
        )

    assert result["exato"] is True
    assert observer.call_args.kwargs["client_id"] == "tenant-a"
    assert observer.call_args.kwargs["loja"] == "store-a"
