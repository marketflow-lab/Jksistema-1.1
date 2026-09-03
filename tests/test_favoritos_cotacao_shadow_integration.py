from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from backend.services import favoritos_endpoints, favoritos_ml
from backend.services import mercadolivre_cotacao_shadow as shared_shadow


def _float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


@pytest.fixture(autouse=True)
def _minimal_runtime(monkeypatch):
    monkeypatch.setattr(favoritos_ml, "_to_float_safe", _float, raising=False)
    monkeypatch.setattr(
        favoritos_ml,
        "_to_rate_safe",
        lambda value: None if _float(value) is None else (_float(value) / 100 if _float(value) > 1 else _float(value)),
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_resolver_custo_por_sku",
        lambda values, sku: values.get(sku),
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_resolver_imposto_rate_por_sku",
        lambda values, sku: values.get(sku),
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_flag_frete_gratis",
        lambda value: str(value or "").strip().lower() in {"1", "true", "sim"},
        raising=False,
    )
    monkeypatch.setattr(
        favoritos_ml,
        "_favoritos_taxa_padrao_por_tipo_anuncio",
        lambda _anuncio: 0.12,
        raising=False,
    )
    monkeypatch.delenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, raising=False)


def _sidecar(**overrides):
    value = {
        "currency_id": "BRL",
        "tarifa_total": 10,
        "tarifa_exata": True,
        "tarifa_fonte": "sites/MLB/listing_prices",
        "tarifa_contexto_preco": 100,
        "frete_vendedor": 5,
        "frete_exato": True,
        "frete_fonte": "users/shipping_options/free",
        "frete_contexto_preco": 100,
        "contexto_financeiro": {
            "category_id": "MLB-CATEGORY",
            "listing_type_id": "gold_special",
            "logistic_type": "cross_docking",
            "shipping_mode": "me2",
        },
    }
    value.update(overrides)
    return value


def _announcement(**overrides):
    value = {
        "id": "MLB-NAO-ENVIAR",
        "sku": "SKU-A",
        "price": 100,
        "ad_cost": 10,
        "shipping_seller_cost": 5,
        favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SIDECAR: _sidecar(),
    }
    value.update(overrides)
    return value


def _calculate(announcement):
    return favoritos_ml._favoritos_aplicar_margem_anuncio_ml(
        announcement,
        "SKU-A",
        {"SKU-A": 30},
        {"SKU-A": 0.10},
        client_id="tenant-a",
        loja="store-a",
    )


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_shadow_off_and_on_keep_identical_public_announcement(monkeypatch):
    off = _announcement()
    baseline = _calculate(off)
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "1")
    on = _announcement()

    with patch.object(shared_shadow, "observar_cotacao_financeira", return_value="match") as observer:
        enabled = _calculate(on)

    assert _bytes(enabled) == _bytes(baseline)
    assert favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SIDECAR not in enabled
    observer.assert_called_once()
    observed = observer.call_args.kwargs
    assert observed["origem"] == "favoritos"
    assert observed["preco_efetivo"] == 100.0
    assert observed["tarifa_total"] == 10.0
    assert observed["frete_vendedor"] == 5.0
    assert observed["tarifa_exata"] is True
    assert observed["frete_exato"] is True
    assert observed["resultado_legado"]["valor_liquido"] == 45.0
    assert observed["client_id"] == "tenant-a"
    assert observed["loja"] == "store-a"


def test_shadow_exception_cannot_change_legacy_result(monkeypatch):
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "true")
    expected_input = _announcement()
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "0")
    baseline = _calculate(expected_input)
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "true")

    with patch.object(
        shared_shadow,
        "observar_cotacao_financeira",
        side_effect=RuntimeError("must be swallowed"),
    ):
        enabled = _calculate(_announcement())

    assert _bytes(enabled) == _bytes(baseline)
    assert favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SIDECAR not in enabled


def test_missing_shipping_remains_missing_for_shadow_but_legacy_is_untouched(monkeypatch):
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "on")
    announcement = _announcement(
        shipping_seller_cost=None,
        shipping_cost=None,
        shipping={},
        **{
            favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SIDECAR: _sidecar(
                frete_vendedor=None,
                frete_exato=False,
                frete_fonte="",
                frete_contexto_preco=None,
            )
        },
    )
    with patch.object(shared_shadow, "observar_cotacao_financeira", return_value="exactness_difference") as observer:
        result = _calculate(announcement)

    assert result["margem_completa"] is True
    assert result["valor_liquido"] == 50.0
    assert observer.call_args.kwargs["frete_vendedor"] is None
    assert observer.call_args.kwargs["frete_exato"] is False


def test_same_sku_shipping_fallback_is_never_marked_exact(monkeypatch):
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "sim")
    announcement = _announcement(shipping_cost_fallback_source="outro_anuncio_mesmo_sku")
    with patch.object(shared_shadow, "observar_cotacao_financeira", return_value="exactness_difference") as observer:
        _calculate(announcement)
    observed = observer.call_args.kwargs
    assert observed["frete_exato"] is False
    assert observed["frete_fallback"] is True
    assert observed["frete_fonte"] == "favoritos.same_sku_fallback"


def test_sidecar_builder_drops_item_seller_sku_and_campaign_identity():
    context = favoritos_endpoints._favoritos_contexto_cotacao_shadow(
        {
            "id": "MLB-SECRET",
            "seller_id": "SELLER-SECRET",
            "seller_custom_field": "SKU-SECRET",
            "category_id": "MLB-CAT",
            "listing_type_id": "gold_pro",
            "condition": "new",
            "currency_id": "BRL",
            "shipping": {"mode": "me2", "logistic_type": "fulfillment"},
        },
        {"price": 100, "promotion_id": "PROMO-SECRET"},
        {
            "ad_cost": 17,
            "ad_cost_source": "sites/MLB/listing_prices",
            "ad_cost_exact_for_price": True,
            "ad_cost_price_context": 100,
        },
        {
            "shipping_cost": 9,
            "shipping_exact_for_price": True,
            "shipping_price_context": 100,
            "shipping_cost_source_path": "official",
        },
    )
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    for forbidden in ("MLB-SECRET", "SELLER-SECRET", "SKU-SECRET", "PROMO-SECRET"):
        assert forbidden not in serialized


def test_sidecar_is_not_built_or_attached_when_shadow_flag_is_off(monkeypatch):
    monkeypatch.delenv(
        favoritos_endpoints.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV,
        raising=False,
    )
    announcement = {"id": "MLB-LEGACY", "price": 100}
    snapshot = copy.deepcopy(announcement)

    with patch.object(
        favoritos_endpoints,
        "_favoritos_contexto_cotacao_shadow",
        side_effect=AssertionError("sidecar builder must stay off"),
    ) as builder:
        result = favoritos_endpoints._favoritos_anexar_contexto_cotacao_shadow(
            announcement,
            {},
            {},
            {},
            {},
        )

    assert result == snapshot
    assert builder.call_count == 0


def test_observer_receives_detached_legacy_and_context_snapshots(monkeypatch):
    monkeypatch.setenv(favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SHADOW_ENV, "1")
    original = _announcement()
    original_copy = copy.deepcopy(original)

    def mutate(**kwargs):
        kwargs["resultado_legado"].clear()
        kwargs["contexto_financeiro"].clear()
        return "shadow_error"

    with patch.object(shared_shadow, "observar_cotacao_financeira", side_effect=mutate):
        result = _calculate(original)

    assert result["valor_liquido"] == 45.0
    assert original_copy["id"] == result["id"]
    assert favoritos_ml.FAVORITOS_FINANCIAL_QUOTE_SIDECAR not in result
