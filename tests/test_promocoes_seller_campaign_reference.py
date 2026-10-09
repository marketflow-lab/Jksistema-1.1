"""Offline regressions for active seller campaign financial references."""
import copy
from types import SimpleNamespace

import pytest
import requests

from backend.services import mercadolivre_legacy_promocoes as promotions
from backend.services import promocoes_api_analise as analysis
from backend.services import promocoes_api_participacoes as participation
from backend.services import promocoes_lightning_reanalise as recovery
from backend.services.promocoes_lightning_acao import lightning_row_fields
from tests import test_promocoes_desconto_ml_fluxo as runtime_fixture


ITEM = "MLB999000123"
OTHER_ITEM = "MLB999000456"
REFERENCE = "C-MLB999000001"
LIGHTNING = "LGH-MLB999000002"
TENANT = "tenant-reference-test"
STORE = "store-reference-test"
SELLER = 111
SKU = "SKU-REFERENCE-TEST"
BASE_URL = "https://api.mercadolibre.com"


def reference_entry(price=101.90, original=128.99, **changes):
    """Campaign-items responses may identify only the listing and active price."""
    return {
        "id": ITEM,
        "status": "started",
        "price": price,
        "original_price": original,
        "start_date": "2030-01-01T00:00:00",
        "end_date": "2030-01-31T23:59:59",
        "sub_type": "FLEXIBLE_PERCENTAGE",
        **changes,
    }


def contextualize(raw, kind="SELLER_CAMPAIGN", status="started"):
    return promotions._promo_contextualizar_entry_campanha(
        raw,
        campaign_id=REFERENCE,
        promotion_type_consultado=kind,
        item_id=ITEM,
        status_consultado=status,
        status_param_consultado="status" if status else "",
    )


@pytest.fixture(autouse=True)
def offline_runtime(monkeypatch):
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        lambda *args, **kwargs: pytest.fail("Real network is forbidden"),
    )
    runtime_fixture.PromocoesDescontoMlFluxoTests.setUpClass()
    monkeypatch.setattr(promotions, "ML_ITEM_PROMOTIONS_CACHE", {}, raising=False)
    monkeypatch.setattr(promotions, "ML_ITEM_PROMOTIONS_CACHE_TTL", 1, raising=False)
    monkeypatch.setattr(promotions, "_cache_get", lambda *args: None, raising=False)
    monkeypatch.setattr(promotions, "_cache_set", lambda *args: None, raising=False)
    monkeypatch.setattr(analysis, "_promo_observar_cotacao_financeira_shadow", lambda **kwargs: None)


@pytest.mark.parametrize("entry_status", ["started", "active"])
@pytest.mark.parametrize("query_status", ["started", ""])
def test_active_seller_reference_without_offer_confirms_provenance(entry_status, query_status):
    raw = reference_entry(status=entry_status)
    result = contextualize(raw, status=query_status)
    assert result["_jk_contexto_promocao_confirmado"] is True
    assert analysis._promo_identidade_financeira_raw(result) == (REFERENCE, "SELLER_CAMPAIGN", ITEM, True)
    assert result["price"] == raw["price"]
    assert not result.get("offer_id") and not result.get("ref_id")
    assert raw == reference_entry(status=entry_status)


@pytest.mark.parametrize("changes", [
    {"campaign_id": "C-MLB999000099"},
    {"campaign": {"id": "C-MLB999000099"}},
    {"id": OTHER_ITEM},
    {"item_id": OTHER_ITEM},
    {"item": {"id": OTHER_ITEM}},
    {"type": "SMART"},
    {"promotion_type": "DEAL"},
    {"status_item": "candidate"},
    {"ref_id": f"CANDIDATE-{ITEM}-STALE"},
    {"offer_id": f"OFFER-{OTHER_ITEM}-FOREIGN"},
    {"offer_id": "invalid-offer-reference"},
    {"offer_id": f"OFFER-{ITEM}-ONE", "ref_id": f"OFFER-{ITEM}-TWO"},
])
def test_seller_reference_rejects_conflicting_identity_status_or_offer(changes):
    assert contextualize(reference_entry(**changes)) == {}


@pytest.mark.parametrize("kind", ["SMART", "PRICE_MATCHING", "PRICE_MATCHING_MELI_ALL", "MARKETPLACE_CAMPAIGN", "DEAL", "LIGHTNING"])
def test_active_offer_requirement_is_preserved_for_other_campaign_types(kind):
    raw = reference_entry()
    if kind == "LIGHTNING":
        raw.pop("start_date")
        raw.pop("end_date")
    assert contextualize(raw, kind=kind) == {}


@pytest.mark.parametrize("status", ["pending", "scheduled", "paused", "finished"])
def test_seller_reference_exception_does_not_authorize_other_states(status):
    assert contextualize(reference_entry(status=status), status="") == {}


def test_seller_reference_without_explicit_status_is_not_confirmed_from_query():
    raw = reference_entry()
    raw.pop("status")
    assert contextualize(raw) == {}


def test_seller_reference_sanitizes_external_confirmation_markers():
    result = contextualize(reference_entry(
        _jk_contexto_promocao_confirmado=True,
        _jk_campaign_id_consultado="C-MLB999000099",
        _jk_item_id_consultado=OTHER_ITEM,
        _jk_promotion_type_consultado="SMART",
    ))
    assert analysis._promo_identidade_financeira_raw(result) == (REFERENCE, "SELLER_CAMPAIGN", ITEM, True)


@pytest.fixture
def reference_flow(monkeypatch):
    state = SimpleNamespace(posts=[], gets=[], fees=[], shipping=[], reference_changes={}, reference_record_changes={}, reference_present=True, margin_minimum=15, tolerance=5)

    def run(deal_price=97.65, reference_price=101.90, original=128.99):
        state.posts.clear()
        state.gets.clear()
        state.fees.clear()
        state.shipping.clear()
        cfg = {"user_id": SELLER, "site_id": "MLB"}
        product = {
            "id": ITEM, "seller_id": SELLER, "site_id": "MLB", "currency_id": "BRL",
            "price": original, "seller_custom_field": SKU,
            "attributes": [{"id": "SELLER_SKU", "value_name": SKU}],
        }
        candidate = {
            "id": LIGHTNING, "item_id": ITEM, "type": "LIGHTNING", "status": "candidate",
            "ref_id": f"CANDIDATE-{ITEM}-REFERENCE-TEST", "price": deal_price,
            "original_price": original, "min_discounted_price": round(original * .2, 2),
            "max_discounted_price": round(original * .9, 2), "stock": {"min": 5, "max": 20},
        }
        active_reference = {
            "id": REFERENCE, "type": "SELLER_CAMPAIGN", "status": "started",
            "price": reference_price, "original_price": original,
        }
        active_reference.update(state.reference_record_changes)
        raw_reference = reference_entry(reference_price, original, **state.reference_changes)
        pending = {**candidate, "status": "pending", "ref_id": f"OFFER-{ITEM}-REFERENCE-TEST"}

        def response(data, status=200):
            return SimpleNamespace(status_code=status, json=lambda: copy.deepcopy(data))

        def request(client, store, current_cfg, method, url, **kwargs):
            assert (client, store) == (TENANT, STORE)
            assert current_cfg["user_id"] == SELLER
            if method == "POST":
                assert url == f"{BASE_URL}/seller-promotions/items/{ITEM}"
                state.posts.append(copy.deepcopy(kwargs["json"]))
                return response(pending, 201), current_cfg
            assert method == "GET", "Unexpected mutation method"
            state.gets.append((url, copy.deepcopy(kwargs.get("params", {}))))
            if url == f"{BASE_URL}/items/{ITEM}/prices":
                return response({"id": ITEM, "prices": [{
                    "type": "standard", "amount": original, "currency_id": "BRL",
                    "conditions": {"context_restrictions": ["channel_marketplace"]},
                }]}), current_cfg
            if url == f"{BASE_URL}/items/{ITEM}/sale_price":
                return response({"amount": reference_price, "currency_id": "BRL"}), current_cfg
            if url == f"{BASE_URL}/items/{ITEM}":
                return response(product), current_cfg
            if url == f"{BASE_URL}/seller-promotions/items/{ITEM}":
                records = [active_reference] if state.reference_present else []
                return response(records + [pending if state.posts else candidate]), current_cfg
            if url == f"{BASE_URL}/seller-promotions/promotions/{REFERENCE}/items":
                assert kwargs["params"]["promotion_type"] == "SELLER_CAMPAIGN"
                assert kwargs["params"]["item_id"] == ITEM
                return response({"results": [raw_reference], "paging": {"total": 1}}), current_cfg
            pytest.fail(f"Unexpected simulated GET: {url}")

        def current_records(client, store, current_cfg, item, **kwargs):
            return promotions._ml_obter_promocoes_item(
                client, store, current_cfg, item, request_fn=request, **kwargs,
            )

        def reference_lookup(*args):
            return promotions._ml_obter_item_promocao_raw(*args, request_fn=request)

        def shipping(client, store, current_cfg, item, product, first, second):
            assert (client, store, item) == (TENANT, STORE, ITEM)
            state.shipping.append(second)
            data = {"shipping_cost": 2, "shipping_exact_for_price": True,
                    "shipping_price_context": second,
                    "shipping_cost_retry_source": "users/shipping_options/free/contexto"}
            return copy.deepcopy(data), data, current_cfg

        def fees(client, store, current_cfg, product, *args, **kwargs):
            assert (client, store) == (TENANT, STORE)
            price = product["price"]
            state.fees.append(price)
            return {"ad_cost": round(price * .1, 2), "ad_cost_price_context": price,
                    "ad_cost_source": "sites/MLB/listing_prices", "ad_cost_exact_for_price": False}, current_cfg

        monkeypatch.setattr(participation, "_ml_api_request", request, raising=False)
        monkeypatch.setattr(participation, "_ml_obter_promocoes_item", current_records, raising=False)
        monkeypatch.setattr(analysis, "_ml_obter_item_promocao_raw", reference_lookup)
        monkeypatch.setattr(analysis, "_carregar_custos_impostos_cadastro_por_sku_loja",
                            lambda client, store: ({SKU: 20}, {SKU: .1}))
        monkeypatch.setattr(analysis, "_promo_obter_fretes_por_preco", shipping)
        monkeypatch.setattr(analysis, "_ml_obter_taxas_anuncio", fees)

        proven_candidate = participation._validar_contexto_entry_campanha(
            candidate, campaign_id=LIGHTNING, promotion_type_consultado="LIGHTNING", item_id=ITEM,
        )
        selected = {"item_id": ITEM, "deal_price": deal_price, "offer_id": candidate["ref_id"],
                    **lightning_row_fields(proven_candidate, "LIGHTNING", LIGHTNING, ITEM)}
        context = {
            "selected": selected, "promotion_id": LIGHTNING, "item": {"currency_id": "BRL"},
            "promocao_a_id": REFERENCE, "promocao_a_type": "SELLER_CAMPAIGN", "site_id": "MLB",
            "margem_minima": state.margin_minimum, "margem_tolerancia": state.tolerance,
            "sku": SKU, "fontes": {"custo": "cadastro/maior_custo_variacao", "imposto": "cadastro/imposto_sku"},
        }
        details = {}
        result = recovery.apply_reanalysed_lightning(
            participation, analysis, TENANT, STORE, cfg, campaign=LIGHTNING, item_id=ITEM,
            selected={"lightning_stock": 5}, row_context=context, result_details=details,
        )
        return result, details

    state.run = run
    return state


@pytest.mark.parametrize("deal,reference,original", [(97.65, 101.90, 128.99), (62.95, 63.90, 80.89)])
def test_lightning_reanalysis_reaches_post_with_seller_reference_without_offer(reference_flow, deal, reference, original):
    (ok, message, _cfg), details = reference_flow.run(deal, reference, original)
    assert ok, message
    assert details["outcome"] == "applied"
    assert details["price_diagnostics"]["decision_code"] == "PARTICIPATION_CONFIRMED"
    assert details["price_diagnostics"]["attempt_count"] == 1
    assert reference_flow.posts == [{"promotion_type": "LIGHTNING", "deal_price": deal, "stock": 5}]
    updates = details["analysis_row_updates"]
    assert updates["action_deal_price"] == deal
    assert updates["action_financeiro_estimado"] is True
    assert updates["reference_status"] == "Ativo"
    assert updates["reference_margin"] > updates["action_margem_ml"] >= 15
    assert reference_flow.fees == [deal, reference]
    assert reference_flow.shipping == [deal, reference]
    assert any(f"/promotions/{REFERENCE}/items" in url for url, _params in reference_flow.gets)
    assert reference_flow.gets[-1][0] == f"{BASE_URL}/seller-promotions/items/{ITEM}"


@pytest.mark.parametrize("changes", [
    {"campaign_id": "C-MLB999000099"},
    {"id": OTHER_ITEM},
    {"type": "SMART"},
    {"ref_id": f"CANDIDATE-{ITEM}-WRONG-STATE"},
])
def test_reference_context_conflicts_block_lightning_before_post(reference_flow, changes):
    reference_flow.reference_changes = changes
    (ok, message, _cfg), details = reference_flow.run()
    assert not ok and "Promocao 1" in message
    assert details["price_diagnostics"]["decision_code"] == "FINANCIAL_CONTEXT_UNCONFIRMED"
    assert details["price_diagnostics"]["attempt_count"] == 0
    assert reference_flow.posts == []


@pytest.mark.parametrize("minimum,tolerance", [(80, 5), (15, 0)])
def test_fresh_reference_margin_and_minimum_still_block_before_post(reference_flow, minimum, tolerance):
    reference_flow.margin_minimum = minimum
    reference_flow.tolerance = tolerance
    (ok, _message, _cfg), details = reference_flow.run()
    assert not ok
    assert details["price_diagnostics"]["decision_code"] == "RECALCULATED_MARGIN_REJECTED"
    assert details["price_diagnostics"]["attempt_count"] == 0
    assert reference_flow.posts == []

@pytest.mark.parametrize("changes", [
    {"id": "C-MLB999000099"},
    {"campaign_id": "C-MLB999000099"},
    {"promotion": {"type": "SMART"}},
    {"item_id": OTHER_ITEM},
    {"status_item": "candidate"},
    {"type": "SMART"},
    {"status": "candidate"},
    {"status": "paused"},
    {"price": 101.89},
    {"price": 101.905},
    {"price": None},
    {"price": True},
    {"price": 0},
    {"price": "NaN"},
])
def test_seller_reference_requires_matching_current_item_endpoint_record(reference_flow, changes):
    reference_flow.reference_record_changes = changes
    (ok, message, _cfg), details = reference_flow.run()
    assert not ok and "Promocao 1" in message
    assert details["price_diagnostics"]["decision_code"] == "FINANCIAL_CONTEXT_UNCONFIRMED"
    assert details["price_diagnostics"]["attempt_count"] == 0
    assert reference_flow.posts == []


def test_seller_reference_missing_from_item_endpoint_blocks_before_post(reference_flow):
    reference_flow.reference_present = False
    (ok, message, _cfg), details = reference_flow.run()
    assert not ok and "Promocao 1" in message
    assert details["price_diagnostics"]["decision_code"] == "FINANCIAL_CONTEXT_UNCONFIRMED"
    assert reference_flow.posts == []


def test_seller_reference_prices_compare_in_cents(reference_flow):
    reference_flow.reference_record_changes = {"price": 101.904}
    (ok, message, _cfg), details = reference_flow.run()
    assert ok, message
    assert details["price_diagnostics"]["decision_code"] == "PARTICIPATION_CONFIRMED"
    assert len(reference_flow.posts) == 1
