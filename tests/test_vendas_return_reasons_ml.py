from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests

from backend.modules.vendas import return_reasons_ml as reasons


ORDER = "2000000000001"
CLAIM = "901"
RETURN = "801"
ORDER_PATH = "/orders/" + ORDER
SEARCH_PATH = "/post-purchase/v1/claims/search"
REASON_PATH = "/post-purchase/v1/claims/reasons/PDD1"


def claim(ident=CLAIM, **changes):
    value = {
        "id": ident, "resource": "order", "resource_id": ORDER, "site_id": "MLB",
        "reason_id": "PDD1", "players": [
            {"role": "complainant", "type": "buyer", "user_id": "456"},
            {"role": "respondent", "type": "seller", "user_id": "123"},
        ],
    }
    value.update(changes)
    return value


def returned(ident=RETURN, **changes):
    value = {"id": ident, "claim_id": CLAIM, "resource_type": "order", "resource_id": ORDER,
             "orders": [{"order_id": ORDER}]}
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
        ORDER_PATH: Response({"id": ORDER, "seller": {"id": "123"}, "context": {"site": "MLB"}}),
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
    state.run = lambda job=None: reasons.resolve_reason("tenant-a", "store-a", "Loja A", {"order_id": ORDER, **(job or {})})
    return state


def test_identifies_only_owned_return_and_uses_exact_current_filters(wire):
    result = wire.run({"sku": "unrelated SKU"})
    assert result == {
        "result_status": "identified", "reason_id": "PDD1", "reason_description": "Recebi outro produto",
        "reason_source": "mercado_livre", "order_id": ORDER, "claim_id": CLAIM, "return_id": RETURN,
        "seller_id": "123", "site_id": "MLB", "retryable": False,
        "retry_after_seconds": 0, "error_code": "",
    }
    assert [call["path"] for call in wire.calls] == [ORDER_PATH, SEARCH_PATH, return_path(), REASON_PATH]
    assert wire.config_calls == [("tenant-a", "Loja A", "store-a")] * 2
    assert wire.calls[1]["params"] == {
        "resource": "order", "resource_id": ORDER, "players.user_id": "123",
        "players.role": "respondent", "limit": 30, "offset": 0, "sort": "last_updated:desc",
    }
    assert all(call["tenant"] == "tenant-a" and call["store"] == "Loja A" for call in wire.calls)
    assert all(call["timeout"] == 10 and call["deadline"] > 0 for call in wire.calls)


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
        resource_id="2000000000002", orders=[{"order_id": "2000000000002"}, {"order_id": ORDER}],
    ))
    assert wire.run()["result_status"] == "identified"
