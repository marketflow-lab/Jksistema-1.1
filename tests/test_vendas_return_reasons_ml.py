from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests

from backend.modules.vendas import return_reasons_ml as reasons


ORDER = "2000000000001"
PACK = "2000000000100"
OTHER_ORDER = "2000000000002"
CLAIM = "901"
RETURN = "801"
SKU = "200"
ITEM = "MLB3840513395"
ORDER_PATH = "/orders/" + ORDER
SEARCH_PATH = "/post-purchase/v1/claims/search"
REASON_PATH = "/post-purchase/v1/claims/reasons/PDD1"
REAL_REQUEST_GET = reasons.client.request_get


def pack_fixture(wire, *, extra_order=None):
    wire.responses["/orders/" + PACK] = Response(status=404)
    wire.responses["/packs/" + PACK] = Response({"id": PACK, "orders": [{"id": ORDER}]})
    wire.responses[ORDER_PATH].payload["pack_id"] = PACK
    if extra_order is not None:
        wire.responses["/packs/" + PACK].payload["orders"].append({"id": OTHER_ORDER})
        wire.responses["/orders/" + OTHER_ORDER] = Response({
            **deepcopy(wire.responses[ORDER_PATH].payload), "id": OTHER_ORDER, **extra_order,
        })


def sold_item(item_id=ITEM, sku=SKU, variation_id=None, quantity=2):
    return {"item": {"id": item_id, "variation_id": variation_id,
                     "seller_sku": sku, "seller_custom_field": sku}, "quantity": quantity}


def return_item(order_id=ORDER, item_id=ITEM, variation_id=None, **changes):
    value = {"order_id": order_id, "item_id": item_id, "variation_id": variation_id,
             "context_type": "partial", "total_quantity": "2.0", "return_quantity": "1.0"}
    value.update(changes)
    return value


def claim(ident=CLAIM, **changes):
    value = {
        "id": ident, "resource": "order", "resource_id": ORDER, "site_id": "MLB",
        "status": "closed", "type": "return", "stage": "none", "related_entities": ["return"],
        "reason_id": "PDD1", "players": [
            {"role": "complainant", "type": "buyer", "user_id": "456"},
            {"role": "respondent", "type": "seller", "user_id": "123"},
        ],
    }
    value.update(changes)
    return value


def returned(ident=RETURN, **changes):
    value = {"id": ident, "claim_id": CLAIM, "resource_type": "order", "resource_id": ORDER,
             "orders": [return_item()], "status": "delivered", "subtype": "return_partial"}
    value.update(changes)
    return value


def return_path(ident=CLAIM):
    return "/post-purchase/v2/claims/" + ident + "/returns"


class Response:
    def __init__(self, payload=None, status=200, headers=None):
        self.payload, self.status_code, self.headers = payload, status, headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return deepcopy(self.payload)


@pytest.fixture
def wire(monkeypatch):
    state = SimpleNamespace(
        store={"store_id": "store-a", "nome": "Loja A"},
        config={"user_id": "123", "site_id": "MLB", "access_token": "secret-token",
                "_store_id_context": "store-a"},
        resolved="Loja A", calls=[], config_calls=[], refreshed=None, after_response=None,
    )
    state.responses = {
        ORDER_PATH: Response({"id": ORDER, "seller": {"id": "123"}, "context": {"site": "MLB"},
                              "order_items": [sold_item()]}),
        SEARCH_PATH: Response({"data": [claim()], "paging": {"total": 1}}),
        "/post-purchase/v1/claims/" + CLAIM: Response(claim()),
        return_path(): Response(returned()),
        REASON_PATH: Response({"id": "PDD1", "name": "different_product", "detail": "Recebi outro produto"}),
        "/users/me": Response({"id": "123", "site_id": "MLB"}),
    }
    monkeypatch.setattr(reasons.client, "resolve_store", lambda tenant, store: (state.resolved, {}))
    monkeypatch.setattr(reasons.runtime, "find_store", lambda tenant, name, store_id: deepcopy(state.store))

    def config(tenant, name, store_id):
        state.config_calls.append((tenant, name, store_id))
        return deepcopy(state.config)

    def request(tenant, store, cfg, url, **kwargs):
        path = url.removeprefix("https://api.mercadolibre.com")
        assert path != url
        state.calls.append({"tenant": tenant, "store": store, "path": path, **deepcopy(kwargs)})
        response = state.responses[path]
        if isinstance(response, list):
            response = response.pop(0)
        if isinstance(response, Exception):
            raise response
        refreshed = deepcopy(state.refreshed or cfg)
        if state.after_response:
            state.after_response(path)
        return response, refreshed

    monkeypatch.setattr(reasons.runtime, "ml_config", config)
    monkeypatch.setattr(reasons.client, "request_get", request)
    state.run = lambda job=None: reasons.resolve_reason("tenant-a", "store-a", "Loja A", {"order_id": ORDER, "sku": SKU, **(job or {})})
    return state


def test_identifies_only_owned_return_and_uses_exact_current_filters(wire):
    result = wire.run()
    assert result == {
        "result_status": "identified", "reason_id": "PDD1", "reason_description": "Recebi outro produto",
        "reason_source": "mercado_livre", "order_id": ORDER, "claim_id": CLAIM, "return_id": RETURN,
        "seller_id": "123", "site_id": "MLB", "retryable": False,
        "retry_after_seconds": 0, "error_code": "",
    }
    assert [call["path"] for call in wire.calls] == [ORDER_PATH, SEARCH_PATH, return_path(), REASON_PATH]
    assert wire.config_calls == [("tenant-a", "Loja A", "store-a")] * 2
    assert wire.calls[1]["params"] == {
        "order_id": ORDER, "players.user_id": "123",
        "players.role": "respondent", "limit": 30, "offset": 0, "sort": "last_updated:desc",
    }
    assert all(call["tenant"] == "tenant-a" and call["store"] == "Loja A" for call in wire.calls)
    assert all(call["timeout"] == 10 and call["deadline"] > 0 for call in wire.calls)


def test_order_404_resolves_pack_to_unique_owned_order_and_keeps_canonical_claim_link(wire):
    pack_fixture(wire, extra_order={"order_items": [sold_item(sku="OTHER")]})
    wire.responses["/packs/" + PACK].payload["orders"].reverse()
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == "identified"
    assert result["order_id"] == ORDER and result["claim_id"] == CLAIM
    assert [call["path"] for call in wire.calls] == [
        "/orders/" + PACK, "/packs/" + PACK, "/orders/" + OTHER_ORDER,
        ORDER_PATH, SEARCH_PATH, return_path(), REASON_PATH,
    ]
    search = next(call for call in wire.calls if call["path"] == SEARCH_PATH)
    assert search["params"]["order_id"] == ORDER
    assert len({call["deadline"] for call in wire.calls}) == 1


def test_genuine_order_and_pack_404_remains_not_linked(wire):
    wire.responses[ORDER_PATH] = Response(status=404)
    wire.responses["/packs/" + ORDER] = Response(status=404)
    result = wire.run()
    assert result["result_status"] == "not_linked" and result["error_code"] == "order_not_found"
    assert [call["path"] for call in wire.calls] == [ORDER_PATH, "/packs/" + ORDER]


def test_null_success_response_is_not_a_404_and_never_triggers_pack_fallback(wire):
    wire.responses[ORDER_PATH] = Response(None)
    result = wire.run()
    assert result["error_code"] == "invalid_response" and result["retryable"]
    assert [call["path"] for call in wire.calls] == [ORDER_PATH]


def test_pack_with_multiple_owned_orders_for_same_sku_is_ambiguous(wire):
    pack_fixture(wire, extra_order={})
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == "ambiguous" and result["error_code"] == "multiple_pack_orders"
    assert result["order_id"] == result["reason_id"] == ""
    assert SEARCH_PATH not in [call["path"] for call in wire.calls]


def test_pack_may_ignore_only_proven_foreign_seller_without_querying_its_claims(wire):
    pack_fixture(wire, extra_order={"seller": {"id": "999"}})
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == "identified" and result["order_id"] == ORDER
    searches = [call for call in wire.calls if call["path"] == SEARCH_PATH]
    assert len(searches) == 1 and searches[0]["params"]["order_id"] == ORDER
    wire.responses[ORDER_PATH].payload["seller"] = {"id": "999"}
    wire.calls.clear()
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == "not_linked" and result["error_code"] == "pack_sku_not_linked"
    assert SEARCH_PATH not in [call["path"] for call in wire.calls]


@pytest.mark.parametrize("changes,error", [
    ({"id": "999"}, "pack_scope_mismatch"),
    ({"site_id": "MLM"}, "pack_site_mismatch"),
    ({"context": {"site": "MLM"}}, "pack_site_mismatch"),
])
def test_pack_identity_and_optional_site_must_match(wire, changes, error):
    pack_fixture(wire)
    wire.responses["/packs/" + PACK].payload.update(changes)
    result = wire.run({"order_id": PACK})
    assert result["error_code"] == error and not result["reason_id"]
    assert [call["path"] for call in wire.calls] == ["/orders/" + PACK, "/packs/" + PACK]


@pytest.mark.parametrize("changes,error", [
    ({"id": "999"}, "order_scope_mismatch"),
    ({"pack_id": None}, "order_pack_mismatch"),
    ({"pack_id": "999"}, "order_pack_mismatch"),
    ({"seller": None}, "order_scope_mismatch"),
    ({"seller": {"id": "not-proven"}}, "order_scope_mismatch"),
    ({"seller_id": "999"}, "order_scope_mismatch"),
    ({"site_id": "MLM"}, "order_site_mismatch"),
    ({"context": {"site": "MLM"}}, "order_site_mismatch"),
])
def test_each_pack_order_requires_exact_identity_pack_seller_and_site(wire, changes, error):
    pack_fixture(wire)
    wire.responses[ORDER_PATH].payload.update(changes)
    result = wire.run({"order_id": PACK})
    assert result["error_code"] == error and not result["reason_id"]
    assert SEARCH_PATH not in [call["path"] for call in wire.calls]


@pytest.mark.parametrize("unknown_order", [ORDER, OTHER_ORDER])
def test_pack_selection_requires_official_sku_even_when_legacy_custom_field_matches(wire, unknown_order):
    pack_fixture(wire, extra_order={"order_items": [sold_item(sku="OTHER")]})
    item = wire.responses["/orders/" + unknown_order].payload["order_items"][0]["item"]
    item.pop("seller_sku")
    item["seller_custom_field"] = SKU
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == "ambiguous" and result["error_code"] == "pack_order_sku_unconfirmed"
    assert SEARCH_PATH not in [call["path"] for call in wire.calls]


@pytest.mark.parametrize("rows,error", [
    (None, "incomplete_pack_orders"), ([], "incomplete_pack_orders"),
    ([{"id": ORDER}, {"id": ORDER}], "invalid_pack_orders"),
    ([{"order_id": ORDER}], "invalid_pack_orders"),
    ([{"id": True}], "invalid_pack_orders"),
    ([{"id": "123/456"}], "invalid_pack_orders"),
])
def test_missing_duplicate_or_invalid_pack_orders_cannot_prove_uniqueness(wire, rows, error):
    pack_fixture(wire)
    wire.responses["/packs/" + PACK].payload["orders"] = rows
    result = wire.run({"order_id": PACK})
    assert result["error_code"] == error and not result["reason_id"]
    assert len(wire.calls) == 2


def test_pack_order_limit_blocks_before_fetching_any_order(wire, monkeypatch):
    monkeypatch.setattr(reasons, "_MAX_PACK_ORDERS", 1)
    pack_fixture(wire, extra_order={})
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == "ambiguous" and result["error_code"] == "pack_order_limit"
    assert len(wire.calls) == 2


@pytest.mark.parametrize("status,expected,retry,error", [
    (401, "reconnect_required", False, "reconnect_required"),
    (429, "unavailable", True, "rate_limited"),
    (206, "unavailable", True, "partial_response"),
    (500, "unavailable", True, "provider_unavailable"),
])
@pytest.mark.parametrize("path", ["/packs/" + PACK, "/orders/" + OTHER_ORDER])
def test_pack_errors_preserve_auth_retry_semantics_and_block_incomplete_selection(wire, path, status, expected, retry, error):
    pack_fixture(wire, extra_order={"order_items": [sold_item(sku="OTHER")]})
    wire.responses[path] = Response(status=status, headers={"Retry-After": "123"})
    result = wire.run({"order_id": PACK})
    assert result["result_status"] == expected and result["retryable"] == retry
    assert result["error_code"] == error and not result["reason_id"]
    if status == 429:
        assert result["retry_after_seconds"] == 123
    assert SEARCH_PATH not in [call["path"] for call in wire.calls]


@pytest.mark.parametrize("status,error", [(403, "pack_order_inaccessible"), (404, "incomplete_pack_orders")])
def test_unreadable_pack_order_is_never_assumed_to_belong_to_another_seller(wire, status, error):
    pack_fixture(wire, extra_order={})
    wire.responses["/orders/" + OTHER_ORDER] = Response(status=status)
    result = wire.run({"order_id": PACK})
    assert result["error_code"] == error and not result["reason_id"]
    assert result["result_status"] == ("ambiguous" if status == 403 else "unavailable")
    assert result["retryable"] == (status == 404)
    assert SEARCH_PATH not in [call["path"] for call in wire.calls]


def test_pack_queries_share_deadline_and_do_not_continue_when_budget_expires(wire, monkeypatch):
    pack_fixture(wire)
    clock = [0]
    monkeypatch.setattr(reasons.time, "monotonic", lambda: clock[0])
    wire.after_response = lambda path: clock.__setitem__(0, 31) if path == "/packs/" + PACK else None
    result = wire.run({"order_id": PACK})
    assert result["error_code"] == "timeout" and result["retryable"]
    assert [call["path"] for call in wire.calls] == ["/orders/" + PACK, "/packs/" + PACK]


@pytest.mark.parametrize("order_id", [None, "", "V 60863", "2000000000001/../x", True, 1.5])
def test_missing_explicit_order_does_not_infer_from_sku_fiscal_origin_or_amount(wire, order_id):
    result = wire.run({"order_id": order_id, "sku": "200", "origem_codigo": "V 60863", "valor": 10})
    assert result["result_status"] == "not_linked"
    assert wire.calls == [] and wire.config_calls == []


@pytest.mark.parametrize("mutation,job", [
    ("other-store", {}), ("other-name", {}), ("no-store", {}),
    ("context", {}), ("seller", {}), ("site", {}),
    (None, {"client_id": "tenant-b"}), (None, {"store_id": "store-b"}),
    (None, {"seller_id": "999"}), (None, {"site_id": "MLM"}),
])
def test_cross_store_tenant_or_expected_identity_never_reaches_order_api(wire, mutation, job):
    if mutation == "other-store": wire.store["store_id"] = "store-b"
    if mutation == "other-name": wire.store["nome"] = "Loja B"
    if mutation == "no-store": wire.resolved = None
    if mutation == "context": wire.config["_store_id_context"] = "store-b"
    if mutation == "seller": wire.config["user_id"] = "not-confirmed"
    if mutation == "site": wire.config["site_id"] = "unknown"
    result = wire.run(job)
    assert result["result_status"] == "unavailable"
    assert result["retryable"] is False and not result["reason_id"]
    assert wire.calls == []


@pytest.mark.parametrize("changes", [
    {"id": "999"}, {"seller": {"id": "999"}}, {"seller_id": "999"},
    {"site_id": "MLM"}, {"context": {"site": "MLM"}},
])
def test_order_identity_conflicts_block_claim_lookup(wire, changes):
    wire.responses[ORDER_PATH].payload.update(changes)
    result = wire.run()
    assert result["result_status"] == "unavailable" and not result["retryable"]
    assert [call["path"] for call in wire.calls] == [ORDER_PATH]


@pytest.mark.parametrize("changes", [
    {"resource_id": "999"}, {"order_id": "999"}, {"resource": "payment"}, {"site_id": "MLM"},
    {"players": [{"role": "respondent", "type": "seller", "user_id": "999"}]},
    {"players": [{"role": "complainant", "type": "buyer", "user_id": "123"}]},
])
def test_claim_must_prove_order_seller_role_and_site(wire, changes):
    wire.responses[SEARCH_PATH].payload["data"] = [claim(**changes)]
    result = wire.run()
    assert result["error_code"] == "claim_scope_mismatch"
    assert result["result_status"] == "unavailable" and not result["reason_id"]
    assert len(wire.calls) == 2


@pytest.mark.parametrize("changes", [
    {"claim_id": "999"}, {"orders": [{"order_id": "999"}]}, {"resource_id": "999"},
    {"site_id": "MLM"}, {"seller_id": "999"},
])
def test_return_must_link_exact_claim_and_order_without_conflicting_identity(wire, changes):
    wire.responses[return_path()] = Response(returned(**changes))
    result = wire.run()
    assert result["result_status"] == "unavailable" and result["error_code"] == "return_scope_mismatch"
    assert result["reason_id"] == "" and REASON_PATH not in [call["path"] for call in wire.calls]


def test_multiple_claims_remain_ambiguous_until_explicit_claim_or_return_disambiguates(wire):
    wire.responses[SEARCH_PATH].payload = {"data": [claim(), claim("902")], "paging": {"total": 2}}
    wire.responses[return_path("902")] = Response(returned("802", claim_id="902"))
    ambiguous = wire.run()
    assert ambiguous["result_status"] == "ambiguous" and ambiguous["reason_id"] == ""
    assert wire.run({"claim_id": CLAIM})["result_status"] == "identified"
    selected = wire.run({"return_id": "802"})
    assert selected["result_status"] == "identified" and selected["claim_id"] == "902"


def test_multiple_return_records_never_choose_first_arbitrarily(wire):
    wire.responses[return_path()] = Response([returned(), returned("802")])
    assert wire.run()["result_status"] == "ambiguous"
    result = wire.run({"return_id": "802"})
    assert result["result_status"] == "identified" and result["return_id"] == "802"


def test_claim_search_paginates_and_rejects_incomplete_coverage(wire, monkeypatch):
    monkeypatch.setattr(reasons, "_PAGE_SIZE", 1)
    monkeypatch.setattr(reasons, "_MAX_CLAIMS", 2)
    wire.responses[SEARCH_PATH] = [
        Response({"data": [claim()], "paging": {"total": 3}}),
        Response({"data": [claim("902")], "paging": {"total": 3}}),
    ]
    result = wire.run()
    assert result["result_status"] == "unavailable" and result["retryable"]
    assert result["error_code"] == "incomplete_claim_search"
    assert [call["params"]["offset"] for call in wire.calls if call["path"] == SEARCH_PATH] == [0, 1]


def test_conflicting_reason_for_same_claim_is_not_overwritten(wire, monkeypatch):
    monkeypatch.setattr(reasons, "_PAGE_SIZE", 1)
    wire.responses[SEARCH_PATH] = [
        Response({"data": [claim()], "paging": {"total": 2}}),
        Response({"data": [claim(reason_id="PDD2")], "paging": {"total": 2}}),
    ]
    assert wire.run()["result_status"] == "ambiguous"


@pytest.mark.parametrize("missing", ["claims", "return", "code", "reason", "description"])
def test_missing_provider_evidence_is_not_informed_without_inventing_generic_reasons(wire, missing):
    if missing == "claims": wire.responses[SEARCH_PATH] = Response({"data": [], "paging": {"total": 0}})
    if missing == "return": wire.responses[return_path()] = Response(status=404)
    if missing == "code": wire.responses[SEARCH_PATH].payload["data"][0]["reason_id"] = ""
    if missing == "reason": wire.responses[REASON_PATH] = Response(status=404)
    if missing == "description": wire.responses[REASON_PATH] = Response({"id": "PDD1"})
    result = wire.run()
    assert result["result_status"] == "not_informed"
    assert result["reason_description"] == "" and result["reason_id"] == ""


@pytest.mark.parametrize("status,expected,retry,error", [
    (401, "reconnect_required", False, "reconnect_required"),
    (403, "unavailable", False, "access_denied"),
    (429, "unavailable", True, "rate_limited"),
    (500, "unavailable", True, "provider_unavailable"),
    (503, "unavailable", True, "provider_unavailable"),
    (206, "unavailable", True, "partial_response"),
])
@pytest.mark.parametrize("path", [ORDER_PATH, REASON_PATH])
def test_http_failures_are_safe_and_have_queue_retry_semantics(wire, caplog, path, status, expected, retry, error):
    wire.responses[path] = Response({"private": "do-not-return-payload"}, status, {"Retry-After": "123"})
    result = wire.run()
    assert result["result_status"] == expected and result["retryable"] == retry
    assert result["error_code"] == error
    if status == 429: assert result["retry_after_seconds"] == 123
    assert "do-not-return-payload" not in str(result)
    assert "secret-token" not in str(result) and caplog.text == ""


@pytest.mark.parametrize("failure,code", [
    (requests.exceptions.Timeout("private timeout arguments"), "timeout"),
    (requests.exceptions.ConnectionError("private connection arguments"), "network_error"),
    (RuntimeError("private provider payload"), "provider_unavailable"),
])
def test_transport_errors_never_propagate_sensitive_details(wire, caplog, failure, code):
    wire.responses[ORDER_PATH] = failure
    result = wire.run()
    assert result["error_code"] == code and result["retryable"]
    assert "private" not in str(result) and caplog.text == ""


def test_invalid_json_and_reason_identity_never_become_positive_evidence(wire):
    wire.responses[REASON_PATH] = Response(ValueError("private invalid JSON"))
    result = wire.run()
    assert result["error_code"] == "invalid_response" and result["retryable"]
    wire.responses[REASON_PATH] = Response({"id": "OTHER", "detail": "Wrong reason"})
    assert wire.run()["error_code"] == "reason_identity_mismatch"


def test_deadline_stops_before_next_request(wire, monkeypatch):
    clock = iter([0, 31])
    monkeypatch.setattr(reasons.time, "monotonic", lambda: next(clock))
    result = wire.run()
    assert result["error_code"] == "timeout" and result["retryable"]
    assert wire.calls == []


def test_missing_site_is_confirmed_by_authenticated_account_without_assuming_brazil(wire):
    wire.config.pop("site_id")
    assert wire.run()["result_status"] == "identified"
    assert wire.calls[0]["path"] == "/users/me"
    wire.responses["/users/me"] = Response({"id": "999", "site_id": "MLB"})
    assert wire.run()["error_code"] == "store_identity_unconfirmed"


def test_transport_refresh_cannot_change_canonical_account(wire):
    wire.refreshed = {**wire.config, "user_id": "999"}
    assert wire.run()["error_code"] == "store_identity_changed"


@pytest.mark.parametrize("changed", ["seller", "site", "store", "name"])
def test_canonical_store_is_revalidated_before_accepting_positive_evidence(wire, changed):
    def change_source(path):
        if path != REASON_PATH:
            return
        if changed == "seller": wire.config["user_id"] = "999"
        if changed == "site": wire.config["site_id"] = "MLM"
        if changed == "store": wire.store["store_id"] = "store-b"
        if changed == "name": wire.store["nome"] = "Loja B"

    wire.after_response = change_source
    result = wire.run()
    assert result["result_status"] == "unavailable" and result["error_code"] == "store_identity_changed"
    assert result["reason_id"] == result["reason_description"] == ""


@pytest.mark.parametrize("key", ["claim_id", "return_id"])
@pytest.mark.parametrize("value", [False, 0, "1/../../other"])
def test_invalid_explicit_link_identifiers_are_never_ignored(wire, key, value):
    assert wire.run({key: value})["result_status"] == "not_linked"
    assert wire.calls == []


@pytest.mark.parametrize("status", [401, 403, 404, 409])
def test_store_configuration_failures_do_not_expose_details_or_loop_retries(wire, monkeypatch, status):
    class ConfigurationFailure(Exception):
        status_code = status

    def fail(*args):
        raise ConfigurationFailure("private configuration payload")

    monkeypatch.setattr(reasons.runtime, "ml_config", fail)
    result = wire.run()
    assert not result["retryable"] and "private" not in str(result)
    assert result["result_status"] == ("reconnect_required" if status == 401 else "unavailable")
    assert wire.calls == []


def test_contradictory_claim_count_is_not_positive_evidence(wire):
    wire.responses[SEARCH_PATH].payload["paging"]["total"] = 0
    result = wire.run()
    assert result["error_code"] == "invalid_claim_search" and result["retryable"]


def test_linked_cart_return_accepts_explicit_order_and_claim_resources(wire):
    wire.responses[return_path()] = Response(returned(
        claim_id="902", resources=[{"resource": "claim", "resource_id": CLAIM}],
        resource_id="2000000000002", orders=[{"order_id": "2000000000002"}, return_item()],
    ))
    assert wire.run()["result_status"] == "identified"


@pytest.mark.parametrize("sku", [None, "", "  ", False, 200])
def test_missing_sku_never_attributes_an_order_reason_to_an_unknown_product(wire, sku):
    assert wire.run({"sku": sku})["error_code"] == "explicit_sku_required"
    assert wire.calls == []


def test_job_sku_must_be_recorded_on_the_sale(wire):
    result = wire.run({"sku": "another-sku"})
    assert result["result_status"] == "not_linked"
    assert result["error_code"] == "order_sku_not_linked"
    assert [call["path"] for call in wire.calls] == [ORDER_PATH]


@pytest.mark.parametrize("missing", ["order_items", "sku", "variation"])
def test_missing_sale_identity_is_not_inferred_from_order_or_item_title(wire, missing):
    order = wire.responses[ORDER_PATH].payload
    item = order["order_items"][0]["item"]
    if missing == "order_items": order.pop("order_items")
    if missing == "sku":
        item.update(seller_sku=None, seller_custom_field=None, title=SKU)
    if missing == "variation": item.pop("variation_id")
    result = wire.run()
    assert result["result_status"] in {"not_linked", "unavailable"}
    assert not result["reason_id"] and len(wire.calls) == 1


@pytest.mark.parametrize("sku_field", ["seller_sku", "seller_custom_field"])
def test_official_sku_or_exact_legacy_fallback_can_prove_the_item(wire, sku_field):
    item = wire.responses[ORDER_PATH].payload["order_items"][0]["item"]
    item.pop("seller_custom_field" if sku_field == "seller_sku" else "seller_sku")
    assert wire.run()["result_status"] == "identified"


def test_official_sale_sku_is_not_invalidated_by_unrelated_custom_field(wire):
    wire.responses[ORDER_PATH].payload["order_items"][0]["item"]["seller_custom_field"] = "other-sku"
    result = wire.run()
    assert result["result_status"] == "identified" and result["reason_id"] == "PDD1"


@pytest.mark.parametrize("official_sku", ["another-sku", 200, True, {"value": SKU}])
def test_custom_field_never_overrides_a_present_official_sku(wire, official_sku):
    item = wire.responses[ORDER_PATH].payload["order_items"][0]["item"]
    item.update(seller_sku=official_sku, seller_custom_field=SKU)
    result = wire.run()
    assert result["result_status"] == "not_linked" and result["error_code"] == "order_sku_not_linked"
    assert len(wire.calls) == 1


@pytest.mark.parametrize("custom_field", ["SKU 200", "0200", 200, {"sku": SKU}])
def test_legacy_custom_field_requires_exact_textual_sku_without_guessing(wire, custom_field):
    item = wire.responses[ORDER_PATH].payload["order_items"][0]["item"]
    item.update(seller_sku=None, seller_custom_field=custom_field)
    result = wire.run()
    assert result["result_status"] == "not_linked" and result["error_code"] == "order_sku_not_linked"


def test_same_sold_item_with_two_official_skus_remains_ambiguous(wire):
    wire.responses[ORDER_PATH].payload["order_items"].append(sold_item(sku="other-sku"))
    result = wire.run()
    assert result["result_status"] == "ambiguous" and result["error_code"] == "conflicting_order_sku"


def test_partial_return_of_another_item_does_not_inherit_reason_for_job_sku(wire):
    other_item = "MLB3840513396"
    wire.responses[ORDER_PATH].payload["order_items"].append(sold_item(other_item, "201"))
    wire.responses[return_path()].payload["orders"] = [return_item(item_id=other_item)]
    result = wire.run()
    assert result["result_status"] == "not_linked" and result["error_code"] == "sku_not_returned"
    assert not result["reason_id"] and REASON_PATH not in [call["path"] for call in wire.calls]


def test_return_matches_exact_sold_variation_and_sku(wire):
    wire.responses[ORDER_PATH].payload["order_items"] = [
        sold_item(variation_id="1001"), sold_item(sku="201", variation_id="1002"),
    ]
    wire.responses[return_path()].payload["orders"] = [return_item(variation_id="1002")]
    assert wire.run()["error_code"] == "sku_not_returned"
    wire.responses[return_path()].payload["orders"] = [return_item(variation_id="1001")]
    assert wire.run()["result_status"] == "identified"


@pytest.mark.parametrize("missing", ["orders", "item_id", "variation_id"])
def test_return_without_exact_item_identity_cannot_use_order_only_fallback(wire, missing):
    row = wire.responses[return_path()].payload
    if missing == "orders": row.pop("orders")
    else: row["orders"][0].pop(missing)
    result = wire.run()
    assert result["result_status"] == "not_linked" and not result["reason_id"]
    assert REASON_PATH not in [call["path"] for call in wire.calls]


@pytest.mark.parametrize("changes", [
    {"item_id": "MLB9999999999"}, {"item_id": "MLM3840513395"}, {"variation_id": "1002"},
])
def test_unpurchased_or_foreign_item_variation_never_gets_the_reason(wire, changes):
    wire.responses[return_path()].payload["orders"][0].update(changes)
    assert wire.run()["result_status"] != "identified"


@pytest.mark.parametrize("changes", [{"return_quantity": "0.0"}, {"context_type": "incomplete"}])
def test_zero_quantity_and_unreceived_units_are_not_returned_products(wire, changes):
    wire.responses[return_path()].payload["orders"][0].update(changes)
    result = wire.run()
    assert result["result_status"] == "not_linked" and result["error_code"] == "sku_not_returned"


@pytest.mark.parametrize("quantity", [None, True, "NaN", "Infinity", "-1", "3.0"])
def test_return_quantity_must_be_finite_positive_and_within_the_sale(wire, quantity):
    wire.responses[return_path()].payload["orders"][0]["return_quantity"] = quantity
    result = wire.run()
    assert result["error_code"] == "invalid_return_quantity" and not result["reason_id"]


def test_repeated_return_lines_cannot_exceed_sold_quantity(wire):
    wire.responses[return_path()].payload["orders"] = [return_item()] * 3
    assert wire.run()["error_code"] == "invalid_return_quantity"


@pytest.mark.parametrize("resource,owner_type", [("payment", "collector"), ("shipment", "sender")])
def test_non_order_claim_requires_resource_owner_role_and_exact_return_order(wire, resource, owner_type):
    candidate = claim(resource=resource, resource_id="34567", players=[
        {"role": "respondent", "type": owner_type, "user_id": "123"},
    ])
    wire.responses[SEARCH_PATH].payload["data"] = [candidate]
    assert wire.run()["result_status"] == "identified"
    wire.responses[return_path()].payload.update(resource_id="2000000000002", orders=[
        return_item(order_id="2000000000002"),
    ])
    assert wire.run()["error_code"] == "return_scope_mismatch"


@pytest.mark.parametrize("resource,owner_type", [("payment", "collector"), ("shipment", "sender")])
@pytest.mark.parametrize("invalid", ["account", "role", "type"])
def test_non_order_claim_cannot_relax_seller_identity(wire, resource, owner_type, invalid):
    player = {"role": "respondent", "type": owner_type, "user_id": "123"}
    if invalid == "account": player["user_id"] = "999"
    if invalid == "role": player["role"] = "complainant"
    if invalid == "type": player["type"] = "buyer"
    wire.responses[SEARCH_PATH].payload["data"] = [claim(resource=resource, resource_id="34567", players=[player])]
    assert wire.run()["error_code"] == "claim_scope_mismatch"


def test_same_return_associated_with_multiple_claims_is_still_ambiguous(wire):
    wire.responses[SEARCH_PATH].payload = {"data": [claim(), claim("902")], "paging": {"total": 2}}
    row = returned(resources=[{"resource": "claim", "resource_id": "902"}])
    wire.responses[return_path()] = Response(row)
    wire.responses[return_path("902")] = Response(row)
    result = wire.run({"return_id": RETURN})
    assert result["result_status"] == "ambiguous" and not result["reason_id"]


def test_changed_evidence_for_duplicate_return_id_is_not_overwritten(wire):
    wire.responses[return_path()] = Response([returned(), returned(orders=[return_item(return_quantity="2.0")])])
    assert wire.run()["error_code"] == "conflicting_return_evidence"


def test_claim_search_covers_all_pages_and_closed_claims(wire, monkeypatch):
    monkeypatch.setattr(reasons, "_PAGE_SIZE", 1)
    wire.responses[SEARCH_PATH] = [
        Response({"data": [claim("902", status="opened")], "paging": {"total": 2}}),
        Response({"data": [claim(status="closed")], "paging": {"total": 2}}),
    ]
    wire.responses[return_path("902")] = Response(status=404)
    assert wire.run()["result_status"] == "identified"
    queries = [call["params"] for call in wire.calls if call["path"] == SEARCH_PATH]
    assert [query["offset"] for query in queries] == [0, 1]
    assert all(query["order_id"] == ORDER and "status" not in query and "resource" not in query for query in queries)


def test_duplicate_paginated_claims_do_not_prove_complete_search(wire, monkeypatch):
    monkeypatch.setattr(reasons, "_PAGE_SIZE", 1)
    wire.responses[SEARCH_PATH] = [
        Response({"data": [claim()], "paging": {"total": 2}}),
        Response({"data": [claim()], "paging": {"total": 2}}),
    ]
    result = wire.run()
    assert result["error_code"] == "incomplete_claim_search" and result["retryable"]


def test_original_claim_reason_is_not_replaced_by_resolution_or_seller_review(wire):
    wire.responses[SEARCH_PATH].payload["data"][0]["resolution"] = {"reason": "item_returned"}
    wire.responses[return_path()].payload["related_entities"] = ["reviews"]
    result = wire.run()
    assert result["reason_id"] == "PDD1" and result["reason_description"] == "Recebi outro produto"
    assert [call["path"] for call in wire.calls] == [ORDER_PATH, SEARCH_PATH, return_path(), REASON_PATH]


@pytest.mark.parametrize("as_pack", [False, True])
def test_401_after_central_oauth_refresh_does_not_start_another_refresh(wire, monkeypatch, as_pack):
    from backend.services import mercadolivre_legacy_api as central

    calls, refreshes = [], []

    def request(tenant, store, token, method, url, **kwargs):
        calls.append((tenant, store, token, method, url))
        if as_pack and url.endswith("/orders/" + PACK):
            return Response(status=404)
        return Response(status=401)

    def refresh(tenant, store, cfg):
        refreshes.append((tenant, store))
        return {**cfg, "access_token": "refreshed-test-token"}

    monkeypatch.setattr(reasons.client, "request_get", REAL_REQUEST_GET)
    monkeypatch.setattr(reasons.runtime, "ml_api_request", central._ml_api_request)
    monkeypatch.setattr(central, "_headers_ml", lambda token: {"Authorization": "Bearer " + token}, raising=False)
    monkeypatch.setattr(central, "_ml_http_request", request, raising=False)
    monkeypatch.setattr(central, "_ml_refresh_token", refresh, raising=False)
    monkeypatch.setattr(central, "_ml_http_invalidar_session", lambda *args: None, raising=False)
    result = wire.run({"order_id": PACK if as_pack else ORDER})
    assert result["result_status"] == "reconnect_required" and not result["retryable"]
    assert refreshes == [("tenant-a", "Loja A")]
    expected_path = "/packs/" + PACK if as_pack else ORDER_PATH
    assert len(calls) == (3 if as_pack else 2)
    assert all(call[3] == "GET" and call[4].endswith(expected_path) for call in calls[-2:])
    assert calls[-1][2] == "refreshed-test-token"
