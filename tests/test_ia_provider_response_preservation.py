from __future__ import annotations

import logging
from types import SimpleNamespace

import openai_codex
import pytest
from fastapi import HTTPException

from backend.schemas.ia import IAChatRequest
from backend.services import ia as _ia_facade  # noqa: F401 - configura os peers legados
from backend.services import ia_providers
from backend.services.codex.console import execution as console_execution
from backend.services.codex.console import security as console_security


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


def _run_codex_provider(monkeypatch, tmp_path, final_response: str) -> tuple[str, str]:
    class _Thread:
        id = "thread-preservation-test"

        def run(self, _prompt: str, **_kwargs):
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
            context={"modulo": "perguntas_pos_venda", "tipo_treinamento": "perguntas_anuncio"},
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


def test_provider_normalizers_treat_whitespace_only_as_empty_without_trimming_valid_text():
    assert ia_providers._extrair_texto_openai_response({"output_text": " \r\n\t "}) == ""
    assert ia_providers._extrair_texto_generate_content(
        {"candidates": [{"content": {"parts": [{"text": " \r\n\t "}]}}]}
    ) == ""


def test_codex_provider_rejects_whitespace_only_response(monkeypatch, tmp_path):
    with pytest.raises(HTTPException) as exc_info:
        _run_codex_provider(monkeypatch, tmp_path, " \r\n\t ")

    assert exc_info.value.status_code == 502
