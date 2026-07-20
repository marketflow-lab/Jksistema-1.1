from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from backend.services import ia as _ia_facade  # noqa: F401 - binds legacy peers
from backend.services import codex_turn_context, ia_conversas, ia_endpoints, ia_providers
from backend.schemas import IAChatRequest


def test_sidebar_snapshot_is_structured_bounded_and_drops_raw_tables():
    snapshot = ia_endpoints._ia_sidebar_screen_context_v2(
        {
            "surface": "sidebar_chat",
            "title": "Vendas",
            "pathname": "/vendas.html?token=secret",
            "modulo_atual": "vendas",
            "store": "JK Pecas",
            "store_mode": "single",
            "multi_store": False,
            "table_rows": [{"pedido": "x" * 5000}] * 100,
            "table": ["raw"] * 100,
            "visible_text": "html bruto" * 2000,
            "filters": [{"key": "sku", "label": "SKU", "value": "001"}],
            "entity_refs": {"sku": "001", "store": "JK Pecas"},
        },
        "Mostre as vendas do SKU 001",
    )

    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert snapshot["schema_version"] == "sidebar-turn-v2"
    assert snapshot["surface"] == "sidebar_chat"
    assert snapshot["store_mode"] == "single"
    assert snapshot["store"] == "JK Pecas"
    assert "table_rows" not in encoded
    assert "visible_text" not in encoded
    assert "html bruto" not in encoded
    assert len(encoded) <= ia_endpoints._IA_SIDEBAR_CONTEXT_MAX_CHARS


def test_general_context_never_inherits_store_and_multi_store_is_explicit():
    general = ia_endpoints._ia_sidebar_screen_context_v2(
        {
            "store": "JK Pecas",
            "store_mode": "single",
            "entity_refs": {"store": "JK Pecas"},
        },
        "Qual e a capital da Franca?",
    )
    assert general["context_kind"] == "general"
    assert general["store_mode"] == "none"
    assert general["store"] == ""
    assert "store" not in general["entity_refs"]

    all_stores = ia_endpoints._ia_sidebar_screen_context_v2(
        {"store_mode": "all", "multi_store": True, "store": "JK Pecas"},
        "Compare as vendas de todas as lojas",
    )
    assert all_stores["store_mode"] == "all"
    assert all_stores["multi_store"] is True
    assert all_stores["store"] == ""


def test_store_change_restarts_quick_chat_thread():
    first = ia_endpoints._ia_sidebar_conversation_decision(
        None,
        client_id="tenant",
        username="alice",
        conversation_mode="quick_chat",
        conversation_id="conv-1",
        screen_snapshot={
            "store": "JK Pecas",
            "store_mode": "single",
            "multi_store": False,
        },
    )
    changed = ia_endpoints._ia_sidebar_conversation_decision(
        {
            "thread_id": "thread-jk-pecas",
            "prompt_fingerprint": first.prompt_fingerprint,
            "schema_fingerprint": first.schema_fingerprint,
            "scope_fingerprint": first.scope_fingerprint,
            "conversation_key": first.conversation_key,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        client_id="tenant",
        username="alice",
        conversation_mode="quick_chat",
        conversation_id="conv-1",
        screen_snapshot={
            "store": "Uai Mineirinho",
            "store_mode": "single",
            "multi_store": False,
        },
    )

    assert changed.reuse_thread is False
    assert changed.thread_action == "restart"
    assert "scope_changed" in changed.restart_reasons


def test_conversation_storage_is_isolated_by_user_and_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(ia_conversas, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(ia_conversas, "logger", None, raising=False)

    assert ia_conversas._ia_conversas_salvar(
        "tenant-a", "alice", "global", "same-id", "Alice",
        [{"role": "user", "text": "mensagem alice"}],
        modo="quick_chat", surface="sidebar_chat",
    )
    assert ia_conversas._ia_conversas_salvar(
        "tenant-a", "bob", "global", "same-id", "Bob",
        [{"role": "user", "text": "mensagem bob"}],
        modo="quick_chat", surface="sidebar_chat",
    )
    assert ia_conversas._ia_conversas_salvar(
        "tenant-a", "alice", "global", "same-id", "Task",
        [{"role": "user", "text": "mensagem task"}],
        modo="codex_task", surface="sidebar_chat",
    )

    alice = ia_conversas._ia_conversas_contexto_conversa(
        "tenant-a", "alice", "same-id", modo="quick_chat", surface="sidebar_chat"
    )
    bob = ia_conversas._ia_conversas_contexto_conversa(
        "tenant-a", "bob", "same-id", modo="quick_chat", surface="sidebar_chat"
    )
    task = ia_conversas._ia_conversas_contexto_conversa(
        "tenant-a", "alice", "same-id", modo="codex_task", surface="sidebar_chat"
    )
    assert alice["recent_messages"][0]["content"] == "mensagem alice"
    assert bob["recent_messages"][0]["content"] == "mensagem bob"
    assert task["recent_messages"][0]["content"] == "mensagem task"


def test_explicit_conversation_does_not_inject_other_conversations(tmp_path, monkeypatch):
    monkeypatch.setattr(ia_conversas, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(ia_conversas, "logger", None, raising=False)
    for conversation_id, text in (("conv-a", "segredo da conversa A"), ("conv-b", "somente conversa B")):
        ia_conversas._ia_conversas_salvar(
            "tenant", "alice", "global", conversation_id, conversation_id,
            [{"role": "user", "text": text}],
            modo="quick_chat", surface="sidebar_chat",
        )
    current = ia_conversas._ia_conversas_contexto_conversa(
        "tenant", "alice", "conv-b", modo="quick_chat", surface="sidebar_chat"
    )
    serialized = json.dumps(current, ensure_ascii=False)
    assert "somente conversa B" in serialized
    assert "segredo da conversa A" not in serialized


def test_sidebar_provider_policy_is_codex_first_and_legacy_model_is_fallback(monkeypatch):
    monkeypatch.setattr(
        ia_endpoints,
        "_carregar_configuracoes_globais",
        lambda: {
            "ia_modelo_chat": "codex:gpt-5.5",
            "ia_sidebar_response_provider_policy": "codex_then_configured_fallback",
        },
        raising=False,
    )
    monkeypatch.setattr(ia_endpoints, "_normalizar_ia_modelo_padrao", ia_providers._normalizar_ia_modelo_padrao, raising=False)
    monkeypatch.setattr(ia_endpoints, "_modelo_eh_codex", ia_providers._modelo_eh_codex, raising=False)
    policy, codex_model, fallback = ia_endpoints._ia_sidebar_provider_settings("vertex:gemini-2.5-flash")
    assert policy == "codex_then_configured_fallback"
    assert codex_model.startswith("codex:")
    assert fallback == "vertex:gemini-2.5-flash"


def test_sidebar_codex_retries_twice_before_deterministic_failure(monkeypatch):
    attempts = []
    prior_contract = codex_turn_context.decide_conversation(
        None,
        surface="sidebar_chat",
        client_id="tenant",
        store="",
        user="alice",
        subject="quick_chat:conv-1",
        prompt_contract={"version": ia_endpoints._IA_SIDEBAR_PROMPT_VERSION},
        schema_contract={
            "version": ia_endpoints._IA_SIDEBAR_SCHEMA_VERSION,
            "shared_contract_hash": codex_turn_context.CONTRACT_HASH,
        },
        scope={
            "conversation_mode": "quick_chat",
            "store_mode": "none",
            "store": "",
            "multi_store": False,
        },
    )
    monkeypatch.setattr(ia_endpoints, "_extrair_username_do_request", lambda _request: "alice", raising=False)
    monkeypatch.setattr(ia_endpoints, "_ia_nome_usuario", lambda *_args: "Alice", raising=False)
    monkeypatch.setattr(ia_endpoints, "_ia_chat_eh_pedido_rapido_sidebar", lambda *_args: True, raising=False)
    monkeypatch.setattr(ia_endpoints, "_usuario_pode_escolher_modelo_chat", lambda *_args: False, raising=False)
    monkeypatch.setattr(ia_endpoints, "_ia_modelo_chat_configurado", lambda: "codex:gpt-5.5", raising=False)
    monkeypatch.setattr(ia_endpoints, "_normalizar_ia_modelo_padrao", ia_providers._normalizar_ia_modelo_padrao, raising=False)
    monkeypatch.setattr(ia_endpoints, "_codex_modelo_nome_curto", ia_providers._codex_modelo_nome_curto, raising=False)
    monkeypatch.setattr(ia_endpoints, "_modelo_eh_codex", ia_providers._modelo_eh_codex, raising=False)
    monkeypatch.setattr(
        ia_endpoints,
        "_ia_conversas_contexto_conversa",
        lambda *_args, **_kwargs: {
            "recent_messages": [],
            "summary": "",
            "codex_thread_id": "thread-old",
            "prompt_fingerprint": prior_contract.prompt_fingerprint,
            "schema_fingerprint": prior_contract.schema_fingerprint,
            "scope_fingerprint": prior_contract.scope_fingerprint,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(
        ia_endpoints,
        "_ia_chat_prepare_data_selection",
        lambda *_args, **_kwargs: ({"status": "no_data_needed", "coverage_complete": True, "evidence": []}, []),
    )
    monkeypatch.setattr(
        ia_endpoints,
        "_ia_sidebar_provider_settings",
        lambda _model: ("codex_only", "codex:gpt-5.5", ""),
    )

    def fail_codex(*_args, **_kwargs):
        attempts.append(dict(_kwargs))
        raise RuntimeError("operational")

    monkeypatch.setattr(ia_endpoints, "_chamar_codex_chat_com_thread", fail_codex, raising=False)
    monkeypatch.setattr(ia_endpoints, "_ia_conversas_salvar", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ia_endpoints, "_ia_conversas_atualizar_thread_codex", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ia_endpoints, "logger", SimpleNamespace(warning=lambda *_a, **_k: None, info=lambda *_a, **_k: None))

    result = ia_endpoints.ia_chat(
        IAChatRequest(
            message="Ola",
            page="global",
            context={
                "surface": "sidebar_chat",
                "conversation_mode": "quick_chat",
                "conversation_id": "conv-1",
                "route": {"module": "inicio"},
            },
            conversa_id="conv-1",
            conversa_mensagens=[{"role": "user", "text": "Ola"}],
        ),
        SimpleNamespace(),
        client_id="tenant",
    )

    assert len(attempts) == 2
    assert attempts[0]["thread_id"] == "thread-old"
    assert attempts[1]["thread_id"] == ""
    assert result["model"] == "codex:unavailable"
    assert result["diagnostico_ia"]["provider_policy"] == "codex_only"
    assert result["diagnostico_ia"]["finalization_reason"] == "all_configured_providers_failed"
    assert len(result["diagnostico_ia"]["provider_attempts"]) == 2


def test_frontend_keeps_quick_chat_and_codex_task_contracts_separate():
    source = Path("static/ia-sidebar/09-chat-bootstrap.part.js").read_text(encoding="utf-8")
    assert "surface: 'sidebar_chat'" in source
    assert "conversation_mode: 'quick_chat'" in source
    assert "conversation_id: convAtualId" in source
    assert "mensagensAtuais.slice(-10)" in source
    assert "table_rows:" not in source
    assert "visible_text:" not in source
    assert "_codexCriarTarefa()" in source
    assert "'/api/codex/tasks'" not in source  # task transport remains in its dedicated chunk
