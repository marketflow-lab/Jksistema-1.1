from __future__ import annotations

from typing import Any

from backend.services import codex_readonly_sources
from backend.services.codex.assistant import catalog as assistant_catalog
from backend.services.codex.assistant import execution as assistant_execution
from backend.services.codex.assistant import registry as assistant_registry
from backend.services.codex.assistant import routing as assistant_routing
from backend.services.marketplace_tools import client as marketplace_client
from backend.services.marketplace_tools import listings as marketplace_listings
from backend.services.marketplace_tools import promotions as marketplace_promotions
from backend.services.marketplace_tools import runtime as marketplace_runtime
from backend.services.marketplace_tools import traffic as marketplace_traffic
from backend.modules.perguntas_pos_venda.endpoints import api as perguntas_pos_venda_endpoints


class FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.headers = {}

    def json(self):
        return self._payload


def _configure_store(monkeypatch, seller_id: str = "12345") -> None:
    monkeypatch.setattr(marketplace_runtime, "ml_connected_stores", lambda _client_id: ["JK Pecas"], raising=False)
    monkeypatch.setattr(
        marketplace_runtime,
        "ml_config",
        lambda _client_id, _store: {"access_token": "secret", "user_id": seller_id, "site_id": "MLB"},
        raising=False,
    )


def test_visits_is_get_only_store_scoped_and_authoritative_zero(monkeypatch) -> None:
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, store, cfg, method, url, **kwargs):
        calls.append((store, method, url, dict(kwargs.get("params") or {})))
        if url.endswith("/items"):
            return FakeResponse(200, [{"code": 200, "body": {"id": "MLB123456789", "seller_id": 12345}}]), cfg
        if url.endswith("/items/MLB123456789/visits/time_window"):
            return FakeResponse(200, {
                "date_from": "2026-07-01",
                "date_to": "2026-07-03",
                "unit": "day",
                "total_visits": 0,
                "results": [{"date": "2026-07-01", "total": 0}],
            }), cfg
        raise AssertionError(url)

    monkeypatch.setattr(marketplace_runtime, "ml_api_request", fake_api, raising=False)
    result = marketplace_traffic.query_visits(
        "tenant-a", "visitas do MLB123456789", loja="jk pecas", item_id="MLB123456789", dias=30,
    )["result"]

    assert all(store == "JK Pecas" and method == "GET" for store, method, _url, _params in calls)
    assert calls[-1][3]["last"] == 30
    assert result["total_visits"] == 0
    assert result["coverage_complete"] is True
    assert result["zero_is_authoritative"] is True


def test_visits_rejects_foreign_mlb_before_visits_endpoint(monkeypatch) -> None:
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, _store, cfg, method, url, **_kwargs):
        calls.append((method, url))
        assert url.endswith("/items")
        return FakeResponse(200, [{"code": 200, "body": {"id": "MLB987654321", "seller_id": 99999}}]), cfg

    monkeypatch.setattr(marketplace_runtime, "ml_api_request", fake_api, raising=False)
    result = marketplace_traffic.query_visits(
        "tenant-a", "visitas", loja="JK Pecas", item_id="MLB987654321",
    )["result"]

    assert result["error"] == "listing_not_in_store"
    assert result["coverage_complete"] is False
    assert len(calls) == 1


def test_promotions_is_get_only_and_complete_zero_is_authoritative(monkeypatch) -> None:
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, store, cfg, method, url, **kwargs):
        calls.append((store, method, url, dict(kwargs.get("params") or {})))
        assert url.endswith("/seller-promotions/users/12345")
        return FakeResponse(200, []), cfg

    monkeypatch.setattr(marketplace_runtime, "ml_api_request", fake_api, raising=False)
    result = marketplace_promotions.query(
        "tenant-a", "promocoes", loja="JK Pecas",
    )["result"]

    assert calls == [("JK Pecas", "GET", calls[0][2], {"app_version": "v2"})]
    assert result["campaigns"] == []
    assert result["coverage_complete"] is True
    assert result["zero_is_authoritative"] is True


def test_listing_commercial_detail_uses_only_official_get_values(monkeypatch) -> None:
    _configure_store(monkeypatch)
    calls = []

    def fake_api(_client_id, _store, cfg, method, url, **kwargs):
        calls.append((method, url, dict(kwargs.get("params") or {})))
        if url.endswith("/items"):
            return FakeResponse(200, [{"code": 200, "body": {
                "id": "MLB123456789",
                "seller_id": 12345,
                "title": "Produto",
                "status": "active",
                "price": 100,
                "currency_id": "BRL",
                "category_id": "MLB1",
                "listing_type_id": "gold_special",
                "shipping": {"mode": "me2", "logistic_type": "drop_off", "free_shipping": True},
            }}]), cfg
        if url.endswith("/items/MLB123456789/sale_price"):
            return FakeResponse(200, {
                "amount": 90,
                "regular_amount": 100,
                "currency_id": "BRL",
                "metadata": {"promotion_id": "PROMO-1", "promotion_type": "DEAL"},
            }), cfg
        if url.endswith("/sites/MLB/listing_prices"):
            return FakeResponse(200, {
                "sale_fee_amount": 14,
                "listing_fee_amount": 0,
                "sale_fee_details": {"fixed_fee": 6, "percentage_fee": 8},
            }), cfg
        raise AssertionError(url)

    monkeypatch.setattr(marketplace_runtime, "ml_api_request", fake_api, raising=False)
    result = marketplace_listings.query(
        "tenant-a",
        "taxas do MLB123456789",
        loja="JK Pecas",
        item_id="MLB123456789",
        incluir_comercial=True,
    )["result"]
    commercial = result["matches"][0]["details"]["commercial"]

    assert all(method == "GET" for method, _url, _params in calls)
    assert commercial["price"]["amount"] == 90
    assert commercial["fees"]["sale_fee_amount"] == 14
    assert commercial["fees"]["estimated"] is False
    assert commercial["shipping"]["seller_cost"] is None


def test_post_sale_detail_minimizes_pii_and_private_attachment_data(monkeypatch) -> None:
    monkeypatch.setattr(
        marketplace_client,
        "resolve_store",
        lambda client_id, loja: ("JK Pecas", {}) if client_id == "tenant-a" and loja == "JK Pecas" else ("", {"code": "store_not_found"}),
    )

    def fake_detail(*, loja, pack_id, order_id, client_id):
        assert (loja, pack_id, order_id, client_id) == ("JK Pecas", "1234567890", "9988776655", "tenant-a")
        return {
            "success": True,
            "conversa": {
                "pack_id": pack_id,
                "order_id": order_id,
                "seller_id": "12345",
                "buyer_id": "secret-buyer",
                "buyer_name": "Pessoa Privada",
                "items": [{"id": "MLB1", "sku": "SKU-1", "title": "Produto", "quantity": 1}],
                "messages": [{
                    "id": "MSG-1",
                    "from_id": "secret-buyer",
                    "from_role": "buyer",
                    "date": "2026-07-18T10:00:00Z",
                    "text": "Meu email cliente@example.com, telefone (31) 99999-9999, CPF 123.456.789-00, CEP 30110-010",
                    "attachments": [{"id": "ATT-SECRET", "name": "foto.jpg", "mime_type": "image/jpeg", "url": "https://private/token"}],
                }],
            },
        }

    monkeypatch.setattr(perguntas_pos_venda_endpoints, "ml_pos_venda_detalhe_conversa", fake_detail)
    result = codex_readonly_sources.mercado_livre_post_sale_detail(
        client_id="tenant-a",
        message="conversa do pack 1234567890",
        loja="JK Pecas",
        pack_id="1234567890",
        order_id="9988776655",
    )
    serialized = str(result)
    message = result["records"][0]["messages"][0]

    assert result["coverage_complete"] is True
    assert "Pessoa Privada" not in serialized
    assert "secret-buyer" not in serialized
    assert "cliente@example.com" not in serialized
    assert "99999-9999" not in serialized
    assert "123.456.789-00" not in serialized
    assert "30110-010" not in serialized
    assert "ATT-SECRET" not in serialized
    assert "https://private/token" not in serialized
    assert message["attachments"] == [{
        "name": "foto.jpg", "mime_type": "image/jpeg", "is_image": True, "available_in_app": True,
    }]


def test_registry_permissions_routing_and_sensitive_cache_contract(monkeypatch) -> None:
    for tool_id in ("mercado_livre_visits", "mercado_livre_promotions", "mercado_livre_post_sale_detail"):
        meta = assistant_catalog._assistant_tool_meta(tool_id)
        assert meta["external"] is True
        assert meta["read_only"] is True
        assert meta["fallbacks"] == []
        assert meta["zero_is_authoritative"] is True
    assert assistant_catalog._assistant_tool_meta("mercado_livre_post_sale_detail")["sensitive"] is True
    assert assistant_catalog.ASSISTANT_TOOL_PERMISSION_REQUIREMENTS["mercado_livre_visits"] == ("anuncios_ml",)
    assert assistant_catalog.ASSISTANT_TOOL_PERMISSION_REQUIREMENTS["mercado_livre_post_sale_detail"] == ("perguntas_pos_venda",)

    visits = assistant_routing._assistant_source_routing_policy("Visitas do anuncio MLB123456789 no Mercado Livre")
    promotions = assistant_routing._assistant_source_routing_policy("Campanhas ativas da loja no Mercado Livre")
    post_sale = assistant_routing._assistant_source_routing_policy("Mensagens da conversa do pack 1234567890 no pos-venda")
    commercial = assistant_routing._assistant_source_routing_policy("Taxas e frete do anuncio MLB123456789")
    assert "mercado_livre_visits" in visits["required_tools"]
    assert "mercado_livre_promotions" in promotions["required_tools"]
    assert "mercado_livre_post_sale_detail" in post_sale["required_tools"]
    assert commercial["include_commercial_detail"] is True

    cache_writes = []
    monkeypatch.setattr(assistant_execution, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(assistant_execution, "_assistant_cache_set", lambda *args, **_kwargs: cache_writes.append(args))
    monkeypatch.setattr(assistant_execution, "_assistant_api_query_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(marketplace_client, "resolve_store", lambda *_args: ("JK Pecas", {}))
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_pos_venda_detalhe_conversa",
        lambda **kwargs: {"success": True, "conversa": {"pack_id": kwargs["pack_id"], "messages": []}},
    )

    result = assistant_execution.execute_tool_call(
        "tenant-a",
        "mercado_livre_post_sale_detail",
        {"mensagem": "conversa do pack 1234567890", "loja": "JK Pecas", "pack_id": "1234567890"},
        permissions={"perguntas_pos_venda": True},
    )

    assert result["success"] is True
    assert cache_writes == []


def test_codex_executor_forwards_new_readonly_filters(monkeypatch) -> None:
    monkeypatch.setattr(assistant_execution, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(assistant_execution, "_assistant_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(assistant_execution, "_assistant_api_query_audit", lambda *_args, **_kwargs: None)
    calls = []

    def fake_call(name, client_id, message, *args, **kwargs):
        calls.append((name, client_id, message, kwargs))
        if name.endswith("visits"):
            return {"function": "get_mercado_livre_visits", "result": {
                "results": [{"date": "2026-07-18", "total": 3}],
                "total_visits": 3,
                "coverage_complete": True,
            }}
        return {"function": "get_mercado_livre_promotions", "result": {
            "campaigns": [{"id": "P-1", "status": "started"}],
            "coverage_complete": True,
        }}

    monkeypatch.setattr(assistant_registry, "_assistant_call_ia_tool", fake_call)
    visits = assistant_execution.execute_tool_call(
        "tenant-a",
        "mercado_livre_visits",
        {"mensagem": "visitas", "loja": "JK Pecas", "item_id": "MLB123456789", "dias": 14},
        permissions={"anuncios_ml": True},
    )
    promotions = assistant_execution.execute_tool_call(
        "tenant-a",
        "mercado_livre_promotions",
        {"mensagem": "campanhas", "loja": "JK Pecas", "status": "started", "incluir_contagens": True},
        permissions={"anuncios_ml": True},
    )

    assert visits["records"] == 1
    assert promotions["records"] == 1
    assert calls[0][3]["item_id"] == "MLB123456789"
    assert calls[0][3]["dias"] == 14
    assert calls[1][3]["status"] == "started"
    assert calls[1][3]["incluir_contagens"] is True
