"""Ad ownership comes from currently authorized seller/site identities."""

import copy
import json

import pytest
import requests

from backend.services import medias_compras_listas as service
from backend.services.promocoes_core_analise import _calcular_margem_liquida_ml


def _store(name, store_id, seller, site="MLB"):
    return {"nome": name, "store_id": store_id, "integracoes": {"mercadolivre": {
        "user_id": seller, "site_id": site, "access_token": "synthetic-token", "connected": True,
    }}}


def _response(status, *, item_id="MLB111", seller="222", site="MLB"):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"id": item_id, "seller_id": seller, "site_id": site,
                                    "shipping": {"mode": "me2"}}).encode()
    return response


class _Harness:
    def __init__(self):
        self.stores = [_store("Carlos", "store-carlos", "111"), _store("JK", "store-jk", "222")]
        self.calls = []
        self.statuses = {"Carlos": 403, "JK": 200}
        self.payload = {}
        self.payloads = {}

    def dependencies(self):
        def stores(client):
            assert client == "tenant-a"
            self.calls.append(("stores", client))
            return copy.deepcopy(self.stores)

        def configuration(client, name, store_id=None):
            assert client == "tenant-a"
            self.calls.append(("cfg", client, name, store_id))
            matches = [row for row in self.stores if row["nome"] == name
                       and (store_id is None or row["store_id"] == store_id)]
            assert len(matches) == 1
            return {**matches[0]["integracoes"]["mercadolivre"], "_store_id_context": matches[0]["store_id"]}

        def request(client, name, cfg, method, url, **kwargs):
            assert client == "tenant-a" and method == "GET"
            self.calls.append(("item", name, cfg.get("_store_id_context"), url, kwargs))
            status = self.statuses.get(name, 200)
            if isinstance(status, Exception):
                raise status
            item_id = url.rsplit("/", 1)[-1]
            return _response(status, **{"item_id": item_id, **self.payload, **self.payloads.get(item_id, {})}), cfg

        def costs(client, name, *identity):
            assert client == "tenant-a"
            self.calls.append(("costs", name, *identity))
            return {"001": 60 if name == "JK" else 90}, {"001": .1}

        def fee(client, name, cfg, item):
            self.calls.append(("fee", name, cfg.get("_store_id_context")))
            return {"ad_cost": item["price"] * .15, "ad_cost_exact_for_price": True,
                    "ad_cost_price_context": item["price"]}, cfg

        def shipping(client, name, cfg, item_id, shipping, **kwargs):
            self.calls.append(("shipping", name, cfg.get("_store_id_context")))
            return {"shipping_cost": 8, "shipping_exact_for_price": True,
                    "shipping_price_context": kwargs["contexto_frete"]["item_price"]}, cfg

        return {"carregar_lojas": stores, "obter_cfg_ml": configuration, "ml_api_request": request,
                "carregar_custos_impostos": costs, "ml_obter_taxas_anuncio": fee,
                "ml_obter_frete_detalhado": shipping, "ml_contexto_frete_item": lambda item, price: {"item_price": price},
                "resolver_custo": lambda costs, sku: costs.get(sku),
                "resolver_imposto": lambda taxes, sku: taxes.get(sku), "calcular_margem": _calcular_margem_liquida_ml}


def _calculate(harness, monkeypatch, *, context=None, prices=None, ads=None, list_store="Carlos"):
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", harness.dependencies)
    return service._calcular_margens_concorrentes_promocoes(
        "tenant-a", list_store, "001", prices or {"concorrente_1": 100},
        ads or {"concorrente_1": "MLB111"}, contexto_consulta=context,
    )


def test_list_store_403_does_not_hide_own_ad_from_another_authorized_store(monkeypatch):
    harness = _Harness()
    result = _calculate(harness, monkeypatch)["concorrente_1"]
    assert result["financeiro_exato"] is True
    assert result["loja"] == "JK"
    assert result["custo_unitario"] == 60
    assert result["margem_percentual"] == 7
    assert ("fee", "JK", "store-jk") in harness.calls
    assert ("shipping", "JK", "store-jk") in harness.calls
    assert harness.calls.count(("stores", "tenant-a")) == 1
    assert ("costs", "JK", "store-jk", "001") in harness.calls
    assert {call[3] for call in harness.calls if call[0] == "cfg"} == {"store-carlos", "store-jk"}


@pytest.mark.parametrize("owner_status", [200, 401, 403])
def test_public_item_from_other_store_requires_owner_authenticated_confirmation(monkeypatch, owner_status):
    harness = _Harness()
    harness.statuses = {"Carlos": 200, "JK": owner_status}
    result = _calculate(harness, monkeypatch)["concorrente_1"]
    assert [call[1] for call in harness.calls if call[0] == "item"] == ["Carlos", "JK"]
    assert result["financeiro_exato"] is (owner_status == 200)
    if owner_status != 200:
        assert not any(call[0] in {"costs", "fee", "shipping"} for call in harness.calls)
        assert f"HTTP {owner_status}" in result["motivo_indisponivel"]


@pytest.mark.parametrize("payload", [{"item_id": "MLB999"}, {"site": "MLC"}, {"site": ""}, {"seller": "999"}, {"seller": ""}])
def test_wrong_item_site_or_unowned_seller_never_enters_financial_calculation(monkeypatch, payload):
    harness = _Harness()
    harness.payload = payload
    result = _calculate(harness, monkeypatch)["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert result["margem_percentual"] is None
    assert not any(call[0] in {"costs", "fee", "shipping"} for call in harness.calls)


@pytest.mark.parametrize("unavailable", ["removed", "oauth_invalid", "disconnected", "provider_unavailable", "missing_site", "wrong_site", "missing_store_id"])
def test_only_current_authorized_store_candidates_are_consulted(monkeypatch, unavailable):
    harness = _Harness()
    owner = harness.stores[1]
    if unavailable == "removed":
        harness.stores.pop()
    elif unavailable == "oauth_invalid":
        owner["integracoes"]["mercadolivre"]["oauth_invalid"] = True
    elif unavailable == "disconnected":
        owner["integracoes"]["mercadolivre"]["connected"] = False
    elif unavailable == "provider_unavailable":
        owner["_unavailable_providers"] = ["mercadolivre"]
    elif unavailable == "missing_site":
        owner["integracoes"]["mercadolivre"].pop("site_id")
    elif unavailable == "wrong_site":
        owner["integracoes"]["mercadolivre"]["site_id"] = "MLC"
    else:
        owner.pop("store_id")
    result = _calculate(harness, monkeypatch)["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert not any(call[0] in {"cfg", "item"} and call[2 if call[0] == "cfg" else 1] == "JK" for call in harness.calls)
    assert not any(call[0] == "fee" for call in harness.calls)


@pytest.mark.parametrize("changed", ["_store_id_context", "user_id", "site_id", "access_token"])
def test_current_configuration_must_match_authorized_store_seller_and_site(monkeypatch, changed):
    harness = _Harness()
    deps = harness.dependencies()
    original = deps["obter_cfg_ml"]

    def changed_configuration(client, name, **kwargs):
        cfg = original(client, name, **kwargs)
        if name == "JK":
            cfg[changed] = "" if changed == "access_token" else "different-identity"
        return cfg

    deps["obter_cfg_ml"] = changed_configuration
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", lambda: deps)
    result = service._calcular_margens_concorrentes_promocoes(
        "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
    )["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert not any(call[0] == "item" and call[1] == "JK" for call in harness.calls)


@pytest.mark.parametrize("ambiguity", ["seller_site", "store_id"])
def test_ambiguous_authorized_account_identity_is_rejected(monkeypatch, ambiguity):
    harness = _Harness()
    duplicate = _store("Another account", "another-store", "222")
    if ambiguity == "store_id":
        duplicate["store_id"] = "store-jk"
    harness.stores.append(duplicate)
    result = _calculate(harness, monkeypatch)["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert not any(call[0] == "fee" for call in harness.calls)


def test_one_authorized_account_snapshot_and_owner_cache_serve_both_price_series(monkeypatch):
    harness = _Harness()
    context = {}
    ads = {"concorrente_1": "MLB111", "concorrente_2": "MLB222", "concorrente_3": "MLB111"}
    rival = _calculate(harness, monkeypatch, context=context, ads=ads,
                       prices={"concorrente_1": 100, "concorrente_2": 110, "concorrente_3": 100})
    own = _calculate(harness, monkeypatch, context=context, ads=ads,
                     prices={"concorrente_1": 120, "concorrente_2": 130, "concorrente_3": 120})
    assert all(row["financeiro_exato"] for row in [*rival.values(), *own.values()])
    assert harness.calls.count(("stores", "tenant-a")) == 1
    assert len([call for call in harness.calls if call[0] == "cfg"]) == 2
    assert len([call for call in harness.calls if call[0] == "item"]) == 4
    assert len([call for call in harness.calls if call[0] == "costs"]) == 1
    assert len([call for call in harness.calls if call[0] == "fee"]) == 4


def test_each_ad_uses_its_confirmed_owner_even_with_multiple_accounts(monkeypatch):
    harness = _Harness()
    harness.statuses = {"Carlos": 200, "JK": 200}
    harness.payloads = {"MLB111": {"seller": "222"}, "MLB222": {"seller": "111"}}
    result = _calculate(harness, monkeypatch, ads={"concorrente_1": "MLB111", "concorrente_2": "MLB222"},
                        prices={"concorrente_1": 100, "concorrente_2": 100})
    assert result["concorrente_1"]["loja"] == "JK" and result["concorrente_1"]["margem_percentual"] == 7
    assert result["concorrente_2"]["loja"] == "Carlos" and result["concorrente_2"]["margem_percentual"] == -23
    assert result["concorrente_1"]["store_id"] == "store-jk"
    assert result["concorrente_2"]["store_id"] == "store-carlos"


@pytest.mark.parametrize("stage", ["item", "fee", "shipping"])
def test_refresh_cannot_switch_account_identity_during_financial_calculation(monkeypatch, stage):
    harness = _Harness()
    deps = harness.dependencies()
    dependency = {"item": "ml_api_request", "fee": "ml_obter_taxas_anuncio", "shipping": "ml_obter_frete_detalhado"}[stage]
    original = deps[dependency]

    def changed(*args, **kwargs):
        payload, cfg = original(*args, **kwargs)
        return payload, {**cfg, "user_id": "999"}

    deps[dependency] = changed
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", lambda: deps)
    result = service._calcular_margens_concorrentes_promocoes(
        "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
    )["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert result["margem_percentual"] is None


def test_timeout_is_sanitized_and_does_not_authorize_financial_fallback(monkeypatch):
    harness = _Harness()
    harness.statuses["JK"] = requests.Timeout("synthetic-private-token-and-path")
    result = _calculate(harness, monkeypatch)["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert "Tempo limite" in result["motivo_indisponivel"]
    assert "synthetic-private" not in str(result)
    assert not any(call[0] == "fee" for call in harness.calls)


@pytest.mark.parametrize("changed", [{"item_id": "MLB999"}, {"seller": "999"}, {"site": "MLC"}])
def test_owner_confirmation_cannot_replace_the_public_item_identity(monkeypatch, changed):
    harness = _Harness()
    harness.statuses = {"Carlos": 200, "JK": 200}
    deps = harness.dependencies()
    original = deps["ml_api_request"]

    def request(client, name, cfg, *args, **kwargs):
        response, cfg = original(client, name, cfg, *args, **kwargs)
        return (_response(200, **changed) if name == "JK" else response), cfg

    deps["ml_api_request"] = request
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", lambda: deps)
    result = service._calcular_margens_concorrentes_promocoes(
        "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
    )["concorrente_1"]
    assert result["financeiro_exato"] is False
    assert not any(call[0] == "fee" for call in harness.calls)


def test_owner_cache_is_not_authority_for_another_tenant(monkeypatch):
    harness = _Harness()
    deps = harness.dependencies()
    original = deps["carregar_lojas"]
    deps["carregar_lojas"] = lambda client: original(client) if client == "tenant-a" else []
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", lambda: deps)
    shared_context = {}
    arguments = ("Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"})
    own = service._calcular_margens_concorrentes_promocoes("tenant-a", *arguments, contexto_consulta=shared_context)
    foreign = service._calcular_margens_concorrentes_promocoes("tenant-b", *arguments, contexto_consulta=shared_context)
    assert own["concorrente_1"]["financeiro_exato"] is True
    assert foreign["concorrente_1"]["financeiro_exato"] is False
    assert len([call for call in harness.calls if call[0] == "fee"]) == 1


def test_owner_401_uses_canonical_refresh_and_retries_the_exact_store(monkeypatch):
    from backend.services import mercadolivre_legacy_api as ml

    harness = _Harness()
    harness.stores = [harness.stores[1]]
    deps = harness.dependencies()
    deps["ml_api_request"] = ml._ml_api_request
    attempts = []
    refreshed = []

    def request(client, name, token, method, url, **kwargs):
        attempts.append((client, name, method, url))
        return _response(401 if len(attempts) == 1 else 200)

    def refresh(client, name, cfg):
        refreshed.append((client, name, cfg["_store_id_context"]))
        return {**cfg, "access_token": "synthetic-renewed-token"}

    monkeypatch.setattr(ml, "_ml_http_request", request, raising=False)
    monkeypatch.setattr(ml, "_ml_refresh_token", refresh)
    monkeypatch.setattr(ml, "_ml_http_invalidar_session", lambda *args: None, raising=False)
    monkeypatch.setattr(service, "_dependencias_margens_concorrentes", lambda: deps)
    result = service._calcular_margens_concorrentes_promocoes(
        "tenant-a", "Carlos", "001", {"concorrente_1": 100}, {"concorrente_1": "MLB111"},
    )["concorrente_1"]
    assert result["financeiro_exato"] is True
    assert refreshed == [("tenant-a", "JK", "store-jk")]
    assert attempts == [("tenant-a", "JK", "GET", "https://api.mercadolibre.com/items/MLB111")] * 2
