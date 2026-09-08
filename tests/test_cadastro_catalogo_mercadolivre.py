from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

import pytest
from fastapi import HTTPException
from requests.structures import CaseInsensitiveDict

from backend.services import cadastro_catalogo_mercadolivre as service


class FakeResponse:
    def __init__(self, status_code: int, payload: Any, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers if headers is not None else {}

    def json(self) -> Any:
        return self._payload


def _store(*, seller_id: str = "111") -> dict[str, Any]:
    return {
        "store_id": "store-a",
        "nome": "Loja A",
        "integracoes": {
            "mercadolivre": {
                "access_token": "token-a",
                "refresh_token": "refresh-a",
                "app_id": "app-a",
                "client_secret": "secret-a",
                "user_id": seller_id,
            }
        },
    }


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[..., tuple[FakeResponse, dict[str, Any]]],
    *,
    seller_id: str = "111",
    user_product_details: dict[str, Any] | None = None,
    user_product_stocks: dict[str, Any] | None = None,
    user_product_calls: list[str] | None = None,
) -> None:
    monkeypatch.setattr(service, "_wait_catalog_rate_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service.integracoes, "carregar_lojas", lambda client_id: [_store(seller_id=seller_id)])
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_cfg_com_store_id_context",
        lambda cfg, store_id: {**cfg, "_store_id_context": store_id},
    )
    def wrapped(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if f"{service.ML_API_BASE}/user-products/" in url:
            if user_product_calls is not None:
                user_product_calls.append(url)
            user_product_id = url.split("/user-products/", 1)[1].split("/", 1)[0]
            is_stock = url.endswith("/stock")
            configured = user_product_stocks if is_stock else user_product_details
            default = (
                {
                    "id": user_product_id,
                    "user_id": int(seller_id),
                    "locations": [{"type": "selling_address", "quantity": 7}],
                }
                if is_stock
                else {
                    "id": user_product_id,
                    "user_id": int(seller_id),
                    "name": f"User Product {user_product_id}",
                    "family_id": "FAMILY-DEFAULT",
                    "domain_id": "MLB-AUTO_PARTS",
                    "attributes": [],
                    "tags": [],
                }
            )
            value = (configured or {}).get(user_product_id, default)
            if isinstance(value, tuple):
                payload, status = value
                return _response(payload, cfg, status=status)
            return _response(value, cfg)
        if f"{service.ML_API_BASE}/categories/" in url:
            category_id = url.rsplit("/", 1)[-1]
            return _response({"id": category_id, "name": f"Categoria {category_id}"}, cfg)
        return handler(
            client_id,
            store_name,
            cfg,
            method,
            url,
            params=params,
            timeout=timeout,
        )

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", wrapped)


def _response(payload: Any, cfg: dict[str, Any], status: int = 200) -> tuple[FakeResponse, dict[str, Any]]:
    return FakeResponse(status, payload), cfg


def _base_item(
    item_id: str,
    *,
    seller_id: str = "111",
    sku: str | None = "SKU-1",
    status: str = "active",
    variations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    attributes: list[dict[str, Any]] = [
        {"id": "BRAND", "name": "Marca", "value_name": "Marca X"},
        {"id": "MODEL", "name": "Modelo", "value_name": "Modelo Y"},
        {"id": "GTIN", "name": "GTIN", "value_name": "7891234567890"},
        {"id": "ITEM_CONDITION", "name": "Condição do item", "value_name": "Novo"},
    ]
    if sku is not None:
        attributes.insert(0, {"id": "SELLER_SKU", "name": "SKU", "value_name": sku})
    return {
        "id": item_id,
        "seller_id": seller_id,
        "title": f"Titulo {item_id}",
        "status": status,
        "category_id": "MLB1234",
        "price": 125.5,
        "currency_id": "BRL",
        "base_price": 140.0,
        "original_price": 150.0,
        "available_quantity": 7,
        "sold_quantity": 3,
        "sale_terms": [{"id": "WARRANTY_TYPE", "value_name": "Garantia do vendedor"}],
        "shipping": {"free_shipping": True, "logistic_type": "fulfillment"},
        "condition": "new",
        "warranty": "Garantia de 90 dias",
        "date_created": "2026-01-01T10:00:00.000Z",
        "last_updated": "2026-08-31T18:00:00.000Z",
        "channels": ["marketplace"],
        "tags": ["good_quality_picture"],
        "family_name": "Familia X",
        "family_id": "MLB-FAMILY-1",
        "domain_id": "MLB-AUTO_PARTS",
        "catalog_listing": True,
        "buying_mode": "buy_it_now",
        "listing_type_id": "gold_special",
        "catalog_product_id": "MLB-CAT-1",
        "user_product_id": "MLBU-1",
        "inventory_id": "INV-1",
        "permalink": f"https://produto.mercadolivre.com.br/{item_id}",
        "pictures": [
            {
                "id": "PIC-1",
                "secure_url": f"https://http2.mlstatic.com/D_NQ_NP_{item_id}.jpg",
            }
        ],
        "attributes": attributes,
        "variations": variations or [],
    }


def test_collects_every_scroll_page_and_groups_duplicate_sku(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    progress: list[dict[str, Any]] = []
    details = {
        "MLB100": _base_item("MLB100", status="active"),
        "MLB200": _base_item("MLB200", status="paused"),
    }
    details["MLB200"]["price"] = 130

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            assert "status" not in (params or {})
            if (params or {}).get("scroll_id") == "scroll-1":
                return _response(
                    {"results": ["MLB200"], "scroll_id": "scroll-1", "paging": {"total": 2}},
                    cfg,
                )
            assert params == {"search_type": "scan", "limit": service.ML_PAGE_SIZE}
            return _response(
                {"results": ["MLB100"], "scroll_id": "scroll-1", "paging": {"total": 2}},
                cfg,
            )
        if url.endswith("/items/bulk"):
            ids = str(params["ids"]).split(",")
            return _response(
                [{**details[item_id], "id": item_id, "status_code": 200} for item_id in ids],
                cfg,
            )
        if url.endswith("/description"):
            item_id = url.split("/")[-2]
            return _response({"plain_text": f"Descricao {item_id}"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre(
        "tenant-a", "store-a", progress_callback=progress.append
    )

    assert result["source"] == "mercadolivre"
    assert result["store_id"] == "store-a"
    assert result["store_name"] == "Loja A"
    assert result["seller_id"] == "111"
    assert result["config_fingerprint"] == service.configuracao_catalogo_fingerprint(
        "mercadolivre",
        "store-a",
        "Loja A",
        _store()["integracoes"]["mercadolivre"],
    )
    assert result["coverage_complete"] is True
    assert result["stats"]["pages_fetched"] == 2
    assert result["stats"]["listed_ids"] == 2
    assert result["stats"]["detailed_listings"] == 2
    assert result["stats"]["duplicate_sku"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["sku"] == "SKU-1"
    assert item["fields"]["titulo_ml"] == "Titulo MLB100"
    assert item["fields"]["mlb_principal"] == "MLB100"
    assert item["fields"]["mlb_ids"] == "MLB100|MLB200"
    assert item["fields"]["titulos_anuncios_mlb"] == "Titulo MLB100 || Titulo MLB200"
    assert item["fields"]["qtd_anuncios_mlb"] == 2
    assert item["fields"]["categoria_id_mlb"] == "MLB1234"
    assert item["fields"]["categoria"] == "Categoria MLB1234"
    assert result["stats"]["categories_requested"] == 1
    assert result["stats"]["categories_complete"] == 1
    assert result["stats"]["user_products_requested"] == 1
    assert result["stats"]["user_products_complete"] == 1
    assert result["stats"]["user_product_stocks_complete"] == 1
    assert item["fields"]["marca"] == "Marca X"
    assert item["fields"]["modelo"] == "Modelo Y"
    assert item["fields"]["gtins_mlb"] == "7891234567890"
    assert item["fields"]["descricao"] == "Descricao MLB100"
    assert item["fields"]["preco_ml"] == 125.5
    assert item["fields"]["moeda_ml"] == "BRL"
    assert item["fields"]["preco_base_ml"] == 140.0
    assert item["fields"]["preco_original_ml"] == 150.0
    assert item["fields"]["estoque_disponivel_ml"] == 7
    assert item["fields"]["vendidos_acumulados_ml"] == 6
    assert item["fields"]["condicao_ml"] == "new"
    assert item["fields"]["condicao_nome_ml"] == "Novo"
    assert item["fields"]["garantia_ml"] == "Garantia de 90 dias"
    assert item["fields"]["criado_em_ml"] == "2026-01-01T10:00:00.000Z"
    assert item["fields"]["atualizado_em_ml"] == "2026-08-31T18:00:00.000Z"
    assert item["fields"]["familia_nome_ml"] == "Familia X"
    assert item["fields"]["familia_id_ml"] == "FAMILY-DEFAULT"
    assert item["fields"]["user_product_nome_ml"] == "User Product MLBU-1"
    assert item["fields"]["estoque_user_product_total_ml"] == 7
    assert item["fields"]["estoque_multiorigem_ml"] is False
    assert '"type":"selling_address"' in item["fields"]["estoque_localizacoes_ml_json"]
    assert item["fields"]["dominio_id_ml"] == "MLB-AUTO_PARTS"
    assert item["fields"]["anuncio_catalogo_ml"] is True
    assert item["fields"]["modo_compra_ml"] == "buy_it_now"
    assert '"WARRANTY_TYPE"' in item["fields"]["termos_venda_ml_json"]
    assert '"fulfillment"' in item["fields"]["envio_ml_json"]
    assert item["fields"]["canais_ml_json"] == '["marketplace"]'
    assert item["fields"]["tags_ml_json"] == '["good_quality_picture"]'
    assert item["fields"]["status_ml"] == "active"
    assert item["fields"]["listing_type_ml"] == "gold_special"
    assert item["fields"]["catalog_product_id_ml"] == "MLB-CAT-1"
    assert item["fields"]["user_product_id_ml"] == "MLBU-1"
    assert item["fields"]["inventory_id_ml"] == "INV-1"
    assert item["fields"]["inventory_ids_ml"] == "INV-1"
    assert '"inventory_id":"INV-1"' in item["fields"]["anuncios_ml_json"]
    assert item["fields"]["link_ml"].endswith("/MLB100")
    assert item["fields"]["foto_url_ml"].endswith("MLB100.jpg")
    assert "atributos_ml_json" in item["fields"]
    assert item["conflicts"]["duplicate_listing_sku"] == ["MLB100", "MLB200"]
    assert item["conflicts"]["preco_ml"] == [125.5, 130]
    assert not {"preco", "estoque", "foto"}.intersection(item["fields"])
    assert calls[0][0].endswith("/users/me")
    assert sum(url.endswith("/items/bulk") for url, _params in calls) == 1
    assert not any(url.endswith("/items") for url, _params in calls)
    assert progress[-1]["stage"] == "complete"


def test_categoria_divergente_do_mesmo_sku_marca_id_e_nome_como_conflito(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details = {
        "MLB100": _base_item("MLB100", status="active"),
        "MLB200": _base_item("MLB200", status="paused"),
    }
    details["MLB100"]["category_id"] = "MLB-CAT-A"
    details["MLB200"]["category_id"] = "MLB-CAT-B"

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB100", "MLB200"], "paging": {"total": 2}}, cfg)
        if url.endswith("/items/bulk"):
            return _response(
                [{"id": item_id, "status_code": 200, "body": details[item_id]} for item_id in details],
                cfg,
            )
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(monkeypatch, handler)

    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    item = result["items"][0]
    assert item["conflicts"]["categoria_id_mlb"] == ["MLB-CAT-A", "MLB-CAT-B"]
    assert item["conflicts"]["categoria"] == ["MLB-CAT-A", "MLB-CAT-B"]


def test_categoria_sem_nome_deixa_coleta_incompleta(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_request(_client_id, _store_name, cfg, url, *, params=None, timeout=None, **_kwargs):
        calls.append(url)
        return {"id": "MLB1234"}, cfg, 200, ""

    monkeypatch.setattr(service, "_request_json", fake_request)

    names, cfg, complete, warnings = service._fetch_category_names(
        "tenant-a",
        {"store_id": "store-a", "store_name": "Loja A"},
        {"access_token": "token"},
        ["MLB1234"],
        None,
        None,
    )

    assert names == {}
    assert cfg["access_token"] == "token"
    assert complete is False
    assert warnings == ["A categoria MLB1234 nao foi detalhada com seguranca."]
    assert calls == [f"{service.ML_API_BASE}/categories/MLB1234"]


def test_scroll_reutiliza_o_token_inicial_ate_completar_o_total(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_scrolls: list[str] = []
    pages = iter(
        [
            {"results": ["MLB1"], "scroll_id": "scroll-estavel", "paging": {"total": 3}},
            {"results": ["MLB2"], "scroll_id": "scroll-estavel", "paging": {"total": 3}},
            {"results": ["MLB3"], "scroll_id": "scroll-estavel", "paging": {"total": 3}},
        ]
    )

    def fake_request(_client_id, _store_name, cfg, _url, *, params=None, timeout=None, **_kwargs):
        requested_scrolls.append(str((params or {}).get("scroll_id") or ""))
        return next(pages), cfg, 200, ""

    monkeypatch.setattr(service, "_request_json", fake_request)

    ids, _cfg, complete, warnings, fetched = service._list_all_ids(
        "tenant-a",
        {"store_id": "store-a", "store_name": "Loja A"},
        {"access_token": "token"},
        "111",
        None,
        None,
    )

    assert ids == ["MLB1", "MLB2", "MLB3"]
    assert complete is True
    assert warnings == []
    assert fetched == 3
    assert requested_scrolls == ["", "scroll-estavel", "scroll-estavel"]


def test_multiple_listings_omit_unproven_stock_and_sum_distinct_sales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details = {
        "MLB130": _base_item("MLB130", status="active"),
        "MLB140": _base_item("MLB140", status="paused"),
    }
    for detail in details.values():
        detail.pop("user_product_id", None)
        detail.pop("inventory_id", None)
    details["MLB130"].update({"available_quantity": 7, "sold_quantity": 3})
    details["MLB140"].update({"available_quantity": 4, "sold_quantity": 5})

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB130", "MLB140"], "paging": {"total": 2}}, cfg)
        if url.endswith("/items/bulk"):
            return _response(
                [
                    {"id": item_id, "status_code": 200, "body": details[item_id]}
                    for item_id in ("MLB130", "MLB140")
                ],
                cfg,
            )
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    item = result["items"][0]
    assert result["coverage_complete"] is True
    assert "estoque_disponivel_ml" not in item["fields"]
    assert item["fields"]["vendidos_acumulados_ml"] == 8
    assert item["conflicts"]["estoque_disponivel_ml"]["reason"] == "inventory_identity_unproven"
    assert "vendidos_acumulados_ml" not in item["conflicts"]


def test_multiple_distinct_inventory_identities_are_aggregated_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details = {
        "MLB150": _base_item("MLB150", status="active"),
        "MLB160": _base_item("MLB160", status="paused"),
    }
    details["MLB150"].update(
        {
            "user_product_id": "MLBU-150",
            "inventory_id": "INV-150",
            "available_quantity": 7,
            "sold_quantity": 3,
        }
    )
    details["MLB160"].update(
        {
            "user_product_id": "MLBU-160",
            "inventory_id": "INV-160",
            "available_quantity": 4,
            "sold_quantity": 5,
        }
    )

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB150", "MLB160"], "paging": {"total": 2}}, cfg)
        if url.endswith("/items/bulk"):
            return _response(
                [
                    {"id": item_id, "status_code": 200, "body": details[item_id]}
                    for item_id in ("MLB150", "MLB160")
                ],
                cfg,
            )
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(
        monkeypatch,
        handler,
        user_product_stocks={
            "MLBU-150": {
                "id": "MLBU-150",
                "user_id": 111,
                "locations": [{"type": "selling_address", "quantity": 7}],
            },
            "MLBU-160": {
                "id": "MLBU-160",
                "user_id": 111,
                "locations": [{"type": "selling_address", "quantity": 4}],
            },
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    item = result["items"][0]
    assert result["coverage_complete"] is True
    assert item["fields"]["estoque_disponivel_ml"] == 11
    assert item["fields"]["vendidos_acumulados_ml"] == 8
    assert "inventory_id_ml" not in item["fields"]
    assert "user_product_id_ml" not in item["fields"]
    assert item["fields"]["inventory_ids_ml"] == "INV-150|INV-160"
    assert item["fields"]["user_product_ids_ml"] == "MLBU-150|MLBU-160"
    assert item["conflicts"]["inventory_id_ml"] == ["INV-150", "INV-160"]
    assert item["conflicts"]["user_product_id_ml"] == ["MLBU-150", "MLBU-160"]
    assert '"inventory_id":"INV-150"' in item["fields"]["anuncios_ml_json"]
    assert '"inventory_id":"INV-160"' in item["fields"]["anuncios_ml_json"]
    assert "estoque_disponivel_ml" not in item["conflicts"]
    assert "vendidos_acumulados_ml" not in item["conflicts"]


def test_warehouse_stock_uses_deduplicated_user_product_locations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    details = {
        "MLB170": _base_item("MLB170", status="active"),
        "MLB180": _base_item("MLB180", status="paused"),
    }
    details["MLB170"].update({"available_quantity": 99, "sold_quantity": 3})
    details["MLB180"].update({"available_quantity": 88, "sold_quantity": 5})
    user_product_calls: list[str] = []

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": ["warehouse_management", "multiwarehouse"]}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB170", "MLB180"], "paging": {"total": 2}}, cfg)
        if url.endswith("/items/bulk"):
            return _response(
                [
                    {"id": item_id, "status_code": 200, "body": details[item_id]}
                    for item_id in ("MLB170", "MLB180")
                ],
                cfg,
            )
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_details={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 111,
                "name": "Produto de usuário",
                "family_id": "FAMILY-AUTHORITATIVE",
                "domain_id": "MLB-AUTO_PARTS",
                "attributes": [],
                "tags": ["user_product_listing"],
            }
        },
        user_product_stocks={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 111,
                "locations": [
                    {
                        "type": "seller_warehouse",
                        "store_id": "STORE-1",
                        "network_node_id": "NODE-1",
                        "quantity": 7,
                    },
                    {
                        "type": "seller_warehouse",
                        "store_id": "STORE-1",
                        "network_node_id": "NODE-1",
                        "quantity": 7,
                    },
                    {"type": "meli_facility", "quantity": 4},
                ],
            }
        },
        user_product_calls=user_product_calls,
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["stats"]["warehouse_management"] is True
    assert result["stats"]["user_products_requested"] == 1
    assert len(user_product_calls) == 2
    fields = result["items"][0]["fields"]
    assert fields["estoque_disponivel_ml"] == 11
    assert fields["estoque_user_product_total_ml"] == 11
    assert fields["estoque_multiorigem_ml"] is True
    assert fields["familia_id_ml"] == "FAMILY-AUTHORITATIVE"
    assert '"network_node_id":"NODE-1"' in fields["estoque_localizacoes_ml_json"]
    assert fields["estoque_localizacoes_ml_json"].count('"network_node_id":"NODE-1"') == 1


def test_warehouse_stock_failure_blocks_apply_but_normal_404_uses_item_quantity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB190")
    warehouse: bool | None = True

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            payload: dict[str, Any] = {"id": 111}
            if warehouse is not None:
                payload["tags"] = ["warehouse_management"] if warehouse else []
            return _response(payload, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB190"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB190", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    stock_404 = {"MLBU-1": ({"message": "stock-locations not found"}, 404)}
    _install_transport(monkeypatch, handler, user_product_stocks=stock_404)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")
    assert result["coverage_complete"] is False
    assert result["stats"]["user_product_failures"] == 1
    assert "estoque_disponivel_ml" not in result["items"][0]["fields"]

    warehouse = False
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")
    assert result["coverage_complete"] is True
    assert result["stats"]["user_product_stocks_absent"] == 1
    assert result["items"][0]["fields"]["estoque_disponivel_ml"] == 7

    warehouse = None
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")
    assert result["coverage_complete"] is False
    assert result["stats"]["user_product_failures"] == 1
    assert result["items"][0]["fields"]["estoque_disponivel_ml"] == 7


def test_user_product_from_another_seller_blocks_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB195")

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": []}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB195"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB195", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_details={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 222,
                "name": "Produto de outro vendedor",
                "attributes": [],
                "tags": [],
            }
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["stats"]["user_product_failures"] == 1
    assert result["stats"]["user_products_complete"] == 0
    assert "user_product_nome_ml" not in result["items"][0]["fields"]
    assert any("nao foi detalhado com seguranca" in warning for warning in result["warnings"])


def test_late_warehouse_detection_reclassifies_previous_stock_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _base_item("MLB196", sku="SKU-1")
    second = _base_item("MLB197", sku="SKU-2")
    first["user_product_id"] = "MLBU-1"
    second["user_product_id"] = "MLBU-2"

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": []}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB196", "MLB197"], "paging": {"total": 2}}, cfg)
        if url.endswith("/items/bulk"):
            return _response(
                [
                    {"id": "MLB196", "status_code": 200, "body": first},
                    {"id": "MLB197", "status_code": 200, "body": second},
                ],
                cfg,
            )
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_stocks={
            "MLBU-1": ({"message": "stock-locations not found"}, 404),
            "MLBU-2": {
                "id": "MLBU-2",
                "user_id": 111,
                "locations": [
                    {"type": "seller_warehouse", "store_id": "STORE-2", "quantity": 5}
                ],
            },
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["stats"]["warehouse_management"] is True
    assert result["stats"]["user_product_stocks_absent"] == 1
    assert result["stats"]["user_product_failures"] == 1
    by_sku = {item["sku"]: item for item in result["items"]}
    assert "estoque_disponivel_ml" not in by_sku["SKU-1"]["fields"]
    assert by_sku["SKU-2"]["fields"]["estoque_disponivel_ml"] == 5


def test_user_product_stock_location_identity_is_deduplicated_and_validated() -> None:
    base = {
        "id": "MLBU-1",
        "user_id": 111,
        "locations": [
            {
                "type": "seller_warehouse",
                "store_id": "STORE-1",
                "network_node_id": "NODE-1",
                "quantity": 7,
            },
            {
                "type": "seller_warehouse",
                "store_id": "STORE-1",
                "network_node_id": "NODE-1",
                "quantity": 7,
            },
        ],
    }
    normalized = service._normalize_user_product_stock(base, "MLBU-1", "111")
    assert normalized is not None
    assert normalized["total"] == 7
    assert len(normalized["locations"]) == 1

    conflicting = copy.deepcopy(base)
    conflicting["locations"][1]["quantity"] = 8
    assert service._normalize_user_product_stock(conflicting, "MLBU-1", "111") is None

    missing_store = copy.deepcopy(base)
    missing_store["locations"][0].pop("store_id")
    missing_store["locations"] = missing_store["locations"][:1]
    assert service._normalize_user_product_stock(missing_store, "MLBU-1", "111") is None


def test_valid_user_product_stock_wins_without_warehouse_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB198")
    detail["available_quantity"] = 99

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": []}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB198"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB198", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_stocks={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 111,
                "locations": [
                    {"type": "meli_facility", "quantity": 4},
                    {"type": "selling_address", "quantity": 7},
                ],
            }
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["stats"]["warehouse_management"] is False
    assert result["items"][0]["fields"]["estoque_disponivel_ml"] == 11
    assert result["items"][0]["fields"]["estoque_user_product_total_ml"] == 11


def test_rejects_fresh_token_seller_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        calls.append(url)
        return _response({"id": 222}, cfg)

    _install_transport(monkeypatch, handler, seller_id="111")
    with pytest.raises(HTTPException) as exc_info:
        service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "ml_seller_mismatch"
    assert calls == [f"{service.ML_API_BASE}/users/me"]


def test_configuration_changed_during_collection_is_rejected_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_state = {"value": _store()}
    detail = _base_item("MLB110")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB110"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB110", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB110/description"):
            store_state["value"]["integracoes"]["mercadolivre"]["access_token"] = "token-other-account"
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        service.integracoes,
        "carregar_lojas",
        lambda client_id: [copy.deepcopy(store_state["value"])],
    )

    with pytest.raises(HTTPException) as exc_info:
        service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "store_config_changed",
        "message": "A configuracao Mercado Livre da loja mudou durante a coleta. Gere uma nova previa.",
    }
    assert "token-other-account" not in str(exc_info.value.detail)
    assert "sha256" not in str(exc_info.value.detail)


def test_transport_token_refresh_is_accepted_only_when_persisted_configuration_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_state = {"value": _store()}
    detail = _base_item("MLB120")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB120"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            refreshed = {
                **cfg,
                "access_token": "token-refreshed",
                "refresh_token": "refresh-refreshed",
                "updated_at": "1234567890",
            }
            store_state["value"]["integracoes"]["mercadolivre"].update(
                {
                    "access_token": "token-refreshed",
                    "refresh_token": "refresh-refreshed",
                    "updated_at": "1234567890",
                }
            )
            return _response(
                [{"id": "MLB120", "status_code": 200, "body": detail}],
                refreshed,
            )
        if url.endswith("/items/MLB120/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(
        service.integracoes,
        "carregar_lojas",
        lambda client_id: [copy.deepcopy(store_state["value"])],
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert [item["sku"] for item in result["items"]] == ["SKU-1"]
    assert result["config_fingerprint"] == service.configuracao_catalogo_fingerprint(
        "mercadolivre",
        "store-a",
        "Loja A",
        store_state["value"]["integracoes"]["mercadolivre"],
    )
    assert "token-refreshed" not in str(result)
    assert "refresh-refreshed" not in str(result)
    assert "sha256" not in str(result)


def test_incomplete_scroll_marks_coverage_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _base_item("MLB100")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response(
                {"results": ["MLB100"], "scroll_id": "same-scroll", "paging": {"total": 2}},
                cfg,
            )
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB100", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB100/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["stats"]["listed_ids"] == 1
    assert any("nao avancou" in warning for warning in result["warnings"])


def test_catalog_limit_aborts_before_fetching_listing_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        calls.append(url)
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response(
                {
                    "results": ["MLB999"],
                    "scroll_id": "unused-scroll",
                    "paging": {"total": service.ML_CATALOG_MAX_ROWS + 1},
                },
                cfg,
            )
        raise AssertionError(f"nao deveria detalhar catalogo acima do limite: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["items"] == []
    assert result["stats"]["listed_ids"] == 0
    assert not any(url.endswith("/items/bulk") for url in calls)
    assert any("25.000 anuncios" in warning for warning in result["warnings"])


def test_exact_catalog_limit_can_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "ML_CATALOG_MAX_ROWS", 1)
    detail = _base_item("MLB699")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB699"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB699", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB699/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert len(result["items"]) == service.ML_CATALOG_MAX_ROWS
    assert not any("limite seguro" in warning for warning in result["warnings"])


def test_aggregate_row_limit_is_fail_closed_and_never_returns_more_than_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "ML_CATALOG_MAX_ROWS", 2)
    detail = _base_item(
        "MLB700",
        sku=None,
        variations=[{"id": 701}, {"id": 702}, {"id": 703}],
    )
    variations = [
        {"id": 701, "seller_sku": "VAR-701"},
        {"id": 702, "seller_sku": "VAR-702"},
        {"id": 703, "seller_sku": "VAR-703"},
    ]

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB700"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB700", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB700/variations"):
            return _response(variations, cfg)
        if url.endswith("/items/MLB700/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert len(result["items"]) <= service.ML_CATALOG_MAX_ROWS
    assert any("2 linhas agregadas" in warning for warning in result["warnings"])


def test_incomplete_bulk_multiget_marks_missing_details(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _base_item("MLB100")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB100", "MLB200"], "paging": {"total": 2}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB100", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB100/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["stats"]["missing_details"] == 1
    assert {entry.get("mlb") for entry in result["skipped"] if entry["reason"] == "listing_details_missing"} == {
        "MLB200"
    }
    assert [item["sku"] for item in result["items"]] == ["SKU-1"]


def test_structurally_incomplete_listing_detail_is_not_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = {
        "id": "MLB250",
        "seller_id": 111,
        "seller_sku": "SHOULD-NOT-IMPORT",
        "status": "active",
        "category_id": "MLB1234",
        # title, attributes and variations are part of a complete item detail.
    }

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB250"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB250", "status_code": 200, "body": detail}], cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["items"] == []
    assert result["stats"]["incomplete_details"] == 1
    assert result["skipped"] == [
        {
            "reason": "listing_detail_incomplete",
            "mlb": "MLB250",
            "missing_fields": ["title", "attributes", "variations"],
        }
    ]


def test_legacy_multiget_is_used_only_as_compatibility_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _base_item("MLB100")
    calls: list[str] = []

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        calls.append(url)
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB100"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response({"message": "not found"}, cfg, status=404)
        if url.endswith("/items"):
            return _response([{"code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB100/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert calls.index(f"{service.ML_API_BASE}/items/bulk") < calls.index(f"{service.ML_API_BASE}/items")
    assert any("multiget legado" in warning for warning in result["warnings"])


def test_variations_without_unique_explicit_sku_are_never_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    summaries = [{"id": variation_id} for variation_id in (301, 302, 303, 304)]
    detail = _base_item("MLB300", sku=None, variations=summaries)
    variations = [
        {"id": 301, "seller_custom_field": "LEGACY-301"},
        {
            "id": 302,
            "attributes": [
                {"id": "SELLER_SKU", "value_name": "SKU-A"},
                {"id": "SKU", "value_name": "SKU-B"},
            ],
        },
        {"id": 303, "seller_sku": "SKU-DUP"},
        {"id": 304, "attributes": [{"id": "SELLER_SKU", "value_name": "SKU-DUP"}]},
    ]

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB300"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB300", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB300/variations"):
            return _response(variations, cfg)
        if url.endswith("/items/MLB300/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert [item["sku"] for item in result["items"]] == ["LEGACY-301"]
    assert result["stats"]["missing_sku"] == 0
    assert result["stats"]["ambiguous_sku"] == 1
    assert result["stats"]["duplicate_sku"] == 1
    assert {entry["reason"] for entry in result["skipped"]} == {
        "sku_ambiguous",
        "sku_duplicate_within_listing",
    }
    assert all(entry.get("sku") not in {"301", "302", "303", "304"} for entry in result["skipped"])
    assert result["items"][0]["fields"]["variacao_id_ml"] == "301"


def test_variation_summary_does_not_erase_detailed_seller_sku(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item(
        "MLB350",
        sku=None,
        variations=[
            {
                "id": 351,
                "seller_sku": "SKU-RESUMO",
                "user_product_id": "MLBU-RESUMO",
                "inventory_id": "INV-RESUMO",
                "attributes": [
                    {"id": "MODEL", "name": "Modelo", "value_name": "Resumo"}
                ],
            }
        ],
    )
    variation_detail = {
        "id": 351,
        "seller_sku": "SKU-DETALHE",
        "user_product_id": "MLBU-DETALHE",
        "inventory_id": "INV-DETALHE",
        "attributes": [
            {"id": "SELLER_SKU", "name": "SKU", "value_name": "SKU-DETALHE"},
            {"id": "COLOR", "name": "Cor", "value_name": "Preto"},
        ],
    }

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB350"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB350", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB350/variations"):
            return _response([variation_detail], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert [item["sku"] for item in result["items"]] == ["SKU-DETALHE"]
    assert result["items"][0]["fields"]["user_product_id_ml"] == "MLBU-DETALHE"
    assert result["items"][0]["fields"]["inventory_id_ml"] == "INV-DETALHE"
    attributes = result["items"][0]["fields"]["atributos_ml_json"]
    assert '"id":"SELLER_SKU"' in attributes
    assert '"id":"MODEL"' in attributes


def test_user_product_sku_and_official_media_are_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB360", sku=None)
    detail["site_id"] = "MLB"

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": []}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB360"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB360", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_details={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 111,
                "site_id": "MLB",
                "name": "Produto no User Product",
                "family_id": "FAMILY-1",
                "domain_id": "MLB-AUTO_PARTS",
                "catalog_product_id": "MLB-CAT-1",
                "attributes": [
                    {"id": "SELLER_SKU", "name": "SKU", "value_name": "SKU-UP"}
                ],
                "pictures": [
                    {
                        "id": "PIC-UP-1",
                        "secure_url": "https://http2.mlstatic.com/D_UP-1-O.jpg",
                    }
                ],
                "thumbnail": {
                    "id": "THUMB-UP-1",
                    "secure_url": "https://http2.mlstatic.com/D_UP-THUMB-O.jpg",
                },
                "tags": ["user_product_listing"],
            }
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert [item["sku"] for item in result["items"]] == ["SKU-UP"]
    fields = result["items"][0]["fields"]
    assert fields["site_id_ml"] == "MLB"
    assert fields["site_id_user_product_ml"] == "MLB"
    assert fields["catalog_product_id_user_product_ml"] == "MLB-CAT-1"
    assert fields["catalog_product_ids_user_product_ml"] == "MLB-CAT-1"
    assert '"id":"PIC-UP-1"' in fields["imagens_user_product_ml_json"]
    assert fields["miniatura_url_user_product_ml"].endswith("D_UP-THUMB-O.jpg")
    assert '"id":"PIC-1"' in fields["imagens_ml_json"]


def test_user_product_sku_conflict_blocks_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB370", sku="SKU-ITEM")

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": []}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB370"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB370", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_details={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 111,
                "attributes": [
                    {"id": "SELLER_SKU", "name": "SKU", "value_name": "SKU-UP"}
                ],
                "tags": [],
            }
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["items"] == []
    assert result["stats"]["user_product_sku_conflicts"] == 1
    assert result["skipped"][0]["reason"] == "user_product_sku_conflict"


def test_user_product_catalog_identity_conflict_blocks_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB380")
    detail["site_id"] = "MLB"

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111, "tags": []}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB380"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB380", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao"}, cfg)
        raise AssertionError(url)

    _install_transport(
        monkeypatch,
        handler,
        user_product_details={
            "MLBU-1": {
                "id": "MLBU-1",
                "user_id": 111,
                "site_id": "MLB",
                "catalog_product_id": "MLB-CAT-DIVERGENTE",
                "attributes": [
                    {"id": "SELLER_SKU", "name": "SKU", "value_name": "SKU-1"}
                ],
                "tags": [],
            }
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["stats"]["user_product_identity_conflicts"] == 1
    conflicts = result["items"][0]["conflicts"]
    assert "identidade_item_user_product_ml" in conflicts


@pytest.mark.parametrize(
    "entity",
    [
        {"seller_custom_field": "LEGACY-PARENT"},
        {"id": 301, "seller_custom_field": "LEGACY-VARIATION"},
    ],
)
def test_seller_custom_field_e_sku_explicito_legado(entity):
    candidates = service._explicit_sku_candidates(entity)

    assert candidates == [
        {
            "sku": entity["seller_custom_field"],
            "sku_normalizado": entity["seller_custom_field"],
            "comparison_key": entity["seller_custom_field"].casefold(),
            "sources": ["seller_custom_field"],
        }
    ]


def test_valid_variation_maps_effective_explicit_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _base_item("MLB400", sku=None, variations=[{"id": 401}])
    variation = {
        "id": 401,
        "seller_sku": "VAR-401",
        "catalog_product_id": "MLB-CAT-VAR-401",
        "user_product_id": "MLBU-VAR-401",
        "inventory_id": "INV-VAR-401",
        "price": 99.9,
        "available_quantity": 4,
        "sold_quantity": 8,
        "attribute_combinations": [
            {"id": "MODEL", "name": "Modelo", "value_name": "Modelo Variacao"},
            {"id": "GTIN", "name": "GTIN", "value_name": "7890000000401"},
        ],
    }

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB400"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB400", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB400/variations"):
            return _response([variation], cfg)
        if url.endswith("/items/MLB400/description"):
            return _response({"plain_text": "Descricao da variacao"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(
        monkeypatch,
        handler,
        user_product_stocks={
            "MLBU-VAR-401": {
                "id": "MLBU-VAR-401",
                "user_id": 111,
                "locations": [{"type": "selling_address", "quantity": 4}],
            }
        },
    )
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert len(result["items"]) == 1
    fields = result["items"][0]["fields"]
    assert result["items"][0]["sku"] == "VAR-401"
    assert fields["variacao_id_ml"] == "401"
    assert fields["preco_ml"] == 99.9
    assert fields["estoque_disponivel_ml"] == 4
    assert fields["vendidos_acumulados_ml"] == 8
    assert fields["modelo"] == "Modelo Variacao"
    assert fields["gtins_mlb"] == "7890000000401"
    assert fields["catalog_product_id_ml"] == "MLB-CAT-VAR-401"
    assert fields["user_product_id_ml"] == "MLBU-VAR-401"
    assert fields["inventory_id_ml"] == "INV-VAR-401"


def test_detail_seller_mismatch_is_skipped_and_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _base_item("MLB500", seller_id="222")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB500"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB500", "status_code": 200, "body": detail}], cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["items"] == []
    assert result["stats"]["owner_mismatch"] == 1
    assert result["skipped"] == [{"reason": "seller_mismatch", "mlb": "MLB500"}]


def test_request_retries_429_respects_retry_after_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    sleeps = []
    responses = iter(
        [
            FakeResponse(429, {"message": "too_many_requests"}, CaseInsensitiveDict({"Retry-After": "2"})),
            FakeResponse(200, {"ok": True}),
        ]
    )

    def transport(*_args, **_kwargs):
        calls.append(1)
        return next(responses), {"app_id": "app-a", "access_token": "rotated"}

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", transport)
    monkeypatch.setattr(service, "_wait_catalog_rate_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_cancelable_sleep",
        lambda seconds, _cancel_event, _deadline: sleeps.append(seconds),
    )

    payload, cfg, status, error = service._request_json(
        "tenant-a",
        "Loja A",
        {"app_id": "app-a", "access_token": "old"},
        f"{service.ML_API_BASE}/items/MLB1",
        deadline=time.monotonic() + 10,
    )

    assert (payload, status, error) == ({"ok": True}, 200, "")
    assert cfg["access_token"] == "rotated"
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_request_retries_timeout_and_503_with_jitter_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    sleeps = []

    def transport(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise service.requests.exceptions.Timeout("timeout")
        if len(calls) == 2:
            return FakeResponse(503, {"message": "temporary"}), {"app_id": "app-a"}
        return FakeResponse(200, {"ok": True}), {"app_id": "app-a"}

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", transport)
    monkeypatch.setattr(service, "_wait_catalog_rate_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service.random, "uniform", lambda _start, end: end)
    monkeypatch.setattr(
        service,
        "_cancelable_sleep",
        lambda seconds, _cancel_event, _deadline: sleeps.append(seconds),
    )

    payload, _cfg, status, error = service._request_json(
        "tenant-a",
        "Loja A",
        {"app_id": "app-a"},
        f"{service.ML_API_BASE}/items/MLB1",
        deadline=time.monotonic() + 10,
    )

    assert (payload, status, error) == ({"ok": True}, 200, "")
    assert len(calls) == 3
    assert sleeps == [0.75, 1.5]


def test_request_exhausts_transient_retries_and_401_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    status_code = 503

    def transport(*_args, **_kwargs):
        calls.append(1)
        return FakeResponse(status_code, {"message": "failure"}), {"app_id": "app-a"}

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", transport)
    monkeypatch.setattr(service, "_wait_catalog_rate_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_cancelable_sleep", lambda *_args, **_kwargs: None)

    payload, _cfg, status, error = service._request_json(
        "tenant-a", "Loja A", {"app_id": "app-a"}, f"{service.ML_API_BASE}/items/MLB1"
    )
    assert payload == {"message": "failure"}
    assert (status, error) == (503, "provider_transient_error")
    assert len(calls) == service.ML_CATALOG_MAX_ATTEMPTS

    calls.clear()
    status_code = 401
    _payload, _cfg, status, error = service._request_json(
        "tenant-a", "Loja A", {"app_id": "app-a"}, f"{service.ML_API_BASE}/items/MLB1"
    )
    assert (status, error) == (401, "")
    assert len(calls) == 1


def test_retry_wait_is_cancelable_and_deadline_blocks_network(monkeypatch: pytest.MonkeyPatch) -> None:
    event = threading.Event()
    calls = []

    def transport(*_args, **_kwargs):
        calls.append(1)
        return FakeResponse(429, {}, {"Retry-After": "1"}), {"app_id": "app-a"}

    def cancel_on_sleep(_seconds):
        event.set()

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", transport)
    monkeypatch.setattr(service, "_wait_catalog_rate_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service.time, "sleep", cancel_on_sleep)

    with pytest.raises(service._CatalogoMLCancelado):
        service._request_json(
            "tenant-a",
            "Loja A",
            {"app_id": "app-a"},
            f"{service.ML_API_BASE}/items/MLB1",
            cancel_event=event,
            deadline=time.monotonic() + 10,
        )
    assert len(calls) == 1

    event.clear()
    calls.clear()
    with pytest.raises(HTTPException) as exc_info:
        service._request_json(
            "tenant-a",
            "Loja A",
            {"app_id": "app-a"},
            f"{service.ML_API_BASE}/items/MLB1",
            deadline=time.monotonic() - 1,
        )
    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["code"] == "catalog_collection_deadline_exceeded"
    assert calls == []


def test_rate_bucket_is_shared_by_app_and_unknown_app_uses_global(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = []
    monkeypatch.setattr(service.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        service,
        "_cancelable_sleep",
        lambda seconds, _cancel_event, _deadline: sleeps.append(round(seconds, 3)),
    )
    service._ML_CATALOG_NEXT_CALL.clear()

    service._wait_catalog_rate_turn({"app_id": "same-app"}, None, None)
    service._wait_catalog_rate_turn({"app_id": "same-app", "access_token": "other"}, None, None)
    service._wait_catalog_rate_turn({"app_id": "other-app"}, None, None)
    service._wait_catalog_rate_turn({"access_token": "one"}, None, None)
    service._wait_catalog_rate_turn({"access_token": "two"}, None, None)

    assert sleeps == [0.0, 0.1, 0.0, 0.0, 0.1]
    assert service._catalog_rate_key({"app_id": "same-app"}) == service._catalog_rate_key(
        {"app_id": "same-app", "access_token": "rotated"}
    )
    assert service._catalog_rate_key({"app_id": "same-app"}) != service._catalog_rate_key(
        {"app_id": "other-app"}
    )
    assert service._catalog_rate_key({}) == "ml-catalog-process-wide"

    sleeps.clear()
    service._ML_CATALOG_NEXT_CALL.clear()
    service._wait_catalog_rate_turn(
        {"app_id": "same-app"}, None, None, bucket="user-product-stock"
    )
    service._wait_catalog_rate_turn(
        {"app_id": "same-app"}, None, None, bucket="user-product-stock"
    )
    assert sleeps == [0.0, 0.6]


def test_stock_requests_observe_official_100_rpm_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"value": 100.0}
    calls: list[float] = []

    def sleep(seconds, _cancel_event, _deadline):
        clock["value"] += max(0.0, float(seconds))

    def transport(_client_id, _store_name, cfg, _method, _url, *, params=None, timeout=None):
        calls.append(clock["value"])
        return FakeResponse(200, {"ok": True}), cfg

    monkeypatch.setattr(service.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(service, "_cancelable_sleep", sleep)
    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", transport)
    service._ML_CATALOG_NEXT_CALL.clear()
    url = f"{service.ML_API_BASE}/user-products/MLBU-1/stock"

    service._request_json("tenant-a", "Loja A", {"app_id": "app-a"}, url)
    service._request_json("tenant-a", "Loja A", {"app_id": "app-a"}, url)

    assert calls == [100.0, 100.6]


def test_retry_after_defers_other_consumer_in_same_app_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(service.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        service,
        "_cancelable_sleep",
        lambda seconds, _cancel_event, _deadline: sleeps.append(round(seconds, 3)),
    )
    monkeypatch.setattr(service, "ML_CATALOG_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_api_request",
        lambda *_args, **_kwargs: (
            FakeResponse(429, {}, {"Retry-After": "3"}),
            {"app_id": "same-app"},
        ),
    )
    service._ML_CATALOG_NEXT_CALL.clear()

    service._request_json(
        "tenant-a",
        "Loja A",
        {"app_id": "same-app"},
        f"{service.ML_API_BASE}/items/MLB1",
    )
    service._wait_catalog_rate_turn({"app_id": "same-app"}, None, None)
    service._wait_catalog_rate_turn({"app_id": "other-app"}, None, None)

    assert sleeps == [0.0, 3.0, 0.0]


def test_retry_after_supports_real_requests_headers_and_caps_value() -> None:
    response = FakeResponse(429, {}, CaseInsensitiveDict({"retry-after": "99"}))
    assert service._retry_after_seconds(response) == service.ML_CATALOG_RETRY_AFTER_MAX_SECONDS


def test_exhausted_detail_retries_leave_catalog_preview_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    bulk_calls = 0

    def handler(_client_id, _store_name, cfg, _method, url, *, params=None, timeout=None):
        nonlocal bulk_calls
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB900"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            bulk_calls += 1
            return _response({"message": "temporary"}, cfg, status=503)
        raise AssertionError(url)

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(service, "_cancelable_sleep", lambda *_args, **_kwargs: None)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["items"] == []
    assert result["stats"]["missing_details"] == 1
    assert bulk_calls == service.ML_CATALOG_MAX_ATTEMPTS


def test_listing_without_description_is_complete_and_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    detail = _base_item("MLB600")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB600"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB600", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB600/description"):
            return _response({"message": "Item has no description"}, cfg, status=404)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["descriptions_absent"] == 1
    assert result["stats"]["description_failures"] == 0
    assert "descricao" not in result["items"][0]["fields"]
    assert any("nao possui descricao" in warning for warning in result["warnings"])


def test_description_failure_keeps_confirmed_skus_applicable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = _base_item("MLB601")

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response({"results": ["MLB601"], "paging": {"total": 1}}, cfg)
        if url.endswith("/items/bulk"):
            return _response([{"id": "MLB601", "status_code": 200, "body": detail}], cfg)
        if url.endswith("/items/MLB601/description"):
            return _response({"message": "description unavailable"}, cfg, status=403)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["description_failures"] == 1
    assert [item["sku"] for item in result["items"]] == ["SKU-1"]
    assert "descricao" not in result["items"][0]["fields"]


def test_listing_without_sku_is_ignored_without_blocking_confirmed_skus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = _base_item("MLB610", sku="SKU-VALIDO")
    without_sku = _base_item("MLB611", sku=None)

    def handler(client_id, store_name, cfg, method, url, *, params=None, timeout=None):
        if url.endswith("/users/me"):
            return _response({"id": 111}, cfg)
        if url.endswith("/users/111/items/search"):
            return _response(
                {"results": ["MLB610", "MLB611"], "paging": {"total": 2}}, cfg
            )
        if url.endswith("/items/bulk"):
            return _response(
                [
                    {"id": "MLB610", "status_code": 200, "body": valid},
                    {"id": "MLB611", "status_code": 200, "body": without_sku},
                ],
                cfg,
            )
        if url.endswith("/description"):
            return _response({"plain_text": "Descricao disponivel"}, cfg)
        raise AssertionError(f"consulta inesperada: {url}")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["missing_sku"] == 1
    assert [item["sku"] for item in result["items"]] == ["SKU-VALIDO"]
    assert any(entry.get("reason") == "sku_missing" for entry in result["skipped"])


def test_cancellation_before_preflight_makes_no_network_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cancel_event = threading.Event()
    cancel_event.set()

    def handler(*args, **kwargs):
        raise AssertionError("nao deveria consultar a rede")

    _install_transport(monkeypatch, handler)
    result = service.coletar_catalogo_mercadolivre(
        "tenant-a", "store-a", cancel_event=cancel_event
    )

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["cancelled"] is True
    assert result["items"] == []
    assert result["stats"]["pages_fetched"] == 0
