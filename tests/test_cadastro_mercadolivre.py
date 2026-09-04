from __future__ import annotations

from typing import Any

import pytest
import requests
from fastapi import HTTPException

from backend.routers.cadastro import create_cadastro_router
from backend.services import cadastro_mercadolivre as service


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


def _attribute(attribute_id: str, value: str) -> dict[str, str]:
    return {"id": attribute_id, "value_name": value}


def _store(store_id: str, seller_id: str, *, name: str = "Loja repetida") -> dict[str, Any]:
    return {
        "store_id": store_id,
        "nome": name,
        "integracoes": {
            "mercadolivre": {
                "access_token": f"token-{store_id}",
                "refresh_token": f"refresh-{store_id}",
                "app_id": "app",
                "client_secret": "secret",
                "user_id": seller_id,
            }
        },
    }


def _configure_store_runtime(monkeypatch, stores):
    monkeypatch.setattr(service.integracoes, "carregar_lojas", lambda _client_id: stores)
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_cfg_com_store_id_context",
        lambda cfg, store_id: {**cfg, "_store_id_context": store_id},
    )
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_normalizar_oauth_compartilhado",
        lambda _client_id, _name, cfg: cfg,
    )
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_descobrir_user_id_oauth",
        lambda _client_id, _name, cfg: cfg,
    )


def _install_api(monkeypatch, *, search, items, categories=None, descriptions=None, variations=None):
    categories = categories or {}
    descriptions = descriptions or {}
    variations = variations or {}
    calls = []

    def request(client_id, store_name, cfg, method, url, *, params=None, timeout=15, **_kwargs):
        calls.append(
            {
                "client_id": client_id,
                "store_name": store_name,
                "store_id": cfg.get("_store_id_context"),
                "seller_id": cfg.get("user_id"),
                "method": method,
                "url": url,
                "params": params or {},
                "timeout": timeout,
            }
        )
        if url.endswith("/items/search"):
            payload, status = search if isinstance(search, tuple) else (search, 200)
            return FakeResponse(payload, status), cfg
        if url.endswith("/items"):
            ids = str((params or {}).get("ids") or "").split(",")
            payload = [
                {"code": 200, "body": items[item_id]}
                for item_id in ids
                if item_id in items
            ]
            return FakeResponse(payload), cfg
        if "/variations" in url:
            item_id = url.split("/items/", 1)[1].split("/", 1)[0]
            payload, status = variations.get(item_id, ([], 200))
            return FakeResponse(payload, status), cfg
        if "/categories/" in url:
            category_id = url.rsplit("/", 1)[-1]
            payload, status = categories.get(category_id, ({}, 404))
            return FakeResponse(payload, status), cfg
        if url.endswith("/description"):
            item_id = url.split("/items/", 1)[1].split("/", 1)[0]
            payload, status = descriptions.get(item_id, ({}, 404))
            return FakeResponse(payload, status), cfg
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", request)
    monkeypatch.setattr(
        service,
        "_download_photo_data_url",
        lambda url, item_id, *_args: {
            "url": url,
            "data_url": "data:image/jpeg;base64,/9j/",
            "filename": f"{item_id}.jpg",
        },
    )
    return calls


def test_lookup_is_read_only_store_exact_and_aggregates_listings(monkeypatch):
    stores = [_store("store-a", "111"), _store("store-b", "222")]
    _configure_store_runtime(monkeypatch, stores)
    items = {
        "MLB200": {
            "id": "MLB200",
            "seller_id": 222,
            "seller_sku": "SKU-1",
            "status": "paused",
            "title": "Titulo pausado",
            "category_id": "MLB2222",
            "attributes": [_attribute("BRAND", "Marca B"), _attribute("MODEL", "Modelo B"), _attribute("GTIN", "790")],
            "pictures": [{"id": "p2", "secure_url": "https://http2.mlstatic.com/p2.jpg"}],
        },
        "MLB100": {
            "id": "MLB100",
            "seller_id": "222",
            "attributes": [_attribute("SELLER_SKU", "SKU-1"), _attribute("BRAND", "Marca A"), _attribute("MODEL", "Modelo A"), _attribute("EAN", "789")],
            "status": "active",
            "title": "Titulo principal",
            "category_id": "MLB1111",
            "pictures": [{"id": "p1", "secure_url": "https://http2.mlstatic.com/p1.jpg"}],
        },
    }
    calls = _install_api(
        monkeypatch,
        search={"results": ["MLB200", "MLB100"], "paging": {"total": 2}},
        items=items,
        categories={"MLB1111": ({"name": "Autopecas"}, 200)},
        descriptions={"MLB100": ({"plain_text": "Descricao oficial"}, 200)},
    )

    result = service.consultar_produto_mercado_livre("client-1", "store-b", "SKU-1")

    assert result["success"] is True
    assert result["read_only"] is True
    assert result["coverage_complete"] is True
    assert result["store_id"] == "store-b"
    assert result["seller_id"] == "222"
    assert result["campos"] == {
        "foto": "https://http2.mlstatic.com/p1.jpg",
        "mlb_principal": "MLB100",
        "mlb_ids": "MLB100|MLB200",
        "qtd_anuncios_mlb": "2",
        "titulo_ml": "Titulo principal",
        "titulos_anuncios_mlb": "Titulo principal || Titulo pausado",
        "categoria": "Autopecas",
        "categoria_id_mlb": "MLB1111",
        "marca": "Marca A",
        "modelo": "Modelo A",
        "gtins_mlb": "789|790",
        "descricao": "Descricao oficial",
    }
    assert result["foto"]["data_url"].startswith("data:image/jpeg;base64,")
    assert result["conflicts"] == {
        "categoria_id_mlb": ["MLB1111", "MLB2222"],
        "marca": ["Marca A", "Marca B"],
        "modelo": ["Modelo A", "Modelo B"],
    }
    assert calls
    assert {call["store_id"] for call in calls} == {"store-b"}
    assert {call["seller_id"] for call in calls} == {"222"}
    assert {call["method"] for call in calls} == {"GET"}
    assert calls[0]["params"]["seller_sku"] == "SKU-1"


def test_exact_variation_supplies_only_its_gtin_model_and_picture(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {
        "id": "MLB300",
        "seller_id": "111",
        "status": "active",
        "title": "Produto com variacoes",
        "category_id": "MLB3333",
        "attributes": [_attribute("BRAND", "Marca Pai"), _attribute("GTIN", "000000")],
        "pictures": [
            {"id": "target-picture", "secure_url": "https://http2.mlstatic.com/target.jpg"},
            {"id": "other-picture", "secure_url": "https://http2.mlstatic.com/other.jpg"},
        ],
        "variations": [
            {"id": "10", "picture_ids": ["target-picture"], "attributes": [_attribute("SELLER_SKU", "SKU-VAR")]},
            {"id": "20", "picture_ids": ["other-picture"], "attributes": [_attribute("SELLER_SKU", "OUTRA")]},
        ],
    }
    calls = _install_api(
        monkeypatch,
        search={"results": ["MLB300"], "paging": {"total": 1}},
        items={"MLB300": item},
        categories={"MLB3333": ({"name": "Categoria"}, 200)},
        descriptions={"MLB300": ({"plain_text": "Descricao"}, 200)},
        variations={
            "MLB300": ([
                {
                    "id": "10",
                    "attributes": [
                        _attribute("SELLER_SKU", "SKU-VAR"),
                        _attribute("MODEL", "Modelo correto"),
                        {"id": "GTIN", "value_name": "111111, 222222", "value_id": "internal-id"},
                        {"id": "EMPTY_GTIN_REASON", "value_name": "Kit", "value_id": "17055159"},
                    ],
                },
                {
                    "id": "20",
                    "attributes": [_attribute("SELLER_SKU", "OUTRA"), _attribute("MODEL", "Modelo errado"), _attribute("GTIN", "999999")],
                },
            ], 200),
        },
    )

    result = service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-VAR")

    assert result["campos"]["modelo"] == "Modelo correto"
    assert result["campos"]["gtins_mlb"] == "111111|222222"
    assert result["campos"]["foto"] == "https://http2.mlstatic.com/target.jpg"
    assert result["anuncios"][0]["variation_id"] == "10"
    assert any("/variations" in call["url"] for call in calls)


def test_preferred_valid_mlb_remains_primary(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    items = {
        item_id: {
            "id": item_id,
            "seller_id": "111",
            "seller_sku": "SKU-1",
            "status": "active",
            "title": item_id,
            "category_id": "MLB1111",
        }
        for item_id in ("MLB100", "MLB200")
    }
    _install_api(
        monkeypatch,
        search={"results": ["MLB100", "MLB200"], "paging": {"total": 2}},
        items=items,
        categories={"MLB1111": ({"name": "Categoria"}, 200)},
        descriptions={"MLB200": ({"plain_text": "Descricao"}, 200)},
    )

    result = service.consultar_produto_mercado_livre(
        "client-1", "store-a", "SKU-1", mlb_principal="MLB200"
    )

    assert result["campos"]["mlb_principal"] == "MLB200"
    assert result["campos"]["mlb_ids"].startswith("MLB200|")


def test_category_and_description_failures_are_partial(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {
        "id": "MLB100",
        "seller_id": "111",
        "seller_sku": "SKU-1",
        "status": "active",
        "title": "Titulo",
        "category_id": "MLB1111",
    }
    _install_api(
        monkeypatch,
        search={"results": ["MLB100"], "paging": {"total": 1}},
        items={"MLB100": item},
        categories={"MLB1111": ({"error": "unavailable"}, 500)},
        descriptions={"MLB100": ({"error": "not_found"}, 404)},
    )

    result = service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-1")

    assert result["campos"]["categoria_id_mlb"] == "MLB1111"
    assert result["campos"]["categoria"] == ""
    assert result["campos"]["descricao"] == ""
    assert any("nome da categoria" in warning for warning in result["warnings"])
    assert any("nao possui descricao" in warning for warning in result["warnings"])


def test_zero_results_is_authoritative_only_for_complete_search(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    _install_api(
        monkeypatch,
        search={"results": [], "paging": {"total": 0}},
        items={},
    )
    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-X")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "sku_not_found_in_store"

    _install_api(
        monkeypatch,
        search=({"results": [], "paging": {"total": 0}}, 206),
        items={},
    )
    with pytest.raises(HTTPException) as partial_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-X")
    assert partial_info.value.status_code == 502
    assert partial_info.value.detail["code"] == "ml_search_incomplete"


def test_owner_mismatch_fails_closed(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {
        "id": "MLB100",
        "seller_id": "999",
        "seller_sku": "SKU-1",
        "status": "active",
        "title": "Outra conta",
    }
    _install_api(
        monkeypatch,
        search={"results": ["MLB100"], "paging": {"total": 1}},
        items={"MLB100": item},
    )

    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-1")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "listing_ownership_unconfirmed"


@pytest.mark.parametrize(
    ("provider_code", "expected_status", "expected_code"),
    [(401, 401, "ml_reconnect_required"), (403, 401, "ml_reconnect_required"), (429, 503, "ml_rate_limited")],
)
def test_multiget_entry_auth_and_rate_errors_keep_their_classification(
    monkeypatch, provider_code, expected_status, expected_code
):
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_api_request",
        lambda *_args, **_kwargs: (FakeResponse([{"code": provider_code, "body": {}}]), {"user_id": "111"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        service._buscar_itens(
            "client-1",
            "Loja",
            {"user_id": "111"},
            ["MLB100"],
            service.time.monotonic() + 10,
        )

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail["code"] == expected_code


def test_sku_comparison_preserves_meaningful_punctuation(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {"id": "MLB100", "seller_id": "111", "seller_sku": "AB12", "status": "active"}
    _install_api(
        monkeypatch,
        search={"results": ["MLB100"], "paging": {"total": 1}},
        items={"MLB100": item},
    )

    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "AB-12")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "exact_sku_not_confirmed"


def test_duplicate_variation_sku_fails_as_ambiguous(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {
        "id": "MLB100",
        "seller_id": "111",
        "status": "active",
        "variations": [{"id": "10"}, {"id": "20"}],
    }
    duplicated = [
        {"id": "10", "attributes": [_attribute("SELLER_SKU", "SKU-DUP")]},
        {"id": "20", "attributes": [_attribute("SELLER_SKU", "SKU-DUP")]},
    ]
    _install_api(
        monkeypatch,
        search={"results": ["MLB100"], "paging": {"total": 1}},
        items={"MLB100": item},
        variations={"MLB100": (duplicated, 200)},
    )

    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-DUP")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "sku_identity_ambiguous"
    assert "pai ou variacao" in exc_info.value.detail["message"]


def test_ambiguous_listing_makes_mixed_result_incomplete_with_warning(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    items = {
        "MLB100": {"id": "MLB100", "seller_id": "111", "seller_sku": "SKU-DUP", "status": "active"},
        "MLB200": {
            "id": "MLB200", "seller_id": "111", "status": "paused",
            "variations": [{"id": "10"}, {"id": "20"}],
        },
    }
    duplicated = [
        {"id": "10", "attributes": [_attribute("SELLER_SKU", "SKU-DUP")]},
        {"id": "20", "attributes": [_attribute("SELLER_SKU", "SKU-DUP")]},
    ]
    _install_api(
        monkeypatch,
        search={"results": ["MLB100", "MLB200"], "paging": {"total": 2}},
        items=items,
        variations={"MLB200": (duplicated, 200)},
    )

    result = service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-DUP")

    assert result["coverage_complete"] is False
    assert result["campos"]["mlb_ids"] == "MLB100"
    assert any("mais de uma identidade" in warning for warning in result["warnings"])


def test_unavailable_variation_detail_is_inconclusive_not_negative(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {"id": "MLB100", "seller_id": "111", "status": "active", "variations": [{"id": "10"}]}
    _install_api(
        monkeypatch,
        search={"results": ["MLB100"], "paging": {"total": 1}},
        items={"MLB100": item},
        variations={"MLB100": ({"error": "unavailable"}, 500)},
    )

    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-X")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["code"] == "variation_identity_incomplete"


@pytest.mark.parametrize(
    "search",
    [
        {"results": ["MLB100"], "paging": {"total": 3}},
        {"results": ["MLB100"]},
    ],
)
def test_incomplete_pagination_is_never_marked_complete(monkeypatch, search):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    item = {"id": "MLB100", "seller_id": "111", "seller_sku": "SKU-1", "status": "active"}
    _install_api(monkeypatch, search=search, items={"MLB100": item})

    result = service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-1")

    assert result["coverage_complete"] is False
    assert any("busca do Mercado Livre" in warning for warning in result["warnings"])


def test_repeated_search_page_is_not_complete(monkeypatch):
    ids = [f"MLB{index}" for index in range(100, 200)]
    calls = []

    def request(_client_id, _store_name, cfg, _method, _url, *, params=None, **_kwargs):
        calls.append(int((params or {}).get("offset") or 0))
        return FakeResponse({
            "results": ids,
            "paging": {"total": 200, "limit": 100, "offset": calls[-1]},
        }), cfg

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", request)
    found, _cfg, warnings, complete = service._buscar_ids_por_sku(
        "client-1",
        "Loja",
        {"user_id": "111"},
        "SKU-1",
        service.time.monotonic() + 10,
    )

    assert calls == [0, 100]
    assert len(found) == 100
    assert complete is False
    assert any("paginacao contraditoria" in warning for warning in warnings)


def test_missing_title_keeps_mlb_alignment_and_untrusted_photo_is_omitted(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    items = {
        "MLB100": {
            "id": "MLB100", "seller_id": "111", "seller_sku": "SKU-1", "status": "active",
            "title": "", "thumbnail": "https://example.test/untrusted.jpg",
        },
        "MLB200": {
            "id": "MLB200", "seller_id": "111", "seller_sku": "SKU-1", "status": "paused",
            "title": "Titulo secundario",
        },
    }
    _install_api(
        monkeypatch,
        search={"results": ["MLB100", "MLB200"], "paging": {"total": 2}},
        items=items,
    )

    result = service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-1")

    assert result["campos"]["mlb_ids"] == "MLB100|MLB200"
    assert result["campos"]["titulos_anuncios_mlb"] == "(sem titulo informado) || Titulo secundario"
    assert result["campos"]["foto"] == ""
    assert result["foto"] == {"url": "", "data_url": "", "filename": ""}


def test_timeout_maps_to_gateway_timeout_without_exposing_config(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])

    def timeout(*_args, **_kwargs):
        raise requests.exceptions.Timeout("token-secret-must-not-leak")

    monkeypatch.setattr(service.mercadolivre, "_ml_api_request", timeout)
    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-1")

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {
        "code": "ml_timeout",
        "message": "A consulta ao Mercado Livre excedeu o tempo limite.",
    }
    assert "token" not in str(exc_info.value.detail).lower()


def test_lookup_has_a_global_deadline(monkeypatch):
    _configure_store_runtime(monkeypatch, [_store("store-a", "111")])
    clock = iter([100.0, 100.0 + service.ML_LOOKUP_DEADLINE_SECONDS + 1])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        service.mercadolivre,
        "_ml_api_request",
        lambda *_args, **_kwargs: pytest.fail("consulta externa nao deve iniciar depois do deadline"),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.consultar_produto_mercado_livre("client-1", "store-a", "SKU-1")

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["code"] == "ml_lookup_deadline"


def test_photo_url_allowlist_rejects_non_ml_and_local_targets():
    assert service._photo_url_allowed("https://http2.mlstatic.com/item.jpg") is True
    assert service._photo_url_allowed("http://http2.mlstatic.com/item.jpg") is False
    assert service._photo_url_allowed("https://mlstatic.com.evil.test/item.jpg") is False
    assert service._photo_url_allowed("https://127.0.0.1/item.jpg") is False
    assert service._photo_url_allowed("https://http2.mlstatic.com:bad/item.jpg") is False


def test_photo_stream_is_closed_and_declared_mime_must_match(monkeypatch):
    class StreamResponse:
        def __init__(self, declared_mime):
            self.status_code = 200
            self.headers = {"Content-Type": declared_mime, "Content-Length": "3"}
            self.closed = False

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"\xff\xd8\xff"

        def close(self):
            self.closed = True

    first_response = StreamResponse("image/jpeg")
    mismatched = StreamResponse("image/png")
    responses = [first_response, mismatched]
    monkeypatch.setattr(service.requests, "get", lambda *_args, **_kwargs: responses.pop(0))

    first = service._download_photo_data_url("https://http2.mlstatic.com/item.jpg", "MLB100")
    assert first["data_url"].startswith("data:image/jpeg;base64,")
    assert first_response.closed is True

    with pytest.raises(ValueError, match="invalid_photo_content_type"):
        service._download_photo_data_url("https://http2.mlstatic.com/item.jpg", "MLB100")
    assert mismatched.closed is True

    slow = StreamResponse("image/jpeg")
    responses.append(slow)
    clock = iter([100.0, 101.0])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="photo_deadline_exceeded"):
        service._download_photo_data_url(
            "https://http2.mlstatic.com/item.jpg",
            "MLB100",
            deadline=100.5,
        )
    assert slow.closed is True


def test_router_exposes_only_get_lookup_route():
    router = create_cadastro_router()
    matches = [
        route
        for route in router.routes
        if getattr(route, "path", "") == "/api/cadastro/lojas/{store_id}/mercado-livre/produto"
    ]
    assert len(matches) == 1
    assert matches[0].methods == {"GET"}
