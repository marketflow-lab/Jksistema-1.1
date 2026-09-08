"""Customer-reply job lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support
from backend.services import perguntas_pos_venda_codex


_SOLICITACAO_STATUSES = frozenset({
    "queued", "running", "waiting_retry", "completed", "cancelled", "failed",
})


def _authorized_store_catalog(request: Request, client_id: str) -> dict[str, dict[str, str]]:
    rows = questions_loading_support.authorized_stores(request, client_id)
    stores: dict[str, dict[str, str]] = {}
    for row in rows:
        store_id = str(row.get("store_id") or "").strip()
        if not store_id:
            continue
        cfg = (row.get("integracoes") or {}).get("mercadolivre") or {}
        identity = {
            "store_id": store_id,
            "loja": str(row.get("nome") or "").strip(),
            "seller_id": str(cfg.get("user_id") or cfg.get("seller_id") or "").strip(),
            "site_id": str(cfg.get("site_id") or row.get("site_id") or "").strip(),
        }
        stores[store_id] = identity
    return stores


def _safe_steps(value: Any) -> list[dict[str, Any]]:
    return [
        {
            key: row.get(key)
            for key in ("state", "step", "at")
            if key in row
        }
        for row in (value if isinstance(value, list) else [])[-60:]
        if isinstance(row, dict)
    ]


def _safe_evidence(value: Any) -> list[dict[str, Any]]:
    return [
        {
            key: row.get(key)
            for key in ("id", "intent", "status", "confidence")
            if key in row
        }
        for row in (value if isinstance(value, list) else [])[:12]
        if isinstance(row, dict)
    ]


def _solicitacao_status_message(job: dict[str, Any]) -> str:
    status = str(job.get("status") or "queued")
    step = str(job.get("current_step") or "")
    if status == "queued":
        return "Solicitacao aguardando processamento."
    if status == "running":
        labels = {
            "entender": "Entendendo a solicitacao.",
            "consultar": "Consultando dados e evidencias.",
            "validar": "Validando as evidencias encontradas.",
            "responder": "Preparando a conclusao.",
            "aprovar": "Preparando a revisao final.",
        }
        return labels.get(step, "Solicitacao em processamento.")
    if status == "waiting_retry":
        return "Uma nova tentativa sera executada."
    if status == "completed":
        if job.get("blocked_without_draft"):
            return "Processamento concluido sem resposta disponivel."
        if job.get("completed_with_partial"):
            return "Processamento concluido com as informacoes disponiveis."
        return "Processamento concluido."
    if status == "cancelled":
        return "Solicitacao cancelada."
    if status == "failed":
        return "Nao foi possivel concluir a solicitacao."
    return "Acompanhando a solicitacao."


def _public_solicitacao(job: dict[str, Any], stores: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    store_id = str(job.get("store_id") or "").strip()
    identity = stores.get(store_id)
    if not identity or any(
        str(job.get(field) or "").strip() != str(identity.get(field) or "").strip()
        for field in ("seller_id", "site_id")
    ):
        return None
    warnings = [
        str(value or "").strip()[:300]
        for value in (job.get("warnings") if isinstance(job.get("warnings"), list) else [])[:12]
        if str(value or "").strip()
    ]
    status_message = _solicitacao_status_message(job)
    return {
        "job_id": str(job.get("job_id") or ""),
        "task_type": str(job.get("task_type") or ""),
        "subject_key": str(job.get("event_subject_key") or job.get("subject_key") or ""),
        "question_id": str(job.get("question_id") or ""),
        "item_id": str(job.get("item_id") or ""),
        "proposal_id": str(job.get("proposal_id") or ""),
        "store_id": store_id,
        "loja": identity["loja"],
        "seller_id": str(job.get("seller_id") or identity["seller_id"]),
        "site_id": str(job.get("site_id") or identity["site_id"]),
        "status": str(job.get("status") or ""),
        "agent_state": str(job.get("agent_state") or ""),
        "current_step": str(job.get("current_step") or ""),
        "status_message": status_message,
        "conclusao_operacional": status_message,
        "steps": _safe_steps(job.get("agent_steps")),
        "attempt_count": max(0, int(job.get("attempt_count") or 0)),
        "retry_count": max(0, int(job.get("retry_count") or 0)),
        "conclusao": status_message if str(job.get("status") or "") in {"completed", "cancelled", "failed"} else "",
        "acao_pendente": (
            "revisar_resposta"
            if str(job.get("agent_state") or "") == "aguardando_aprovacao"
            else ("aguardar_processamento" if str(job.get("status") or "") in {"queued", "running", "waiting_retry"} else "")
        ),
        "completion_reason": str(job.get("completion_reason") or ""),
        "evidencias": _safe_evidence(job.get("evidence_status")),
        "avisos": warnings,
        "created_at": str(job.get("created_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "completed_at": str(job.get("completed_at") or ""),
    }


def _customer_reply_wait_or_raise(client_id: str, job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        raise HTTPException(status_code=500, detail="O orquestrador nao retornou a identificacao da tarefa.")
    if job.get("queued"):
        # A chamada sincrona acompanha no maximo uma tentativa de 180s. O job
        # conserva separadamente o limite total de 15 minutos iniciado no claim.
        job = perguntas_pos_venda_codex.wait_job(client_id, job_id, timeout=180)
    status = str(job.get("status") or "")
    if status == "failed":
        raise HTTPException(status_code=503, detail=str(job.get("error") or "Falha no orquestrador Codex."))
    if status == "cancelled":
        raise HTTPException(status_code=409, detail="A geracao foi cancelada.")
    if status != "completed":
        return job
    return job


def _customer_reply_requires_approval() -> bool:
    """Automatic mode only prepares drafts; publishing always needs approval."""

    return True


def ml_customer_reply_job_status(job_id: str, client_id: str = Depends(get_tenant_id)):
    job = perguntas_pos_venda_codex.get_job(client_id, str(job_id or "").strip())
    if not isinstance(job, dict):
        raise HTTPException(status_code=404, detail="Tarefa de atendimento nao encontrada.")
    return jsonable_encoder(job)


def ml_customer_reply_job_cancel(job_id: str, client_id: str = Depends(get_tenant_id)):
    job = perguntas_pos_venda_codex.cancel_job(client_id, str(job_id or "").strip())
    if not isinstance(job, dict):
        raise HTTPException(status_code=404, detail="Tarefa de atendimento nao encontrada.")
    return jsonable_encoder(job)


def ml_customer_reply_solicitacoes_list(
    request: Request,
    store_id: str = "",
    status: str = "",
    limit: int = 20,
    offset: int = 0,
    client_id: str = Depends(get_tenant_id),
):
    stores = _authorized_store_catalog(request, client_id)
    selected_store_id = str(store_id or "").strip()
    if selected_store_id and selected_store_id not in stores:
        raise HTTPException(status_code=403, detail="Loja nao autorizada nesta sessao.")
    clean_status = str(status or "").strip().lower()
    if clean_status in {"todos", "all"}:
        clean_status = ""
    if clean_status and clean_status not in _SOLICITACAO_STATUSES:
        raise HTTPException(status_code=400, detail="Status de solicitacao invalido.")
    bounded_limit = max(1, min(int(limit or 20), 100))
    bounded_offset = max(0, int(offset or 0))
    allowed_store_ids = [selected_store_id] if selected_store_id else list(stores)
    allowed_scopes = [
        (store_id_value, stores[store_id_value]["seller_id"], stores[store_id_value]["site_id"])
        for store_id_value in allowed_store_ids
    ]
    page = perguntas_pos_venda_codex.list_solicitacoes(
        client_id,
        store_scopes=allowed_scopes,
        status=clean_status,
        limit=bounded_limit,
        offset=bounded_offset,
    )
    solicitacoes = [
        public
        for job in page.get("rows") or []
        if isinstance(job, dict)
        for public in [_public_solicitacao(job, stores)]
        if public is not None
    ]
    return jsonable_encoder({
        "success": True,
        "solicitacoes": solicitacoes,
        "total": int(page.get("total") or 0),
        "limit": int(page.get("limit") or bounded_limit),
        "offset": int(page.get("offset") or bounded_offset),
        "status_resumo": dict(page.get("status_resumo") or {}),
    })


__all__ = [
    "ml_customer_reply_job_status",
    "ml_customer_reply_job_cancel",
    "ml_customer_reply_solicitacoes_list",
]
