"""Customer-reply job reconciliation helpers."""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional


from backend.modules.perguntas_pos_venda.ai import approval as perguntas_agent_approval
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.services import perguntas_pos_venda_codex
from backend.modules.perguntas_pos_venda.endpoints.diagnostics import (
    _perguntas_ia_diagnostico_aprovacao,
)

_ia_modo_perguntas_configurado = runtime_adapter("_ia_modo_perguntas_configurado")
_ia_modo_pos_venda_configurado = runtime_adapter("_ia_modo_pos_venda_configurado")
_ml_pos_venda_pipeline_resumo = runtime_adapter("_ml_pos_venda_pipeline_resumo")
_perguntas_ia_aprovacao_id = runtime_adapter("_perguntas_ia_aprovacao_id")
_pos_venda_ia_aprovacao_id = runtime_adapter("_pos_venda_ia_aprovacao_id")


def _customer_reply_job_ready_for_approval(job: Any) -> bool:
    if not isinstance(job, dict) or str(job.get("status") or "") != "completed":
        return False
    if str(job.get("agent_state") or "") != "aguardando_aprovacao":
        return False
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return bool(
        str(result.get("resposta") or "").strip()
        and result.get("requires_approval") is not False
        and result.get("publish_attempted") is not True
    )


def _customer_reply_approval_job_current(client_id: str, approval: Any) -> bool:
    """Only current-contract AI jobs may keep or publish a public-question draft."""

    if not isinstance(approval, dict):
        return False
    job_id = str(
        approval.get("proposal_id")
        or approval.get("codex_job_id")
        or approval.get("research_job_id")
        or ""
    ).strip()
    if not job_id:
        return False
    return perguntas_pos_venda_codex.approval_job_current(client_id, job_id)


def _customer_reply_job_already_reconciled(aprovacoes: Any, job_id: Any) -> bool:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return False
    return any(
        isinstance(item, dict)
        and normalized_job_id
        in {
            str(item.get("codex_job_id") or "").strip(),
            str(item.get("proposal_id") or "").strip(),
            str(item.get("research_job_id") or "").strip(),
        }
        for item in (aprovacoes or [])
    )


def _customer_reply_late_reconciliation_candidate(
    *,
    client_id: str,
    task_type: str,
    store: str,
    subject_key: str,
    request: dict[str, Any],
    approvals: Any,
) -> tuple[Optional[dict[str, Any]], bool]:
    latest_job = perguntas_pos_venda_codex.latest_job_for_request(
        client_id=client_id,
        task_type=task_type,
        store=store,
        subject_key=subject_key,
        request=request,
    )
    if not _customer_reply_job_ready_for_approval(latest_job):
        return None, False
    return latest_job, _customer_reply_job_already_reconciled(
        approvals,
        latest_job.get("job_id"),
    )


def _customer_reply_automation_terminal_blocker(
    *,
    client_id: str,
    task_type: str,
    store: str,
    subject_key: str,
    request: dict[str, Any],
) -> str:
    latest_job = perguntas_pos_venda_codex.latest_job_for_request(
        client_id=client_id,
        task_type=task_type,
        store=store,
        subject_key=subject_key,
        request=request,
    )
    return perguntas_pos_venda_codex.automation_terminal_blocker(latest_job)


def _customer_reply_job_draft(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    resposta = str(result.get("resposta") or "")
    contexto = dict(result.get("contexto") or {}) if isinstance(result.get("contexto"), dict) else {}
    contexto.update(
        {
            "codex_job_id": str(job.get("job_id") or ""),
            "proposal_version": result.get("proposal_version") or job.get("proposal_version") or 1,
            "proposal_hash": result.get("proposal_hash") or job.get("proposal_hash") or "",
            "data_sufficient": bool(result.get("data_sufficient")),
            "warnings": list(result.get("warnings") or job.get("warnings") or []),
        }
    )
    return resposta, contexto


def _customer_reply_question_approval(
    *,
    nome_loja: str,
    question_id: str,
    pergunta: dict[str, Any],
    resposta: str,
    contexto: dict[str, Any],
) -> dict[str, Any]:
    intencao_ctx = (
        contexto.get("intencao_atendimento")
        if isinstance(contexto.get("intencao_atendimento"), dict)
        else {}
    )
    approval = {
        "id": _perguntas_ia_aprovacao_id(nome_loja, question_id),
        "status": "pending",
        "loja": nome_loja,
        "question_id": question_id,
        "item_id": contexto.get("item_id") or pergunta.get("item_id") or "",
        "sku": contexto.get("sku") or pergunta.get("item_sku") or pergunta.get("sku") or "",
        "titulo": contexto.get("titulo") or pergunta.get("item_title") or "",
        "permalink": contexto.get("permalink") or pergunta.get("item_permalink") or "",
        "descricao_anuncio": str(contexto.get("descricao") or "")[:2500],
        "pergunta": contexto.get("pergunta") or pergunta.get("text") or "",
        "mensagens": perguntas_agent_approval.build_messages(pergunta, nome_loja),
        "resposta_sugerida": resposta,
        "model": contexto.get("model") or "",
        "ia_origem": "mercado_livre_perguntas",
        "ia_finalidade": intencao_ctx.get("fluxo") or "perguntas_anuncio",
        "ia_intencao": intencao_ctx,
        "ia_modo": contexto.get("modo_ia") or _ia_modo_perguntas_configurado(),
        "aprovacao_obrigatoria_ia": perguntas_agent_approval.question_requires_approval(),
        "ia_decision": contexto.get("ia_decision") or "",
        "ia_categoria": contexto.get("ia_categoria") or "",
        "ia_validacao_ok": contexto.get("ia_validacao_ok"),
        "ia_validacao_issues": contexto.get("ia_validacao_issues") or [],
        "ia_requer_revisao_humana": bool(contexto.get("ia_requer_revisao_humana")),
        "manual_edit_required": bool(contexto.get("manual_edit_required")),
        "manual_edit_reason": str(contexto.get("manual_edit_reason") or ""),
        "codex_job_id": contexto.get("codex_job_id") or "",
        "proposal_id": contexto.get("codex_job_id") or "",
        "proposal_version": contexto.get("proposal_version") or 1,
        "proposal_hash": contexto.get("proposal_hash") or "",
        "data_sufficient": contexto.get("data_sufficient"),
        "warnings": contexto.get("warnings") or [],
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    approval.update(_perguntas_ia_diagnostico_aprovacao(contexto))
    return approval


def _customer_reply_post_sale_job_result(job: dict[str, Any]) -> dict[str, Any]:
    job_result = job.get("result") if isinstance(job.get("result"), dict) else {}
    resposta, job_context = _customer_reply_job_draft(job)
    return {
        "resposta": resposta,
        "model": job_result.get("model") or job_context.get("model") or "",
        "pode_enviar_automaticamente": False,
        "decisao": job_context.get("decisao") or {},
        "validacao": job_context.get("validacao") or {},
        "contexto_ia": job_context,
        "codex_job_id": job.get("job_id") or "",
        "proposal_version": job_result.get("proposal_version") or job.get("proposal_version") or 1,
        "proposal_hash": job_result.get("proposal_hash") or job.get("proposal_hash") or "",
        "data_sufficient": bool(job_result.get("data_sufficient")),
        "warnings": job_result.get("warnings") or job.get("warnings") or [],
    }


def _customer_reply_post_sale_approval(
    *,
    nome_loja: str,
    chave: str,
    pack_id: str,
    order_id: str,
    buyer_id: str,
    item: dict[str, Any],
    conversa: dict[str, Any],
    message_event_id: str,
    last_text: str,
    max_chars: int,
    resultado_ia: dict[str, Any],
) -> dict[str, Any]:
    contexto_ia = (
        resultado_ia.get("contexto_ia")
        if isinstance(resultado_ia.get("contexto_ia"), dict)
        else {}
    )
    conversa_aprovacao = {
        "pack_id": conversa.get("pack_id") or pack_id,
        "order_id": conversa.get("order_id") or order_id,
        "buyer_id": conversa.get("buyer_id") or buyer_id,
        "buyer_nickname": conversa.get("buyer_nickname") or "",
        "items": conversa.get("items") or [],
        "messages": conversa.get("messages") or [],
        "last_message_text": conversa.get("last_message_text") or last_text,
        "seller_max_message_length": max_chars,
        "buyer_listing_question_history": conversa.get("buyer_listing_question_history") or [],
        "buyer_listing_question_chat": conversa.get("buyer_listing_question_chat") or [],
        "buyer_listing_question_history_count": conversa.get("buyer_listing_question_history_count") or 0,
    }
    return {
        "id": _pos_venda_ia_aprovacao_id(nome_loja, pack_id, message_event_id),
        "tipo": "pos_venda",
        "approval_type": "pos_venda",
        "status": "pending",
        "loja": nome_loja,
        "question_id": chave,
        "pack_id": pack_id,
        "order_id": order_id,
        "buyer_id": buyer_id,
        "item_id": item.get("id") or "",
        "sku": item.get("sku") or "",
        "titulo": item.get("title") or conversa.get("item_title") or "",
        "permalink": item.get("permalink") or item.get("link") or item.get("url") or "",
        "pergunta": last_text,
        "conversa": conversa_aprovacao,
        "mensagens": conversa_aprovacao["messages"],
        "resposta_sugerida": str(resultado_ia.get("resposta") or ""),
        "max_chars": max_chars,
        "model": str(resultado_ia.get("model") or "").strip(),
        "ia_origem": "mercado_livre_pos_venda",
        "ia_finalidade": "pos_venda",
        "ia_modo": _ia_modo_pos_venda_configurado(),
        "aprovacao_obrigatoria_ia": perguntas_agent_approval.post_sale_requires_approval(),
        "ia_pipeline": _ml_pos_venda_pipeline_resumo(contexto_ia),
        "ia_decisao": resultado_ia.get("decisao") or {},
        "ia_validacao": resultado_ia.get("validacao") or {},
        "audit_id": resultado_ia.get("audit_id") or "",
        "codex_job_id": resultado_ia.get("codex_job_id") or "",
        "proposal_id": resultado_ia.get("codex_job_id") or "",
        "proposal_version": resultado_ia.get("proposal_version") or 1,
        "proposal_hash": resultado_ia.get("proposal_hash") or "",
        "data_sufficient": resultado_ia.get("data_sufficient"),
        "warnings": resultado_ia.get("warnings") or [],
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
