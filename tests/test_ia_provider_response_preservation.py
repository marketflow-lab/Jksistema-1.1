from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import openai_codex
import pytest
from fastapi import HTTPException

from backend.schemas.ia import IAChatRequest
from backend.services import ia as _ia_facade  # noqa: F401 - configura os peers legados
from backend.services import ia_providers
from backend.services import perguntas_pos_venda_pos_venda as post_sale_runtime
from backend.services import perguntas_pos_venda_core as _ppv_facade  # noqa: F401 - binds split runtime peers
from backend.services.codex.console import execution as console_execution
from backend.services.codex.console import security as console_security
from backend.modules.perguntas_pos_venda.ai.api import validate_post_sale_response


class _JsonResponse:
    ok = True
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _run_openai_provider(monkeypatch, response_payload: dict) -> str:
    monkeypatch.setattr(
        ia_providers,
        "logger",
        logging.getLogger("test_ia_provider_response_preservation"),
    )
    monkeypatch.setattr(ia_providers, "_ia_validar_provedor_ativo", lambda *_args: None)
    monkeypatch.setattr(ia_providers, "_obter_openai_api_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(ia_providers, "_ia_chat_eh_saudacao_curta", lambda *_args: False)
    monkeypatch.setattr(ia_providers, "_ia_contexto_desativa_recursos_chat", lambda *_args: True)
    monkeypatch.setattr(
        ia_providers.requests,
        "post",
        lambda *_args, **_kwargs: _JsonResponse(response_payload),
    )
    return ia_providers._chamar_openai_responses(
        IAChatRequest(
            message="Responda ao comprador",
            model="gpt-5.4-nano",
            context={"tipo": "novo_fluxo_perguntas_v2"},
        ),
        "tenant-test",
    )


def _run_codex_provider(
    monkeypatch, tmp_path, final_response: str, *,
    data_selection: dict | None = None, prompts: list[str] | None = None,
    history: list[dict] | None = None,
) -> tuple[str, str]:
    class _Thread:
        id = "thread-preservation-test"

        def run(self, _prompt: str, **_kwargs):
            if prompts is not None:
                prompts.append(_prompt)
            return SimpleNamespace(
                status=SimpleNamespace(value="completed"),
                final_response=final_response,
            )

    class _Codex:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def thread_start(self, **_kwargs):
            return _Thread()

    monkeypatch.setattr(console_execution, "enabled", lambda: True)
    monkeypatch.setattr(console_execution, "sdk_installed", lambda: True)
    monkeypatch.setattr(console_execution, "auth_detected", lambda: True)
    monkeypatch.setattr(console_execution, "runtime_bin", lambda: "codex")
    monkeypatch.setattr(console_execution, "sdk_env", lambda: {})
    monkeypatch.setattr(console_execution, "readonly_config_overrides", lambda: {})
    monkeypatch.setattr(console_security, "readonly_cwd", lambda *_args: str(tmp_path))
    monkeypatch.setattr(openai_codex, "Codex", _Codex)
    monkeypatch.setattr(openai_codex, "CodexConfig", lambda **kwargs: kwargs)
    return ia_providers._chamar_codex_chat_com_thread(
        IAChatRequest(
            message="Responda ao comprador",
            model="codex:gpt-5.5",
            history=history or [],
            context={
                "modulo": "perguntas_pos_venda", "tipo_treinamento": "perguntas_anuncio",
                "desativar_recursos_chat": True,
                **({"data_selection": data_selection} if data_selection is not None else {}),
            },
        ),
        "tenant-test",
        reasoning_effort="medium",
    )


def test_openai_provider_preserves_output_text_byte_for_byte(monkeypatch):
    exact = "\r\n  Resposta com espacos externos.  \n\n"

    assert _run_openai_provider(monkeypatch, {"output_text": exact}) == exact


def test_openai_provider_preserves_every_fallback_part_without_inserted_separator(monkeypatch):
    parts = ["\n  Primeira parte ", " \r\n ", "segunda parte.  \n"]
    response_payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": part}
                    for part in parts
                ]
            }
        ]
    }

    assert _run_openai_provider(monkeypatch, response_payload) == "".join(parts)


def test_gemini_direct_provider_preserves_every_part_byte_for_byte(monkeypatch):
    parts = ["\r\n  Primeira parte ", " \n ", "segunda parte.  \r\n"]
    response_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": part} for part in parts],
                }
            }
        ]
    }
    monkeypatch.setattr(
        ia_providers.requests,
        "post",
        lambda *_args, **_kwargs: _JsonResponse(response_payload),
    )

    result = ia_providers._chamar_gemini_api_direta(
        "gemini-2.5-flash",
        {"contents": []},
        "synthetic-test-key",
    )

    assert result == "".join(parts)


def test_codex_provider_preserves_final_response_byte_for_byte(monkeypatch, tmp_path):
    exact = "\r\n  Resposta final do Codex.  \n\n"

    response, thread_id = _run_codex_provider(monkeypatch, tmp_path, exact)

    assert response == exact
    assert thread_id == "thread-preservation-test"


def test_codex_receives_deep_store_sku_question_and_all_history_without_compaction(monkeypatch, tmp_path):
    literal = "  Sim, o cabo mede 8 metros.  \n"
    prompts: list[str] = []
    long_description = "DESCRICAO_INTEGRAL:" + "D" * 18_000
    packet = {
        "schema_version": 1,
        "evidence": [{
            "function": "store_sku_question_context",
            "result": {
                "identity": {"store_id": "store-A", "seller_id": "seller-A", "site_id": "MLB",
                             "item_id": "MLB100", "variation_id": "V1", "sku": "SKU460"},
                "question": {"text": "Qual o comprimento do cabo elétrico?", "history": [
                    {"role": "buyer", "text": f"Pergunta anterior {index}"} for index in range(20)
                ]},
                "catalog_product_context": {"catalog_document": {
                    "technical_data": {"cable_length": "8 metros", "description": long_description},
                }},
                "access_token": "SECRET_TOKEN_TESTE",
            },
        }],
    }
    chat_history = [{"role": "user", "content": f"Mensagem {index}"} for index in range(15)]

    response, _ = _run_codex_provider(
        monkeypatch, tmp_path, literal, data_selection=packet, prompts=prompts, history=chat_history,
    )

    assert response == literal
    assert len(prompts) == 1
    prompt = prompts[0]
    assert "Qual o comprimento do cabo elétrico?" in prompt
    assert '"store_id":"store-A"' in prompt
    assert '"cable_length":"8 metros"' in prompt
    assert long_description in prompt
    assert "Pergunta anterior 19" in prompt
    assert "Mensagem 14" in prompt
    assert "[limite de profundidade]" not in prompt
    assert "SECRET_TOKEN_TESTE" not in prompt


def test_post_sale_sends_complete_context_and_preserves_model_reply(monkeypatch):
    captured: dict = {}
    literal = "  Olá! Já conferi o pedido e vou cuidar disso com você.  \n"
    document = "DOC_INTEGRAL:" + "Z" * 26_000
    messages = [{"from_role": "buyer", "text": f"Mensagem {index} " + "M" * 800} for index in range(15)]
    listing_chat = [{"role": "buyer", "text": f"Pergunta no anúncio {index}"} for index in range(25)]
    items = [{"id": f"MLB{index}", "sku": f"SKU{index}", "title": f"Produto {index}"} for index in range(10)]
    conversation = {
        "pack_id": "PACK1", "order_id": "ORDER1", "buyer_id": "BUYER1",
        "messages": messages, "buyer_listing_question_chat": listing_chat, "items": items,
        "_resposta_atual": "RESPOSTA_ANTERIOR:" + "R" * 2200,
        "_orientacao_usuario": "ORIENTACAO_INTEGRAL:" + "O" * 1500,
        "_agent_subquestions": [{"question": f"Dúvida {index}"} for index in range(12)],
    }
    pipeline = {"anuncios": [{"id": "MLB9", "description": document,
                               "catalog_product_context": [{"catalog_document": {"technical_document": document}}]}]}

    monkeypatch.setattr(post_sale_runtime, "IAChatRequest", IAChatRequest, raising=False)
    monkeypatch.setattr(post_sale_runtime, "ML_POS_VENDA_IA_V2_MODO", "novo_fluxo_pos_venda_v2", raising=False)
    monkeypatch.setattr(post_sale_runtime, "_ia_modelo_pos_venda_configurado", lambda: "codex:gpt-5.5", raising=False)
    monkeypatch.setattr(
        post_sale_runtime.perguntas_agent_providers,
        "select_response_provider", lambda *_args: {"model": "codex:gpt-5.5", "policy": "codex_primary"},
    )

    def invoke(_tenant, payload, model):
        captured["message"] = payload.message
        return json.dumps({
            "action": "answer",
            "flow": "pre_sale",
            "category": "post_sale_support",
            "subquestions": ["Ajudar o comprador com o pedido."],
            "research_requests": [],
            "answer": literal,
            "confidence": 0.9,
            "reason": "O contexto autenticado ja permite responder.",
            "requires_human_review": False,
            "decision": "not_applicable",
            "commercial_state": "not_applicable",
            "compatibility_analysis": {
                "applicable": False,
                "target": "",
                "decision": "not_applicable",
                "condition": "",
                "missing_fields": [],
                "evidence_refs": [],
            },
            "missing_fact_owner": "none",
            "buyer_detail_needed": "",
        }, ensure_ascii=False), model

    monkeypatch.setattr(post_sale_runtime.perguntas_agent_providers, "invoke_model", invoke)

    reply, model = post_sale_runtime._ml_pos_venda_gerar_resposta_ia(
        "tenant-test", "Loja Teste", conversation, contexto_pipeline=pipeline,
    )

    assert reply == literal
    assert model == "codex:gpt-5.5"
    prompt = captured["message"]
    assert document in prompt
    assert "Mensagem 0" in prompt and "Mensagem 14" in prompt
    assert "Pergunta no anúncio 24" in prompt
    assert "Produto 9" in prompt
    assert conversation["_resposta_atual"] in prompt
    assert conversation["_orientacao_usuario"] in prompt
    assert '"Dúvida 11"' in prompt
    assert "Finalize exatamente com" not in prompt


def test_post_sale_publication_metadata_is_independent_of_reply_text():
    context = {"regras_oficiais": {"precisa_consultar": False},
               "decisao_automacao": {"pode_responder_automaticamente": True}}

    first = validate_post_sale_response("Mensagem natural.", context, 340)
    second = validate_post_sale_response("  " + "Outra frase. " * 100 + "\n", context, 340)

    assert first == second
    assert first["ok"] is True


@pytest.mark.parametrize(
    ("provider", "secret_reader"),
    [("_chamar_openai_responses", "_obter_openai_api_key"),
     ("_chamar_deepseek_chat", "_obter_deepseek_api_key")],
)
def test_ml_reply_provider_without_key_reports_failure_instead_of_fake_draft(
    monkeypatch, provider, secret_reader,
):
    monkeypatch.setattr(ia_providers, "logger", logging.getLogger("test_ml_provider_unavailable"))
    monkeypatch.setattr(ia_providers, "_ia_validar_provedor_ativo", lambda *_args: None)
    monkeypatch.setattr(ia_providers, secret_reader, lambda: "")
    payload = IAChatRequest(
        message="Qual o comprimento do cabo?",
        context={"modulo": "perguntas_pos_venda", "tipo_treinamento": "perguntas_anuncio",
                 "desativar_recursos_chat": True},
    )

    with pytest.raises(HTTPException) as captured:
        getattr(ia_providers, provider)(payload, "tenant-test")

    assert captured.value.status_code == 503


def test_provider_normalizers_treat_whitespace_only_as_empty_without_trimming_valid_text():
    assert ia_providers._extrair_texto_openai_response({"output_text": " \r\n\t "}) == ""
    assert ia_providers._extrair_texto_generate_content(
        {"candidates": [{"content": {"parts": [{"text": " \r\n\t "}]}}]}
    ) == ""


def test_codex_provider_rejects_whitespace_only_response(monkeypatch, tmp_path):
    with pytest.raises(HTTPException) as exc_info:
        _run_codex_provider(monkeypatch, tmp_path, " \r\n\t ")

    assert exc_info.value.status_code == 502
