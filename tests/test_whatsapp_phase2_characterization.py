from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import whatsapp_bridge


def _evidence(status: str, reason: str = "") -> dict:
    conclusive = status in {"complete", "confirmed_zero"}
    return {"schema": "jk.codex.evidence.v1", "status": status,
        "claim_scope": "full" if conclusive else "none", "coverage_complete": conclusive,
        "confidence": "high" if conclusive else "low", "freshness": "live",
        "retryable": status == "unavailable", "reason": reason or status,
        "missing_fields": [] if conclusive else ["decisive_evidence"], "sources": [],
        "attempted_fallbacks": [], "next_sources": []}


def _raw_bling_balance(store: str, quantity, *, auth_expired: bool = False) -> dict:
    ranking = [] if quantity is None else [
        {
            "sku": "001",
            "title": "Produto 001",
            "quantity": quantity,
            "quantity_reliable": True,
        }
    ]
    return {
        "success": True,
        "tool_id": "bling_stock_balances",
        "summary": [
            {
                "tool_id": "bling_stock_balances",
                "loja": store,
                "summary": {
                    "stock_scope": "bling_non_full_only",
                    "full_provider": "mercado_livre_api_only",
                    "chart_data": {
                        "stores": [store],
                        "totals": {
                            "skus": 0 if quantity is None else 1,
                            "store_available": quantity,
                        },
                        "ranking": ranking,
                        "coverage_complete": quantity is not None,
                    },
                    "partial": quantity is None,
                },
            }
        ],
        "warnings": ["Token Bling expirado para esta loja."] if auth_expired else [],
        "evidence": _evidence(
            "unavailable" if auth_expired else "confirmed_zero" if quantity == 0 else "complete",
            "token expirado" if auth_expired else "saldo confirmado",
        ),
    }


def test_phase2_settings_contracts_are_bounded_and_sanitized() -> None:
    assert whatsapp_bridge._normalize_capacity("invalid", 4, 1, 8) == 4
    assert whatsapp_bridge._normalize_capacity(99, 4, 1, 8) == 8
    assert whatsapp_bridge._normalize_conversation_interval(1) == 10
    assert whatsapp_bridge._normalize_progress_interval(999) == 60
    assert whatsapp_bridge._normalize_phone_ai_behavior("  Linha   um\r\nLinha\t dois\x00  ") == "Linha um\nLinha dois"

    settings = whatsapp_bridge._normalize_phone_notification_settings(
        {
            "label": "  Telefone   principal  ",
            "send_ml_question_suggestions": False,
            "send_weekly_report": True,
            "ai_behavior": " Responda   curto ",
            "allow_voice_calls": True,
        }
    )
    assert settings == {
        "label": "Telefone principal",
        "send_ml_question_suggestions": False,
        "ai_behavior": "Responda curto",
    }


def test_phase2_settings_reject_invalid_models_and_keep_dual_defaults() -> None:
    with pytest.raises(HTTPException):
        whatsapp_bridge._normalize_codex_agent_model("modelo invalido!", "gpt-5.6-sol")
    settings = whatsapp_bridge._whatsapp_dual_agent_settings({})
    assert settings["agent_architecture"] == "dual_codex"
    assert settings["conversation_agent_model"] == "gpt-5.6-luna"
    assert settings["task_agent_model"] == "gpt-5.6-sol"
    assert settings["task_agent_reasoning"] == "low"
    assert settings["preserve_order_per_phone"] is True


def test_phase2_media_contracts_parse_and_validate_references(tmp_path: Path) -> None:
    text = "Veja ![frente](https://example.test/a.jpg) e ![lado](https://example.test/b.png)."
    assert whatsapp_bridge._whatsapp_image_requested("mande a foto do SKU 001") is True
    assert whatsapp_bridge._whatsapp_image_references(text) == [
        ("frente", "https://example.test/a.jpg"),
        ("lado", "https://example.test/b.png"),
    ]
    assert whatsapp_bridge._whatsapp_strip_image_references(text) == "Veja  e ."
    assert whatsapp_bridge._whatsapp_image_matches_skus(Path("001.jpg"), ["001"]) is True
    image_path = tmp_path / "produto.jpeg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0imagem")
    assert whatsapp_bridge._whatsapp_image_mime(image_path) == "image/jpeg"
    assert whatsapp_bridge._safe_filename(" ../produto 001?.jpg ", "arquivo.bin") == "produto 001-.jpg"
    assert whatsapp_bridge._media_extension("image/png") == ".png"

    root = tmp_path.resolve()
    assert whatsapp_bridge._whatsapp_path_within(root / "produto.jpg", [root]) is True
    assert whatsapp_bridge._whatsapp_path_within(root.parent / "fora.jpg", [root]) is False


def test_phase2_message_contracts_keep_phone_and_prompt_behavior(monkeypatch) -> None:
    config = {"subject_id": "subject-1", "personal_phone": "+55 (31) 99999-0000"}
    message = {"message_id": "wamid.1", "subject_id": "subject-1", "text_body": "mande a foto do SKU 001"}
    assert whatsapp_bridge._message_phone(config, message) == "5531999990000"
    assert whatsapp_bridge._normalize_registered_phone("+55 (31) 99999-0000") == "5531999990000"
    assert whatsapp_bridge._mask_phone("5531999990000") == "+55 **** *** 0000"
    assert whatsapp_bridge._message_request_text(message, {"success": True, "text": "audio transcrito"}) == (
        "mande a foto do SKU 001\n\naudio transcrito"
    )

    prompt = whatsapp_bridge._message_prompt(
        message,
        None,
        None,
        mobile_full_access=True,
        query_policy={"mode": "query_only", "domains": ["estoque"], "store": "JK Pecas"},
        ai_behavior="Seja objetivo",
    )
    assert "usuario full" in prompt
    assert "Seja objetivo" in prompt
    assert "exclusivamente a loja exata JK Pecas" in prompt
    assert "foto do cadastro" in prompt

    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5511999999999")
    first_conversation = whatsapp_bridge._conversation_id(config, message)
    monkeypatch.setattr(whatsapp_bridge, "_message_phone", lambda *_args: "5521999999999")
    assert whatsapp_bridge._conversation_id(config, message) != first_conversation


def test_phase2_intent_contracts_preserve_readonly_and_store_scope() -> None:
    assert whatsapp_bridge._whatsapp_query_only_domains("relatorio de vendas do Mercado Livre") == [
        "vendas",
        "anuncios_ml",
    ]
    assert whatsapp_bridge._whatsapp_readonly_inquiry("qual o saldo em estoque?") is True
    assert whatsapp_bridge._whatsapp_mutation_intent("qual o saldo em estoque?") is False
    assert whatsapp_bridge._whatsapp_mutation_intent("altere o estoque agora") is True
    assert whatsapp_bridge._whatsapp_protected_mutation_domains("pause o anuncio do Mercado Livre") == [
        "anuncios_ml"
    ]
    assert whatsapp_bridge._whatsapp_exact_store_matches(
        "consulte a JK Pecas",
        ["JK", "JK Pecas", "Deckas"],
    ) == ["JK Pecas"]
    assert whatsapp_bridge._whatsapp_all_stores_requested("compare todas as lojas") is True
    assert whatsapp_bridge._whatsapp_store_scoped_request("vendas deste mes") is True
    assert whatsapp_bridge._whatsapp_pagination_request("mais resultados") is True
    assert whatsapp_bridge._whatsapp_contextual_report_request("relatorio deste mes na mesma loja") is True


def test_phase2_retry_contracts_keep_security_and_coverage_rules() -> None:
    assert whatsapp_bridge._dual_retry_classification("HTTP 403 token expirado") == ("authentication", False)
    assert whatsapp_bridge._dual_retry_classification("HTTP 429") == ("rate_limited", True)
    assert whatsapp_bridge._dual_retry_classification("HTTP 504 timeout") == ("transient_dependency", True)
    assert [whatsapp_bridge._dual_retry_delay_seconds(index, "", "job") for index in range(1, 4)] == [2, 5, 15]

    completed = {
        "status": "completed",
        "verified_facts": ["Saldo 10"],
        "sources": ["Bling"],
        "confidence": "high",
        "evidence_sufficient": True,
        "coverage_complete": True,
    }
    assert whatsapp_bridge._dual_worker_disposition({}, completed) == "completed"
    completed["coverage_complete"] = False
    completed["verified_facts"] = ["Nenhum registro encontrado"]
    assert whatsapp_bridge._dual_worker_disposition({}, completed) == "waiting_retry"


def test_phase2_tool_result_contracts_keep_zero_auth_and_partial_evidence() -> None:
    positive = whatsapp_bridge._function_manager_compact_result(_raw_bling_balance("JK Pecas", 12))
    zero = whatsapp_bridge._function_manager_compact_result(_raw_bling_balance("Deckas", 0))
    expired = whatsapp_bridge._function_manager_compact_result(
        _raw_bling_balance("Uai Mineirinho", None, auth_expired=True)
    )
    assert positive["stock_balance"]["store_available"] == 12
    assert positive["evidence"]["status"] == "complete"
    assert zero["stock_balance"]["store_available"] == 0
    assert zero["evidence"]["status"] == "confirmed_zero"
    assert expired["stock_balance"]["auth_failed"] is True
    assert expired["error_class"] == "authentication"
    assert expired["evidence"]["status"] == "unavailable"

    for store, item in (("JK Pecas", positive), ("Deckas", zero), ("Uai Mineirinho", expired)):
        item.update({"manager_store": store, "manager_required": True})
    evidence = whatsapp_bridge._function_manager_evidence({}, [positive, zero, expired])
    assert evidence["answerable"] is True
    assert evidence["evidence_sufficient"] is False
    assert evidence["coverage_complete"] is False
    assert any(
        item.get("store") == "Uai Mineirinho" and item.get("status") == "unavailable"
        for item in evidence["validations"]
    )


def test_phase2_tool_contracts_cover_marketplace_local_and_timeout() -> None:
    listing = whatsapp_bridge._marketplace_listing_stock_contract(
        {
            "evidence": _evidence("complete"),
            "top_rows": [{"id": "MLB1", "seller_sku": "001", "available_quantity": 7}],
        }
    )
    local = whatsapp_bridge._local_stock_contract(
        {
            "evidence": _evidence("complete"),
            "summary": [
                {
                    "tool_id": "stock_data",
                    "summary": {
                        "found": True,
                        "sku": "001",
                        "nome": "Produto 001",
                        "saldo_loja_total": 5,
                        "saldo_full_total": 2,
                        "saldo_total": 7,
                    },
                }
            ],
        }
    )
    timeout = whatsapp_bridge._normalize_tool_result_contract(
        {"success": False, "error": "HTTP 504 timeout", "data": [], "evidence": _evidence("unavailable", "timeout")}
    )
    assert listing["confirmed"] is True
    assert listing["rows"][0]["available_quantity"] == 7
    assert local["confirmed"] is True
    assert local["total_available"] == 7
    assert timeout["evidence"]["status"] == "unavailable"
    assert timeout["retryable"] is True
