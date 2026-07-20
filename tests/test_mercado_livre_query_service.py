from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from backend.services import mercado_livre_query_catalog as catalog
from backend.services import mercado_livre_query_service as service


class _Response:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = dict(headers or {})

    def json(self):
        return self._payload


def _wire_store(monkeypatch, responses):
    from backend.services import ia_tools_marketplaces, mercadolivre_legacy_api

    calls = []
    queue = list(responses)
    monkeypatch.setattr(ia_tools_marketplaces, "_ia_ml_resolver_loja_exata", lambda client_id, loja: ("Loja A", {}))
    monkeypatch.setattr(mercadolivre_legacy_api, "_obter_cfg_ml", lambda client_id, loja: {"user_id": "123", "site_id": "MLB", "access_token": "never-return"})

    def request_get(client_id, loja, cfg, url, **kwargs):
        calls.append({"url": url, "params": kwargs.get("params") or {}, "headers": kwargs.get("headers") or {}})
        return queue.pop(0), cfg

    monkeypatch.setattr(ia_tools_marketplaces, "_ia_ml_request_get", request_get)
    return calls


def test_catalog_is_versioned_unique_official_and_read_only():
    resources = catalog.MERCADO_LIVRE_QUERY_RESOURCES
    assert len(resources) == 165
    assert len({entry["resource_id"] for entry in resources}) == 165
    assert {entry["method"] for entry in resources} <= {"GET", "POST"}
    assert all(entry["read_only"] is True for entry in resources)
    assert all(str(entry["official_source"]).startswith("https://developers.mercadolivre.com.br/") for entry in resources)
    assert sum(entry["policy"] == "tombstone" for entry in resources) == 1
    assert all(entry["policy"] in {"allowlisted", "allowlisted_redacted", "delegated", "document_only", "tombstone"} for entry in resources)
    assert all(entry["method"] == "GET" for entry in resources if entry["policy"] in {"allowlisted", "allowlisted_redacted", "delegated"})
    guide = (Path(__file__).resolve().parents[1] / "docs" / "knowledge" / "mercado-livre-api-consultas.md").read_text(encoding="utf-8")
    assert all(f"`{entry['resource_id']}`" in guide for entry in resources)


def test_catalog_search_document_only_and_tombstone_never_touch_network():
    listed = service.execute_mercado_livre_query("000002", message="billing", limit=5)
    assert listed["result"]["success"] is True
    assert listed["result"]["returned"] == 5

    document_only = service.execute_mercado_livre_query("000002", resource_id="ml.ads.advertisers")
    assert document_only["result"]["error_code"] == "resource_document_only"

    removed = service.execute_mercado_livre_query("000002", resource_id="ml.compat.chunk_search_removed")
    assert removed["result"]["error_code"] == "resource_deprecated"
    assert removed["result"]["deprecated_at"] == "2026-07-15"


def test_free_url_unknown_parameter_and_missing_store_fail_closed(monkeypatch):
    assert service.execute_mercado_livre_query("000002", resource_id="https://api.mercadolibre.com/users/me")["result"]["error_code"] == "free_url_blocked"

    from backend.services import ia_tools_marketplaces
    monkeypatch.setattr(ia_tools_marketplaces, "_ia_ml_resolver_loja_exata", lambda client_id, loja: (None, {"code": "store_required", "message": "exact"}))
    missing = service.execute_mercado_livre_query("000002", resource_id="ml.users.me")
    assert missing["result"]["error_code"] == "store_required"

    _wire_store(monkeypatch, [])
    invalid = service.execute_mercado_livre_query("000002", loja="Loja A", resource_id="ml.users.me", params={"seller_id": "999"})
    assert invalid["result"]["error_code"] == "invalid_parameters"


def test_server_injects_seller_and_site_filters(monkeypatch):
    calls = _wire_store(monkeypatch, [_Response({"results": []})])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.claims.search",
        params={"status": "opened", "limit": 500},
    )
    assert output["result"]["success"] is True
    assert calls[0]["params"]["players.user_id"] == "123"
    assert calls[0]["params"]["players.role"] == "respondent"
    assert calls[0]["params"]["limit"] == 100
    assert output["arguments"]["parameter_count"] == 2


def test_owned_item_is_checked_before_query(monkeypatch):
    from backend.services import ia_tools_marketplaces

    _wire_store(monkeypatch, [])
    monkeypatch.setattr(
        ia_tools_marketplaces, "_ia_ml_owned_item",
        lambda *args, **kwargs: (None, args[2], {"code": "listing_not_in_store", "message": "foreign"}),
    )
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.items.prices", params={"item_id": "MLB123456789"},
    )
    assert output["result"]["error_code"] == "listing_not_in_store"


def test_user_product_stock_validates_owner_and_returns_redacted_data(monkeypatch):
    calls = _wire_store(monkeypatch, [
        _Response({"user_id": "123", "id": "MLBU123456"}),
        _Response({
            "user_id": "123", "locations": [{"quantity": 7}],
            "email": "buyer@example.com", "access_token": "secret-value",
        }, headers={"x-version": "42"}),
    ])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.stock.user_product",
        params={"user_product_id": "MLBU123456"},
    )
    assert output["result"]["success"] is True
    assert calls[0]["url"].endswith("/user-products/MLBU123456")
    assert calls[1]["url"].endswith("/user-products/MLBU123456/stock")
    assert output["result"]["response_headers"] == {"x-version": "42"}
    encoded = json.dumps(output, ensure_ascii=False)
    assert "buyer@example.com" not in encoded
    assert "secret-value" not in encoded


def test_billing_info_is_derived_from_owned_order_and_pii_is_removed(monkeypatch):
    calls = _wire_store(monkeypatch, [
        _Response({"seller": {"id": 123}, "buyer": {"billing_info": {"id": "BILL-9"}}}),
        _Response({
            "business_name": "Cliente X", "email": "x@example.com",
            "identification": {"number": "12345678901"}, "amount": 12.5,
            "taxpayer_id": "12345678901", "customer_document": "12345678000199",
            "full_name": "Pessoa Exposta",
            "additional_info": [
                {"type": "DOC_NUMBER", "value": "12345678901"},
                {"type": "STREET_NAME", "value": "Rua Privada"},
            ],
        }),
    ])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.orders.billing_info", params={"order_id": "200000000000"},
    )
    assert output["result"]["success"] is True
    assert calls[0]["url"].endswith("/orders/200000000000")
    assert calls[1]["url"].endswith("/orders/billing-info/MLB/BILL-9")
    encoded = json.dumps(output, ensure_ascii=False)
    assert "Cliente X" not in encoded
    assert "Pessoa Exposta" not in encoded
    assert "12345678000199" not in encoded
    assert "Rua Privada" not in encoded
    assert "x@example.com" not in encoded
    assert "12345678901" not in encoded


def test_foreign_shipment_is_rejected_and_safe_header_is_fixed(monkeypatch):
    calls = _wire_store(monkeypatch, [_Response({"sender_id": 999})])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.shipments.items", params={"shipment_id": "123456"},
    )
    assert output["result"]["error_code"] == "ownership_denied"
    assert calls[0]["headers"] == {"x-format-new": "true"}


def test_http_206_is_explicitly_partial(monkeypatch):
    _wire_store(monkeypatch, [_Response([{"id": "MLB"}], status_code=206)])
    output = service.execute_mercado_livre_query("000002", loja="Loja A", resource_id="ml.sites.list")
    assert output["result"]["success"] is True
    assert output["result"]["partial_response"] is True
    assert output["result"]["coverage_complete"] is False


def test_received_questions_remove_buyer_identity_and_raw_text(monkeypatch):
    _wire_store(monkeypatch, [_Response({
        "questions": [{
            "id": 987, "status": "UNANSWERED", "text": "Meu telefone e 31999998888",
            "from": {"id": 456, "email": "buyer@example.com"},
        }],
        "total": 1,
    })])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.questions.received", params={"status": "UNANSWERED"},
    )
    assert output["result"]["success"] is True
    encoded = json.dumps(output, ensure_ascii=False)
    assert "buyer@example.com" not in encoded
    assert "Meu telefone" not in encoded
    assert '"from"' not in encoded


def test_dates_nested_params_and_large_payload_fail_or_shrink_safely(monkeypatch):
    _wire_store(monkeypatch, [])
    ancient = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.visits.items_period",
        params={"ids": "MLB123456789", "date_from": "1900-01-01", "date_to": "2100-01-01"},
    )
    assert ancient["result"]["error_code"] == "invalid_parameters"

    nested = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.categories.predict",
        params={"q": {"raw": "blocked"}},
    )
    assert nested["result"]["error_code"] == "invalid_parameters"

    huge = {f"field_{index}": ["x" * 1000] * 3 for index in range(200)}
    _wire_store(monkeypatch, [_Response(huge)])
    reduced = service.execute_mercado_livre_query("000002", loja="Loja A", resource_id="ml.sites.list")
    encoded = json.dumps(reduced, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= 240_000
    assert reduced["result"]["truncated"] is True
    assert reduced["result"]["coverage_complete"] is False


def test_direct_question_and_order_search_preserve_supported_filters(monkeypatch):
    calls = _wire_store(monkeypatch, [_Response({"questions": []}), _Response({"results": []})])
    question = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.questions.search",
        params={"item_id": "MLB123456789", "search_type": "scan", "limit": 20},
    )
    assert question["result"]["success"] is True
    assert calls[0]["params"]["seller_id"] == "123"
    assert calls[0]["params"]["item_id"] == "MLB123456789"
    assert calls[0]["params"]["search_type"] == "scan"

    date_today = (date.today() - timedelta(days=15)).isoformat()
    orders = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.orders.search",
        params={"order.date_created.from": date_today, "q": "MLB123456789", "sort": "date_desc"},
    )
    assert orders["result"]["success"] is True
    assert calls[1]["params"]["seller"] == "123"
    assert calls[1]["params"]["order.date_created.from"] == date_today
    assert calls[1]["params"]["q"] == "MLB123456789"
    assert calls[1]["params"]["sort"] == "date_desc"


def test_pack_requires_every_linked_order_to_belong_to_selected_store(monkeypatch):
    calls = _wire_store(monkeypatch, [
        _Response({"id": 900, "orders": [{"id": 1001}, {"id": 1002}]}),
        _Response({"seller": {"id": 123}, "id": 1001}),
        _Response({"seller": {"id": 999}, "id": 1002}),
    ])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.packs.get", params={"pack_id": "900"},
    )
    assert output["result"]["error_code"] == "ownership_denied"
    assert all("Loja B" not in call["url"] for call in calls)


def test_conversation_validates_pack_forces_no_read_side_effect_and_redacts_text(monkeypatch):
    calls = _wire_store(monkeypatch, [
        _Response({"id": 900, "orders": [{"id": 1001}]}),
        _Response({"seller": {"id": 123}, "id": 1001}),
        _Response({"messages": [{"id": "m1", "text": "conteudo privado", "from": {"id": 456}}]}),
    ])
    output = service.execute_mercado_livre_query(
        "000002", loja="Loja A", resource_id="ml.messages.conversation",
        params={"pack_id": "900", "offset": 10, "limit": 20},
    )
    assert output["result"]["success"] is True
    assert calls[-1]["params"]["mark_as_read"] == "false"
    assert calls[-1]["params"]["tag"] == "post_sale"
    assert calls[-1]["params"]["offset"] == 10
    encoded = json.dumps(output, ensure_ascii=False)
    assert "conteudo privado" not in encoded
    assert '"from"' not in encoded


def test_rate_limit_and_reconnect_have_stable_public_errors(monkeypatch):
    _wire_store(monkeypatch, [_Response({}, status_code=429)])
    limited = service.execute_mercado_livre_query("000002", loja="Loja A", resource_id="ml.sites.list")
    assert limited["result"]["error_code"] == "rate_limited"

    _wire_store(monkeypatch, [_Response({}, status_code=401)])
    reconnect = service.execute_mercado_livre_query("000002", loja="Loja A", resource_id="ml.sites.list")
    assert reconnect["result"]["error_code"] == "reconnect_required"
    assert reconnect["result"]["reconnect_required"] is True


def test_assistant_contract_is_full_only_and_can_search_catalog(monkeypatch):
    from backend.services import codex_assistant

    contract = next(item for item in codex_assistant.CODEX_DATA_TOOLS if item["id"] == "mercado_livre_resource_query")
    assert contract["read_only"] is True
    assert contract["executor"] == "mercado_livre_query_service.execute_mercado_livre_query"
    assert "mercado_livre_resource_query" in codex_assistant.ASSISTANT_FULL_ONLY_TOOLS

    denied = codex_assistant.codex_assistant_execute_tool_call(
        "000002", "mercado_livre_resource_query", {"mensagem": "estoque"}, permissions={"anuncios_ml": True},
    )
    assert denied["error_code"] == "tool_permission_denied"

    allowed = codex_assistant.codex_assistant_execute_tool_call(
        "000002", "mercado_livre_resource_query", {"mensagem": "estoque", "limite": 3}, permissions={"full": True},
    )
    assert allowed["success"] is True
    assert allowed["read_only"] is True
    assert allowed["args"] == {}


def test_rejected_params_never_echo_secrets_or_personal_data():
    output = service.execute_mercado_livre_query(
        "000002",
        resource_id="https://api.mercadolibre.com/users/me",
        params={
            "access_token": "TOPSECRET",
            "email": "buyer@example.com",
            "question": "telefone 31999998888",
        },
    )
    encoded = json.dumps(output, ensure_ascii=False)
    assert "TOPSECRET" not in encoded
    assert "buyer@example.com" not in encoded
    assert "31999998888" not in encoded
