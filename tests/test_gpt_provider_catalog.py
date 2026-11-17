from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import requests

from backend.schemas.ia import IAChatRequest
from backend.services import ia as _ia_facade  # noqa: F401
from backend.services import gpt_model_catalog, ia_providers


@pytest.fixture
def api_catalog(monkeypatch):
    monkeypatch.setattr(ia_providers, "_OPENAI_MODEL_CATALOG_CACHE", {})
    monkeypatch.setattr(ia_providers, "_obter_openai_api_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(ia_providers, "requests_tls_verify", lambda: True)


def test_openai_catalog_is_separate_from_codex_and_cached(api_catalog, monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "gpt-6-astra"}, {"id": "gpt-5.6-luna"}, {"id": "embedding-test"}, {"id": "gpt-audio"}, {"id": "gpt-realtime"}, {"id": "gpt-6-astra"}]},
        )

    monkeypatch.setattr(ia_providers.requests, "get", get)
    first = ia_providers._listar_modelos_openai_api()
    first[0]["reasoning_efforts"].append("fake-effort")
    second = ia_providers._listar_modelos_openai_api()

    assert len(calls) == 1
    assert calls[0][0] == "https://api.openai.com/v1/models"
    assert calls[0][1]["timeout"] == (3, 6)
    assert {row["name"] for row in second} == {"gpt-6-astra", "gpt-5.6-luna"}
    assert all(row["source"] == "openai_api" and row["available"] is True for row in second)
    assert all("ultra" not in row["reasoning_efforts"] and "fake-effort" not in row["reasoning_efforts"] for row in second)


def test_openai_catalog_timeout_uses_safe_fallback_without_model_generation(api_catalog, monkeypatch):
    monkeypatch.setattr(ia_providers.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout()))
    monkeypatch.setattr(ia_providers.requests, "post", lambda *_args, **_kwargs: pytest.fail("Catalog discovery must never generate a response"))

    rows = ia_providers._listar_modelos_openai_api()

    assert rows == gpt_model_catalog.get_api_fallback_models()
    assert all(not row["name"].startswith("codex:") for row in rows)


@pytest.mark.parametrize(("model", "requested", "expected"), (("gpt-6-astra", "max", "max"), ("gpt-6-astra", "ultra", "max"), ("gpt-6.2-future", "ultra", None)))
def test_responses_preserves_requested_gpt_model_and_sends_only_supported_effort(monkeypatch, model, requested, expected):
    captured = []
    monkeypatch.setattr(ia_providers, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(ia_providers, "_ia_validar_provedor_ativo", lambda _provider: None)
    monkeypatch.setattr(ia_providers, "_obter_openai_api_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(ia_providers, "requests_tls_verify", lambda: True)
    monkeypatch.setattr(ia_providers, "_extrair_texto_openai_response", lambda _response: "Resposta sintetica")

    def post(_url, **kwargs):
        captured.append(kwargs["json"])
        return SimpleNamespace(ok=True, json=lambda: {})

    monkeypatch.setattr(ia_providers.requests, "post", post)
    payload = IAChatRequest(message="Explique este produto", model=model, reasoning_effort=requested, context={"desativar_recursos_chat": True})

    assert ia_providers._chamar_openai_responses.__wrapped__(payload, "tenant-test") == "Resposta sintetica"
    assert captured[0]["model"] == model
    if expected is None:
        assert "reasoning" not in captured[0]
    else:
        assert captured[0]["reasoning"] == {"effort": expected}


@pytest.mark.parametrize(("model", "requested", "expected"), (("gpt-6-astra", "max", "max"), ("gpt-6-astra", "ultra", "ultra"), ("gpt-6-luna", "ultra", "max")))
def test_codex_provider_transmits_extended_effort_without_downgrading(monkeypatch, tmp_path, model, requested, expected):
    import openai_codex
    from backend.services.codex.console import execution as console_execution
    from backend.services.codex.console import security as console_security

    captured = {}

    class _Turn:
        def run(self):
            return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="Resposta sintetica")

    class _Thread:
        id = "thread-synthetic"

        def turn(self, _prompt, **kwargs):
            captured.update(kwargs)
            return _Turn()

    class _Codex:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def thread_start(self, **kwargs):
            captured["thread_model"] = kwargs["model"]
            return _Thread()

    monkeypatch.setattr(ia_providers, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(ia_providers, "_ia_chat_planned_context_text", lambda *_args: "")
    for name in ("enabled", "sdk_installed", "auth_detected"):
        monkeypatch.setattr(console_execution, name, lambda: True)
    monkeypatch.setattr(console_execution, "runtime_bin", lambda: "synthetic-codex")
    monkeypatch.setattr(console_execution, "sdk_env", lambda: {})
    monkeypatch.setattr(console_execution, "readonly_config_overrides", lambda: ())
    monkeypatch.setattr(console_security, "readonly_cwd", lambda *_args: str(tmp_path))
    monkeypatch.setattr(openai_codex, "Codex", _Codex)
    monkeypatch.setattr(openai_codex, "CodexConfig", lambda **kwargs: kwargs)

    response, _thread_id = ia_providers._chamar_codex_chat_com_thread.__wrapped__(
        IAChatRequest(message="Pergunta sintetica", model=f"codex:{model}", reasoning_effort=requested),
        "tenant-test",
    )

    assert response == "Resposta sintetica"
    assert captured["thread_model"] == model
    assert captured["model"] == model
    assert captured["effort"].value == expected
