from backend.services.favoritos_margem import margem_calcular_anuncio


def test_margin_uses_decimal_cent_rounding_for_all_required_components():
    result = margem_calcular_anuncio(
        {
            "price": "99.99",
            "sale_fee_amount": "13.27",
            "shipping_seller_cost": "8.54",
            "currency_id": "BRL",
        },
        sku_hint="A",
        custo="31.11",
        imposto_rate="7.25",
        require_shipping=True,
    )

    assert result["margem_completa"] is True
    assert result["imposto_valor"] == 7.25
    assert result["valor_liquido"] == 39.82
    assert result["margem_percentual"] == 39.82


def test_zero_shipping_is_valid_only_when_explicitly_present():
    common = {"price": 100, "sale_fee_amount": 10, "currency_id": "BRL"}

    missing = margem_calcular_anuncio(
        common,
        custo=30,
        imposto_rate=10,
        require_shipping=True,
    )
    confirmed = margem_calcular_anuncio(
        {**common, "shipping_seller_cost": 0},
        custo=30,
        imposto_rate=10,
        require_shipping=True,
    )

    assert "frete" in missing["faltando_margem"]
    assert confirmed["margem_completa"] is True
    assert confirmed["frete_ml"] == 0


def test_non_brl_and_negative_components_fail_closed():
    result = margem_calcular_anuncio(
        {"price": 100, "sale_fee_amount": -1, "shipping_seller_cost": -2, "currency_id": "USD"},
        custo=-3,
        imposto_rate=-1,
        require_shipping=True,
    )

    assert result["margem_completa"] is False
    assert set(result["faltando_margem"]) == {"moeda", "custo", "imposto", "tarifa", "frete"}
