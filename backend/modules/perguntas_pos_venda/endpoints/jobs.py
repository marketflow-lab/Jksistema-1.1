"""Customer-reply job lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import perguntas_pos_venda_codex


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


__all__ = [
    "ml_customer_reply_job_status",
    "ml_customer_reply_job_cancel",
]
