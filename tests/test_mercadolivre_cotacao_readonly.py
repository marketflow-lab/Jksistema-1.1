from __future__ import annotations

import copy

from backend.services.mercadolivre_cotacao_readonly import (
    cotar_preco_candidato_readonly,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return copy.deepcopy(self._payload)


def _item():
    return {
        "id": "MLB123",
        "price": 120,
        "currency_id": "BRL",
        "category_id": "MLB-CATEGORY",
        "listing_type_id": "gold_special",
        "condition": "new",
        "shipping": {
            "mode": "me2",
            "logistic_type": "cross_docking",
            "free_shipping": True,
            "dimensions": "10x10x10,500",
        },
    }


def _context(item, price):
    return {
        "item_price": price,
        "listing_type_id": item["listing_type_id"],
        "condition": item["condition"],
        "category_id": item["category_id"],
        "mode": item["shipping"]["mode"],
        "logistic_type": item["shipping"]["logistic_type"],
        "dimensions": item["shipping"]["dimensions"],
        "free_shipping": item["shipping"]["free_shipping"],
    }


def _requester(*, fee=15, shipping=8, fee_status=200, shipping_status=200):
    calls = []

    def request(client_id, loja, cfg, method, url, **kwargs):
        calls.append((client_id, loja, method, url, copy.deepcopy(kwargs)))
        next_cfg = {**cfg, "request_count": len(calls)}
        if url.endswith("/listing_prices"):
            return _Response(
                {
                    "sale_fee_amount": fee,
                    "sale_fee_details": {"fixed_fee": 7.75},
                },
                fee_status,
            ), next_cfg
        if "/shipping_options/free" in url:
            payload = (
                {"coverage": {"all_country": {"seller_cost": shipping}}}
                if shipping is not None
                else {"coverage": {"all_country": {}}}
            )
            return _Response(payload, shipping_status), next_cfg
        raise AssertionError(f"unexpected URL: {url}")

    return request, calls


def test_readonly_quote_resolves_candidate_fee_and_shipping_exactly():
    request_fn, calls = _requester()
    original = _item()
    snapshot = copy.deepcopy(original)
    result, cfg = cotar_preco_candidato_readonly(
        client_id="tenant",
        loja="store",
        cfg={"user_id": "seller"},
        item=original,
        preco_efetivo=100,
        custo_produto=30,
        aliquota_imposto=0.10,
        construir_contexto_frete=_context,
        request_fn=request_fn,
    )

    assert original == snapshot
    assert len(calls) == 2
    assert calls[0][3].endswith("/sites/MLB/listing_prices")
    assert calls[0][4]["params"]["price"] == "100"
    assert calls[1][3].endswith("/users/seller/shipping_options/free")
    assert calls[1][4]["params"]["item_price"] == "100"
    assert result == {
        "contract_version": "mercadolivre.financial_quote_readonly.v1",
        "quote_contract_version": "mercadolivre.financial_quote.v1",
        "status": "exact",
        "decision_eligible": True,
        "tax_amount": "10",
        "net_amount": "37",
        "margin_percent": "37",
        "decision_margin_percent": "37",
        "missing": [],
        "conflicts": [],
        "components": {
            "sale_fee": {
                "amount": "15",
                "exact": True,
                "source": "mercadolivre.listing_prices",
            },
            "seller_shipping": {
                "amount": "8",
                "exact": True,
                "source": "mercadolivre.shipping_options",
            },
        },
    }
    assert cfg["request_count"] == 2


def test_readonly_quote_fails_closed_when_shipping_is_not_bound_to_price():
    request_fn, _calls = _requester(shipping=None)
    result, _cfg = cotar_preco_candidato_readonly(
        client_id="tenant",
        loja="store",
        cfg={"user_id": "seller"},
        item=_item(),
        preco_efetivo=100,
        custo_produto=30,
        aliquota_imposto=0.10,
        construir_contexto_frete=_context,
        request_fn=request_fn,
    )

    assert result["status"] == "incomplete"
    assert result["decision_eligible"] is False
    assert result["net_amount"] is None
    assert result["margin_percent"] is None
    assert result["components"]["seller_shipping"] == {
        "amount": None,
        "exact": False,
        "source": "unavailable",
    }


def test_sale_fee_is_consumed_as_total_without_adding_fixed_fee_again():
    request_fn, _calls = _requester(fee=20, shipping=0)
    result, _cfg = cotar_preco_candidato_readonly(
        client_id="tenant",
        loja="store",
        cfg={"user_id": "seller"},
        item=_item(),
        preco_efetivo=100,
        custo_produto=30,
        aliquota_imposto=0.10,
        construir_contexto_frete=_context,
        request_fn=request_fn,
    )

    assert result["status"] == "exact"
    assert result["net_amount"] == "40"


def test_explicit_negative_shipping_credit_is_preserved():
    request_fn, _calls = _requester(fee=10, shipping=-2)
    result, _cfg = cotar_preco_candidato_readonly(
        client_id="tenant",
        loja="store",
        cfg={"user_id": "seller"},
        item=_item(),
        preco_efetivo=100,
        custo_produto=30,
        aliquota_imposto=0.10,
        construir_contexto_frete=_context,
        request_fn=request_fn,
    )
    assert result["status"] == "exact"
    assert result["net_amount"] == "52"


def test_readonly_quote_never_uses_legacy_financial_resolver_or_cache():
    request_fn, calls = _requester()
    cotar_preco_candidato_readonly(
        client_id="tenant",
        loja="store-a",
        cfg={"user_id": "seller-a"},
        item=_item(),
        preco_efetivo=100,
        custo_produto=30,
        aliquota_imposto=0.10,
        construir_contexto_frete=_context,
        request_fn=request_fn,
    )
    cotar_preco_candidato_readonly(
        client_id="tenant",
        loja="store-b",
        cfg={"user_id": "seller-b"},
        item=_item(),
        preco_efetivo=100,
        custo_produto=30,
        aliquota_imposto=0.10,
        construir_contexto_frete=_context,
        request_fn=request_fn,
    )

    listing_calls = [call for call in calls if call[3].endswith("/listing_prices")]
    shipping_calls = [call for call in calls if "/shipping_options/free" in call[3]]
    assert [call[1] for call in listing_calls] == ["store-a", "store-b"]
    assert [call[1] for call in shipping_calls] == ["store-a", "store-b"]
    assert shipping_calls[0][3] != shipping_calls[1][3]
