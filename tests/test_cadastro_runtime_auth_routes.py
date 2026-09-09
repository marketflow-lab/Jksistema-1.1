import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient

from backend.routers.cadastro import create_cadastro_router
from backend.services import cadastro, cadastro_lojas_listagem, cadastro_lojas_produtos


AUTH_RUNTIME_MODULES = (
    "backend.services.cadastro_common",
    "backend.services.cadastro_fornecedores",
    "backend.services.cadastro_fotos",
    "backend.services.cadastro_importacao",
    "backend.services.cadastro_importacao_catalogos",
    "backend.services.cadastro_listagem",
    "backend.services.cadastro_lojas_produtos",
    "backend.services.cadastro_mercadolivre",
    "backend.services.cadastro_produtos",
    "backend.services.cadastro_sync_ncm",
)

LATE_BOUND_AUTH_ROUTE_NAMES = {
    "iniciar_sync_ncm_cadastro",
    "progresso_sync_ncm_cadastro",
    "listar_produtos_cadastro",
    "salvar_produto_cadastro",
    "listar_fornecedores_cadastro",
    "criar_fornecedor_cadastro",
    "atualizar_fornecedor_cadastro",
    "excluir_fornecedor_cadastro",
    "importar_colunas_cadastro_por_sku",
    "listar_colunas_cadastro",
    "obter_produto_cadastro",
    "atualizar_produto_cadastro_completo",
    "incluir_produto_cadastro_completo",
    "obter_produto_cadastro_query",
    "atualizar_produto_cadastro_completo_query",
    "listar_produtos_loja",
    "criar_produto_loja",
    "obter_produto_loja",
    "atualizar_produto_loja",
    "excluir_produto_loja",
    "listar_colunas_produtos_loja",
    "consultar_produto_mercado_livre_loja",
    "importar_colunas_cadastro_loja",
    "preview_migracao_produtos_loja",
}

PREEXISTING_STABLE_AUTH_ROUTES = {
    ("/api/cadastro/foto/upload", "POST", "upload_foto_cadastro"),
    ("/api/cadastro/foto-upload", "POST", "upload_foto_cadastro"),
    ("/api/cadastro/foto/{client_id}/{filename:path}", "GET", "servir_foto_cadastro"),
    ("/api/cadastro/foto-arquivo/{filename:path}", "GET", "servir_foto_cadastro_por_arquivo"),
    (
        "/api/cadastro/lojas/{store_id}/importacoes/{source}/preview",
        "POST",
        "iniciar_preview_importacao_catalogo",
    ),
    (
        "/api/cadastro/lojas/{store_id}/importacoes/{job_id}",
        "GET",
        "obter_importacao_catalogo",
    ),
    (
        "/api/cadastro/lojas/{store_id}/importacoes/{job_id}/cancelar",
        "POST",
        "cancelar_importacao_catalogo",
    ),
    (
        "/api/cadastro/lojas/{store_id}/importacoes/{job_id}/aplicar",
        "POST",
        "aplicar_importacao_catalogo",
    ),
}


@pytest.fixture(autouse=True)
def restore_cadastro_runtime_bindings():
    sentinel = object()
    modules = [importlib.import_module(name) for name in AUTH_RUNTIME_MODULES]
    keys = (
        "get_tenant_id",
        "get_tenant_path",
        "_runtime_get_tenant_id",
        "_get_tenant_id_dependency",
        "_runtime",
        "logger",
    )
    snapshots = {
        module: {key: getattr(module, key, sentinel) for key in keys}
        for module in modules
    }
    yield
    for module, values in snapshots.items():
        for key, value in values.items():
            if value is sentinel:
                module.__dict__.pop(key, None)
            else:
                setattr(module, key, value)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/cadastro/lojas/store-a/produtos",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
        }
    )


def _resolve_dependency(call, authorization="Bearer tenant-a"):
    result = call(_request(), authorization)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _configure_runtime(provider):
    cadastro.configure_cadastro_runtime(
        SimpleNamespace(
            get_tenant_id=provider,
            get_tenant_path=lambda client_id: f"tenant/{client_id}",
        )
    )


def _provider(*, asynchronous: bool):
    def resolve(_request, authorization=None):
        if not authorization:
            raise HTTPException(status_code=401, detail="Token ausente.")
        token = str(authorization).removeprefix("Bearer ").strip()
        if token not in {"tenant-a", "tenant-b"}:
            raise HTTPException(status_code=401, detail="Token invalido.")
        return token

    if not asynchronous:
        return resolve

    async def resolve_async(request, authorization=None):
        return resolve(request, authorization)

    return resolve_async


def test_cadastro_preserves_32_routes_and_all_24_late_bound_auth_dependencies_follow_reconfiguration():
    router = create_cadastro_router()
    routes = {route.name: route for route in router.routes}

    assert len([route for route in router.routes if route.name != "listar_produtos_lojas"]) == 32
    assert len(router.routes) == 33
    assert LATE_BOUND_AUTH_ROUTE_NAMES <= routes.keys()
    assert len(LATE_BOUND_AUTH_ROUTE_NAMES) == 24

    captured = {}
    for name in LATE_BOUND_AUTH_ROUTE_NAMES:
        dependencies = [dependency.call for dependency in routes[name].dependant.dependencies]
        assert len(dependencies) == 1, f"{name} deve ter exatamente uma dependencia de tenant"
        captured[name] = dependencies[0]

    _configure_runtime(_provider(asynchronous=False))
    assert {
        name: _resolve_dependency(call)
        for name, call in captured.items()
    } == {name: "tenant-a" for name in LATE_BOUND_AUTH_ROUTE_NAMES}

    original_ids = {name: id(call) for name, call in captured.items()}
    _configure_runtime(lambda _request, _authorization=None: "tenant-reconfigured")
    assert {name: id(call) for name, call in captured.items()} == original_ids
    assert {
        name: _resolve_dependency(call)
        for name, call in captured.items()
    } == {name: "tenant-reconfigured" for name in LATE_BOUND_AUTH_ROUTE_NAMES}


def test_other_8_existing_routes_and_new_bulk_route_keep_tenant_dependency():
    router = create_cadastro_router()
    existing = {
        (route.path, method, route.name): route
        for route in router.routes
        for method in route.methods or set()
    }
    assert len(PREEXISTING_STABLE_AUTH_ROUTES) == 8
    assert PREEXISTING_STABLE_AUTH_ROUTES <= existing.keys()
    bulk_key = ("/api/cadastro/lojas/produtos", "GET", "listar_produtos_lojas")
    assert bulk_key in existing

    _configure_runtime(_provider(asynchronous=True))
    for key in [*sorted(PREEXISTING_STABLE_AUTH_ROUTES), bulk_key]:
        dependencies = [item.call for item in existing[key].dependant.dependencies]
        assert len(dependencies) == 1, f"{key} deve continuar autenticada por tenant"
        assert _resolve_dependency(dependencies[0]) == "tenant-a"

    assert all(
        len(route.dependant.dependencies) == 1
        for route in router.routes
    ), "todas as 32 rotas anteriores e a nova bulk devem exigir tenant"


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync-provider", "async-provider"])
def test_store_products_route_resolves_late_runtime_auth_through_real_asgi(
    monkeypatch,
    asynchronous,
):
    _configure_runtime(_provider(asynchronous=asynchronous))
    calls = []

    def list_products(client_id, store_id, *, include_deleted=False, view="full"):
        calls.append((client_id, store_id, include_deleted, view))
        return [{"sku": "SKU-1", "tenant_marker": client_id, "store_id": store_id}]

    monkeypatch.setattr(
        cadastro_lojas_listagem,
        "listar_produtos_loja_snapshot_sync",
        list_products,
    )
    app = FastAPI()
    app.include_router(create_cadastro_router())

    with TestClient(app, raise_server_exceptions=False) as client:
        tenant_a = client.get(
            "/api/cadastro/lojas/store-a/produtos?view=summary",
            headers={"Authorization": "Bearer tenant-a"},
        )
        tenant_b = client.get(
            "/api/cadastro/lojas/store-a/produtos?view=summary",
            headers={"Authorization": "Bearer tenant-b"},
        )
        missing = client.get("/api/cadastro/lojas/store-a/produtos")

    assert tenant_a.status_code == 200
    assert tenant_a.json() == [
        {"sku": "SKU-1", "tenant_marker": "tenant-a", "store_id": "store-a"}
    ]
    assert tenant_b.status_code == 200
    assert tenant_b.json() == [
        {"sku": "SKU-1", "tenant_marker": "tenant-b", "store_id": "store-a"}
    ]
    assert missing.status_code == 401
    assert calls == [
        ("tenant-a", "store-a", False, "summary"),
        ("tenant-b", "store-a", False, "summary"),
    ]


def test_bulk_route_is_authenticated_and_validates_summary_view_through_real_asgi(
    monkeypatch,
):
    _configure_runtime(_provider(asynchronous=True))
    calls = []

    def list_all(client_id, *, view="summary"):
        calls.append((client_id, view))
        return {
            "produtos": [{"store_id": "store-a", "sku": "001"}],
            "lojas": [
                {
                    "store_id": "store-a",
                    "loja_sync": "Loja A",
                    "status": "ok",
                    "total": 1,
                }
            ],
            "total": 1,
            "partial": False,
        }

    monkeypatch.setattr(
        cadastro_lojas_listagem,
        "listar_produtos_lojas_snapshot_sync",
        list_all,
    )
    app = FastAPI()
    app.include_router(create_cadastro_router())

    with TestClient(app, raise_server_exceptions=False) as client:
        ok = client.get(
            "/api/cadastro/lojas/produtos?view=summary",
            headers={"Authorization": "Bearer tenant-a"},
        )
        missing = client.get("/api/cadastro/lojas/produtos?view=summary")
        invalid_view = client.get(
            "/api/cadastro/lojas/produtos?view=unknown",
            headers={"Authorization": "Bearer tenant-a"},
        )
        full_view = client.get(
            "/api/cadastro/lojas/produtos?view=full",
            headers={"Authorization": "Bearer tenant-a"},
        )

    assert ok.status_code == 200
    assert ok.json()["total"] == 1
    assert missing.status_code == 401
    assert invalid_view.status_code == 422
    assert full_view.status_code == 422
    assert calls == [("tenant-a", "summary")]


def test_store_products_route_returns_controlled_503_when_auth_runtime_is_unconfigured(
    monkeypatch,
):
    monkeypatch.setattr(cadastro_lojas_produtos, "_runtime_get_tenant_id", None)
    monkeypatch.setattr(
        cadastro_lojas_listagem,
        "listar_produtos_loja_snapshot_sync",
        lambda *_args, **_kwargs: pytest.fail("handler nao deve rodar sem auth"),
    )
    app = FastAPI()
    app.include_router(create_cadastro_router())

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/cadastro/lojas/store-a/produtos",
        headers={"Authorization": "Bearer tenant-a"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Contexto de autenticacao do Cadastro ainda nao inicializado."
    }


def test_store_products_route_preserves_runtime_permission_denial_without_running_handler(
    monkeypatch,
):
    async def deny(_request, _authorization=None):
        raise HTTPException(status_code=403, detail="Permissao insuficiente.")

    _configure_runtime(deny)
    monkeypatch.setattr(
        cadastro_lojas_listagem,
        "listar_produtos_loja_snapshot_sync",
        lambda *_args, **_kwargs: pytest.fail("handler nao deve rodar sem permissao"),
    )
    app = FastAPI()
    app.include_router(create_cadastro_router())

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/cadastro/lojas/store-a/produtos?view=summary",
        headers={"Authorization": "Bearer sem-cadastro"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Permissao insuficiente."}
