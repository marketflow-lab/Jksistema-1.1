"""Optional, last-resort response provider for Black Jhon WhatsApp.

Codex remains the semantic core.  This module is called only after two failed
Codex conversation attempts and only when the explicit configuration policy
allows the configured provider fallback.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.schemas import IAChatRequest
from backend.services.whatsapp import black_jhon_prompting, settings


FALLBACK_FAILURE_THRESHOLD = 2
TERMINAL_UNAVAILABLE_MESSAGE = (
    "O servico de IA esta temporariamente indisponivel. Tente novamente em instantes."
)


def _provider_reply(model: str, payload: IAChatRequest, client_id: str) -> tuple[str, str]:
    from backend.services import ia as ia_service

    if ia_service._modelo_eh_vertex_ai(model):
        return (
            str(ia_service._chamar_vertex_ai_chat(payload, client_id) or ""),
            f"vertex:{ia_service._vertex_modelo_nome_curto(model)}",
        )
    if ia_service._modelo_eh_gemini_api(model):
        return (
            str(ia_service._chamar_gemini_chat(payload, client_id) or ""),
            f"gemini:{ia_service._gemini_nome_curto(model)}",
        )
    if model.startswith("deepseek-"):
        return str(ia_service._chamar_deepseek_chat(payload, client_id) or ""), model
    return str(ia_service._chamar_openai_responses(payload, client_id) or ""), model


def configured_fallback_reply(
    config: Mapping[str, Any],
    *,
    failure_count: int,
    client_id: str,
    event_type: str,
    user_message: str,
    worker_result: Optional[Mapping[str, Any]] = None,
    resolved_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Return one ready-to-send fallback reply or an empty mapping.

    The function never selects a provider based on question complexity.  It
    activates only for the explicit fallback policy after the fixed Codex
    failure threshold.
    """

    source = dict(config) if isinstance(config, Mapping) else {}
    ai = settings.ai_settings(source)
    if (
        int(failure_count or 0) < FALLBACK_FAILURE_THRESHOLD
        or ai.get("response_provider_policy") != "codex_then_configured_fallback"
        or ai.get("fallback_provider") == "codex"
    ):
        return {}
    event = str(event_type or "").strip()
    if event not in {"user_message", "worker_partial", "worker_result"}:
        return {}

    evidence = black_jhon_prompting.normalize_evidence_envelope_v2(worker_result or {})
    context_json = black_jhon_prompting.bounded_context_json(
        {
            "objective": str(user_message or "")[:3500],
            "confirmed_context": dict(resolved_context or {}),
            "constraints": [
                "Responda em portugues do Brasil e somente com o texto pronto para WhatsApp.",
                "Nao use titulo, assinatura, emoji ou nomes internos de agentes/modelos.",
                "Nao invente fatos, numeros, fontes ou cobertura.",
                "Numeros so podem ser confirmados quando evidence_sufficient e coverage_complete permitirem.",
                "Com cobertura parcial, declare exatamente a lacuna. Faca no maximo uma pergunta indispensavel.",
            ],
            "evidence": evidence,
            "expected_result": "Uma resposta curta e natural; nenhuma explicacao interna.",
        }
    )
    payload = IAChatRequest(
        message=(
            "Fallback operacional do Black Jhon apos duas falhas do Codex. "
            "Use exclusivamente o pacote JSON a seguir. Se ele nao sustentar a resposta, informe a indisponibilidade.\n"
            + context_json
        ),
        page="WhatsApp - Black Jhon - fallback operacional",
        context={"origem": "whatsapp", "read_only": True, "event_type": event},
        history=[],
        attachments=[],
        model=str(ai.get("fallback_model") or ""),
        tool_results=[],
        fallback_read_only=True,
    )
    reply, effective_model = _provider_reply(
        str(ai.get("fallback_model") or ""), payload, str(client_id or "default")
    )
    reply = str(reply or "").strip()[:3500]
    if not reply:
        return {}
    return {
        "action": "reply",
        "reply_text": reply,
        "response_provider": str(ai.get("fallback_provider") or ""),
        "effective_model": effective_model[:100],
        "codex_failure_count": int(failure_count),
        "fallback_after_codex_failures": True,
    }


def conversation_decision_valid(value: Mapping[str, Any], user_message: str) -> bool:
    action = str(value.get("action") or "").strip()
    if action not in {"reply", "request_information", "delegate", "queue", "steer", "cancel_job", "wait"}:
        return False
    if action in {"reply", "request_information", "steer", "cancel_job"}:
        return bool(str(value.get("reply_text") or "").strip())
    if action in {"delegate", "queue"}:
        return bool(str(value.get("job_prompt") or user_message or "").strip())
    return True


def resolve_conversation_decision(
    invoke: Any,
    *,
    initial_thread_id: str,
    config: Mapping[str, Any],
    client_id: str,
    event_type: str,
    user_message: str,
    worker_result: Optional[Mapping[str, Any]],
    conversation_state: Mapping[str, Any],
) -> tuple[dict[str, Any], int, str]:
    """Try Codex twice, then the explicitly configured operational fallback."""

    first_error = ""
    failure_count = 0
    try:
        decision = invoke(initial_thread_id)
    except Exception as exc:
        decision = {}
        first_error = str(exc)[:500]
    if not conversation_decision_valid(decision, user_message):
        failure_count += 1
        try:
            decision = invoke("")
            if conversation_decision_valid(decision, user_message):
                decision["thread_reused"] = False
                decision["thread_reset_reason"] = "invalid_decision_retry"
        except Exception as exc:
            decision = {}
            first_error = first_error or str(exc)[:500]
    if conversation_decision_valid(decision, user_message):
        return decision, failure_count, ""
    failure_count += 1
    try:
        decision = configured_fallback_reply(
            config,
            failure_count=failure_count,
            client_id=client_id,
            event_type=event_type,
            user_message=user_message,
            worker_result=worker_result,
            resolved_context=conversation_state,
        )
    except Exception as exc:
        decision = {}
        first_error = first_error or str(exc)[:500]
    if conversation_decision_valid(decision, user_message):
        return decision, failure_count, ""
    error = first_error or "conversation_agent_empty_or_invalid_reply"
    return {
        "action": "reply",
        "reply_text": TERMINAL_UNAVAILABLE_MESSAGE,
        "thread_id": "",
        "codex_failure_count": failure_count,
        "response_provider": "none",
        "fallback_terminal": True,
    }, failure_count, error


__all__ = [
    "FALLBACK_FAILURE_THRESHOLD",
    "TERMINAL_UNAVAILABLE_MESSAGE",
    "conversation_decision_valid",
    "configured_fallback_reply",
    "resolve_conversation_decision",
]
