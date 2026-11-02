import copy
from urllib.parse import urlsplit

import pytest
import requests
from fastapi import HTTPException

from backend.services import importacoes_sku_anuncios as service


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return copy.deepcopy(self.body)


def product(item_id="MLB1000000001", **overrides):
    return {"id": item_id, "seller_id": "seller-a", "site_id": "MLB", "status": "active",
            "currency_id": "BRL", "price": 50.0,
            "permalink": "https://produto.mercadolivre.com.br/" + item_id.replace("MLB", "MLB-") + "-produto-_JM",
            "attributes": [{"id": "SELLER_SKU", "value_name": "35", "values": [{"name": "35"}]}],
            "variations": [], **overrides}


@pytest.fixture
def env(monkeypatch):
    state = {"client": "tenant-a", "store": "store-a", "seller": "seller-a", "name": "Carlos José",
             "ids": ["MLB1000000001"], "items": {}, "calls": [], "cfg_calls": [],
             "request": None, "revoke_at": None, "cfg_change_at": None}

    def rows(client):
        assert client == state["client"]
        return [{"store_id": state["store"], "nome": state["name"], "integracoes": {
            "mercadolivre": {"user_id": state["seller"], "site_id": "MLB", "connected": True}}}]

    def config(client, name, store_id, **kwargs):
        state["cfg_calls"].append((client, name, store_id, kwargs))
        assert (client, name, store_id) == (state["client"], state["name"], state["store"])
        assert kwargs == {"seller_id": state["seller"], "site_id": "MLB", "require_name_match": True}
        if state["revoke_at"] and len(state["cfg_calls"]) >= state["revoke_at"]:
            raise HTTPException(403, "A autorização foi revogada.")
        return {"_store_id_context": store_id, "user_id": "foreign" if state["cfg_change_at"] == len(state["cfg_calls"]) else state["seller"],
                "site_id": "MLB", "access_token": "synthetic-token"}

    def request(client, name, cfg, method, url, *, params, timeout):
        path = urlsplit(url).path
        state["calls"].append((client, name, path, params))
        assert method == "GET" and timeout == 12
        assert client == state["client"] and name == state["name"]
        assert cfg["_store_id_context"] == state["store"] and cfg["user_id"] == state["seller"]
        if state["request"]:
            custom = state["request"](path, params, cfg)
            if custom is not None:
                return custom, cfg
        if path.endswith("/items/search"):
            assert path == "/users/" + state["seller"] + "/items/search"
            assert params["seller_sku"] == "35" and params["status"] == "active"
            assert "q" not in params
            offset, limit = params["offset"], params["limit"]
            return Response({"results": state["ids"][offset:offset + limit],
                             "paging": {"total": len(state["ids"]), "offset": offset, "limit": limit}}), cfg
        item_id = path.rsplit("/", 1)[-1]
        assert params == {"include_attributes": "all"}
        return Response(state["items"].get(item_id, product(item_id, seller_id=state["seller"]))), cfg

    monkeypatch.setattr(service.integracoes, "ler_lojas", rows)
    monkeypatch.setattr(service, "obter_cfg_ml_snapshot", config)
    monkeypatch.setattr(service, "_ml_api_request", request)
    return state


def lookup(state, **list_overrides):
    return service.buscar_anuncios_sku_da_lista(state["client"],
        {"store_id": state["store"], "loja": "nome antigo", "itens": [{"SKU": "35"}], **list_overrides}, "35")


def error_code(call, status, code):
    with pytest.raises(HTTPException) as caught:
        call()
    assert caught.value.status_code == status
    assert caught.value.detail["code"] == code


def test_selected_store_and_current_exact_sku_supply_own_ad(env):
    result = lookup(env)
    assert result["store_id"] == "store-a" and result["loja"] == "Carlos José"
    assert result["anuncios"] == [{"item_id": "MLB1000000001", "preco": 50,
        "url": product()["permalink"], "seller_id": "seller-a", "site_id": "MLB", "sku": "35"}]
    assert result["total_encontrado"] == 1 and result["truncado"] is False
    assert len(env["cfg_calls"]) == 3


@pytest.mark.parametrize("sku", ["350", "035", "35-1", "35;350", "35,35", "", None])
def test_literal_sku_never_matches_alias_title_prefix_or_compound(sku):
    item = product(attributes=[], seller_custom_field=sku, title="Produto SKU 35")
    assert service.anuncio_corresponde_sku_exato(item, "35") is False


@pytest.mark.parametrize("variations,parent,expected", [
    ([{"seller_sku": "35"}], "35", True),
    ([{"seller_sku": "35"}, {"seller_sku": "35"}], None, True),
    ([{"seller_sku": "350"}], "35", False),
    ([{}], "35", False),
    ([{"seller_sku": "35"}, {}], "35", False),
    ([{"seller_sku": "35"}], "350", False),
    ({}, "35", False),
])
def test_variations_require_unambiguous_explicit_sku(variations, parent, expected):
    item = product(attributes=[], seller_custom_field=parent, variations=variations)
    assert service.anuncio_corresponde_sku_exato(item, "35") is expected


def test_conflicting_current_sku_attributes_are_rejected():
    item = product()
    item["attributes"].append({"id": "SELLER_SKU", "value_name": "350"})
    assert not service.anuncio_corresponde_sku_exato(item, "35")


def test_current_sku_attribute_overrides_old_custom_field_261():
    assert service.anuncio_corresponde_sku_exato(product(seller_custom_field="261"), "35")


@pytest.mark.parametrize("legacy,expected", [("35", True), ("261", False), ("035", False)])
def test_without_current_attribute_legacy_sku_must_match_literally(legacy, expected):
    assert service.anuncio_corresponde_sku_exato(product(attributes=[], seller_custom_field=legacy), "35") is expected


@pytest.mark.parametrize("field,value", [("seller_id", "foreign"), ("site_id", "MLA"), ("id", "MLB9999999999")])
def test_wrong_account_site_or_returned_id_cannot_supply_an_own_ad(env, field, value):
    env["items"][env["ids"][0]] = product(**{field: value})
    error_code(lambda: lookup(env), 409, "sku_ads_item_identity_mismatch")


@pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf"), True, "missing"])
def test_absent_or_invalid_price_is_not_invented(env, price):
    env["items"][env["ids"][0]] = product(price=price)
    assert lookup(env)["anuncios"] == []


@pytest.mark.parametrize("url", [None, "https://foreign.test/MLB1000000001", "https://produto.mercadolivre.com.br/MLB-9999999999-produto-_JM"])
def test_permalink_must_come_from_the_same_actual_item(env, url):
    env["items"][env["ids"][0]] = product(permalink=url)
    assert lookup(env)["anuncios"] == []


def test_active_sku_mismatch_is_excluded_without_title_fallback(env):
    env["items"][env["ids"][0]] = product(attributes=[], seller_sku="350", title="SKU 35")
    assert lookup(env)["anuncios"] == []


def test_exact_variations_with_conflicting_prices_are_unavailable(env):
    env["items"][env["ids"][0]] = product(variations=[{"seller_sku": "35", "price": 55}])
    assert lookup(env)["anuncios"] == []


def test_pagination_and_more_than_five_results_are_explicit(env, monkeypatch):
    monkeypatch.setattr(service, "TAMANHO_PAGINA", 2)
    env["ids"] = ["MLB100000000" + str(number) for number in (6, 4, 2, 5, 3, 1)]
    result = lookup(env)
    assert [ad["item_id"] for ad in result["anuncios"]] == sorted(env["ids"])[:5]
    assert result["total_encontrado"] == result["total_candidatos"] == 6
    assert result["truncado"] is True and result["limite"] == 5
    searches = [call for call in env["calls"] if call[2].endswith("/search")]
    assert [call[3]["offset"] for call in searches] == [0, 2, 4]


@pytest.mark.parametrize("paging,results,status,code", [
    ({"total": 101, "offset": 0, "limit": 50}, [], 503, "sku_ads_search_limit"),
    ({"total": 1, "offset": 5, "limit": 50}, [], 502, "sku_ads_invalid_paging"),
    ({"total": 1, "offset": 0, "limit": 50}, [], 409, "sku_ads_catalog_changed"),
    ({}, [], 502, "sku_ads_invalid_paging"),
])
def test_paging_limits_and_incomplete_metadata_are_not_silently_accepted(env, paging, results, status, code):
    env["request"] = lambda path, params, cfg: Response({"paging": paging, "results": results})
    error_code(lambda: lookup(env), status, code)


def test_empty_search_is_a_valid_empty_result(env):
    env["ids"] = []
    assert lookup(env)["anuncios"] == []
    assert len(env["calls"]) == 1


def test_ad_removed_after_search_is_not_invented(env):
    env["request"] = lambda path, params, cfg: Response({}, 404) if path.startswith("/items/") else None
    assert lookup(env)["anuncios"] == []


@pytest.mark.parametrize("failure,status,code", [
    (requests.Timeout("private-token"), 504, "sku_ads_timeout"),
    (requests.ConnectionError("private-token"), 503, "sku_ads_network_unavailable"),
    (Response({}, 403), 403, "sku_ads_access_denied"),
    (Response({}, 429), 503, "sku_ads_rate_limited"),
    (Response({}, 500), 503, "sku_ads_unavailable"),
    (Response(ValueError("private-token")), 502, "sku_ads_invalid_response"),
])
def test_unavailable_api_has_sanitized_informative_error(env, failure, status, code):
    def request(*_args):
        if isinstance(failure, Exception):
            raise failure
        return failure
    env["request"] = request
    error_code(lambda: lookup(env), status, code)


def test_authorization_revoked_after_network_does_not_publish_ads(env):
    env["revoke_at"] = 3
    with pytest.raises(HTTPException) as caught:
        lookup(env)
    assert caught.value.status_code == 403
    assert len(env["calls"]) == 2


def test_identity_changed_between_search_and_detail_is_rejected(env):
    env["cfg_change_at"] = 2
    error_code(lambda: lookup(env), 409, "sku_ads_identity_changed")
    assert len(env["calls"]) == 1


def test_tenant_and_store_never_reuse_another_own_ad_snapshot(env):
    assert lookup(env)["anuncios"][0]["preco"] == 50
    env.update(client="tenant-b", store="store-b", seller="seller-b", name="Loja B")
    env["items"][env["ids"][0]] = product(seller_id="seller-b", price=70)
    result = lookup(env)
    assert result["store_id"] == "store-b" and result["anuncios"][0]["seller_id"] == "seller-b"
    assert result["anuncios"][0]["preco"] == 70
    error_code(lambda: lookup(env, store_id="store-a"), 403, "sku_ads_store_not_authorized")


def test_store_id_and_list_membership_are_required_before_api(env):
    error_code(lambda: lookup(env, store_id=""), 409, "sku_ads_store_required")
    error_code(lambda: lookup(env, itens=[{"SKU": "350"}]), 404, "sku_ads_sku_not_in_list")
    assert env["calls"] == []
