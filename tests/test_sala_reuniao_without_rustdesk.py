from pathlib import Path

from backend.routers.sala_reuniao import create_sala_reuniao_router


ROOT = Path(__file__).resolve().parents[1]


def test_sala_reuniao_preserves_daily_routes_only() -> None:
    router = create_sala_reuniao_router()
    routes = {
        (method, route.path)
        for route in router.routes
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }

    assert routes == {
        ("GET", "/api/sala-reuniao/status"),
        ("POST", "/api/sala-reuniao/salas"),
        ("GET", "/api/sala-reuniao/reunioes-ativas"),
        ("GET", "/api/sala-reuniao/salas-ativas"),
        ("GET", "/api/sala-reuniao/salas"),
        ("POST", "/api/sala-reuniao/reunioes-ativas/encerrar-local"),
        ("POST", "/api/sala-reuniao/reunioes-ativas/encerrar"),
        ("POST", "/api/sala-reuniao/reunioes-ativas/encerrar-todas"),
        ("POST", "/api/sala-reuniao/salas/encerrar-local"),
        ("GET", "/api/sala-reuniao/uso-mensal"),
        ("POST", "/api/sala-reuniao/uso-mensal/adicionar"),
        ("GET", "/api/sala-reuniao/gravacoes"),
        ("GET", "/api/sala-reuniao/transcricoes"),
    }


def test_rustdesk_runtime_surfaces_are_absent() -> None:
    assert not (ROOT / "rustdesk.html").exists()
    assert not (ROOT / "static" / "rustdesk.html").exists()

    runtime_sources = (
        "sala_reuniao.html",
        "static/sala_reuniao.html",
        "electron_shell.html",
        "preload.js",
        "electron_tab_preload.js",
        "electron_app/preload.js",
        "electron_app/main/modules/ipc.js",
        "backend/routers/sala_reuniao.py",
        "backend/services/sala_reuniao.py",
        "backend/services/sala_reuniao_api.py",
    )
    forbidden = (
        "rustdesk",
        "acesso remoto",
        "get-native-window-handle",
        "getnativewindowhandle",
        "jk-tab-frame-bounds",
    )

    for relative_path in runtime_sources:
        source = (ROOT / relative_path).read_text(encoding="utf-8-sig").lower()
        assert not any(token in source for token in forbidden), relative_path
