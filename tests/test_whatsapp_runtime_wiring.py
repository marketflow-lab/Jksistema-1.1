from __future__ import annotations

import json
from pathlib import Path

from backend.lifecycle import SHUTDOWN_EVENTS, STARTUP_EVENTS
from backend.services import codex_console


ROOT = Path(__file__).resolve().parents[1]


def test_whatsapp_bridge_is_registered_in_backend_startup() -> None:
    startup_names = {item.endpoint_name for item in STARTUP_EVENTS}
    assert "_whatsapp_bridge_iniciar_background" in startup_names

    backend_source = (ROOT / "backend_api.py").read_text(encoding="utf-8")
    assert "create_whatsapp_bridge_router" in backend_source
    assert "app.include_router(create_whatsapp_bridge_router())" in backend_source
    assert (
        "_whatsapp_bridge_iniciar_background = "
        "_whatsapp_bridge_module.whatsapp_bridge_iniciar_background"
    ) in backend_source
    assert "_whatsapp_bridge_parar_background" in {item.endpoint_name for item in SHUTDOWN_EVENTS}
    assert (
        "_whatsapp_bridge_parar_background = "
        "_whatsapp_bridge_module.whatsapp_bridge_parar_background"
    ) in backend_source


def test_installer_requires_whatsapp_startup_files_with_source_parity() -> None:
    manifest = json.loads(
        (ROOT / "electron_app" / "installer-required-resources.json").read_text(
            encoding="utf-8"
        )
    )
    parity = set(manifest.get("requiredPackagedSourceParity") or [])
    assert {
        "backend_api.py",
        "backend/lifecycle.py",
        "backend/routers/__init__.py",
        "backend/routers/whatsapp_bridge.py",
        "backend/schemas/__init__.py",
        "backend/services/codex_assistant.py",
        "backend/services/codex_agent_runtime.py",
        "backend/services/codex_console.py",
        "backend/services/codex_readonly_sources.py",
        "backend/services/codex_whatsapp_agents.py",
        "backend/services/favoritos_planilhas_colar.py",
        "backend/services/ia_tools_marketplaces.py",
        "backend/services/whatsapp_bridge.py",
    } <= parity


def test_whatsapp_injects_original_latest_event_request_into_ml_call() -> None:
    task = {
        "origin": "whatsapp",
        "prompt": (
            "Contexto interno\nTexto recebido:\n"
            "Qual foi o numero da venda da ultima devolucao do 200 na JK Pecas?"
        ),
    }

    prepared, report_mode = codex_console._codex_whatsapp_prepare_agent_tool_call(
        task,
        "mercado_livre_returns",
        {"loja": "JK Pecas", "limite": 100},
    )

    assert report_mode is False
    assert prepared["message"] == (
        "Qual foi o numero da venda da ultima devolucao do 200 na JK Pecas?"
    )
    assert prepared["limite"] == 100


def test_whatsapp_required_marketplace_calls_keep_policy_order() -> None:
    calls = [
        {"tool_id": "mercado_livre_returns", "args": {}},
        {"tool_id": "sales_returns_query", "args": {}},
        {"tool_id": "mercado_livre_orders", "args": {}},
    ]
    ordered = codex_console._codex_agent_order_calls_by_source_policy(
        calls,
        {"required_tools": ["mercado_livre_orders", "mercado_livre_returns"]},
    )

    assert [item["tool_id"] for item in ordered] == [
        "mercado_livre_orders",
        "mercado_livre_returns",
        "sales_returns_query",
    ]
