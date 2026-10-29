"""Monthly view HTTP integration using synthetic tenants and durable snapshots."""

import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.services import medias_compras_cache as cache
from backend.services import medias_compras_cache_disco as disk
from backend.services import medias_compras_common as common
from backend.services import medias_compras_fontes as fontes
from backend.services import medias_compras_visao as visao


def test_http_restart_scope_stale_refresh_and_authorization(monkeypatch, tmp_path):
    roots = {client: tmp_path / client for client in ("tenant-a", "tenant-b")}
    for root in roots.values():
        root.mkdir()
    calls = []
    entered, release = threading.Event(), threading.Event()
    slow = False

    def authorize(request: Request):
        client = request.headers.get("X-Tenant", "tenant-a")
        if client not in roots:
            raise HTTPException(403, "denied")
        return client

    def scope(_client, loja, store_id):
        identity = store_id or {"Nome Antigo": "store-a", "Loja A": "store-a"}.get(loja, "store-a")
        if identity not in {"store-a", "store-b"}:
            raise HTTPException(409, "store_scope_unresolved")
        return {"store_id": identity, "loja": "Loja A" if identity == "store-a" else "Loja B"}

    def calculate(**kwargs):
        calls.append((kwargs["client_id"], kwargs["meses"], kwargs["_escopo_validado"]["store_id"]))
        if slow:
            entered.set()
            assert release.wait(5), "Synthetic refresh was not released"
        return {"success": True, "periodo_meses": kwargs["meses"],
                "colunas_meses": [{"key": "2026-10", "label": "10/2026"}],
                "itens": [{"sku": "001", "titulo_anuncio": str(calls[-1]), "media_mensal": len(calls)}],
                "resumo": {"total_skus": 1}}

    monkeypatch.setattr(visao, "get_tenant_path", lambda client: str(roots[client]))
    monkeypatch.setattr(visao, "_resolver_escopo_loja_medias", scope)
    monkeypatch.setattr(visao, "_listar_bancos_vendas_tenant", lambda *_args: [], raising=False)
    monkeypatch.setattr(visao, "_api_medias_compras_visao_sync", calculate)
    monkeypatch.setattr(fontes, "extras_runtime", lambda _module: [])
    monkeypatch.setattr(cache, "resultados_medias", cache.ResultadoCache())
    app = FastAPI()
    app.dependency_overrides[common.get_tenant_id] = authorize
    app.add_api_route("/visao", visao.api_medias_compras_visao)
    url = "/visao?meses=6&store_id=store-a"

    def ready(client):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get(url + "&cache_mode=stale")
            assert response.status_code in (200, 202), response.text
            if response.status_code == 200 and response.json()["_cache"]["estado"] == "ready":
                return response
            time.sleep(.01)
        raise AssertionError("Synthetic refresh did not complete")

    with TestClient(app) as client:
        original = client.get(url)
        assert original.status_code == 200
        assert "_cache" not in original.json(), "Legacy response contract stays unchanged"
        assert len(calls) == 1
        assert len(list((roots["tenant-a"] / disk.DIRECTORY).glob("*.json"))) == 1

    # The endpoint is wired to a new in-memory service. Only disk can supply this hit.
    monkeypatch.setattr(cache, "resultados_medias", cache.ResultadoCache())
    with TestClient(app) as client:
        restored = client.get(url + "&cache_mode=stale")
        assert restored.status_code == 200
        data = restored.json()
        assert data["itens"] == original.json()["itens"]
        assert data["_cache"]["estado"] == "ready" and not data["_cache"]["stale"]
        assert len(calls) == 1, "Restart must not repeat the calculation when sources match"
        renamed = client.get("/visao?meses=6&loja=Nome%20Antigo&cache_mode=stale")
        assert renamed.json()["itens"] == data["itens"] and len(calls) == 1
        assert client.get(url + "&cache_mode=stale", headers={"X-Tenant": "denied"}).status_code == 403
        assert client.get("/visao?meses=6&store_id=forbidden&cache_mode=stale").status_code == 409
        assert client.get("/visao?meses=4&cache_mode=stale").status_code == 400
        assert len(calls) == 1
        assert client.get("/visao?meses=3&store_id=store-a").json()["periodo_meses"] == 3
        assert client.get("/visao?meses=6&store_id=store-b").status_code == 200
        other = client.get(url, headers={"X-Tenant": "tenant-b"}).json()
        assert other["itens"] != data["itens"]
        assert len(calls) == 4

        # A changed stock source triggers background refresh while the prior result is delivered.
        (roots["tenant-a"] / "produtos_compilado.csv").write_text("sku,estoque\n001,12\n")
        slow = True
        stale = client.get(url + "&cache_mode=stale")
        assert stale.status_code == 200
        assert stale.json()["itens"] == original.json()["itens"]
        assert stale.json()["_cache"]["stale"] and stale.json()["_cache"]["atualizando"]
        assert entered.wait(3)
        release.set()
        current = ready(client)
        assert current.status_code == 200
        assert current.json()["itens"] != original.json()["itens"]
        assert not current.json()["_cache"]["stale"]
        assert len(calls) == 5, "Polling must share the in-flight refresh"

    monkeypatch.setattr(cache, "resultados_medias", cache.ResultadoCache())
    with TestClient(app) as client:
        updated = client.get(url + "&cache_mode=stale")
        assert updated.json()["itens"] == current.json()["itens"] and len(calls) == 5
        # Revoked source authority must hide the stored snapshot even across service restart.
        (roots["tenant-a"] / "lojas_public_snapshot.json").write_text('{"generation":"new-policy"}')
        entered.clear()
        release.clear()
        try:
            pending = client.get(url + "&cache_mode=stale")
            assert pending.status_code == 202 and pending.json()["dados"] is None
            assert "itens" not in pending.json()
        finally:
            release.set()
            assert ready(client).status_code == 200
