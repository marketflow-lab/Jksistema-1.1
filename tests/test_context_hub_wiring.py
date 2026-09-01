from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.lifecycle import SHUTDOWN_EVENTS, STARTUP_EVENTS, register_startup_events
from backend.routers.context_hub import create_context_hub_router
from backend.services import codex_console
from backend.services.codex.console import runtime as console_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_context_hub_router_exposes_only_the_administrative_contract(tmp_path: Path) -> None:
    router = create_context_hub_router(
        base_dir=tmp_path,
        info_root=tmp_path / "info",
        surface="test",
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in (route.methods or set())
    }
    expected = {
        ("/api/admin/context-hub/status", "GET"),
        ("/api/admin/context-hub/settings", "PUT"),
        ("/api/admin/context-hub/rebuild", "POST"),
        ("/api/admin/context-hub/generations", "GET"),
        ("/api/admin/context-hub/generations/{generation_id}", "GET"),
        ("/api/admin/context-hub/generations/{generation_id}/publish", "POST"),
        ("/api/admin/context-hub/generations/{generation_id}/rollback", "POST"),
        ("/api/admin/context-hub/search", "POST"),
    }
    assert expected <= routes


def test_lifecycle_registers_extra_startup_and_shutdown_handlers() -> None:
    calls: list[tuple[str, object]] = []
    module_members = {
        spec.endpoint_name: (lambda: None)
        for spec in (*STARTUP_EVENTS, *SHUTDOWN_EVENTS)
    }
    app = SimpleNamespace(
        router=SimpleNamespace(
            add_event_handler=lambda event, handler: calls.append((event, handler))
        )
    )
    startup = lambda: None
    shutdown = lambda: None

    register_startup_events(
        app,
        SimpleNamespace(**module_members),
        extra_handlers=(startup,),
        extra_shutdown_handlers=(shutdown,),
    )

    assert ("startup", startup) in calls
    assert ("shutdown", shutdown) in calls


def test_backend_composes_context_hub_with_explicit_surface_and_lifecycle() -> None:
    source = (ROOT / "backend_api.py").read_text(encoding="utf-8")
    electron_backend = (
        ROOT / "electron_app" / "main" / "modules" / "backend.js"
    ).read_text(encoding="utf-8")

    assert "app.include_router(create_context_hub_router())" in source
    assert "_context_hub_iniciar_background" in source
    assert "_context_hub_parar_background" in source
    assert 'JK_CONTEXT_HUB_SURFACE=installed' in electron_backend
    assert "JK_CONTEXT_HUB_SURFACE: 'installed'" in electron_backend


def test_release_manifest_requires_context_hub_runtime_files() -> None:
    manifest = json.loads(
        (ROOT / "electron_app" / "installer-required-resources.json").read_text(
            encoding="utf-8"
        )
    )
    critical = {
        "backend/modules/context_hub/product_evidence_rendering.py",
        "backend/routers/context_hub.py",
        "backend/services/context_hub.py",
        "backend/services/context_hub_endpoints.py",
        "backend/services/context_hub_inventory.py",
        "electron_app/main/modules/context-vault-security.js",
    }
    source_files = set(manifest.get("requiredSourceFiles") or [])
    packaged_files = set(manifest.get("requiredPackagedFiles") or [])
    parity = set(manifest.get("requiredPackagedSourceParity") or [])
    source_directories = {
        str(item.get("path") or ""): int(item.get("minFiles") or 0)
        for item in manifest.get("requiredSourceDirectories") or []
        if isinstance(item, dict)
    }
    packaged_directories = {
        str(item.get("path") or ""): int(item.get("minFiles") or 0)
        for item in manifest.get("requiredPackagedDirectories") or []
        if isinstance(item, dict)
    }

    assert critical <= source_files
    assert {item for item in critical if item.startswith("backend/")} <= parity
    assert {f"local_app/{item}" for item in critical} <= packaged_files
    assert source_directories["backend/services/context_inventory"] >= 16
    assert packaged_directories["local_app/backend/services/context_inventory"] >= 16


def test_non_full_user_gets_403_on_every_mutating_or_search_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(console_runtime, "_codex_require_authenticated",
        lambda _request, _authorization: {
            "client_id": "000002",
            "username": "operador",
            "permissions": {"ia": True},
        },
    )
    app = FastAPI()
    app.include_router(
        create_context_hub_router(
            base_dir=tmp_path,
            info_root=tmp_path / "info",
            surface="test",
        )
    )
    client = TestClient(app)
    generation = "0" * 32

    requests = (
        ("put", "/api/admin/context-hub/settings", {}),
        ("post", "/api/admin/context-hub/rebuild", {}),
        ("post", f"/api/admin/context-hub/generations/{generation}/publish", {}),
        ("post", f"/api/admin/context-hub/generations/{generation}/rollback", {}),
        ("post", "/api/admin/context-hub/search", {"query": "produto"}),
    )
    for method, path, payload in requests:
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 403, (method, path, response.text)


def test_request_body_cannot_select_another_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(console_runtime, "_codex_require_authenticated",
        lambda _request, _authorization: {
            "client_id": "000002",
            "username": "admin",
            "permissions": {"full": True},
        },
    )
    app = FastAPI()
    app.include_router(
        create_context_hub_router(
            base_dir=tmp_path,
            info_root=tmp_path / "info",
            surface="test",
        )
    )
    response = TestClient(app).post(
        "/api/admin/context-hub/rebuild",
        json={"client_id": "000003", "reason": "manual_admin"},
    )

    assert response.status_code == 422
    assert not (tmp_path / "info" / "000003").exists()
