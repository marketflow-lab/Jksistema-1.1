from __future__ import annotations

import json
from types import SimpleNamespace

from backend.schemas import IAChatRequest
from backend.services import codex_assistant, codex_data_selection_agent, ia_endpoints


def _request(username: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(username=username), headers={})


def _plan(*, action: str, calls: list[dict] | None = None, missing: list[str] | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "action": action,
        "intents": ["test"],
        "entities": {
            "sku": "001" if action == "collect" else "",
            "mlb": "",
            "order_id": "",
            "period": "",
            "store_ref": "JK Pecas" if action == "collect" else "",
            "store_mode": "single" if action == "collect" else "none",
        },
        "requested_fields": ["stock"] if action == "collect" else [],
        "tool_calls": list(calls or []),
        "context_hub": {
            "mode": "not_applicable",
            "query": "",
            "filters": {},
            "top_k": 0,
            "snippet_max_chars": 0,
        },
        "missing_user_fields": list(missing or []),
        "confidence": 0.95,
        "reason": "teste",
    }


def _server_context(monkeypatch) -> None:
    monkeypatch.setattr(
        ia_endpoints,
        "_ia_chat_selection_permission_catalog",
        lambda _client_id, _username: (
            {"full": True},
            [{
                "id": "bling_stock_balances",
                "read_only": True,
                "input_schema": {"properties": {"sku": {}, "loja": {}}},
            }],
            True,
        ),
    )
    monkeypatch.setattr(
        ia_endpoints,
        "_ia_chat_selection_authorized_stores",
        lambda _client_id: (["JK Pecas"], True),
    )


def test_system_general_question_executes_zero_tools(monkeypatch):
    _server_context(monkeypatch)
    monkeypatch.setattr(
        codex_data_selection_agent.DATA_SELECTION_RUNTIME,
        "plan",
        lambda **_kwargs: _plan(action="answer_without_data"),
    )
    monkeypatch.setattr(
        codex_assistant,
        "codex_assistant_execute_tool_call",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("nenhuma ferramenta deveria executar")),
    )

    envelope, evidence = ia_endpoints._ia_chat_prepare_data_selection(
        IAChatRequest(message="Explique o que e margem."),
        "tenant-a",
        _request(),
        username="admin",
    )

    assert envelope["action"] == "answer_without_data"
    assert envelope["selected_tools"] == []
    assert evidence == []


def test_system_continuation_passes_only_minimal_recent_turns_to_selector(monkeypatch):
    _server_context(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(
        codex_data_selection_agent.DATA_SELECTION_RUNTIME,
        "plan",
        lambda **kwargs: captured.update(kwargs) or _plan(action="answer_without_data"),
    )
    payload = IAChatRequest(
        message="E esse SKU?",
        history=[
            {"role": "user", "content": "Consulte o SKU 001."},
            {"role": "assistant", "content": "O SKU 001 foi localizado."},
        ],
        context={"visible_text": "TELA_BRUTA_NAO_DEVE_IR", "selection": {"sku": "001"}},
    )

    ia_endpoints._ia_chat_prepare_data_selection(
        payload,
        "tenant-a",
        _request(),
        username="admin",
    )

    assert captured["conversation_anchors"]["recent_turns"] == [
        {"role": "user", "content": "Consulte o SKU 001."},
        {"role": "assistant", "content": "O SKU 001 foi localizado."},
    ]
    assert "visible_text" not in captured["conversation_anchors"]


def test_system_executes_only_agent_selected_read_only_call(monkeypatch):
    _server_context(monkeypatch)
    monkeypatch.setattr(
        codex_data_selection_agent.DATA_SELECTION_RUNTIME,
        "plan",
        lambda **_kwargs: _plan(
            action="collect",
            calls=[{
                "tool_id": "bling_stock_balances",
                "arguments": json.dumps({"sku": "001", "loja": "JK Pecas"}),
                "required": True,
                "reason": "saldo atual",
                "depends_on": [],
            }],
        ),
    )
    captured: list[dict] = []

    def execute(**kwargs):
        captured.append(kwargs)
        return {
            "tool_id": kwargs["tool_id"],
            "success": True,
            "records": 1,
            "rows": [{"sku": "001", "available": 7}],
            "sources_human": ["Bling"],
        }

    monkeypatch.setattr(codex_assistant, "codex_assistant_execute_tool_call", execute)
    envelope, evidence = ia_endpoints._ia_chat_prepare_data_selection(
        IAChatRequest(message="Qual o estoque do SKU 001?"),
        "tenant-a",
        _request(),
        username="admin",
    )

    assert [item["tool_id"] for item in captured] == ["bling_stock_balances"]
    assert captured[0]["client_id"] == "tenant-a"
    assert captured[0]["args"]["loja"] == "JK Pecas"
    assert envelope["selected_tools"] == ["bling_stock_balances"]
    assert evidence[0]["success"] is True


def test_system_planner_failure_returns_unavailable_without_tools(monkeypatch):
    _server_context(monkeypatch)
    monkeypatch.setattr(
        codex_data_selection_agent.DATA_SELECTION_RUNTIME,
        "plan",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("secret must not escape")),
    )

    envelope, evidence = ia_endpoints._ia_chat_prepare_data_selection(
        IAChatRequest(message="consulta"),
        "tenant-a",
        _request(),
        username="admin",
    )

    assert envelope["status"] == "selection_unavailable"
    assert evidence == []
    assert "secret must not escape" not in json.dumps(envelope)


def test_system_context_hub_evidence_is_limited_to_six_short_snippets():
    compact = ia_endpoints._ia_chat_selection_compact_result(
        "context_hub_search",
        {
            "success": True,
            "results": [
                {"doc_id": f"doc-{index}", "snippet": "x" * 1000, "score": 1.0}
                for index in range(10)
            ],
        },
    )

    assert compact["success"] is True
    assert len(compact["rows"]) == 6
    assert all(len(row["snippet"]) == 320 for row in compact["rows"])


def test_legacy_deterministic_chat_route_delegates_to_unified_flow(monkeypatch):
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_require_full_admin",
        lambda *_args, **_kwargs: {"client_id": "tenant-a", "username": "admin", "permissions": {"full": True}},
    )
    monkeypatch.setattr(
        ia_endpoints,
        "ia_chat",
        lambda payload, request, client_id: {
            "success": True,
            "resposta": f"unificado:{payload.message}",
            "model": "codex:test",
            "tool_results": [],
        },
    )
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_collect_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("roteador deterministico reativado")),
    )

    response = codex_assistant.codex_assistant_chat(
        codex_assistant.CodexAssistantChatRequest(message="ola", screen_context={}),
        _request(),
    )

    assert response["answer"] == "unificado:ola"
    assert response["context"]["tool_plan"]["managed_by"] == "CodexDataSelectionAgent"
