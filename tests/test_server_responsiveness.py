"""Exercise the production wrappers over a real local Uvicorn HTTP server."""

import asyncio
import contextvars
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import anyio
import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Header

from backend.routers.frontend import FrontendRouterConfig, create_frontend_router
from backend.routers.infra import InfraRouterConfig, create_infra_router
from backend.routers.medias_compras import create_medias_compras_router
from backend.services import cadastro_listagem, integracoes_api
from backend.services import medias_compras_common, medias_compras_listas
from backend.services.blocking_workers import heavy_worker_limiter, run_heavy


_TENANT = contextvars.ContextVar("responsiveness_test_tenant", default="")


@contextmanager
def _uvicorn_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, loop="asyncio", ws="none",
        lifespan="off", log_level="critical", access_log=False,
        timeout_graceful_shutdown=3,
    ))
    worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started, "Uvicorn did not start"
        yield f"http://127.0.0.1:{port}", worker.ident
    finally:
        server.should_exit = True
        worker.join(5)
        listener.close()
        assert not worker.is_alive(), "Uvicorn did not stop"


def test_health_html_auth_and_stores_respond_during_50_second_import_workers(tmp_path, monkeypatch):
    html_names = ("dashboard", "vendas", "perguntas_pos_venda", "importacoes_lista")
    static = tmp_path / "static"
    static.mkdir()
    for name in html_names:
        (static / f"{name}.html").write_text(f"<html>{name}</html>", encoding="utf-8")

    app = FastAPI()

    async def tenant(x_test_tenant: str = Header(default="tenant-a")):
        _TENANT.set(x_test_tenant)
        return x_test_tenant

    app.dependency_overrides[medias_compras_common.get_tenant_id] = tenant
    app.dependency_overrides[cadastro_listagem.get_tenant_id] = tenant
    app.dependency_overrides[integracoes_api.get_tenant_id] = tenant
    app.include_router(create_medias_compras_router())
    app.add_api_route("/api/cadastro/produtos", cadastro_listagem.listar_produtos_cadastro)
    app.add_api_route("/api/lojas", integracoes_api.get_lojas)
    app.include_router(create_frontend_router(FrontendRouterConfig(base_dir=str(tmp_path))))
    app.include_router(create_infra_router(InfraRouterConfig(
        app_version=lambda: "test", firebase_configured=lambda: False,
        firebase_active=lambda: False, firebase_live_features=lambda: False,
        firebase_last_error=lambda: "", minimum_app_version=lambda: "",
        base_dir=str(tmp_path), pasta_info=str(tmp_path / "info"),
    )))

    @app.get("/test-auth-sync")
    def auth_sync():
        return {"ready": True}

    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    starts = []
    active = 0
    maximum = 0

    def slow_import(*args, client_id="", **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            starts.append((client_id, _TENANT.get(), threading.get_ident(), time.monotonic()))
            if len(starts) >= 2:
                entered.set()
        try:
            assert release.wait(65), "Test workers were not released"
            return {"success": True, "client_id": client_id}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(medias_compras_listas, "_api_medias_compras_lista_pedido_detalhe_sync", slow_import)
    monkeypatch.setattr(medias_compras_listas, "_api_medias_compras_lista_pedido_custo_posto_sync", slow_import)
    monkeypatch.setattr(cadastro_listagem, "_listar_produtos_cadastro_sync", slow_import)
    monkeypatch.setattr(integracoes_api, "_get_lojas_sync", lambda client_id: [{"store_id": _TENANT.get(), "client_id": client_id}])

    with _uvicorn_server(app) as (base, loop_thread), ThreadPoolExecutor(max_workers=3) as clients:
        def get(path, client_id):
            with httpx.Client(timeout=75, trust_env=False) as client:
                return client.get(base + path, headers={"X-Test-Tenant": client_id})

        first = clients.submit(get, "/api/medias-compras/listas-pedidos/list-a", "tenant-a")
        second = clients.submit(get, "/api/medias-compras/listas-pedidos/list-b/landed-cost", "tenant-b")
        try:
            assert entered.wait(5), "Production import handlers did not enter the workers"
            third = clients.submit(get, "/api/cadastro/produtos", "tenant-c")
            blocked_until = time.monotonic() + 50
            probes = ("/health", *(f"/{name}.html" for name in html_names), "/test-auth-sync", "/api/lojas")
            with httpx.Client(timeout=1, trust_env=False) as client:
                while time.monotonic() < blocked_until:
                    for path in probes:
                        started = time.monotonic()
                        response = client.get(base + path, headers={"X-Test-Tenant": "tenant-free"})
                        assert response.status_code == 200, (path, response.text)
                        assert time.monotonic() - started < 1, path
                        if path == "/api/lojas":
                            assert response.json() == [{"store_id": "tenant-free", "client_id": "tenant-free"}]
                    with lock:
                        assert len(starts) == 2, "Queued request exceeded the two-worker limit"
                    assert not third.done()
                    time.sleep(.2)
        finally:
            release.set()
        responses = (first.result(5), second.result(5), third.result(5))

    assert all(response.status_code == 200 for response in responses)
    assert maximum == 2
    assert {row[0] for row in starts} == {"tenant-a", "tenant-b", "tenant-c"}
    assert all(client_id == context and thread != loop_thread for client_id, context, thread, _ in starts)
    assert min(row[3] for row in starts[2:]) - min(row[3] for row in starts[:2]) >= 50


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/cadastro/fornecedores", None),
    ("POST", "/api/cadastro/fornecedores", {"nome_empresa": "New synthetic supplier"}),
    ("PUT", "/api/cadastro/fornecedores/supplier-a", {"nome_empresa": "Updated synthetic supplier"}),
    ("DELETE", "/api/cadastro/fornecedores/supplier-a", None),
])
def test_health_and_html_respond_while_supplier_handler_waits_for_registry_lock(
    tmp_path, monkeypatch, method, path, payload,
):
    import json
    from backend.services import cadastro_fornecedores as suppliers

    tenant_dir = tmp_path / "tenant-a"
    tenant_dir.mkdir()
    registry = tenant_dir / "cadastro_fornecedores.json"
    registry.write_text(json.dumps({
        "schema": "jk.cadastro.fornecedores.v1",
        "fornecedores": [{"id": "supplier-a", "nome_empresa": "Synthetic supplier"}],
    }), encoding="utf-8")
    static = tmp_path / "static"
    static.mkdir()
    (static / "dashboard.html").write_text("<html>Supplier test dashboard</html>", encoding="utf-8")
    monkeypatch.setattr(suppliers, "get_tenant_path", lambda client_id: str(tmp_path / client_id))

    entered = threading.Event()
    supplier_lock = suppliers._FORNECEDORES_LOCK
    lock_waiters = []

    class ObservedLock:
        def __enter__(self):
            lock_waiters.append((threading.get_ident(), _TENANT.get()))
            entered.set()
            supplier_lock.acquire()
            return self

        def __exit__(self, *args):
            supplier_lock.release()

    monkeypatch.setattr(suppliers, "_FORNECEDORES_LOCK", ObservedLock())
    app = FastAPI()

    async def tenant():
        _TENANT.set("tenant-a")
        return "tenant-a"

    app.dependency_overrides[suppliers.get_tenant_id] = tenant
    for route_method, handler, route_path in (
        ("GET", suppliers.listar_fornecedores_cadastro, "/api/cadastro/fornecedores"),
        ("POST", suppliers.criar_fornecedor_cadastro, "/api/cadastro/fornecedores"),
        ("PUT", suppliers.atualizar_fornecedor_cadastro, "/api/cadastro/fornecedores/{fornecedor_id}"),
        ("DELETE", suppliers.excluir_fornecedor_cadastro, "/api/cadastro/fornecedores/{fornecedor_id}"),
    ):
        app.add_api_route(route_path, handler, methods=[route_method])
    app.include_router(create_frontend_router(FrontendRouterConfig(base_dir=str(tmp_path))))
    app.include_router(create_infra_router(InfraRouterConfig(
        app_version=lambda: "test", firebase_configured=lambda: False,
        firebase_active=lambda: False, firebase_live_features=lambda: False,
        firebase_last_error=lambda: "", minimum_app_version=lambda: "",
        base_dir=str(tmp_path), pasta_info=str(tmp_path / "info"),
    )))

    with _uvicorn_server(app) as (base, loop_thread), ThreadPoolExecutor(max_workers=1) as clients:
        def request_supplier():
            with httpx.Client(timeout=10, trust_env=False) as client:
                return client.request(method, base + path, json=payload)

        supplier_lock.acquire()
        try:
            pending = clients.submit(request_supplier)
            assert entered.wait(3), "Supplier handler did not reach the registry lock"
            with httpx.Client(timeout=1, trust_env=False) as client:
                for _ in range(3):
                    for probe in ("/health", "/dashboard.html"):
                        started = time.monotonic()
                        response = client.get(base + probe)
                        assert response.status_code == 200, response.text
                        assert time.monotonic() - started < 1, probe
            assert not pending.done(), "Supplier request should still be waiting for its registry lock"
            assert lock_waiters == [(lock_waiters[0][0], "tenant-a")]
            assert lock_waiters[0][0] != loop_thread
        finally:
            supplier_lock.release()
        response = pending.result(5)

    assert response.status_code == 200, response.text
    rows = json.loads(registry.read_text(encoding="utf-8"))["fornecedores"]
    if method == "GET":
        assert response.json() == rows
    elif method == "DELETE":
        assert rows == [] and response.json()["total"] == 0
    else:
        assert response.json()["fornecedor"] in rows
        assert any(row["nome_empresa"] == payload["nome_empresa"] for row in rows)


def test_cancelled_scope_retains_heavy_admission_until_worker_finishes():
    async def scenario():
        first_started = threading.Event()
        second_started = threading.Event()
        third_started = threading.Event()
        release = threading.Event()
        scopes = []

        def blocked(started):
            started.set()
            assert release.wait(5)

        async def first():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                await run_heavy(blocked, first_started)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first)
            tasks.start_soon(run_heavy, blocked, second_started)
            assert await anyio.to_thread.run_sync(first_started.wait, 2)
            assert await anyio.to_thread.run_sync(second_started.wait, 2)
            scopes[0].cancel()
            tasks.start_soon(run_heavy, third_started.set)
            try:
                await anyio.sleep(.05)
                assert heavy_worker_limiter().borrowed_tokens == 2
                assert not third_started.is_set()
            finally:
                release.set()
        assert third_started.is_set()
        with anyio.fail_after(2):
            while heavy_worker_limiter().borrowed_tokens:
                await anyio.sleep(.01)

    anyio.run(scenario)


def test_raw_task_cancel_does_not_release_admission_while_worker_is_alive():
    async def scenario():
        started = [threading.Event(), threading.Event(), threading.Event()]
        release = threading.Event()
        finished = threading.Event()

        def blocked(index):
            started[index].set()
            assert release.wait(5)
            if index == 0:
                finished.set()

        first = asyncio.create_task(run_heavy(blocked, 0))
        second = asyncio.create_task(run_heavy(blocked, 1))
        assert await anyio.to_thread.run_sync(started[0].wait, 2)
        assert await anyio.to_thread.run_sync(started[1].wait, 2)
        first.cancel()
        third = asyncio.create_task(run_heavy(blocked, 2))
        try:
            await anyio.sleep(.05)
            assert first.cancelled()
            assert heavy_worker_limiter().borrowed_tokens == 2
            assert not started[2].is_set()
            assert not finished.is_set()
        finally:
            release.set()
        await asyncio.gather(first, second, third, return_exceptions=True)
        assert finished.is_set() and started[2].is_set()
        with anyio.fail_after(2):
            while heavy_worker_limiter().borrowed_tokens:
                await anyio.sleep(.01)

    anyio.run(scenario)


async def _stream_bytes(response):
    return b"".join([chunk async for chunk in response.body_iterator])


def test_download_captures_cache_before_concurrent_edit_removes_file(tmp_path, monkeypatch):
    cached = tmp_path / "cached.xlsx"
    cached.write_bytes(b"complete-cached-workbook")
    monkeypatch.setattr(medias_compras_listas, "_carregar_listas_pedidos", lambda _: [{"id": "a", "nome_lista": "Order A"}])
    monkeypatch.setattr(medias_compras_listas, "_arquivo_cache_lista_pedido", lambda *_: str(cached))
    response = anyio.run(medias_compras_listas.api_medias_compras_lista_pedido_download, "a", "tenant-a")
    cached.unlink()
    assert anyio.run(_stream_bytes, response) == b"complete-cached-workbook"
    assert "Order_A_" in response.headers["content-disposition"]


def test_download_cache_separates_edits_with_the_same_updated_at(tmp_path, monkeypatch):
    order = {"id": "a", "nome_lista": "Order A", "updated_at": "2026-10-02T12:00:00", "itens": [{"SKU": "1", "Quantidade": 1}]}
    generated = []

    def path(_client, _order, version):
        return str(tmp_path / (version.replace(":", "") + ".xlsx"))

    def generate(_name, items, **kwargs):
        quantity = items[0]["Quantidade"]
        generated.append(quantity)
        return f"workbook-quantity-{quantity}".encode()

    def save(client_id, lista_id, version, payload):
        from pathlib import Path
        Path(path(client_id, lista_id, version)).write_bytes(payload)

    monkeypatch.setattr(medias_compras_listas, "_carregar_listas_pedidos", lambda _: [order])
    monkeypatch.setattr(medias_compras_listas, "_arquivo_cache_lista_pedido", path)
    monkeypatch.setattr(medias_compras_listas, "_salvar_bytes_cache_lista_pedido", save)
    monkeypatch.setattr(medias_compras_listas, "_gerar_excel_lista_pedido_bytes", generate)
    monkeypatch.setattr(medias_compras_listas, "LISTA_PEDIDO_XLSX_CACHE", {})
    first = anyio.run(medias_compras_listas.api_medias_compras_lista_pedido_download, "a", "tenant-a")
    order["itens"][0]["Quantidade"] = 2
    second = anyio.run(medias_compras_listas.api_medias_compras_lista_pedido_download, "a", "tenant-a")
    assert anyio.run(_stream_bytes, first) == b"workbook-quantity-1"
    assert anyio.run(_stream_bytes, second) == b"workbook-quantity-2"
    assert generated == [1, 2]


def test_excel_import_uses_upload_filename_when_list_name_is_empty(monkeypatch, tmp_path):
    import io
    import json
    import openpyxl
    from fastapi import UploadFile
    from backend.services import medias_compras_common as common
    from backend.services import medias_compras_importacao as imports

    tenant = tmp_path / "tenant-a"
    tenant.mkdir()
    (tenant / "cadastro_fornecedores.json").write_text(json.dumps({
        "schema": "jk.cadastro.fornecedores.v1",
        "fornecedores": [{"id": "supplier-a", "nome_empresa": "Synthetic supplier"}],
    }), encoding="utf-8")
    monkeypatch.setattr(common, "get_tenant_path", lambda _: str(tenant))
    workbook = openpyxl.Workbook()
    workbook.active.append(["SKU", "Quantity", "Cost"])
    workbook.active.append(["001", 2, 7])
    contents = io.BytesIO()
    workbook.save(contents)
    contents.seek(0)
    upload = UploadFile(contents, filename="Pedido fornecedor.xlsx")
    saved = []
    monkeypatch.setattr(imports, "_carregar_listas_pedidos", lambda _: [])
    monkeypatch.setattr(imports, "_salvar_listas_pedidos", lambda _, rows: saved.extend(rows))
    monkeypatch.setattr(imports, "_limpar_cache_lista_pedido", lambda *args, **kwargs: None)
    monkeypatch.setattr(imports, "_recalcular_frete_internacional_itens_lista", lambda _, rows, **kwargs: rows)
    monkeypatch.setattr(imports, "_resolver_escopo_loja_medias", lambda *args, **kwargs: {"loja": "Store A", "store_id": "store-a"})

    async def scenario():
        return await imports.api_medias_compras_lista_pedido_importar_excel_nova_lista(
            upload, nome_lista="", loja="Store A", store_id="store-a", fornecedor_id="supplier-a", client_id="tenant-a",
        )

    try:
        response = anyio.run(scenario)
    finally:
        contents.close()
    assert response["lista"]["nome_lista"] == "Pedido fornecedor"
    assert saved[0]["nome_lista"] == "Pedido fornecedor"
    assert response["lista"]["fornecedor_id"] == saved[0]["fornecedor_id"] == "supplier-a"
    assert response["lista"]["supplier"] == saved[0]["supplier"] == "Synthetic supplier"


def test_operational_stores_and_catalogue_with_isolated_configured_runtime(tmp_path, monkeypatch):
    import json
    from backend.services import cadastro_common, cadastro_custos, cadastro_fotos
    from backend.services import cadastro_lojas_produtos, central_accounts_client, integracoes
    from backend.services import store_public_snapshot

    tenant = tmp_path / "tenant-a"
    tenant.mkdir()

    def tenant_path(client_id):
        assert client_id == "tenant-a"
        return str(tenant)

    stores = [{"store_id": "store-a", "nome": "Store A", "integracoes": {
        "mercadolivre": {"user_id": "seller-a", "site_id": "MLB", "access_token": "synthetic"},
        "bling": {"oauth_connection_id": "synthetic-epoch", "access_token": "synthetic"},
    }}]
    (tenant / "lojas_config.json").write_text(json.dumps(stores), encoding="utf-8")
    store_public_snapshot.write_snapshot(tenant, store_public_snapshot.build_snapshot(stores))
    (tenant / "cadastro_produtos.csv").write_text("sku,nome,cg_m3 individual\n001,Synthetic product,0.125\n", encoding="utf-8")
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "ARQUIVO_LOJAS", "")
    for module in (cadastro_common, cadastro_custos, cadastro_fotos, cadastro_lojas_produtos, cadastro_listagem):
        monkeypatch.setattr(module, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_listagem, "ARQUIVO_DB_CADASTRO_PRODUTOS", "", raising=False)
    monkeypatch.setattr(cadastro_listagem, "ARQUIVO_DB_PRODUTOS", "", raising=False)
    monkeypatch.setattr(cadastro_listagem, "_migrar_arquivo_legado_para_tenant", lambda client_id, filename, _: str(tenant / filename), raising=False)

    async def scenario():
        token = central_accounts_client._current.set(None)
        try:
            return (
                await integracoes_api.get_lojas("tenant-a"),
                await cadastro_listagem.listar_produtos_cadastro("tenant-a"),
            )
        finally:
            central_accounts_client._current.reset(token)

    rows, products = anyio.run(scenario)
    assert rows[0]["store_id"] == "store-a"
    assert set(rows[0]["integracoes"]) == {"mercadolivre", "bling"}
    assert products[0]["sku"] == "001"
    assert products[0]["nome"] == "Synthetic product"
    assert products[0]["cg_m3 individual"] == "0.125"
