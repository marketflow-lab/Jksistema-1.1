from __future__ import annotations

import copy
import io
from types import SimpleNamespace

import pytest
import requests
from urllib3.exceptions import ProtocolError, ReadTimeoutError

from backend.modules.vendas import return_reasons_bling as resolver
from backend.services.bling_oauth import BlingAuthenticationError
from backend.services.bling_return_references import explicit_reason, extract_reference, safe_explicit_reason


BASE = "https://api.bling.com.br/Api/v3"


@pytest.fixture
def context(monkeypatch):
    cfg = {"id": "fixture-app", "secret": "fixture-secret", "access_token": "fixture-access",
           "refresh_token": "fixture-refresh", "oauth_connection_id": "connection-a", "connected": True}
    state = {"cfg": cfg, "responses": [], "calls": [], "helpers": [], "store_id": "store-a",
             "xml_responses": [], "xml_calls": [], "xml_closed": [],
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

    def xml_request(session, method, url, **kwargs):
        assert state["xml_responses"], "Tests must not contact a network service"
        state["xml_calls"].append((url, {**kwargs, "trust_env": session.trust_env, "method": method}))
        response = state["xml_responses"].pop(0)
        if isinstance(response, Exception):
            raise response
        callback = response.get("after_get")
        if callback:
            callback(state)

        class Stream(io.BytesIO):
            def read1(self, amount, *, decode_content=False):
                assert decode_content is False
                if response.get("read_error"):
                    raise response["read_error"]
                if response.get("after_read"):
                    response["after_read"]()
                return super().read1(amount)

        return SimpleNamespace(status_code=response.get("status", 200),
                               url=response.get("url", url), headers=response.get("headers", {}),
                               history=response.get("history", []), raw=Stream(response.get("body", b"")),
                               close=lambda: state["xml_closed"].append(True))

    monkeypatch.setattr(resolver, "buscar_loja", store)
    monkeypatch.setattr(resolver, "_bling_get_with_adaptive_limit", get)
    monkeypatch.setattr(resolver, "_bling_executar_com_refresh", execute)
    monkeypatch.setattr(requests.sessions.Session, "request", xml_request)
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


INBOUND_KEY = "3" * 44
ORIGINAL_KEY = "4" * 44
SECOND_KEY = "5" * 44
XML_URL = "https://www.bling.com.br/relatorios/nfe.xml.php?chaveAcesso=" + INBOUND_KEY + "&signature=fixture-signature"


def invoice_xml(*keys, key=INBOUND_KEY, tipo="0", sku="200"):
    references = "".join(f"<NFref><refNFe>{ref}</refNFe></NFref>" for ref in keys)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe><infNFe Id="NFe{key}">'
            f'<ide><tpNF>{tipo}</tpNF>{references}</ide>'
            f'<det nItem="1"><prod><cProd>{sku}</cProd></prod></det>'
            f'</infNFe></NFe><protNFe><infProt><chNFe>{key}</chNFe></infProt></protNFe></nfeProc>').encode()


def xml_context(context, *, keys=(ORIGINAL_KEY,), document=None, body=None, original=None, listing=None):
    context["responses"] = [
        {"data": inbound(chaveAcesso=INBOUND_KEY, xml=XML_URL, itens=[{"codigo": "200"}], **(document or {}))},
        {"data": [{"id": 400}] if listing is None else listing},
        {"data": original or {"id": 400, "tipo": 1, "chaveAcesso": ORIGINAL_KEY,
                              "numero": "845", "numeroPedidoLoja": "2000000000000001",
                              "itens": [{"codigo": "200"}]}},
    ]
    context["xml_responses"] = [{"body": invoice_xml(*keys) if body is None else body}]


def test_xml_reference_resolves_exact_original_invoice_before_using_marketplace_order(context):
    xml_context(context)
    result = resolve(context)
    assert result["order_id"] == "2000000000000001"
    assert result["result_status"] == "not_linked"
    assert [url for url, _ in context["calls"]] == [BASE + "/nfe/101", BASE + "/nfe", BASE + "/nfe/400"]
    assert context["calls"][1][1]["params"] == {
        "chaveAcesso": ORIGINAL_KEY, "tipo": 1, "limite": 2, "pagina": 1,
    }
    url, args = context["xml_calls"][0]
    assert url == XML_URL and args["method"].lower() == "get"
    assert args["trust_env"] is False
    assert args["allow_redirects"] is False and args["stream"] is True
    assert not any(key.casefold() == "authorization" for key in args["headers"])
    assert args["headers"]["Accept-Encoding"] == "identity"
    assert context["xml_closed"] == [True]
    assert "signature" not in str(result) and ORIGINAL_KEY not in str(result)


def test_explicit_marketplace_order_precedes_xml_download(context):
    xml_context(context, document={"numeroPedidoLoja": "2000000000000001"})
    result = resolve(context)
    assert result["order_id"] == "2000000000000001"
    assert len(context["calls"]) == 1 and not context["xml_calls"]


def test_signed_xml_url_preserves_empty_s_parameter_returned_by_bling(context):
    xml_context(context)
    url = XML_URL.replace("&signature=", "&s=&signature=")
    context["responses"][0]["data"]["xml"] = url
    result = resolve(context)
    assert result["order_id"] == "2000000000000001"
    assert context["xml_calls"][0][0] == url


def test_signed_xml_url_preserves_bare_s_parameter_returned_by_bling(context):
    xml_context(context)
    url = XML_URL.replace("&signature=", "&s&signature=")
    context["responses"][0]["data"]["xml"] = url
    result = resolve(context)
    assert result["order_id"] == "2000000000000001"
    assert context["xml_calls"][0][0] == url


@pytest.mark.parametrize("suffix", ["&other", "&signature", "&chaveAcesso", "&s&s",
                                      "&%73", "&s%20", "&\uff53"])
def test_only_exact_bare_s_is_normalized_for_xml_url_validation(context, suffix):
    xml_context(context)
    context["responses"][0]["data"]["xml"] = XML_URL + suffix
    result = resolve(context)
    assert result["error_code"] == "bling_xml_unsafe_url" and not result["order_id"]
    assert not context["xml_calls"]


@pytest.mark.parametrize("suffix", ["&s=1", "&s=%20", "&s=&s=", "&other=",
                                      "&signature=duplicate", "&chaveAcesso=" + INBOUND_KEY])
def test_optional_empty_s_does_not_allow_extra_or_duplicate_xml_parameters(context, suffix):
    xml_context(context)
    context["responses"][0]["data"]["xml"] = XML_URL + suffix
    result = resolve(context)
    assert result["error_code"] == "bling_xml_unsafe_url" and not result["order_id"]
    assert not context["xml_calls"]


def test_xml_response_url_must_preserve_optional_empty_s_parameter(context):
    xml_context(context)
    context["responses"][0]["data"]["xml"] = XML_URL.replace("&signature=", "&s=&signature=")
    context["xml_responses"][0]["url"] = XML_URL
    result = resolve(context)
    assert result["error_code"] == "bling_xml_unsafe_url" and not result["order_id"]


def test_xml_reconfirms_same_captured_order_reference(context):
    xml_context(context)
    assert resolve(context, order_id="2000000000000001")["order_id"] == "2000000000000001"


def test_xml_cannot_replace_conflicting_captured_order(context):
    xml_context(context)
    result = resolve(context, order_id="2000000000000002")
    assert result["result_status"] == "ambiguous" and not result["order_id"]


def test_original_invoice_number_is_never_treated_as_marketplace_order(context):
    xml_context(context, original={"id": 400, "tipo": 1, "chaveAcesso": ORIGINAL_KEY,
                                  "numero": "2000000000000001", "itens": [{"codigo": "200"}]})
    result = resolve(context)
    assert result["result_status"] == "not_linked" and not result["order_id"]


def test_multiple_xml_references_are_resolved_only_by_unique_matching_sku(context):
    xml_context(context, keys=(ORIGINAL_KEY, SECOND_KEY))
    context["responses"][2]["data"]["itens"] = [{"codigo": "999"}]
    context["responses"] += [{"data": [{"id": 401, "tipo": 1, "chaveAcesso": SECOND_KEY}]},
                             {"data": {"id": 401, "tipo": 1, "chaveAcesso": SECOND_KEY,
                                       "numeroPedidoLoja": "2000000000000002", "itens": [{"codigo": "200"}]}}]
    assert resolve(context)["order_id"] == "2000000000000002"
    assert len(context["calls"]) == 5


def test_two_original_invoices_with_same_sku_are_ambiguous(context):
    xml_context(context, keys=(ORIGINAL_KEY, SECOND_KEY))
    context["responses"] += [{"data": [{"id": 401}]},
                             {"data": {"id": 401, "tipo": 1, "chaveAcesso": SECOND_KEY,
                                       "numeroPedidoLoja": "2000000000000002", "itens": [{"codigo": "200"}]}}]
    result = resolve(context)
    assert result["result_status"] == "ambiguous" and not result["order_id"]


def test_unavailable_second_reference_does_not_establish_unique_sku_match(context):
    xml_context(context, keys=(ORIGINAL_KEY, SECOND_KEY))
    context["responses"] += [{"data": []}]
    result = resolve(context)
    assert result["result_status"] == "ambiguous" and not result["order_id"]


def test_repeated_identical_xml_reference_does_not_duplicate_lookup(context):
    xml_context(context, keys=(ORIGINAL_KEY, ORIGINAL_KEY))
    assert resolve(context)["order_id"] == "2000000000000001"
    assert len(context["calls"]) == 3


@pytest.mark.parametrize("listing,code,status", [
    ([{"id": 400}, {"id": 401}], "bling_ambiguous_document", "ambiguous"),
    ([{"id": 400, "chaveAcesso": SECOND_KEY}], "bling_document_mismatch", "unavailable"),
    ([{"id": 400, "tipo": 0}], "bling_document_mismatch", "unavailable"),
    ([{"tipo": 1}], "bling_document_mismatch", "unavailable"),
])
def test_original_key_search_rejects_ambiguous_or_conflicting_listing(context, listing, code, status):
    xml_context(context, listing=listing)
    result = resolve(context)
    assert result["result_status"] == status and result["error_code"] == code
    assert not result["order_id"]


@pytest.mark.parametrize("field,value", [("id", 401), ("chaveAcesso", SECOND_KEY),
                                          ("chaveAcesso", ""), ("tipo", 0), ("tipo", None)])
def test_original_detail_requires_exact_identity_key_and_outbound_type(context, field, value):
    xml_context(context)
    context["responses"][2]["data"][field] = value
    result = resolve(context)
    assert result["error_code"] == "bling_document_mismatch" and not result["order_id"]


def test_xml_original_must_contain_the_returned_sku(context):
    xml_context(context)
    context["responses"][2]["data"]["itens"] = [{"codigo": "999"}]
    result = resolve(context)
    assert result["result_status"] == "not_linked" and not result["order_id"]


@pytest.mark.parametrize("body,code", [
    (invoice_xml(ORIGINAL_KEY, key=SECOND_KEY), "bling_xml_document_mismatch"),
    (invoice_xml(ORIGINAL_KEY, tipo="1"), "bling_xml_document_mismatch"),
    (invoice_xml(ORIGINAL_KEY, sku="999"), "bling_xml_sku_mismatch"),
    (invoice_xml("123"), "bling_xml_invalid_reference"),
    (b"<not-xml", "bling_xml_invalid"),
    (b"<html>Authentication required</html>", "bling_xml_invalid"),
])
def test_xml_must_confirm_inbound_identity_type_and_sku(context, body, code):
    xml_context(context, body=body)
    result = resolve(context)
    assert result["error_code"] == code and not result["order_id"]
    assert len(context["calls"]) == 1


@pytest.mark.parametrize("url", [
    XML_URL.replace("https:", "http:"), XML_URL.replace("www.bling.com.br", "127.0.0.1"),
    XML_URL.replace("www.bling.com.br", "www.bling.com.br.evil.example"),
    XML_URL.replace("www.bling.com.br", "user@www.bling.com.br"),
    XML_URL.replace("www.bling.com.br", "www.bling.com.br:8080"),
    XML_URL.replace("/relatorios/nfe.xml.php", "/relatorios/../nfe.xml.php"),
    XML_URL.replace("/relatorios/nfe.xml.php", "/redirect"),
    XML_URL + "&redirect=https://127.0.0.1", XML_URL + "#fragment",
    XML_URL.replace(INBOUND_KEY, SECOND_KEY), XML_URL + "&chaveAcesso=" + SECOND_KEY,
    XML_URL.replace("&signature=fixture-signature", ""), " " + XML_URL,
])
def test_untrusted_or_mismatched_xml_links_never_reach_network(context, url):
    xml_context(context)
    context["responses"][0]["data"]["xml"] = url
    result = resolve(context)
    assert result["error_code"] == "bling_xml_unsafe_url" and not result["order_id"]
    assert not context["xml_calls"] and len(context["calls"]) == 1


@pytest.mark.parametrize("response,code,retry", [
    ({"status": 302, "headers": {"Location": "http://127.0.0.1"}}, "bling_xml_redirect", False),
    ({"status": 429, "headers": {"Retry-After": "120"}}, "bling_rate_limit", True),
    ({"status": 503}, "bling_unavailable", True),
    ({"status": 403}, "bling_xml_unavailable", False),
    ({"url": XML_URL.replace("www.bling.com.br", "evil.example")}, "bling_xml_unsafe_url", False),
    ({"headers": {"Content-Length": "999999999"}}, "bling_xml_too_large", False),
    ({"headers": {"Content-Encoding": "gzip"}}, "bling_xml_compressed", False),
    ({"body": b"x" * (1024 * 1024 + 1)}, "bling_xml_too_large", False),
])
def test_xml_http_failures_are_bounded_classified_and_closed(context, response, code, retry):
    xml_context(context)
    context["xml_responses"] = [response]
    result = resolve(context)
    assert result["error_code"] == code and result["retryable"] is retry
    assert not result["order_id"] and context["xml_closed"] == [True]


@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE nfeProc [<!ENTITY xxe SYSTEM "file:///secret.txt">]>',
    '<!DOCTYPE nfeProc SYSTEM "https://127.0.0.1/private">',
    '<!DOCTYPE nfeProc [<!ENTITY small "abc"><!ENTITY big "&small;&small;&small;">]>',
])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_dtd_and_entities_are_rejected_without_external_access(context, declaration, encoding):
    payload = invoice_xml(ORIGINAL_KEY).decode().replace('<?xml version="1.0" encoding="UTF-8"?>', "")
    body = (f'<?xml version="1.0" encoding="{encoding}"?>' + declaration + payload).encode(encoding)
    xml_context(context, body=body)
    result = resolve(context)
    assert result["error_code"] == "bling_xml_unsafe" and not result["order_id"]
    assert len(context["xml_calls"]) == 1 and len(context["calls"]) == 1


def test_duplicate_invoice_nodes_cannot_supply_a_different_reference(context):
    body = invoice_xml(ORIGINAL_KEY)
    extra = f'<NFe><infNFe Id="NFe{SECOND_KEY}"><ide><tpNF>0</tpNF></ide></infNFe></NFe>'.encode()
    xml_context(context, body=body.replace(b"</nfeProc>", extra + b"</nfeProc>"))
    result = resolve(context)
    assert result["error_code"] == "bling_xml_invalid" and not result["order_id"]


def test_xml_timeout_does_not_expose_signed_url_or_exception_message(context):
    xml_context(context)
    context["xml_responses"] = [requests.exceptions.Timeout("DO-NOT-STORE " + XML_URL)]
    result = resolve(context)
    assert result["error_code"] == "bling_timeout" and result["retryable"]
    assert "DO-NOT-STORE" not in str(result) and "signature" not in str(result)


def test_overall_deadline_also_limits_xml_body_reading(context, monkeypatch):
    clock = [0]
    monkeypatch.setattr(resolver.time, "monotonic", lambda: clock[0])
    xml_context(context)
    context["xml_responses"][0]["after_read"] = lambda: clock.__setitem__(0, 26)
    result = resolve(context)
    assert result["error_code"] == "bling_timeout" and result["retryable"]
    assert len(context["calls"]) == 1 and context["xml_closed"] == [True]


def test_store_connection_change_during_xml_download_rejects_link(context):
    xml_context(context)
    context["xml_responses"][0]["after_get"] = lambda state: state.update(cfg={"access_token": "other-account"})
    result = resolve(context)
    assert result["error_code"] == "bling_identity_changed" and not result["order_id"]
    assert len(context["calls"]) == 1


def test_xml_without_original_reference_preserves_explicit_bling_reason(context):
    xml_context(context, keys=(), document={"motivoDevolucao": "Produto danificado"})
    result = resolve(context)
    assert result["result_status"] == "identified" and result["reason_description"] == "Produto danificado"
    assert not result["order_id"] and len(context["calls"]) == 1


@pytest.mark.parametrize("failure,code", [
    (ReadTimeoutError(None, XML_URL, "DO-NOT-STORE"), "bling_timeout"),
    (ProtocolError("DO-NOT-STORE " + XML_URL), "bling_xml_unavailable"),
])
def test_raw_xml_stream_failures_are_classified_without_payload(context, failure, code):
    xml_context(context)
    context["xml_responses"][0]["read_error"] = failure
    result = resolve(context)
    assert result["error_code"] == code and result["retryable"]
    assert context["xml_closed"] == [True] and len(context["calls"]) == 1
    assert "DO-NOT-STORE" not in str(result) and "signature" not in str(result)


def test_truncated_xml_stream_cannot_be_accepted_as_complete(context):
    xml_context(context)
    context["xml_responses"][0]["headers"] = {"Content-Length": str(len(invoice_xml(ORIGINAL_KEY)) + 20)}
    result = resolve(context)
    assert result["error_code"] == "bling_xml_incomplete" and result["retryable"]
    assert not result["order_id"]


def test_missing_sku_on_competing_original_cannot_prove_uniqueness(context):
    xml_context(context, keys=(ORIGINAL_KEY, SECOND_KEY))
    context["responses"] += [{"data": [{"id": 401}]},
                             {"data": {"id": 401, "tipo": 1, "chaveAcesso": SECOND_KEY,
                                       "numeroPedidoLoja": "2000000000000002", "itens": [{"descricao": "fixture"}]}}]
    result = resolve(context)
    assert result["error_code"] == "bling_incomplete_response" and not result["order_id"]


@pytest.mark.parametrize("payload", [
    '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">' + '<extra>' * 65 + '</extra>' * 65 + '</nfeProc>',
    '<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">' + '<extra/>' * 20001 + '</nfeProc>',
], ids=["depth-limit", "node-limit"])
def test_xml_structure_has_fixed_depth_and_node_limits(context, payload):
    xml_context(context, body=payload.encode())
    result = resolve(context)
    assert result["error_code"] == "bling_xml_too_complex" and not result["order_id"]


def test_many_xml_references_are_ambiguous_before_fanning_out_requests(context):
    keys = tuple(str(digit) * 44 for digit in range(4, 10))
    xml_context(context, keys=keys)
    result = resolve(context)
    assert result["result_status"] == "ambiguous" and not result["order_id"]
    assert len(context["calls"]) == 1


@pytest.mark.parametrize("status,code,retry", [(429, "bling_rate_limit", True),
                                             (503, "bling_unavailable", True),
                                             (403, "bling_forbidden", False)])
def test_original_lookup_errors_remain_classified_after_valid_xml(context, status, code, retry):
    xml_context(context)
    context["responses"][1] = {"status": status}
    result = resolve(context)
    assert result["error_code"] == code and result["retryable"] is retry
    assert not result["order_id"]


def test_xml_original_lookup_remains_scoped_after_connection_changes(context):
    xml_context(context)
    context["responses"][1]["after_get"] = lambda state: state.update(store_id="store-b")
    result = resolve(context)
    assert result["error_code"] == "bling_identity_changed" and not result["order_id"]
    assert len(context["calls"]) == 2
