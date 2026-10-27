"""Offline regressions for preserving each source campaign's discount."""

import copy
import math
from types import SimpleNamespace

import pytest
import requests

from backend.services import mercadolivre_legacy_pricing as pricing
from backend.services import renovacao_promocoes as promotions
from backend.services import renovacao_promocoes_helpers as helpers
from backend.services.mercadolivre_legacy_planilhas import _ml_iterar_campos_payload_limitado


TENANT = "tenant-renovacao-test"
STORE = "loja-renovacao-test"
ITEM = "MLB999000123"
SOURCE = "C-MLB999000001"
DESTINATION = "C-MLB999000002"
CFG = {"user_id": 123, "site_id": "MLB"}


def _parse_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _response(body, status=201):
    return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(body))


def _participation(price, base, campaign=DESTINATION):
    return {
        "id": ITEM,
        "item_id": ITEM,
        "promotion_id": campaign,
        "campaign_id": campaign,
        "promotion_type": "SELLER_CAMPAIGN",
        "status": "pending",
        "price": price,
        "original_price": base,
    }


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected real or unconfigured network request")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected)
    monkeypatch.setattr(pricing, "_parse_float_flex", _parse_float, raising=False)
    monkeypatch.setattr(
        pricing,
        "_ml_iterar_campos_payload_limitado",
        _ml_iterar_campos_payload_limitado,
        raising=False,
    )
    monkeypatch.setattr(helpers.ctx, "_parse_float_flex", _parse_float)
    monkeypatch.setattr(
        helpers.ctx, "_ml_extrair_preco_promocao_raw", pricing._ml_extrair_preco_promocao_raw
    )
    monkeypatch.setattr(helpers.ctx, "_ml_api_request", unexpected)
    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", unexpected)
    monkeypatch.setattr(
        helpers.ctx,
        "_ml_obter_item_promocao_raw",
        lambda client, store, cfg, campaign, kind, item: ({}, cfg),
    )
    monkeypatch.setattr(helpers.ctx, "_ml_parse_error_detail", lambda response, message: message)
    monkeypatch.setattr(helpers.ctx, "_cache_invalidar_loja", lambda *args: None)


def _patch_offer_state(monkeypatch, destination=None):
    state = SimpleNamespace(
        source=_participation(80, 100, SOURCE),
        destination=copy.deepcopy(destination),
        lookups=[],
    )

    def fresh(client, store, cfg, item, **kwargs):
        assert (client, store, item) == (TENANT, STORE, ITEM)
        assert kwargs.get("strict") is True
        assert kwargs.get("force_refresh") is True
        state.lookups.append(copy.deepcopy(state.destination))
        records = [copy.deepcopy(state.source)]
        if state.destination is not None:
            records.append(copy.deepcopy(state.destination))
        return records, cfg

    monkeypatch.setattr(helpers.ctx, "_ml_obter_promocoes_item", fresh)
    return state


@pytest.mark.parametrize("base,percentage,expected", [
    (200, 20, 160),
    (0.05, 10, 0.05),
    (129.90, 15, 110.42),
])
def test_campaign_price_rounds_half_up_in_cents(base, percentage, expected):
    assert helpers._renovacao_preco_com_desconto(base, percentage) == expected


def test_source_percentage_has_priority_over_components_and_current_item_price():
    source = {"price": 80, "original_price": 100, "discount_percentage": 20,
              "seller_percentage": 3, "meli_percentage": 2}
    percentage, _, _ = helpers._renovacao_desconto_item(
        ITEM, source, {"price": 80, "original_price": 200}
    )
    assert percentage == 20


def test_source_percentage_is_inferred_from_source_prices_only():
    percentage, _, _ = helpers._renovacao_desconto_item(
        ITEM, {"price": 80, "original_price": 100},
        {"price": 160, "original_price": 200},
    )
    assert percentage == 20


def test_current_item_base_cannot_invent_an_unknown_source_percentage():
    percentage, _, _ = helpers._renovacao_desconto_item(
        ITEM, {"price": 80}, {"price": 160, "original_price": 200}
    )
    assert percentage is None


@pytest.mark.parametrize("percentage", [0, 100, math.nan])
def test_invalid_source_percentage_is_rejected(percentage):
    result, _, _ = helpers._renovacao_desconto_item(
        ITEM, {"discount_percentage": percentage}, {"price": 80, "original_price": 200}
    )
    assert result is None


def test_current_original_price_prevents_compounding_an_active_discount():
    base, cfg = helpers._renovacao_preco_base_atual(
        TENANT, STORE, CFG, ITEM,
        {"id": ITEM, "price": 80, "original_price": 100, "currency_id": "BRL"},
    )
    assert base == 100
    assert cfg == CFG
    assert helpers._renovacao_preco_com_desconto(base, 20) == 80


def _standard(amount=100, **changes):
    return {"type": "standard", "amount": amount, "currency_id": "BRL",
            "conditions": {"context_restrictions": ["channel_marketplace"]}, **changes}


def _patch_prices(monkeypatch, body):
    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "GET")
        assert url == f"https://api.mercadolibre.com/items/{ITEM}/prices"
        return _response(body, 200), cfg

    monkeypatch.setattr(helpers.ctx, "_ml_api_request", request)


def test_standard_price_is_used_when_item_price_may_already_be_promotional(monkeypatch):
    _patch_prices(monkeypatch, {"id": ITEM, "prices": [
        _standard(), {"type": "promotion", "amount": 80, "currency_id": "BRL"},
    ]})
    base, _ = helpers._renovacao_preco_base_atual(
        TENANT, STORE, CFG, ITEM, {"id": ITEM, "price": 80, "currency_id": "BRL"}
    )
    assert base == 100


@pytest.mark.parametrize("body", [
    {"id": ITEM, "prices": [{"type": "promotion", "amount": 80, "currency_id": "BRL"}]},
    {"id": ITEM, "prices": [_standard(100), _standard(120)]},
    {"id": "MLB999000999", "prices": [_standard()]},
    {"id": ITEM, "prices": [_standard(currency_id="USD")]},
])
def test_missing_conflicting_or_foreign_standard_price_is_not_replaced_by_sale_price(monkeypatch, body):
    _patch_prices(monkeypatch, body)
    base, _ = helpers._renovacao_preco_base_atual(
        TENANT, STORE, CFG, ITEM, {"id": ITEM, "price": 80, "currency_id": "BRL"}
    )
    assert base is None


@pytest.mark.parametrize("prefer_percentage", [False, True])
def test_inclusion_recalculates_documented_price_from_source_percentage(monkeypatch, prefer_percentage):
    calls = []
    state = _patch_offer_state(monkeypatch)

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store, method) == (TENANT, STORE, "POST")
        assert url == f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}"
        calls.append(kwargs["json"])
        state.destination = _participation(160, 200)
        return _response(_participation(160, 200)), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80,
        preferir_percentual=prefer_percentage, campanha_origem_id=SOURCE,
    )
    assert ok
    assert calls == [{"promotion_id": DESTINATION, "promotion_type": "SELLER_CAMPAIGN", "deal_price": 160}]
    assert state.lookups[0] is None and state.lookups[-1]["price"] == 160


def test_http_success_with_a_divergent_discount_is_not_reported_as_preserved(monkeypatch):
    calls = []
    state = _patch_offer_state(monkeypatch)

    def request(client, store, cfg, method, url, **kwargs):
        assert method in {"POST", "PUT"}
        calls.append(method)
        state.destination = _participation(180, 200)
        return _response(_participation(180, 200)), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert not ok
    assert error
    assert len(calls) <= 2


def test_empty_http_success_requires_confirmed_destination_prices(monkeypatch):
    state = _patch_offer_state(monkeypatch)
    monkeypatch.setattr(promotions.ctx, "_ml_api_request",
                        lambda client, store, cfg, *args, **kwargs: (_response({}), cfg))
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert not ok
    assert error
    assert state.lookups and all(raw is None for raw in state.lookups)


def test_empty_http_success_can_be_confirmed_by_exact_destination_lookup(monkeypatch):
    state = _patch_offer_state(monkeypatch)

    def request(client, store, cfg, method, url, **kwargs):
        assert method == "POST"
        state.destination = _participation(160, 200)
        return _response({}), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert ok
    assert state.lookups[0] is None and state.lookups[-1] == _participation(160, 200)


def test_concurrent_base_change_allows_one_recalculation_without_delete(monkeypatch):
    calls = []
    state = _patch_offer_state(monkeypatch)

    def request(client, store, cfg, method, url, **kwargs):
        calls.append((method, kwargs["json"]["deal_price"]))
        body = _participation(160, 250) if len(calls) == 1 else _participation(200, 250)
        state.destination = copy.deepcopy(body)
        return _response(body), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert ok
    assert calls == [("POST", 160), ("PUT", 200)]


def _patch_copy_flow(monkeypatch, destination_price=None):
    calls = []
    destination = _participation(destination_price, 200) if destination_price else None
    state = _patch_offer_state(monkeypatch, destination)
    state.source["discount_percentage"] = 20

    def listing(client, store, cfg, campaign, *args, **kwargs):
        assert (client, store) == (TENANT, STORE)
        raw = state.source if campaign == SOURCE else state.destination
        return ([{"id": ITEM}], {ITEM: copy.deepcopy(raw)}, cfg) if raw else ([], {}, cfg)

    def request(client, store, cfg, method, url, **kwargs):
        assert (client, store) == (TENANT, STORE)
        assert method in {"POST", "PUT"}
        assert url == f"https://api.mercadolibre.com/seller-promotions/items/{ITEM}"
        calls.append((method, copy.deepcopy(kwargs["json"])))
        state.destination = _participation(160, 200)
        return _response(_participation(160, 200)), cfg

    monkeypatch.setattr(promotions, "_renovacao_listar_itens_campanha", listing)
    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    monkeypatch.setattr(promotions.ctx, "_ml_buscar_itens_batch",
                        lambda client, store, cfg, ids: ([{
                            "id": ITEM, "price": 80, "original_price": 200, "currency_id": "BRL",
                        }], cfg))
    return calls


@pytest.mark.parametrize("destination_price,expected_updates", [(180, 1), (160, 0)])
def test_existing_items_are_compared_and_only_divergent_prices_are_updated(monkeypatch, destination_price, expected_updates):
    calls = _patch_copy_flow(monkeypatch, destination_price)
    result, _ = promotions._renovacao_copiar_mlbs_campanha(
        TENANT, STORE, CFG, SOURCE, DESTINATION, expected_origem_count=1, expected_destino_count=1,
    )
    assert result["mlbs_ja_presentes"] == 1
    assert result["incluidos"] == 0
    assert result["atualizados"] == expected_updates
    assert result["falhas_total"] == 0
    assert calls == ([('PUT', {"promotion_id": DESTINATION, "promotion_type": "SELLER_CAMPAIGN", "deal_price": 160})]
                     if expected_updates else [])


@pytest.mark.parametrize("operation", ["monthly", "synchronization"])
def test_monthly_renewal_and_synchronization_preserve_the_same_source_percentage(monkeypatch, operation):
    calls = _patch_copy_flow(monkeypatch)
    campaigns = [{"id": SOURCE, "name": "Promo Outubro", "promotion_type": "SELLER_CAMPAIGN",
                  "start_date": "2030-10-01T00:00:00", "finish_date": "2030-11-01T00:00:00",
                  "active_count": 1, "programmed_count": 0}]
    if operation == "synchronization":
        campaigns.append({"id": DESTINATION, "name": "Promo Novembro", "promotion_type": "SELLER_CAMPAIGN",
                          "active_count": 0, "programmed_count": 0})
    monkeypatch.setattr(promotions, "_renovacao_ml_listar_campanhas_usuario",
                        lambda client, store: (copy.deepcopy(campaigns), CFG))
    monkeypatch.setattr(promotions, "_renovacao_criar_campanha_ml",
                        lambda client, store, cfg, name, source, **kwargs:
                        (DESTINATION, {"name": name, "start_date": "2030-11-01T00:00:00",
                                       "finish_date": "2030-12-01T00:00:00"}, cfg))
    if operation == "monthly":
        result = promotions._renovacao_criar_ou_completar_proximo_mes(
            TENANT, STORE, SOURCE, "Promo Novembro"
        )
    else:
        result = promotions._renovacao_sincronizar_promocao_existente(
            TENANT, STORE, SOURCE, DESTINATION
        )
    assert result["success"]
    assert result["incluidos"] == 1
    assert result["falhas_total"] == 0
    assert calls == [('POST', {"promotion_id": DESTINATION, "promotion_type": "SELLER_CAMPAIGN", "deal_price": 160})]


@pytest.mark.parametrize("status", ["candidate", "eligible", "finished"])
def test_nonparticipating_fresh_state_is_not_overridden_by_http_success_or_cached_readback(monkeypatch, status):
    state = _patch_offer_state(monkeypatch)
    body = {**_participation(160, 200), "status": status}

    def request(client, store, cfg, method, url, **kwargs):
        assert method == "POST"
        state.destination = copy.deepcopy(body)
        return _response(_participation(160, 200)), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    monkeypatch.setattr(promotions.ctx, "_ml_obter_item_promocao_raw",
                        lambda client, store, cfg, *args: (_participation(160, 200), cfg))
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert not ok
    assert error


def test_documented_response_without_status_requires_fresh_membership_to_confirm_discount(monkeypatch):
    state = _patch_offer_state(monkeypatch)
    body = _participation(160, 200)
    body.pop("status")

    def request(client, store, cfg, method, url, **kwargs):
        assert method == "POST"
        state.destination = _participation(160, 200)
        return _response(body), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    ok, _, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert ok
    assert len(state.lookups) >= 2 and state.lookups[-1]["status"] == "pending"


@pytest.mark.parametrize("conflict", [
    {"campaign": {"id": SOURCE}},
    {"promotion": {"id": SOURCE}},
    {"item": {"id": "MLB999000999"}},
])
def test_nested_fresh_identity_conflict_is_not_overridden_by_http_success_or_cached_readback(monkeypatch, conflict):
    state = _patch_offer_state(monkeypatch)
    body = {**_participation(160, 200), **conflict}

    def request(client, store, cfg, method, url, **kwargs):
        assert method == "POST"
        state.destination = copy.deepcopy(body)
        return _response(_participation(160, 200)), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    monkeypatch.setattr(promotions.ctx, "_ml_obter_item_promocao_raw",
                        lambda client, store, cfg, *args: (_participation(160, 200), cfg))
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert not ok
    assert error


def test_stale_readback_with_old_base_cannot_hide_wrong_applied_percentage(monkeypatch):
    calls = []
    legacy_lookups = []
    state = _patch_offer_state(monkeypatch)

    def request(client, store, cfg, method, url, **kwargs):
        calls.append((method, kwargs["json"]["deal_price"]))
        state.destination = _participation(100, 200)
        return _response(_participation(100, 200)), cfg

    def cached_lookup(client, store, cfg, *args):
        legacy_lookups.append(args)
        return _participation(80, 100), cfg

    monkeypatch.setattr(promotions.ctx, "_ml_api_request", request)
    monkeypatch.setattr(promotions.ctx, "_ml_obter_item_promocao_raw", cached_lookup)
    ok, error, _ = promotions._renovacao_adicionar_item_campanha(
        TENANT, STORE, CFG, ITEM, DESTINATION, 20, 200, 80, campanha_origem_id=SOURCE,
    )
    assert not ok
    assert error
    assert calls == [("POST", 160), ("PUT", 160)]
    assert legacy_lookups == []
