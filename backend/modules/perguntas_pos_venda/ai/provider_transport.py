"""Low-level model transport without dependencies on the PPV state graph."""

from __future__ import annotations

import os

from backend.schemas.ia import IAChatRequest
from backend.services.ia_providers import (
    _chamar_codex_chat,
    _chamar_codex_chat_com_thread,
    _chamar_deepseek_chat,
    _chamar_gemini_chat,
    _chamar_openai_responses,
    _chamar_vertex_ai_chat,
    _codex_modelo_nome_curto,
    _gemini_nome_curto,
    _modelo_eh_codex,
    _modelo_eh_gemini_api,
    _modelo_eh_vertex_ai,
    _vertex_ai_modelo_padrao,
    _vertex_modelo_nome_curto,
)


def _model_adapter(name: str, default):
    from .runtime import resolve_runtime_adapter

    return resolve_runtime_adapter("models", name, default)


def invoke_model(client_id: str, payload: IAChatRequest, model_req: str) -> tuple[str, str]:
    if _modelo_eh_codex(model_req):
        context = payload.context if isinstance(payload.context, dict) else {}
        on_thread_ready = context.pop("_codex_on_thread_ready", None)
        thread_id = str(context.get("_codex_thread_id") or "").strip()
        if context.get("_codex_persist_thread") or thread_id:
            response, resulting_thread_id = _model_adapter("call_codex_thread", _chamar_codex_chat_com_thread)(
                payload,
                client_id,
                thread_id=thread_id,
                persist_thread=True,
                conversation_key=str(context.get("_codex_conversation_key") or context.get("_codex_job_id") or ""),
                active_turn_key=str(context.get("_codex_active_turn_key") or context.get("_codex_job_id") or ""),
                on_thread_ready=on_thread_ready if callable(on_thread_ready) else None,
            )
            context["_codex_thread_id_result"] = resulting_thread_id
            payload.context = context
        else:
            response = _model_adapter("call_codex", _chamar_codex_chat)(payload, client_id)
        return response, f"codex:{_codex_modelo_nome_curto(model_req)}"
    if _modelo_eh_vertex_ai(model_req):
        response = _model_adapter("call_vertex", _chamar_vertex_ai_chat)(payload, client_id)
        return response, f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    if _modelo_eh_gemini_api(model_req):
        response = _model_adapter("call_gemini", _chamar_gemini_chat)(payload, client_id)
        return response, f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    if model_req.startswith("deepseek-"):
        return _model_adapter("call_deepseek", _chamar_deepseek_chat)(payload, client_id), model_req
    response = _model_adapter("call_openai", _chamar_openai_responses)(payload, client_id)
    return response, model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()


__all__ = ["invoke_model"]
