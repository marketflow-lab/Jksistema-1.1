from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
import requests

from backend.modules.vendas import return_reasons_bling as resolver
from backend.services.bling_oauth import BlingAuthenticationError
from backend.services.bling_return_references import explicit_reason, extract_reference, safe_explicit_reason


BASE = "https://api.bling.com.br/Api/v3"


@pytest.fixture
def context(monkeypatch):
    cfg = {"id": "fixture-app", "secret": "fixture-secret", "access_token": "fixture-access",
           "refresh_token": "fixture-refresh", "oauth_connection_id": "connection-a", "connected": True}
    state = {"cfg": cfg, "responses": [], "calls": [], "helpers": [], "store_id": "store-a",
             "missing_store": False, "helper_error": None}

    def store(client, loja, *, store_id):
        assert (client, loja, store_id) == ("tenant-a", "Mesmo nome", "store-a")
        if state["missing_store"]:
            return None
        return {"nome": loja, "store_id": state["store_id"],
                "integracoes": {"bling": copy.deepcopy(state["cfg"])}}

    def get(url, **kwargs):
        state["calls"].append((url, kwargs))
        response = state["responses"].pop(0)
        if isinstance(response, Exception):
            raise response
        callback = response.get("after_get")
        if callback:
            callback(state)
        body = response.get("body", {"data": response.get("data")})

        def json_body():
            if isinstance(body, Exception):
                raise body
            return copy.deepcopy(body)

        return SimpleNamespace(status_code=response.get("status", 200), json=json_body,
                               url=response.get("url", url), headers=response.get("headers", {}))

    def execute(client, loja, config, operation, *, store_id):
        assert (client, loja, store_id) == ("tenant-a", "Mesmo nome", "store-a")
        state["helpers"].append({"store_id": store_id, "config": copy.deepcopy(config)})
        if state["helper_error"]:
            raise state["helper_error"]
        data, status = operation(config.get("access_token"))
        return data, status, copy.deepcopy(config)

    def forbid_network(*_args, **_kwargs):
        raise AssertionError("Tests must not contact a network service")

    monkeypatch.setattr(resolver, "buscar_loja", store)
    monkeypatch.setattr(resolver, "_bling_get_with_adaptive_limit", get)
    monkeypatch.setattr(resolver, "_bling_executar_com_refresh", execute)
    monkeypatch.setattr(requests.sessions.Session, "request", forbid_network)
    return state


def inbound(**kwargs):
    return {"id": 101, "tipo": 0, **kwargs}


def resolve(context, **job):
    return resolver.resolve_reference("tenant-a", "store-a", "Mesmo nome",
                                      {"store_id": "store-a", "nota_id": "101", "sku": "200", **job})


@pytest.mark.parametrize("field", ["numeroPedidoLoja", "numeroLoja"])
def test_direct_shop_order_reference_uses_only_confirmed_inbound_invoice(context, field):
    context["responses"] = [{"data": inbound(**{field: "2001"})}]
    result = resolve(context)
    assert result["order_id"] == "2001"
    assert result["result_status"] == "not_linked"
    assert len(context["calls"]) == 1
    url, args = context["calls"][0]
    assert url == BASE + "/nfe/101"
    assert args["params"] is None
    assert args["headers"] == {"Authorization": "Bearer fixture-access"}
    assert args["max_attempts"] == 1
    assert args["timeout"][0] == 3.05 and 0 < args["timeout"][1] <= 8
    assert 0 < args["total_timeout"] <= 10
    assert context["helpers"][0]["store_id"] == "store-a"


def test_no_explicit_reference_does_not_search_by_sku_or_invoice_number(context):
    context["responses"] = [{"data": inbound(numero="2001", itens=[{"codigo": "200"}],
                                              observacoes="Pedido mencionado sem vínculo 2001")}]
    result = resolve(context)
    assert result["result_status"] == "not_linked"
    assert result["order_id"] == ""
    assert len(context["calls"]) == 1


@pytest.mark.parametrize("document,code", [
    ({"id": 102, "tipo": 0, "numeroPedidoLoja": "2001"}, "bling_document_mismatch"),
    ({"id": 101, "tipo": 1, "numeroPedidoLoja": "2001"}, "bling_not_inbound_invoice"),
    ({"id": 101, "numeroPedidoLoja": "2001"}, "bling_not_inbound_invoice"),
])
def test_invoice_identity_and_inbound_type_are_mandatory(context, document, code):
    context["responses"] = [{"data": document}]
    result = resolve(context)
    assert result["result_status"] == "unavailable"
    assert result["error_code"] == code
    assert not result["order_id"]


@pytest.mark.parametrize("note_id", ["", "../102", "101?other=102", True, "1/102", "0"])
def test_invalid_invoice_identifier_never_reaches_api(context, note_id):
    result = resolve(context, nota_id=note_id)
    assert not result["order_id"]
    assert not context["calls"]


def test_changed_cached_order_reference_is_ambiguous(context):
    context["responses"] = [{"data": inbound(numeroPedidoLoja="2002")}]
    result = resolve(context, order_id="2001")
    assert result["result_status"] == "ambiguous"
    assert result["error_code"] == "bling_order_reference_changed"
    assert result["order_id"] == ""


@pytest.mark.parametrize("origin,field,number", [
    ("N 00045", "original_invoice_number", "45"),
    ("v 00031", "bling_order_number", "31"),
    ({"tipo": "N", "numero": "00045"}, "original_invoice_number", "45"),
])
def test_origins_are_document_numbers_and_never_marketplace_ids(origin, field, number):
    assert extract_reference({"origem": origin}) == {field: number}


@pytest.mark.parametrize("origin,resource,number,linked", [
    ("N 45", "nfe", "45", {"id": 400, "tipo": 1, "numero": "45", "numeroPedidoLoja": "2001"}),
    ("V 31", "pedidos/vendas", "31", {"id": 300, "numero": "31", "numeroLoja": "2001"}),
])
def test_exact_unique_origin_number_resolves_detail_before_using_shop_id(context, origin, resource, number, linked):
    context["responses"] = [
        {"data": inbound(origem_codigo=origin)},
        {"data": [{"id": linked["id"], "numero": number, **({"tipo": 1} if resource == "nfe" else {})}]},
        {"data": linked},
    ]
    result = resolve(context)
    assert result["order_id"] == "2001"
    assert [call[0] for call in context["calls"]] == [BASE + "/nfe/101", BASE + "/" + resource,
                                                      BASE + "/" + resource + "/" + str(linked["id"])]
    assert context["calls"][1][1]["params"] == {
        "numero": number, "limite": 2, "pagina": 1, **({"tipo": 1} if resource == "nfe" else {}),
    }


def test_explicit_bling_order_id_is_fetched_as_bling_not_marketplace(context):
    context["responses"] = [{"data": inbound(pedidoVenda={"id": "300"})},
                            {"data": {"id": 300, "numeroPedidoLoja": "2001"}}]
    result = resolve(context)
    assert result["order_id"] == "2001"
    assert context["calls"][1][0] == BASE + "/pedidos/vendas/300"


@pytest.mark.parametrize("rows,status,code", [
    ([{"id": 400, "numero": "45", "tipo": 1}, {"id": 401, "numero": "45", "tipo": 1}],
     "ambiguous", "bling_ambiguous_document"),
    ([{"id": 400, "numero": "46", "tipo": 1}], "unavailable", "bling_document_mismatch"),
    ([{"id": 400, "numero": "45", "tipo": 0}], "unavailable", "bling_document_mismatch"),
    ([{"numero": "45", "tipo": 1}], "unavailable", "bling_document_mismatch"),
])
def test_number_lookup_rejects_ambiguous_or_mismatched_list_rows(context, rows, status, code):
    context["responses"] = [{"data": inbound(origem_codigo="N 45")}, {"data": rows}]
    result = resolve(context)
    assert result["result_status"] == status
    assert result["error_code"] == code
    assert not result["order_id"]
    assert len(context["calls"]) == 2


@pytest.mark.parametrize("detail", [
    {"id": 401, "numero": "45", "tipo": 1, "numeroLoja": "2001"},
    {"id": 400, "numero": "46", "tipo": 1, "numeroLoja": "2001"},
    {"id": 400, "numero": "45", "tipo": 0, "numeroLoja": "2001"},
])
def test_number_lookup_also_revalidates_fetched_detail(context, detail):
    context["responses"] = [{"data": inbound(origem_codigo="N 45")},
                            {"data": [{"id": 400, "numero": "45", "tipo": 1}]}, {"data": detail}]
    result = resolve(context)
    assert result["error_code"] == "bling_document_mismatch"
    assert not result["order_id"]


def test_empty_exact_number_result_is_not_linked(context):
    context["responses"] = [{"data": inbound(origem_codigo="N 45")}, {"data": []}]
    result = resolve(context)
    assert result["result_status"] == "not_linked"
    assert not result["order_id"]


@pytest.mark.parametrize("changed", [
    {"missing_store": True}, {"store_id": "store-b"},
    {"cfg": {"access_token": "other-account"}},
])
def test_store_or_account_configuration_changed_during_request_is_rejected(context, changed):
    context["responses"] = [{"data": inbound(numeroLoja="2001"),
                             "after_get": lambda state: state.update(changed)}]
    result = resolve(context)
    assert result["result_status"] == "unavailable"
    assert result["error_code"] == "bling_identity_changed"
    assert result["retryable"]
    assert not result["order_id"]


@pytest.mark.parametrize("changed", [{"missing_store": True}, {"store_id": "store-b"}])
def test_invalid_store_is_rejected_before_any_request(context, changed):
    context.update(changed)
    result = resolve(context)
    assert result["error_code"] == "store_identity_mismatch"
    assert not context["calls"]


def test_job_from_another_store_is_rejected_before_any_request(context):
    result = resolve(context, store_id="store-b")
    assert result["result_status"] == "unavailable"
    assert not context["calls"]


@pytest.mark.parametrize("flags", [{"oauth_invalid": True}, {"status": "reautenticacao_necessaria"}])
def test_oauth_reauthorization_flags_prevent_requests_and_refresh(context, flags):
    context["cfg"].update(flags)
    result = resolve(context)
    assert result["result_status"] == "reconnect_required"
    assert result["error_code"] == "bling_reconnect_required"
    assert not context["helpers"] and not context["calls"]


def test_unconnected_store_is_not_queried(context):
    context["cfg"].pop("access_token")
    result = resolve(context)
    assert result["error_code"] == "bling_not_connected"
    assert not context["calls"]


def test_401_returned_by_refresh_helper_requires_reconnection(context):
    context["responses"] = [{"status": 401}]
    result = resolve(context)
    assert result["result_status"] == "reconnect_required"
    assert result["error_code"] == "bling_reconnect_required"
    assert result["retry_after_seconds"] == 900


def test_oauth_refresh_authentication_exception_requires_reconnection(context):
    context["helper_error"] = BlingAuthenticationError("DO-NOT-STORE personal@example.com")
    result = resolve(context)
    assert result["result_status"] == "reconnect_required"
    assert "DO-NOT-STORE" not in str(result)


@pytest.mark.parametrize("status,headers,code,retry", [
    (429, {"Retry-After": "120"}, "bling_rate_limit", True),
    (429, {"Retry-After": "not-a-number"}, "bling_rate_limit", True),
    (500, {}, "bling_unavailable", True), (503, {}, "bling_unavailable", True),
    (404, {}, "bling_not_found", False), (403, {}, "bling_forbidden", False),
])
def test_http_error_classification_has_no_response_payload(context, status, headers, code, retry):
    context["responses"] = [{"status": status, "headers": headers,
                             "body": {"access_token": "DO-NOT-STORE", "email": "person@example.com"}}]
    result = resolve(context)
    assert result["result_status"] == "unavailable"
    assert result["error_code"] == code
    assert result["retryable"] is retry
    assert not result["order_id"]
    assert "DO-NOT-STORE" not in str(result)
    if status == 429:
        assert 5 <= result["retry_after_seconds"] <= 3600


def test_timeout_is_retryable_without_exception_text(context):
    context["responses"] = [requests.exceptions.Timeout("DO-NOT-STORE token")]
    result = resolve(context)
    assert result["error_code"] == "bling_timeout"
    assert result["retryable"]
    assert "DO-NOT-STORE" not in str(result)


def test_overall_deadline_prevents_network_request(context, monkeypatch):
    timestamps = iter((0, 26))
    monkeypatch.setattr(resolver.time, "monotonic", lambda: next(timestamps))
    result = resolve(context)
    assert result["error_code"] == "bling_timeout"
    assert not context["calls"]


@pytest.mark.parametrize("body", [{}, [], {"data": None}, {"data": []}, ValueError("DO-NOT-STORE")])
def test_incomplete_or_invalid_json_never_returns_a_link(context, body):
    context["responses"] = [{"body": body}]
    result = resolve(context)
    assert result["result_status"] == "unavailable"
    assert not result["order_id"]
    assert "DO-NOT-STORE" not in str(result)


@pytest.mark.parametrize("url", [
    "http://api.bling.com.br/Api/v3/nfe/101", "https://other.example/Api/v3/nfe/101",
    "https://api.bling.com.br/Api/v3/nfe/102", "https://user@api.bling.com.br/Api/v3/nfe/101",
])
def test_response_destination_must_match_exact_requested_document(context, url):
    context["responses"] = [{"data": inbound(numeroLoja="2001"), "url": url}]
    result = resolve(context)
    assert result["error_code"] == "bling_unexpected_destination"
    assert not result["order_id"]


@pytest.mark.parametrize("document", [
    {"motivoDevolucao": "Produto danificado"},
    {"motivo_devolucao": "Produto danificado"},
    {"observacoes": "Motivo da devolução: Produto danificado"},
    {"observacoesInternas": "Motivo devolucao: Produto danificado"},
    {"itens": [{"codigo": "200", "informacoesAdicionais": "Motivo da devolução: Produto danificado"}]},
])
def test_explicitly_labelled_reason_is_kept_without_raw_document(document):
    assert explicit_reason(document, "200") == "Produto danificado"


def test_explicit_bling_reason_is_a_fallback_without_order_link(context):
    context["responses"] = [{"data": inbound(observacoes="Motivo da devolução: Produto danificado",
                                              contato={"email": "DO-NOT-STORE"})}]
    result = resolve(context)
    assert result["result_status"] == "identified"
    assert result["reason_description"] == "Produto danificado"
    assert result["reason_source"] == "bling"
    assert not result["order_id"]
    assert "DO-NOT-STORE" not in str(result)


@pytest.mark.parametrize("text", [
    "Produto danificado", "CFOP 1202", "Natureza da operação: devolução de venda",
    "Cliente falou sobre entrega, sem rótulo explícito de motivo",
])
def test_free_text_and_fiscal_classification_do_not_infer_reason(text):
    assert explicit_reason({"observacoes": text, "naturezaOperacao": {"descricao": text},
                            "itens": [{"codigo": "200", "cfop": "1202"}]}, "200") == ""


def test_conflicting_explicit_reasons_do_not_infer_one():
    assert explicit_reason({"motivoDevolucao": "Produto danificado",
                            "observacoes": "Motivo da devolução: Compra cancelada"}) == ""


def test_item_reason_requires_unique_sku_inside_confirmed_invoice():
    row = {"codigo": "200", "informacoesAdicionais": "Motivo da devolução: Produto danificado"}
    assert explicit_reason({"itens": [row, dict(row)]}, "200") == ""
    assert explicit_reason({"itens": [row]}, "201") == ""
    assert explicit_reason({"itens": [row]}, "") == ""


@pytest.mark.parametrize("text", [
    "Contato pessoa@example.com", "CPF 123.456.789-01", "CNPJ 12.345.678/0001-99",
    "Telefone (11) 99999-9999", "Bearer secret-value", "access_token=secret-value",
    "refresh_token=secret-value", "api_key=secret-value", "Senha confidential",
])
def test_reason_with_personal_or_secret_information_is_rejected(text):
    assert safe_explicit_reason(text) == ""
    assert explicit_reason({"motivoDevolucao": text}) == ""


def test_conflicting_direct_shop_ids_do_not_infer_an_alternative_link(context):
    context["responses"] = [{"data": inbound(numeroPedidoLoja="2001", numeroLoja="2002",
                                              origem_codigo="V 31")},
                            {"data": [{"id": 300, "numero": "31"}]},
                            {"data": {"id": 300, "numero": "31", "numeroLoja": "2003"}}]
    result = resolve(context)
    assert result["result_status"] == "ambiguous"
    assert not result["order_id"]
    assert len(context["calls"]) == 1


def test_conflicting_origin_references_are_not_resolved_by_first_field(context):
    context["responses"] = [{"data": inbound(origem_codigo="N 45", origem="N 46")},
                            {"data": [{"id": 400, "numero": "45", "tipo": 1}]},
                            {"data": {"id": 400, "numero": "45", "tipo": 1, "numeroLoja": "2001"}}]
    result = resolve(context)
    assert result["result_status"] == "ambiguous"
    assert not result["order_id"]
    assert len(context["calls"]) == 1
