"""HTTP contracts and scope checks around the real detail enrichment fixture."""

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.services import medias_compras_common as common
from backend.services import medias_compras_listas as listas
from test_medias_compras_carregamento import ambiente


def _ready(client, headers=None):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get("/listas/lista?cache_mode=stale", headers=headers)
        assert response.status_code in (200, 202), response.text
        payload = response.json()
        if response.status_code == 200 and payload["_cache"]["estado"] == "ready":
            return payload
        time.sleep(.01)
    raise AssertionError("fixture did not complete")


def test_http_opt_in_cold_warm_auth_edit_delete_and_legacy(ambiente):
    app = FastAPI()
    auth_calls = []
    def authorize(request: Request):
        tenant = request.headers.get("X-Tenant", "cliente-a")
        auth_calls.append(tenant)
        if tenant not in {"cliente-a", "cliente-b"}:
            raise HTTPException(403, "denied")
        return tenant
    app.dependency_overrides[common.get_tenant_id] = authorize
    app.add_api_route("/listas/{lista_id}", listas.api_medias_compras_lista_pedido_detalhe)
    with TestClient(app) as client:
        first = client.get("/listas/lista?cache_mode=stale")
        assert first.status_code == 202
        assert first.json()["dados"] is None
        assert set(first.json()["_cache"]) == {"estado", "gerado_em", "versao", "stale", "atualizando", "retry_after_ms", "erro", "autoridade", "politica_fotos", "pendente_fontes", "completo"}
        ready = _ready(client)
        calls_before = ambiente["counts"]["contexto_catalogo"]
        repeated = _ready(client)
        assert repeated == ready
        assert ambiente["counts"]["contexto_catalogo"] == calls_before
        assert client.get("/listas/lista?cache_mode=stale", headers={"X-Tenant": "denied"}).status_code == 403
        second_tenant = _ready(client, {"X-Tenant": "cliente-b"})
        assert second_tenant["lista"]["itens"][0]["Foto"] == ambiente["refs"]["cliente-b", "store-a"]
        ambiente["lists"]["cliente-a"][0]["itens"][0]["Quantidade"] = 7
        edited = client.get("/listas/lista?cache_mode=stale")
        assert edited.status_code == 202 and edited.json()["dados"] is None
        updated = _ready(client)
        assert updated["lista"]["itens"][0]["Quantidade"] == 7
        legacy = client.get("/listas/lista").json()
        assert "_cache" not in legacy
        assert legacy["lista"] == updated["lista"]
        ambiente["lists"]["cliente-a"].clear()
        assert client.get("/listas/lista?cache_mode=stale").status_code == 404
    assert "denied" in auth_calls and len(auth_calls) >= 8


def test_visao_canonical_store_key_months_and_invalid_scope(monkeypatch, tmp_path):
    from backend.services import medias_compras_visao as visao
    calls = []
    def scope(_client, loja, store_id):
        if store_id == "forbidden":
            raise HTTPException(409, "store_scope_unresolved")
        return {"store_id": "store-a", "loja": "Loja Atual"}
    def load(**kwargs):
        calls.append(kwargs)
        return {"success": True, "periodo_meses": kwargs["meses"], "itens": [{"SKU": "001"}]}
    monkeypatch.setattr(visao, "get_tenant_path", lambda _client: str(tmp_path))
    monkeypatch.setattr(visao, "_resolver_escopo_loja_medias", scope)
    monkeypatch.setattr(visao, "_listar_bancos_vendas_tenant", lambda *_a: [], raising=False)
    monkeypatch.setattr(visao, "_api_medias_compras_visao_sync", load)
    app = FastAPI()
    app.dependency_overrides[common.get_tenant_id] = lambda: "client"
    app.add_api_route("/visao", visao.api_medias_compras_visao)
    with TestClient(app) as client:
        # Legacy publishes and returns the original payload shape.
        first = client.get("/visao?meses=6&store_id=store-a").json()
        assert "_cache" not in first and len(calls) == 1
        same = client.get("/visao?meses=6&loja=Nome%20Antigo&cache_mode=stale")
        assert same.status_code == 200 and same.json()["_cache"]["estado"] == "ready"
        assert len(calls) == 1
        different = client.get("/visao?meses=3&store_id=store-a").json()
        assert different["periodo_meses"] == 3 and len(calls) == 2
        assert client.get("/visao?meses=4&cache_mode=stale").status_code == 400
        assert client.get("/visao?store_id=forbidden&cache_mode=stale").status_code == 409
        assert client.get("/visao?cache_mode=unexpected").status_code == 422


def test_saved_opt_in_returns_current_authority_without_full_calculation(ambiente):
    app = FastAPI()
    app.dependency_overrides[common.get_tenant_id] = lambda: "cliente-a"
    app.add_api_route("/listas/{lista_id}", listas.api_medias_compras_lista_pedido_detalhe)
    with TestClient(app) as client:
        response = client.get("/listas/lista?somente_salvos=true&cache_mode=stale")
        assert response.status_code == 200
        metadata = response.json()["_cache"]
        assert metadata["estado"] == "ready" and metadata["completo"] is False
        assert metadata["autoridade"] and metadata["politica_fotos"]
        assert metadata["pendente_fontes"] is False
        assert not ambiente["counts"]
        ambiente["lists"]["cliente-a"][0]["itens"][0]["Quantidade"] = 7
        changed = client.get("/listas/lista?somente_salvos=true&cache_mode=stale").json()["_cache"]
        assert changed["autoridade"] != metadata["autoridade"]
        assert changed["politica_fotos"] == metadata["politica_fotos"]
        tenant = ambiente["root"] / "cliente-a"
        journal = tenant / "_stores_publication" / "transaction.json"
        journal.parent.mkdir(exist_ok=True)
        journal.write_text("{}")
        pending = client.get("/listas/lista?somente_salvos=true&cache_mode=stale")
        assert pending.status_code == 202
        assert pending.json()["dados"] is None
        assert pending.json()["_cache"]["pendente_fontes"] is True
        assert not ambiente["counts"]
