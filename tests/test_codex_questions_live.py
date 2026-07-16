from __future__ import annotations

from typing import Any

from backend.services import codex_assistant, codex_readonly_sources, perguntas_pos_venda_endpoints


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
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_api_query_audit", lambda *_args, **_kwargs: None)


def test_questions_tool_contract_is_live_store_scoped_and_authoritative() -> None:
    meta = codex_assistant._assistant_tool_meta("questions_post_sale_query")
    schema = codex_assistant._assistant_tool_input_schema("questions_post_sale_query")

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

    complete = codex_assistant.codex_assistant_execute_tool_call(
        "tenant-complete",
        "questions_post_sale_query",
        {"mensagem": "Consulte a fila atual", "todas_lojas": True, "force_refresh": True},
        permissions={"perguntas_pos_venda": True},
    )

    assert complete["records"] == 0
    assert complete["dados_suficientes"] is True
    assert complete["empty_reason"] == ""
    assert complete["tool_validation"]["confidence"] == "alta"
    assert "todas as lojas" in complete["tool_validation"]["motivo"].lower()

    _patch_live_api(
        monkeypatch,
        [_store("JK Peças"), _store("Deckas")],
        {"JK Peças": [], "Deckas": RuntimeError("API indisponível")},
    )
    partial = codex_assistant.codex_assistant_execute_tool_call(
        "tenant-partial",
        "questions_post_sale_query",
        {"mensagem": "Consulte a fila atual", "todas_lojas": True, "force_refresh": True},
        permissions={"perguntas_pos_venda": True},
    )

    assert partial["records"] == 0
    assert partial["dados_suficientes"] is False
    assert partial["tool_validation"]["confidence"] == "baixa"
    assert "Deckas" in partial["empty_reason"]


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
