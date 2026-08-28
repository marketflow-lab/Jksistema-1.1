from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from backend.services import mercadolivre_cotacao_comparacao as comparison
from backend.services.promocoes_cotacao_shadow import observar_cotacao_promocao


def _observation(**overrides):
    value = {
        "origin": "promocoes",
        "classification": "match",
        "inputs": {
            "effective_price": Decimal("100"),
            "product_cost": Decimal("30"),
            "tax_rate": Decimal("0.10"),
            "sale_fee": Decimal("10"),
            "seller_shipping": Decimal("5"),
        },
        "evidence": {
            "sale_fee_state": "contextual",
            "seller_shipping_state": "contextual",
            "sale_fee_source_present": True,
            "seller_shipping_source_present": True,
        },
        "legacy": {
            "status": "exact",
            "tax_amount": Decimal("10"),
            "net_amount": Decimal("45"),
            "margin_percent": Decimal("45"),
        },
        "quote": {
            "status": "exact",
            "decision_eligible": True,
            "tax_amount": Decimal("10"),
            "net_amount": Decimal("45"),
            "margin_percent": Decimal("45"),
        },
        "delta": {
            "tax_amount": Decimal("0"),
            "net_amount": Decimal("0"),
            "margin_percent": Decimal("0"),
        },
        "item_id": "MLB-NAO-GUARDAR",
        "sku": "SKU-NAO-GUARDAR",
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    comparison.reset_observacoes_comparacao()
    monkeypatch.delenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, raising=False)
    monkeypatch.delenv(comparison.FINANCIAL_COMPARISON_UI_ENV, raising=False)
    yield
    comparison.reset_observacoes_comparacao()


def test_capture_is_off_by_default_and_unknown_values_fail_closed(monkeypatch):
    for value in (None, "", "0", "false", "unexpected"):
        if value is None:
            monkeypatch.delenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, raising=False)
        else:
            monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, value)
        assert comparison.capturar_observacao_comparacao(
            client_id="tenant-a",
            loja="loja-a",
            observacao=_observation(),
        ) is False
    assert comparison.resumo_comparacao(client_id="tenant-a", loja="loja-a")["total"] == 0


def test_capture_keeps_only_allowlisted_financial_data_and_hmac_scope(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "true")
    assert comparison.capturar_observacao_comparacao(
        client_id="tenant-secret-a",
        loja="Loja Ultra Secreta",
        observacao=_observation(),
        now=1_000,
    ) is True

    payload = comparison.resumo_comparacao(
        client_id="tenant-secret-a",
        loja="Loja Ultra Secreta",
        now=1_001,
    )
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["total"] == 1
    assert payload["samples"][0]["inputs"]["effective_price"] == "100"
    assert payload["samples"][0]["delta"]["net_amount"] == "0"
    for forbidden in (
        "tenant-secret-a",
        "Loja Ultra Secreta",
        "MLB-NAO-GUARDAR",
        "SKU-NAO-GUARDAR",
        "_scope",
        "_epoch",
    ):
        assert forbidden not in serialized


def test_tenant_and_store_are_strictly_isolated(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "1")
    comparison.capturar_observacao_comparacao(
        client_id="tenant-a", loja="store-1", observacao=_observation()
    )

    assert comparison.resumo_comparacao(client_id="tenant-a", loja="store-1")["total"] == 1
    assert comparison.resumo_comparacao(client_id="tenant-a", loja="store-2")["total"] == 0
    assert comparison.resumo_comparacao(client_id="tenant-b", loja="store-1")["total"] == 0
    assert comparison.resumo_comparacao(client_id="tenant-a", loja="Store-1")["total"] == 0


def test_summary_is_deeply_detached_from_internal_memory(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "1")
    comparison.capturar_observacao_comparacao(
        client_id="tenant",
        loja="store",
        observacao=_observation(),
    )
    first = comparison.resumo_comparacao(client_id="tenant", loja="store")
    first["samples"][0]["inputs"]["effective_price"] = "999999"
    first["samples"][0]["quote"]["net_amount"] = "999999"

    second = comparison.resumo_comparacao(client_id="tenant", loja="store")
    assert second["samples"][0]["inputs"]["effective_price"] == "100"
    assert second["samples"][0]["quote"]["net_amount"] == "45"


def test_readonly_quote_rates_are_bounded_by_tenant_and_store(monkeypatch):
    monkeypatch.setattr(comparison, "FINANCIAL_COMPARISON_QUOTE_RATE_LIMIT", 2)
    monkeypatch.setattr(
        comparison,
        "FINANCIAL_COMPARISON_QUOTE_TENANT_RATE_LIMIT",
        3,
    )
    assert comparison.permitir_recotacao_readonly_tenant(
        client_id="tenant", now=100
    )
    assert comparison.permitir_recotacao_readonly_tenant(
        client_id="tenant", now=101
    )
    assert comparison.permitir_recotacao_readonly_tenant(
        client_id="tenant", now=102
    )
    assert not comparison.permitir_recotacao_readonly_tenant(
        client_id="tenant", now=103
    )
    assert comparison.permitir_recotacao_readonly(
        client_id="tenant", loja="Store", now=100
    )
    assert comparison.permitir_recotacao_readonly(
        client_id="tenant", loja="Store", now=101
    )
    assert not comparison.permitir_recotacao_readonly(
        client_id="tenant", loja="Store", now=102
    )
    assert comparison.permitir_recotacao_readonly(
        client_id="tenant", loja="store", now=102
    )


def test_ttl_and_per_scope_limit_bound_memory(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "on")
    monkeypatch.setattr(comparison, "FINANCIAL_COMPARISON_MAX_PER_SCOPE", 2)
    for index in range(3):
        comparison.capturar_observacao_comparacao(
            client_id="tenant",
            loja="store",
            observacao=_observation(classification="match"),
            now=100 + index,
        )
    assert comparison.resumo_comparacao(
        client_id="tenant", loja="store", now=103
    )["total"] == 2
    assert comparison.resumo_comparacao(
        client_id="tenant",
        loja="store",
        now=103 + comparison.FINANCIAL_COMPARISON_TTL_SECONDS,
    )["total"] == 0


def test_capture_is_thread_safe(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "sim")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: comparison.capturar_observacao_comparacao(
                    client_id="tenant",
                    loja="store",
                    observacao=_observation(),
                    now=1_000 + index,
                ),
                range(100),
            )
        )
    assert results == [True] * 100
    assert comparison.resumo_comparacao(
        client_id="tenant", loja="store", now=1_200
    )["total"] == 100


def test_shared_promotion_observer_can_capture_a_sanitized_sample(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "1")
    classification = observar_cotacao_promocao(
        preco_efetivo="100",
        custo_produto="30",
        aliquota_imposto="0.10",
        tarifa_total="10",
        tarifa_exata=True,
        tarifa_fonte="sites/MLB/listing_prices",
        tarifa_contexto_preco="100",
        frete_vendedor="5",
        frete_exato=True,
        frete_fonte="users/shipping_options/free",
        frete_contexto_preco="100",
        promocao={"item_id": "MLB-EPHEMERAL", "campaign_id": "SECRET"},
        resultado_legado={
            "exato": True,
            "imposto": "10",
            "valor_liquido": "45",
            "margem": "45",
        },
        client_id="tenant-a",
        loja="store-a",
    )

    payload = comparison.resumo_comparacao(client_id="tenant-a", loja="store-a")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert classification == "match"
    assert payload["total"] == 1
    assert payload["samples"][0]["origin"] == "promocoes"
    assert "MLB-EPHEMERAL" not in serialized
    assert "SECRET" not in serialized
