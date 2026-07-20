from backend.services.whatsapp import response_fallback


def test_fallback_is_disabled_by_default_and_before_two_failures(monkeypatch):
    calls = []
    monkeypatch.setattr(
        response_fallback,
        "_provider_reply",
        lambda *args, **kwargs: calls.append((args, kwargs)) or ("nao deveria chamar", "model"),
    )

    assert response_fallback.configured_fallback_reply(
        {"ai_model": "deepseek-chat", "response_provider_policy": "codex_only"},
        failure_count=2,
        client_id="000002",
        event_type="user_message",
        user_message="Oi",
    ) == {}
    assert response_fallback.configured_fallback_reply(
        {"ai_model": "deepseek-chat", "response_provider_policy": "codex_then_configured_fallback"},
        failure_count=1,
        client_id="000002",
        event_type="user_message",
        user_message="Oi",
    ) == {}
    assert calls == []


def test_fallback_runs_once_after_two_codex_failures(monkeypatch):
    captured = {}

    def provider(model, payload, client_id):
        captured.update(model=model, payload=payload, client_id=client_id)
        return "A consulta ficou parcial: faltou a Loja B.", "deepseek-chat"

    monkeypatch.setattr(response_fallback, "_provider_reply", provider)
    result = response_fallback.configured_fallback_reply(
        {"ai_model": "deepseek-chat", "response_provider_policy": "codex_then_configured_fallback"},
        failure_count=2,
        client_id="000002",
        event_type="worker_result",
        user_message="Compare as lojas",
        worker_result={
            "status": "partial",
            "verified_facts": ["Loja A: 12 pedidos"],
            "sources": ["API Loja A"],
            "missing": ["Loja B indisponivel"],
            "confidence": "low",
            "evidence_sufficient": False,
            "coverage_complete": False,
        },
    )

    assert result["action"] == "reply"
    assert result["codex_failure_count"] == 2
    assert result["fallback_after_codex_failures"] is True
    assert result["response_provider"] == "deepseek"
    assert captured["client_id"] == "000002"
    assert "Loja B indisponivel" in captured["payload"].message


def test_codex_model_is_never_used_as_external_fallback(monkeypatch):
    monkeypatch.setattr(
        response_fallback,
        "_provider_reply",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider chamado")),
    )
    assert response_fallback.configured_fallback_reply(
        {"ai_model": "codex:gpt-5.5", "response_provider_policy": "codex_then_configured_fallback"},
        failure_count=2,
        client_id="000002",
        event_type="worker_result",
        user_message="resultado",
    ) == {}
