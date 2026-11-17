from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.codex_console import create_codex_console_router
from backend.services import codex_console, gpt_model_catalog as catalog
from backend.services.codex.console import runtime
from backend.services.codex.console import bindings


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "_CACHE", [])
    monkeypatch.setattr(catalog, "_CACHE_AT", 0.0)
    monkeypatch.setattr(catalog, "_ATTEMPT_AT", 0.0)
    monkeypatch.setattr(catalog, "_INFLIGHT", None)
    monkeypatch.setenv("JK_GPT_CATALOG_DISCOVERY_ENABLED", "0")


def _response(slug="gpt-6.2-example", efforts=("low", "max", "ultra")):
    return {"data": [{
        "id": slug, "model": slug, "displayName": "GPT Example", "isDefault": True,
        "supportedReasoningEfforts": [{"reasoningEffort": effort, "description": "Public label"} for effort in efforts],
        "defaultReasoningEffort": "max", "private_field": "DO NOT RETAIN",
    }]}


def test_future_runtime_model_and_capabilities_round_trip_without_private_metadata():
    rows = catalog.register_codex_models(_response())
    assert rows[0]["name"] == "codex:gpt-6.2-example"
    assert rows[0]["reasoning_efforts"] == ["low", "max", "ultra"]
    assert rows[0]["default_reasoning_effort"] == "max"
    assert "DO NOT RETAIN" not in str(rows)
    assert catalog.normalize_codex_model(rows[0]["name"]) == "gpt-6.2-example"
    assert catalog.normalize_reasoning("ultra", model=rows[0]["id"]) == "ultra"
    rows[0]["reasoning_efforts"].clear()
    assert catalog.reasoning_efforts("gpt-6.2-example") == ("low", "max", "ultra")


def test_runtime_catalog_filters_invalid_ids_hidden_models_and_instruction_like_labels():
    payload = _response()
    payload["data"].extend([
        {"id": "../other-tenant", "supportedReasoningEfforts": []},
        {"id": "gpt-hidden", "hidden": True},
        {"id": "gpt-safe", "displayName": "<script>unsafe</script>", "api_key": "DO NOT RETAIN"},
    ])
    rows = catalog.register_codex_models(payload)
    assert [row["id"] for row in rows] == ["gpt-6.2-example", "gpt-safe"]
    assert "<script>" not in str(rows)
    assert "DO NOT RETAIN" not in str(rows)


def test_provider_capabilities_are_separate_and_unknown_api_models_are_not_guessed():
    assert "ultra" in catalog.reasoning_efforts("gpt-6.1-sol")
    assert "ultra" not in catalog.reasoning_efforts("gpt-6.1-sol", provider="openai")
    assert "none" not in catalog.reasoning_efforts("gpt-6.1-sol", provider="openai")
    assert "none" in catalog.reasoning_efforts("gpt-6.1-terra", provider="openai")
    assert catalog.reasoning_efforts("gpt-6.2-example", provider="openai") == ()
    assert not catalog.public_model("gpt-6.1-sol", provider="openai")["name"].startswith("codex:")


def test_clamping_and_adaptive_reasoning_never_drop_a_supported_high_level():
    assert catalog.normalize_reasoning("Máximo", model="gpt-6.1-sol") == "max"
    assert catalog.normalize_reasoning("ultra", model="gpt-6.1-sol") == "ultra"
    assert catalog.normalize_reasoning("ultra", model="gpt-6-luna") == "max"
    assert catalog.normalize_reasoning("none", model="gpt-6.1-sol") == "low"
    assert catalog.next_reasoning("xhigh", "ultra", model="gpt-6.1-sol") == "max"
    assert catalog.next_reasoning("max", "ultra", model="gpt-6.1-sol") == "ultra"
    assert catalog.next_reasoning("max", "ultra", model="gpt-6-luna") == "max"
    assert catalog.next_reasoning("ultra", "high", model="gpt-6.1-sol") == "ultra"
    with pytest.raises(ValueError):
        catalog.normalize_reasoning("unrecognized", strict=True)


def test_failed_discovery_preserves_last_good_catalog(monkeypatch):
    catalog.register_codex_models(_response())
    monkeypatch.setenv("JK_GPT_CATALOG_DISCOVERY_ENABLED", "1")
    monkeypatch.setattr(catalog, "_discover_codex_models", lambda: (_ for _ in ()).throw(TimeoutError()))
    rows = catalog.get_codex_models(refresh=True)
    assert rows[0]["id"] == "gpt-6.2-example"
    assert rows[0]["available"] is True


def test_discovery_is_bounded_and_single_flight(monkeypatch):
    monkeypatch.setenv("JK_GPT_CATALOG_DISCOVERY_ENABLED", "1")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def discover():
        calls.append(True)
        entered.set()
        release.wait(5)
        return _response()

    monkeypatch.setattr(catalog, "_discover_codex_models", discover)
    started = time.monotonic()
    rows = catalog.get_codex_models()
    assert time.monotonic() - started < 3
    assert entered.is_set()
    assert rows[0]["source"] == "bundled"
    # A subsequent reader joins the existing refresh rather than starting a
    # second runtime. Release it immediately to keep this test bounded.
    event = catalog._INFLIGHT
    release.set()
    assert event is not None and event.wait(3)
    assert len(calls) == 1
    assert catalog.get_codex_models()[0]["id"] == "gpt-6.2-example"


def test_authenticated_model_endpoint_uses_shared_capabilities_and_rejects_missing_identity(monkeypatch):
    catalog.register_codex_models(_response())
    app = FastAPI()
    app.include_router(create_codex_console_router())
    identity = {}
    monkeypatch.setattr(bindings, "_RUNTIME", replace(
        bindings.current(), session_loader=lambda _authorization: dict(identity),
        permissions_loader=lambda _username, _client_id: {},
    ))
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/codex/models").status_code == 401
        identity.update({
            "username": "synthetic-user", "client_id": "synthetic-tenant", "permissions": {},
        })
        response = client.get("/api/codex/models")
    assert response.status_code == 200
    assert response.json()["default_model"] == "gpt-6.2-example"
    assert response.json()["models"][0]["reasoning_efforts"] == ["low", "max", "ultra"]


def test_runtime_default_and_explicit_environment_are_consistent_in_endpoint_and_execution(monkeypatch):
    from backend.routers import codex_models
    from backend.services.codex.console import agent_loop
    from starlette.requests import Request

    catalog.register_codex_models(_response("gpt-6-luna", ("low", "medium", "high", "xhigh", "max")))
    monkeypatch.delenv("JK_CODEX_MODEL", raising=False)
    monkeypatch.setattr(codex_models, "require_authenticated", lambda *_args: {})
    request = Request({"type": "http", "method": "GET", "path": "/api/codex/models", "headers": []})
    assert codex_models.codex_listar_modelos(request)["default_model"] == "gpt-6-luna"
    assert agent_loop._codex_normalizar_model(None) == "gpt-6-luna"
    monkeypatch.setenv("JK_CODEX_MODEL", "gpt-6-astra")
    assert codex_models.codex_listar_modelos(request)["default_model"] == "gpt-6-astra"
    assert agent_loop._codex_normalizar_model(None) == "gpt-6-astra"
