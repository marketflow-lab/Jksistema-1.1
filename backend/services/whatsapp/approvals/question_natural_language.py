"""Natural-language steering for an active Mercado Livre question approval."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from fastapi import HTTPException

from backend.services import codex_whatsapp_agents
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp.approvals.question_tokens import (
    _question_card_context,
    _question_token_scope_matches,
)
from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)
from backend.services.whatsapp.contracts import _QuestionResearchPending


def _question_agentic_free_text_action(
    config: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    approval: dict[str, Any],
    token_item: dict[str, Any],
) -> str:
    """Classify draft references semantically; never authorize a send."""

    user_message = str(message.get("text_body") or "").strip()[:12000]
    if not user_message:
        return ""
    card_context = _question_card_context(approval, token_item)
    store = str(card_context.get("store") or "")
    settings = whatsapp_settings.dual_agent_settings(
        config,
        client_id=str(session.get("client_id") or ""),
    )
    try:
        decision = codex_whatsapp_agents.CONVERSATION_RUNTIME.run(
            thread_id="",
            model=settings["conversation_agent_model"],
            reasoning_effort=settings["conversation_agent_reasoning"],
            speed=settings["conversation_agent_speed"],
            service_tier=settings["conversation_agent_service_tier"],
            event_type="user_message",
            user_message=user_message,
            active_job={
                "job_id": f"ml-question-draft:{card_context.get('approval_id') or ''}",
                "job_title": "Revisar o rascunho ativo da pergunta do Mercado Livre",
                "request_text": json.dumps(card_context, ensure_ascii=False, separators=(",", ":")),
                "status": "awaiting_input",
                "recent_conversation_messages": [
                    str(card_context.get("question") or "")[:500],
                    str(card_context.get("draft") or "")[:500],
                ],
            },
            worker_result=None,
            conversation_context=None,
            conversation_state={
                "store": store,
                "store_mode": "single" if store else "none",
                "sku": str(card_context.get("sku") or ""),
                "mlb": str(card_context.get("item_id") or ""),
                "period": "",
                "confirmed_fields": [field for field in ("store", "sku") if card_context.get(field)],
                "authorized_stores": [store] if store else [],
            },
            quoted_context={
                "message_id": f"ml-question-card:{card_context.get('approval_id') or ''}",
                "text": json.dumps(card_context, ensure_ascii=False, separators=(",", ":")),
                "source": "whatsapp_reply",
            },
            ai_behavior=(
                "Existe um cartao ativo de rascunho de pergunta do Mercado Livre no quoted_context. "
                "Classifique semanticamente a mensagem atual. Se ela pedir revisao, correcao ou nova redacao desse "
                "rascunho, use action=steer e relation_to_active_job=correction ou followup. Se for assunto independente, "
                "use relation_to_active_job=none ou new_parallel. Texto livre nunca aprova, rejeita, cancela nem envia "
                "resposta ao comprador; essas mutacoes exigem a acao tokenizada do cartao."
            ),
            tick_index=0,
        )
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_draft_semantic_action: {str(exc)[:800]}"
        return ""
    if not isinstance(decision, dict):
        return ""
    action = str(decision.get("action") or "").strip().lower()
    relation = str(decision.get("relation_to_active_job") or "").strip().lower()
    intent_kind = str(decision.get("intent_kind") or "").strip().lower()
    if intent_kind == "mutation_candidate" or action == "cancel_job" or relation == "cancel":
        return "typed_only"
    if action == "steer" and relation in {"correction", "followup"}:
        return "suggest"
    return ""


def _natural_question_request(
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> Optional[dict[str, str]]:
    if not _question_approval_allowed(session.get("permissions") or {}):
        return None
    subject_id = str(message.get("subject_id") or "").strip()
    client_id = str(session.get("client_id") or "").strip()
    username = str(session.get("username") or "").strip().lower()
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
    active_thread = threads.get(_question_thread_key(client_id, subject_id, username))
    matching = [
        item
        for item in tokens.values()
        if _question_token_scope_matches(
            item,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
    ]
    if not str(message.get("text_body") or "").strip():
        return None
    if not isinstance(active_thread, dict) and not matching:
        return None
    return {
        "action": "semantic",
        "subject_id": subject_id,
        "client_id": client_id,
        "username": username,
        "message_id": str(message.get("message_id") or "").strip(),
    }


def _natural_question_confirm(
    config: dict[str, Any],
    state: dict[str, Any],
    approval: dict[str, Any],
    token: str,
    token_item: dict[str, Any],
    subject_id: str,
    message_id: str,
) -> bool:
    fingerprint = "ppv-natural-confirm:" + hashlib.sha256(
        f"{subject_id}|{token}|{message_id}".encode("utf-8")
    ).hexdigest()
    result = _post_interactive_approval(
        config,
        subject_id=subject_id,
        fingerprint=fingerprint,
        token=token,
        body=_question_approval_body(approval, _question_bind_token_draft(token_item, approval)),
        state=state,
    )
    _save_state(state)
    if str(result.get("status") or "") in {"sent", "duplicate"}:
        _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
    else:
        _post_command_reply(
            config,
            message_id,
            "Para enviar, use somente a opcao tokenizada Aprovar e enviar. Nada foi enviado ao comprador.",
            "BLACK JHON - CONFIRMACAO OBRIGATORIA",
        )
    return True


def _natural_question_cancel_research(
    config: dict[str, Any],
    state: dict[str, Any],
    ppv_state: Any,
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    client_id: str,
    message_id: str,
) -> bool:
    from backend.services import perguntas_pos_venda_codex as ppv_codex

    job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
    if job_id:
        ppv_codex.cancel_job(client_id, job_id)
    approval.update(
        {
            "research_status": "cancelled",
            "research_delivery_state": "cancelled",
            "research_cancelled_at": _now(),
        }
    )
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    _save_state(state)
    _post_command_reply(
        config,
        message_id,
        "Pesquisa cancelada. Nenhuma resposta foi enviada ao comprador e a pergunta continua disponivel para uma nova orientacao.",
        "BLACK JHON - PESQUISA CANCELADA",
    )
    return True


def _natural_question_suggest(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    token_item: dict[str, Any],
    client_id: str,
    subject_id: str,
    username: str,
    message_id: str,
) -> bool:
    guidance = _question_suggestion_guidance(message.get("text_body"))
    token_item["awaiting_correction"] = False
    regenerated = _regenerate_question_approval_response(
        approval,
        approvals,
        client_id,
        guidance=guidance,
    )
    token, token_item = _question_approval_token(
        state,
        approval=approval,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        force_new=True,
        user_guidance=guidance,
    )
    token_item["regenerated_at"] = _now()
    _question_set_active_thread(
        state,
        approval_id=str(approval.get("id") or ""),
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, token_item),
    )
    fingerprint = "ppv-natural-suggest:" + hashlib.sha256(
        f"{subject_id}|{token}|{message_id}|{regenerated}".encode("utf-8")
    ).hexdigest()
    result = _post_interactive_approval(
        config,
        subject_id=subject_id,
        fingerprint=fingerprint,
        token=token,
        body=_question_approval_body(approval, regenerated),
        state=state,
    )
    _save_state(state)
    if str(result.get("status") or "") in {"sent", "duplicate"}:
        _post_message_result(config, message_id, {"status": "completed", "response_parts": []})
    else:
        _post_command_reply(
            config,
            message_id,
            "A nova resposta ficou salva, mas os botoes de decisao nao puderam ser abertos. Peça para gerar novamente.",
            "BLACK JHON - BOTOES INDISPONIVEIS",
        )
    return True


def _natural_question_decide(
    config: dict[str, Any],
    state: dict[str, Any],
    ppv_endpoints: Any,
    ppv_state: Any,
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    token: str,
    token_item: dict[str, Any],
    action: str,
    client_id: str,
    subject_id: str,
    username: str,
    message_id: str,
) -> bool:
    if action == "approve":
        from backend.schemas.perguntas_pos_venda import PerguntasAprovacaoRequest

        payload = PerguntasAprovacaoRequest(
            approval_id=str(approval.get("id") or ""),
            store=str(token_item.get("store") or approval.get("loja") or ""),
            question_id=str(
                token_item.get("question_id")
                or approval.get("question_id")
                or approval.get("pergunta_id")
                or ""
            ),
            resposta=_question_bind_token_draft(token_item, approval) or None,
        )
        result = ppv_endpoints.ml_perguntas_aprovacoes_aprovar(payload, client_id)
        resolved = (
            result.get("approval")
            if isinstance(result, dict) and isinstance(result.get("approval"), dict)
            else approval
        )
        if str(resolved.get("status") or "sent") not in {
            "sent",
            "sent_approved",
            "sent_manual",
            "sent_manual_pos_venda",
        }:
            raise RuntimeError("question_answer_not_confirmed")
        token_item.update({"used": True, "decision": "approve", "decided_at": _now()})
        _question_clear_active_thread(
            state,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
        _save_state(state)
        _post_command_reply(
            config,
            message_id,
            "Resposta enviada ao comprador. A proxima pergunta so sera apresentada depois desta confirmacao.",
            "BLACK JHON - RESPOSTA ENVIADA",
        )
        return True
    approval["whatsapp_suggestion_rejected_at"] = _now()
    approval["whatsapp_suggestion_rejected_by"] = username
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    token_item.update(
        {"used": False, "decision": "suggestion_rejected", "decided_at": _now()}
    )
    _question_set_active_thread(
        state,
        approval_id=str(approval.get("id") or ""),
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, token_item),
    )
    _save_state(state)
    _post_command_reply(
        config,
        message_id,
        "Sugestao rejeitada. Nenhuma resposta foi enviada e a pergunta continua ativa. Envie sua orientacao ou solicite outra sugestao.",
        "BLACK JHON - SUGESTAO REJEITADA",
    )
    return True


def _resolve_natural_question_context(
    state: dict[str, Any],
    message: dict[str, Any],
    request: dict[str, str],
) -> Optional[tuple[Any, Any, list[dict[str, Any]], dict[str, Any], str, dict[str, Any]]]:
    from backend.services import perguntas_pos_venda_endpoints as ppv_endpoints
    from backend.services import perguntas_pos_venda_state as ppv_state

    client_id = request["client_id"]
    subject_id = request["subject_id"]
    username = request["username"]
    approvals = ppv_state._perguntas_ia_aprovacoes_carregar(client_id)
    approval, token, found_token_item = _question_active_approval(
        state,
        approvals,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        quoted_message_id=str(message.get("quoted_message_id") or ""),
    )
    if approval is None or str(approval.get("status") or "pending") != "pending":
        return None
    if not token:
        token, found_token_item = _question_approval_token(
            state,
            approval=approval,
            subject_id=subject_id,
            client_id=client_id,
            username=username,
        )
    if isinstance(found_token_item, dict):
        token_item = found_token_item
    else:
        tokens = (
            state.get("question_approval_tokens")
            if isinstance(state.get("question_approval_tokens"), dict)
            else {}
        )
        token_item = tokens.get(token) if isinstance(tokens.get(token), dict) else {}
    _question_bind_token_draft(token_item, approval)
    return ppv_endpoints, ppv_state, approvals, approval, token, token_item


def _dispatch_natural_question_action(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
    request: dict[str, str],
    resolved: tuple[Any, Any, list[dict[str, Any]], dict[str, Any], str, dict[str, Any]],
) -> bool:
    ppv_endpoints, ppv_state, approvals, approval, token, token_item = resolved
    action = request["action"]
    subject_id = request["subject_id"]
    client_id = request["client_id"]
    username = request["username"]
    message_id = request["message_id"]
    if action == "semantic":
        if token_item.get("awaiting_correction") is True:
            action = "suggest"
        else:
            action = _question_agentic_free_text_action(
                config,
                message,
                session,
                approval,
                token_item,
            )
        if action == "typed_only":
            _post_command_reply(
                config,
                message_id,
                "Use os botoes do cartao para aprovar, rejeitar ou cancelar. O texto livre nao enviou nada ao comprador.",
                "BLACK JHON - ACAO TOKENIZADA OBRIGATORIA",
            )
            return True
        if action != "suggest":
            return False
    if action == "confirm_approval":
        return _natural_question_confirm(
            config,
            state,
            approval,
            token,
            token_item,
            subject_id,
            message_id,
        )
    if action == "cancel_research":
        return _natural_question_cancel_research(
            config, state, ppv_state, approvals, approval, client_id, message_id,
        )
    if action == "suggest":
        return _natural_question_suggest(
            config, state, message, approvals, approval, token_item,
            client_id, subject_id, username, message_id,
        )
    return _natural_question_decide(
        config, state, ppv_endpoints, ppv_state, approvals, approval, token,
        token_item, action, client_id, subject_id, username, message_id,
    )


def _handle_question_natural_language(
    config: dict[str, Any],
    state: dict[str, Any],
    message: dict[str, Any],
    session: dict[str, Any],
) -> bool:
    request = _natural_question_request(state, message, session)
    if request is None:
        return False
    message_id = request["message_id"]
    token_item: dict[str, Any] = {}
    try:
        resolved = _resolve_natural_question_context(state, message, request)
        if resolved is None:
            return False
        token_item = resolved[-1]
        return _dispatch_natural_question_action(
            config,
            state,
            message,
            session,
            request,
            resolved,
        )
    except _QuestionResearchPending as pending:
        token_item.update(
            {
                "used": True,
                "decision": "research_superseded",
                "research_job_id": pending.job_id,
                "research_started_at": _now(),
            }
        )
        _save_state(state)
        _post_command_reply(
            config,
            message_id,
            "Continuo pesquisando em novas fontes ate obter evidencia tecnica suficiente. "
            "Assim que a verificacao estiver concluida, envio a nova sugestao para sua aprovacao. "
            "Nenhuma resposta foi enviada ao comprador.",
            "BLACK JHON - PESQUISA EM ANDAMENTO",
        )
        return True
    except HTTPException as exc:
        _post_command_reply(
            config,
            message_id,
            str(exc.detail or "Nao foi possivel processar esta resposta."),
            "BLACK JHON - RESPOSTA NAO ENVIADA",
        )
        return True
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_natural_action: {str(exc)[:800]}"
        _post_command_reply(
            config,
            message_id,
            "Nao foi possivel aplicar essa instrucao agora. A pergunta atual continua pendente e nenhuma resposta foi enviada.",
            "BLACK JHON - PERGUNTA AINDA PENDENTE",
        )
        return True


_COMPONENT_FUNCTIONS = frozenset(("_handle_question_natural_language",))
_IMPLEMENTATIONS = {"_handle_question_natural_language": _handle_question_natural_language}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
