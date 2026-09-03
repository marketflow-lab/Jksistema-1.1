from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers import financial_comparison as router_module
from backend.services import mercadolivre_cotacao_comparacao as comparison


def _paths(router):
    return {route.path: set(route.methods or ()) for route in router.routes}


def _store(name="Store", seller="seller-1"):
    return {
        "nome": name,
        "integracoes": {
            "mercadolivre": {
                "access_token": "test-token",
                "user_id": seller,
                "app_id": "test-app",
                "client_secret": "test-secret",
            }
        },
    }


def _shipping_context(item, price):
    shipping = item["shipping"]
    return {
        "item_price": price,
        "listing_type_id": item["listing_type_id"],
        "condition": item["condition"],
        "category_id": item["category_id"],
        "mode": shipping["mode"],
        "logistic_type": shipping["logistic_type"],
        "dimensions": shipping["dimensions"],
        "free_shipping": shipping["free_shipping"],
    }


def _config(api_request=lambda *_args, **_kwargs: None, stores=None):
    configured_stores = list(stores or [_store()])
    return router_module.FinancialComparisonRouterConfig(
        load_ml_stores=lambda _client_id: copy.deepcopy(configured_stores),
        ml_api_request=api_request,
        build_shipping_context=_shipping_context,
    )


def test_router_is_empty_for_absent_false_and_unknown_flag(monkeypatch):
    for value in (None, "", "0", "false", "unknown"):
        if value is None:
            monkeypatch.delenv(comparison.FINANCIAL_COMPARISON_UI_ENV, raising=False)
        else:
            monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, value)
        assert router_module.create_financial_comparison_router().routes == []


def test_enabled_router_registers_get_assets_and_post_data_apis(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    paths = _paths(router_module.create_financial_comparison_router())
    assert paths == {
        "/internal/financial-comparison": {"GET"},
        "/internal/financial-comparison/app.js": {"GET"},
        "/api/internal/financial-comparison/summary": {"POST"},
        "/api/internal/financial-comparison/candidate-quote": {"POST"},
    }


def test_summary_uses_authenticated_tenant_and_exact_canonical_store(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "true")
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "true")
    comparison.reset_observacoes_comparacao()
    observed = {}

    def summary(**kwargs):
        observed.update(kwargs)
        return {
            "contract_version": "test",
            "generated_at": "2026-08-28T00:00:00Z",
            "window_seconds": 1,
            "total": 0,
            "classifications": {},
            "origins": {},
            "samples": [],
        }

    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant-jwt"},
    )
    monkeypatch.setattr(router_module, "resumo_comparacao", summary)
    app = FastAPI()
    app.include_router(router_module.create_financial_comparison_router(_config()))
    response = TestClient(app).post(
        "/api/internal/financial-comparison/summary",
        json={"loja": "Store", "limit": 50},
    )

    assert response.status_code == 200
    assert observed == {"client_id": "tenant-jwt", "loja": "Store", "limit": 50}
    assert response.headers["cache-control"].startswith("no-store")


def test_summary_rejects_store_alias_instead_of_merging_scope(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, "1")
    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant"},
    )
    app = FastAPI()
    app.include_router(router_module.create_financial_comparison_router(_config()))
    response = TestClient(app).post(
        "/api/internal/financial-comparison/summary",
        json={"loja": "store"},
    )
    assert response.status_code == 404


def test_summary_returns_503_when_capture_is_disabled(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    monkeypatch.delenv(comparison.FINANCIAL_COMPARISON_CAPTURE_ENV, raising=False)
    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant"},
    )
    app = FastAPI()
    app.include_router(router_module.create_financial_comparison_router(_config()))
    response = TestClient(app).post(
        "/api/internal/financial-comparison/summary",
        json={"loja": "Store"},
    )
    assert response.status_code == 503
    assert "desabilitada" in response.json()["detail"]


def test_data_api_propagates_fail_closed_authentication(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")

    def denied(*_args):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(router_module, "require_full_admin", denied)
    app = FastAPI()
    app.include_router(router_module.create_financial_comparison_router(_config()))
    response = TestClient(app).post(
        "/api/internal/financial-comparison/candidate-quote",
        content=b"{invalid-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 403


def test_authenticated_data_api_rejects_oversized_body_before_json_parse(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant"},
    )
    app = FastAPI()
    app.include_router(router_module.create_financial_comparison_router(_config()))
    response = TestClient(app).post(
        "/api/internal/financial-comparison/candidate-quote",
        content=b'{' + (b'"padding":"' + b'x' * 5000 + b'"}'),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_candidate_quote_is_read_only_tenant_bound_and_identifier_free(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    comparison.reset_observacoes_comparacao()
    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant-jwt"},
    )
    calls = []

    class Response:
        def __init__(self, payload, status_code=200):
            self.payload = payload
            self.status_code = status_code

        def json(self):
            return copy.deepcopy(self.payload)

    item = {
        "id": "MLB123456",
        "seller_id": "seller-1",
        "price": 120,
        "currency_id": "BRL",
        "category_id": "MLB-CAT",
        "listing_type_id": "gold_special",
        "condition": "new",
        "shipping": {
            "mode": "me2",
            "logistic_type": "cross_docking",
            "free_shipping": True,
            "dimensions": "10x10x10,500",
        },
    }

    def api_request(client_id, store, cfg, method, url, **kwargs):
        calls.append((client_id, store, method, url, copy.deepcopy(kwargs)))
        if "/items/MLB123456" in url:
            return Response(item), cfg
        if url.endswith("/listing_prices"):
            return Response({"sale_fee_amount": 15}), cfg
        if url.endswith("/users/seller-1/shipping_options/free"):
            return Response(
                {"coverage": {"all_country": {"seller_cost": 8}}}
            ), cfg
        raise AssertionError(f"unexpected URL: {url}")

    app = FastAPI()
    app.include_router(
        router_module.create_financial_comparison_router(_config(api_request))
    )
    response = TestClient(app).post(
        "/api/internal/financial-comparison/candidate-quote",
        json={
            "loja": "Store",
            "item_id": "MLB123456",
            "preco": "100",
            "custo": "30",
            "imposto_percentual": "10",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "exact"
    assert body["net_amount"] == "37"
    assert len(calls) == 3
    assert all(call[0] == "tenant-jwt" for call in calls)
    assert all(call[1] == "Store" for call in calls)
    assert all(call[2] == "GET" for call in calls)
    assert "MLB123456" not in response.text
    assert "tenant-jwt" not in response.text
    assert response.headers["cache-control"].startswith("no-store")


def test_seller_mismatch_stops_before_financial_queries(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    comparison.reset_observacoes_comparacao()
    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant"},
    )
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "MLB123456", "seller_id": "another-seller"}

    def api_request(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(), args[2]

    app = FastAPI()
    app.include_router(
        router_module.create_financial_comparison_router(_config(api_request))
    )
    response = TestClient(app).post(
        "/api/internal/financial-comparison/candidate-quote",
        json={
            "loja": "Store",
            "item_id": "MLB123456",
            "preco": "100",
            "custo": "30",
            "imposto_percentual": "10",
        },
    )
    assert response.status_code == 404
    assert len(calls) == 1


def test_candidate_body_rejects_extra_fields_controls_and_huge_decimals(monkeypatch):
    monkeypatch.setenv(comparison.FINANCIAL_COMPARISON_UI_ENV, "1")
    monkeypatch.setattr(
        router_module,
        "require_full_admin",
        lambda *_args: {"client_id": "tenant"},
    )
    app = FastAPI()
    app.include_router(router_module.create_financial_comparison_router(_config()))
    client = TestClient(app)
    base = {
        "loja": "Store",
        "item_id": "MLB123456",
        "preco": "100",
        "custo": "30",
        "imposto_percentual": "10",
    }
    assert client.post(
        "/api/internal/financial-comparison/candidate-quote",
        json={**base, "client_id": "forged"},
    ).status_code == 422
    assert client.post(
        "/api/internal/financial-comparison/candidate-quote",
        json={**base, "loja": "Store\nInjected"},
    ).status_code == 422
    assert client.post(
        "/api/internal/financial-comparison/candidate-quote",
        json={**base, "preco": "12345678901234567890.00"},
    ).status_code == 422


def test_page_assets_use_post_bodies_without_mutation_or_external_channel():
    base = Path(router_module.__file__).resolve().parents[1] / "ui" / "financial_comparison"
    html = (base / "index.html").read_text(encoding="utf-8")
    script = (base / "app.js").read_text(encoding="utf-8")
    combined = f"{html}\n{script}".lower()

    assert script.lower().count("method: 'post'") == 2
    assert "urlsearchparams" not in combined
    assert "localstorage.setitem" not in combined
    assert "sessionstorage" not in combined
    assert "method: 'put'" not in combined
    assert "method: 'delete'" not in combined
    assert "http://" not in combined
    assert "https://" not in combined
    assert "/api/internal/financial-comparison/summary" in script
    assert "/api/internal/financial-comparison/candidate-quote" in script


def test_runtime_store_loader_reads_snapshot_without_legacy_loader_or_write(
    monkeypatch,
    tmp_path,
):
    import backend_api

    info_root = tmp_path / "info"
    tenant_dir = info_root / "tenant-a"
    tenant_dir.mkdir(parents=True)
    snapshot_path = tenant_dir / "lojas_config.json"
    snapshot = [_store(name="Canonical Store")]
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )
    before = snapshot_path.read_bytes()
    monkeypatch.setattr(backend_api, "PASTA_INFO", str(info_root))
    monkeypatch.setattr(
        backend_api,
        "carregar_lojas",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("legacy store loader must not run")
        ),
    )

    stores = backend_api._financial_comparison_load_ml_stores("tenant-a")

    assert [store["nome"] for store in stores] == ["Canonical Store"]
    assert snapshot_path.read_bytes() == before


def test_runtime_ml_reader_allows_only_get_and_never_refreshes_configuration(
    monkeypatch,
):
    import backend_api

    calls = []
    response = object()

    def http_request(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(backend_api, "_ml_http_request", http_request)
    cfg = {"access_token": "test-token", "user_id": "seller-1"}
    result, returned_cfg = backend_api._financial_comparison_ml_readonly_request(
        "tenant-a",
        "Canonical Store",
        cfg,
        "GET",
        "https://api.mercadolibre.com/sites/MLB/listing_prices",
        params={"price": "100"},
    )

    assert result is response
    assert returned_cfg == cfg
    assert returned_cfg is not cfg
    assert len(calls) == 1
    assert calls[0][0][3] == "GET"
    assert calls[0][1]["verify_ssl"] is True
    with pytest.raises(ValueError):
        backend_api._financial_comparison_ml_readonly_request(
            "tenant-a",
            "Canonical Store",
            cfg,
            "POST",
            "https://api.mercadolibre.com/items/MLB123",
        )
