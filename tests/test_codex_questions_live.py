from __future__ import annotations

from typing import Any

from backend.modules.perguntas_pos_venda.endpoints import api as perguntas_pos_venda_endpoints
from backend.services import codex_readonly_sources
from backend.services.codex.assistant import catalog as assistant_catalog
from backend.services.codex.assistant import execution as assistant_execution
from backend.services.codex.assistant import normalization as assistant_normalization
from backend.services.codex.assistant import routing as assistant_routing


def _store(name: str, *, connected: bool = True) -> dict[str, Any]:
    return {
        "nome": name,
        "mercadolivre_conectado": connected,
        "mercadolivre_status": "conectado" if connected else "reautenticar",
        "mercadolivre_motivo": "" if connected else "Conta desconectada.",
    }


def _question(question_id: int, text: str = "Serve neste produto?") -> dict[str, Any]:
    return {
        "id": question_id,
        "status": "UNANSWERED",
        "text": text,
        "item_id": "MLB123456789",
        "item_sku": "SKU-1",
        "item_title": "Produto de teste",
        "item_permalink": "https://produto.mercadolivre.com.br/MLB-123456789",
        "buyer_question_history": [{"id": question_id - 1, "text": "Pergunta anterior"}],
        "buyer_question_history_count": 1,
    }


def _patch_live_api(monkeypatch, stores: list[dict[str, Any]], responses: dict[str, Any]) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        perguntas_pos_venda_endpoints,
        "ml_perguntas_listar_lojas",
        lambda _client_id: {"success": True, "lojas": stores},
    )

    def fake_list(*, loja: str, status: str | None, **_kwargs: Any) -> dict[str, Any]:
        calls.append((loja, str(status or "")))
        value = responses.get(loja, [])
        if isinstance(value, Exception):
            raise value
        questions = list(value or [])
        return {
            "success": True,
            "loja": loja,
            "total": len(questions),
            "retornadas": len(questions),
            "status_resumo": {"UNANSWERED": len(questions)} if questions else {},
            "interrompido": False,
            "questions": questions,
        }

    monkeypatch.setattr(perguntas_pos_venda_endpoints, "ml_listar_perguntas", fake_list)
    return calls


def _disable_assistant_cache_and_audit(monkeypatch) -> None:
    monkeypatch.setattr(assistant_execution, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(assistant_execution, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(assistant_execution, "_assistant_api_query_audit", lambda *_args, **_kwargs: None)


def test_questions_tool_contract_is_live_store_scoped_and_authoritative() -> None:
    meta = assistant_catalog._assistant_tool_meta("questions_post_sale_query")
    schema = assistant_catalog._assistant_tool_input_schema("questions_post_sale_query")

    assert meta["external"] is True
    assert meta["read_only"] is True
    assert meta["cache_ttl_seconds"] == 15
    assert meta["zero_is_authoritative"] is True
    assert meta["fallbacks"] == []
    assert {"loja", "status", "todas_lojas", "force_refresh"} <= set(schema)


def test_all_stores_uses_live_queue_and_preserves_product_and_buyer_history(monkeypatch) -> None:
    calls = _patch_live_api(
        monkeypatch,
        [_store("JK Peças"), _store("Uai Mineirinho")],
        {"JK Peças": [_question(101)], "Uai Mineirinho": []},
    )

    result = codex_readonly_sources.questions_post_sale_query(
        client_id="tenant",
        message="Tem perguntas para responder em todas as lojas?",
        all_stores=True,
        limit=50,
    )

    assert set(calls) == {("JK Peças", "UNANSWERED"), ("Uai Mineirinho", "UNANSWERED")}
    assert result["live_query"] is True
    assert result["coverage_complete"] is True
    assert result["pending_by_store"] == {"JK Peças": 1, "Uai Mineirinho": 0}
    assert result["record_count"] == 1
    row = result["records"][0]
    assert row["loja"] == "JK Peças"
    assert row["item_title"] == "Produto de teste"
    assert row["buyer_question_history_count"] == 1
    assert row["buyer_question_history"][0]["text"] == "Pergunta anterior"


def test_store_name_inside_message_limits_the_live_query_to_that_store(monkeypatch) -> None:
    calls = _patch_live_api(
        monkeypatch,
        [_store("JK Peças"), _store("Uai Mineirinho")],
        {"JK Peças": [_question(101)], "Uai Mineirinho": [_question(202)]},
    )

    result = codex_readonly_sources.questions_post_sale_query(
        client_id="tenant",
        message="Consulte as perguntas pendentes da Uai Mineirinho",
    )

    assert calls == [("Uai Mineirinho", "UNANSWERED")]
    assert result["pending_by_store"] == {"Uai Mineirinho": 1}
    assert result["records"][0]["id"] == 202


def test_disconnected_store_is_not_reported_as_zero(monkeypatch) -> None:
    calls = _patch_live_api(
        monkeypatch,
        [_store("JK Peças"), _store("Deckas", connected=False)],
        {"JK Peças": []},
    )

    result = codex_readonly_sources.questions_post_sale_query(
        client_id="tenant",
        message="Consulte todas as contas",
        all_stores=True,
    )

    assert calls == [("JK Peças", "UNANSWERED")]
    assert result["pending_by_store"] == {"JK Peças": 0, "Deckas": None}
    assert result["coverage_complete"] is False
    assert result["stores_failed"] == 1
    assert result["errors_by_store"] == {"Deckas": "Conta desconectada."}


def test_complete_live_zero_is_sufficient_but_partial_zero_is_not(monkeypatch) -> None:
    _disable_assistant_cache_and_audit(monkeypatch)
    _patch_live_api(
        monkeypatch,
        [_store("JK Peças"), _store("Uai Mineirinho")],
        {"JK Peças": [], "Uai Mineirinho": []},
    )

    complete = assistant_execution.execute_tool_call(
        "tenant-complete",
        "questions_post_sale_query",
        {"mensagem": "Consulte a fila atual", "todas_lojas": True, "force_refresh": True},
        permissions={"perguntas_pos_venda": True},
    )

    assert complete["records"] == 0
    assert complete["evidence"]["status"] == "confirmed_zero"
    assert complete["empty_reason"] == ""
    assert complete["evidence"]["confidence"] == "high"
    assert complete["evidence"]["coverage_complete"] is True

    _patch_live_api(
        monkeypatch,
        [_store("JK Peças"), _store("Deckas")],
        {"JK Peças": [], "Deckas": RuntimeError("API indisponível")},
    )
    partial = assistant_execution.execute_tool_call(
        "tenant-partial",
        "questions_post_sale_query",
        {"mensagem": "Consulte a fila atual", "todas_lojas": True, "force_refresh": True},
        permissions={"perguntas_pos_venda": True},
    )

    assert partial["records"] == 0
    assert partial["evidence"]["status"] == "unavailable"
    assert partial["evidence"]["confidence"] == "low"
    assert "Deckas" in partial["empty_reason"]


def test_string_false_does_not_expand_question_scope_or_keep_stale_product(monkeypatch) -> None:
    _disable_assistant_cache_and_audit(monkeypatch)
    calls = _patch_live_api(
        monkeypatch,
        [_store("Loja A"), _store("Loja B")],
        {"Loja A": [], "Loja B": [_question(404)]},
    )
    captured_plan: dict[str, Any] = {}

    def capture_cache_key(client_id: str, tool_id: str, plan: dict[str, Any]) -> str:
        del client_id, tool_id
        captured_plan.update(plan)
        return "questions-scope-test"

    monkeypatch.setattr(assistant_execution, "_assistant_external_cache_key", capture_cache_key)

    result = assistant_execution.execute_tool_call(
        "tenant-scope",
        "questions_post_sale_query",
        {
            "mensagem": "Perguntei se tem perguntas em aberto para responder",
            "loja": "Loja A",
            "sku": "SKU-ANTIGO",
            "mlb": "MLB123456789",
            "todas_lojas": "false",
            "force_refresh": True,
        },
        permissions={"perguntas_pos_venda": True},
    )

    assert calls == [("Loja A", "UNANSWERED")]
    assert result["records"] == 0
    assert result["evidence"]["status"] == "confirmed_zero"
    assert captured_plan["separar_por_loja"] is False
    assert captured_plan["sku"] == ""
    assert captured_plan["item_id"] == ""
    assert captured_plan["mlb"] == ""


def test_readonly_dispatch_parses_string_booleans_strictly(monkeypatch) -> None:
    calls = _patch_live_api(
        monkeypatch,
        [_store("Loja A"), _store("Loja B")],
        {"Loja A": [], "Loja B": []},
    )

    codex_readonly_sources.execute_readonly_source_tool(
        client_id="tenant",
        tool_id="questions_post_sale_query",
        message="Tem perguntas?",
        loja="Loja A",
        args={"all_stores": "false"},
    )
    assert calls == [("Loja A", "UNANSWERED")]

    calls.clear()
    codex_readonly_sources.execute_readonly_source_tool(
        client_id="tenant",
        tool_id="questions_post_sale_query",
        message="Tem perguntas?",
        loja="Loja A",
        args={"all_stores": "true"},
    )
    assert set(calls) == {("Loja A", "UNANSWERED"), ("Loja B", "UNANSWERED")}


def test_broad_open_questions_plan_uses_only_the_authoritative_live_queue() -> None:
    message = "Perguntei se tem perguntas em aberto para responder"
    stale_screen_context = {
        "sku": "SKU-ANTIGO",
        "selected_sku": "SKU-ANTIGO",
        "mlb": "MLB123456789",
    }

    plan = assistant_routing._assistant_registry_plan(
        "tenant",
        message,
        stale_screen_context,
        "chat",
    )

    assert plan["selected_tools"] == ["questions_post_sale_query"]
    assert plan["source_policy"]["required_tools"] == ["questions_post_sale_query"]
    assert plan["source_policy"]["force_refresh"] is True
    assert plan["sku"] == ""


def test_short_queue_question_is_not_confused_with_a_question_to_the_assistant() -> None:
    assert assistant_normalization._assistant_is_broad_open_questions_query("Tem perguntas?") is True
    assert assistant_normalization._assistant_is_broad_open_questions_query("Voce tem perguntas?") is False
    assert assistant_normalization._assistant_is_broad_open_questions_query("Black Jhon tem perguntas?") is False
    assert "questions_post_sale_query" not in assistant_routing._assistant_select_tool_ids(
        "Black Jhon tem perguntas?",
        "chat",
        {},
    )


def test_mercado_livre_question_fallback_uses_the_same_live_queue(monkeypatch) -> None:
    calls = _patch_live_api(
        monkeypatch,
        [_store("JK Peças")],
        {"JK Peças": [_question(303)]},
    )

    result = codex_readonly_sources.mercado_livre_readonly(
        client_id="tenant",
        message="Existe pergunta aberta na JK Peças?",
        loja="JK Peças",
        limit=50,
    )

    assert calls == [("JK Peças", "UNANSWERED")]
    assert result["tool_id"] == "mercado_livre_readonly"
    assert result["records"][0]["id"] == 303
    assert not any("unexpected keyword argument" in warning for warning in result["warnings"])
