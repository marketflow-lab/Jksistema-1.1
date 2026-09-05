"""Common imports and runtime glue for Vendas endpoint modules."""

from __future__ import annotations
from backend.services.central_accounts_client import with_request_context

import asyncio
import copy
import datetime as dt
import functools
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import traceback
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
from backend.schemas import VendasSyncRequest

from .dependencies import logger
from .errors import VendasDomainError as HTTPException
from .legacy import _listar_bancos_vendas_tenant
from .performance import invalidate_vendas_cache, prepare_vendas_database
from .progress import (
    _criar_progresso,
    _set_progresso,
    _sync_active_jobs,
    _sync_active_jobs_unlocked,
    _sync_context_key,
    _sync_log,
    _sync_register_active_unlocked,
    _sync_unregister_active,
    _verificar_cancelamento,
)
from .state import (
    SYNC_ACTIVE_LOCK,
    SYNC_CANCEL_FLAGS,
    SYNC_DAY_CONTEXT,
    SYNC_LOGS,
    SYNC_MAX_ACTIVE_VENDAS,
    SYNC_META,
    SYNC_PROGRESS,
    SYNC_THREAD_CONTEXT,
)
from .sync_period import _sincronizar_vendas_periodo_impl
from .sync_persistence import (
    _vendas_sync_dias_periodo,
    _vendas_sync_job_key,
    _vendas_sync_load_state,
    _vendas_sync_prepare_job,
    _vendas_sync_update_job,
)

def _marcar_sync_interrompida_se_necessario(req: "VendasSyncRequest", client_id: str, motivo: str) -> None:
    try:
        job_key = _vendas_sync_job_key(req.loja, req.data_inicio, req.data_fim, req.forcar_resync)
        state = _vendas_sync_load_state(client_id)
        job = (state.get("jobs") or {}).get(job_key)
        if not isinstance(job, dict):
            return
        status = str(job.get("status") or "").strip().lower()
        if status not in {"running", "finished"}:
            return
        dias_total = int(job.get("dias_total") or 0)
        dias_concluidos = [d for d in (job.get("dias_concluidos") or []) if str(d or "").strip()]
        if dias_total and len(dias_concluidos) >= dias_total:
            return
        _vendas_sync_update_job(
            client_id,
            job_key,
            {
                "status": "interrompido",
                "ultimo_erro": motivo,
                "interrupted_at": datetime.now().isoformat(),
                "dias_concluidos": dias_concluidos,
            },
        )
    except Exception:
        logger.exception("[SYNC] Falha ao marcar sincronizacao interrompida.")

def _sincronizar_vendas_thread_worker(req: "VendasSyncRequest", client_id: str, job_id: str):
    """Executa a sincronização de vendas em thread separada para não bloquear o servidor."""
    SYNC_THREAD_CONTEXT.job_id = job_id
    SYNC_THREAD_CONTEXT.loja = req.loja
    try:
        asyncio.run(_sincronizar_vendas_impl(req, client_id))
        for db_path in _listar_bancos_vendas_tenant(client_id, req.loja):
            try:
                prepare_vendas_database(db_path)
            except Exception as exc:
                logger.warning("[VENDAS] Falha ao atualizar indices apos sync em %s: %s", db_path, exc)
        invalidate_vendas_cache(client_id)
    except HTTPException as e:
        msg = e.detail or "Erro na sincronização"
        if e.status_code == 409:
            _sync_log(client_id, f"[SYNC] Sincronizacao interrompida: {msg}")
            _set_progresso(client_id, _criar_progresso("Cancelamento", 0, 0, 0, str(msg)[:100]))
        else:
            _sync_log(client_id, f"[SYNC] ❌ {msg}")
            _set_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, str(msg)[:100]))
    except Exception as e:
        msg = f"Erro inesperado: {str(e)}"
        logger.exception(f"[SYNC] ❌ {msg}")
        _set_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, msg[:100]))
    finally:
        progresso_final = SYNC_PROGRESS.get(job_id) if isinstance(SYNC_PROGRESS.get(job_id), dict) else {}
        etapa_final = str((progresso_final or {}).get("etapa") or "").strip().lower()
        status_final = "complete" if (progresso_final or {}).get("concluido") else ("erro" if etapa_final == "erro" else ("cancelado" if etapa_final == "cancelamento" else "finished"))
        if status_final in {"erro", "finished"}:
            motivo = (
                str((progresso_final or {}).get("mensagem") or "").strip()
                or "Sincronizacao interrompida antes de concluir todos os dias."
            )
            _marcar_sync_interrompida_se_necessario(req, client_id, motivo)
        SYNC_META[job_id] = {
            **(SYNC_META.get(job_id) or {}),
            "status": status_final,
            "finished_at": datetime.now().isoformat(),
        }
        _sync_unregister_active(client_id, job_id)
        for attr in ("job_id", "loja"):
            try:
                delattr(SYNC_THREAD_CONTEXT, attr)
            except Exception:
                pass

def sincronizar_vendas(req: VendasSyncRequest, client_id: str):
    # Validações básicas antes de iniciar a thread
    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")
    if not req.data_inicio or not req.data_fim:
        raise HTTPException(status_code=400, detail="Informe data inicial e final.")

    with SYNC_ACTIVE_LOCK:
        active = dict(_sync_active_jobs_unlocked(client_id))
        loja_norm = str(req.loja or "").strip().lower()
        for job_id, meta in active.items():
            if str((meta or {}).get("loja") or "").strip().lower() == loja_norm:
                return {
                    "started": False,
                    "already_running": True,
                    "job_id": job_id,
                    "message": "Esta loja ja esta sincronizando.",
                }
        if len(active) >= SYNC_MAX_ACTIVE_VENDAS:
            return {
                "started": False,
                "already_running": True,
                "limit_reached": True,
                "active_count": len(active),
                "limit": SYNC_MAX_ACTIVE_VENDAS,
                "message": "Limite de 2 contas sincronizando ao mesmo tempo atingido.",
            }
        if not active:
            SYNC_CANCEL_FLAGS.pop(client_id, None)
            SYNC_LOGS[client_id] = []
        job_id = _sync_register_active_unlocked(client_id, req)

    # Iniciar em thread de background — não bloqueia o servidor nem o cliente
    t = threading.Thread(
        target=with_request_context(_sincronizar_vendas_thread_worker),
        args=(req, client_id, job_id),
        daemon=True
    )
    t.start()
    return {
        "started": True,
        "job_id": job_id,
        "active_count": len(_sync_active_jobs(client_id)),
        "limit": SYNC_MAX_ACTIVE_VENDAS,
        "message": "Sincronizacao diaria iniciada em background.",
    }

async def _sincronizar_vendas_impl(req: VendasSyncRequest, client_id: str):
    job_context_key = _sync_context_key(client_id)
    SYNC_CANCEL_FLAGS.pop(job_context_key, None)
    SYNC_LOGS[job_context_key] = []
    _set_progresso(client_id, _criar_progresso("Preparando", 0, 0, 0, "Iniciando sincronizacao por dia."))
    _sync_log(client_id, "[SYNC] Iniciando sincronizacao por dia")

    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")
    if not req.data_inicio or not req.data_fim:
        raise HTTPException(status_code=400, detail="Informe data inicial e final.")

    dias = _vendas_sync_dias_periodo(req.data_inicio, req.data_fim)
    job_key, job = _vendas_sync_prepare_job(client_id, req, dias)
    dias_concluidos = set(str(d or "") for d in (job.get("dias_concluidos") or []) if str(d or "").strip())
    retomando = bool(dias_concluidos)
    if retomando:
        _sync_log(client_id, f"[SYNC] Retomando sincronizacao: {len(dias_concluidos)}/{len(dias)} dia(s) ja concluidos.")
    _sync_log(client_id, f"[SYNC] Periodo dividido em {len(dias)} dia(s): {req.data_inicio} ate {req.data_fim}")

    totais = {
        "total": 0,
        "notas_entrada_total": 0,
        "nf_enriquecidas": 0,
        "nf_pendentes_restantes": 0,
        "vendas_processadas": 0,
        "notas_processadas": 0,
    }

    try:
        for idx, dia in enumerate(dias, start=1):
            _verificar_cancelamento(client_id)
            if dia in dias_concluidos:
                percentual = int(((idx - 1) / max(len(dias), 1)) * 100)
                _set_progresso(
                    client_id,
                    _criar_progresso("Retomada", idx, len(dias), percentual, f"Dia {idx}/{len(dias)} ja sincronizado. Avancando..."),
                )
                continue

            _vendas_sync_update_job(
                client_id,
                job_key,
                {
                    "status": "running",
                    "dia_atual": dia,
                    "dia_indice": idx,
                    "dias_total": len(dias),
                },
            )
            meta_job = {
                "loja": req.loja,
                "data_inicio": req.data_inicio,
                "data_fim": req.data_fim,
                "started_at": (SYNC_META.get(job_context_key) or SYNC_META.get(client_id) or {}).get("started_at") or datetime.now().isoformat(),
                "modo": "diario",
                "dia_atual": dia,
                "dia_indice": idx,
                "dias_total": len(dias),
            }
            SYNC_META[job_context_key] = {**(SYNC_META.get(job_context_key) or {}), **meta_job}
            SYNC_META[client_id] = {
                **(SYNC_META.get(client_id) or {}),
                "modo": "diario_paralelo",
                "limite_contas": SYNC_MAX_ACTIVE_VENDAS,
                "active_count": len(_sync_active_jobs(client_id)),
                "ultimo_job": meta_job,
            }
            _sync_log(client_id, f"[SYNC] Dia {idx}/{len(dias)}: sincronizando {dia}")
            _set_progresso(
                client_id,
                _criar_progresso("Dia", idx, len(dias), int(((idx - 1) / max(len(dias), 1)) * 100), f"Iniciando dia {idx}/{len(dias)} ({dia})..."),
            )

            dia_req = VendasSyncRequest(
                loja=req.loja,
                data_inicio=dia,
                data_fim=dia,
                forcar_resync=req.forcar_resync,
            )
            SYNC_DAY_CONTEXT[job_context_key] = {
                "dia_atual": dia,
                "dia_indice": idx,
                "total_dias": len(dias),
            }
            try:
                resultado_dia = await _sincronizar_vendas_periodo_impl(dia_req, client_id, reset_estado=False)
            finally:
                SYNC_DAY_CONTEXT.pop(job_context_key, None)

            dias_concluidos.add(dia)
            dias_concluidos_ordenados = [d for d in dias if d in dias_concluidos]
            _vendas_sync_update_job(
                client_id,
                job_key,
                {
                    "status": "running",
                    "dia_atual": dia,
                    "dia_indice": idx,
                    "dias_concluidos": dias_concluidos_ordenados,
                    "ultimo_resultado": resultado_dia,
                },
            )

            for campo in ("total", "notas_entrada_total", "nf_enriquecidas", "vendas_processadas", "notas_processadas"):
                totais[campo] += int((resultado_dia or {}).get(campo) or 0)
            totais["nf_pendentes_restantes"] = int((resultado_dia or {}).get("nf_pendentes_restantes") or 0)

        _vendas_sync_update_job(
            client_id,
            job_key,
            {
                "status": "complete",
                "dia_atual": dias[-1] if dias else None,
                "dia_indice": len(dias),
                "dias_concluidos": dias,
                "completed_at": datetime.now().isoformat(),
                "resultado_total": totais,
            },
        )
    except HTTPException as exc:
        status = "interrompido" if exc.status_code == 409 else "erro"
        _vendas_sync_update_job(
            client_id,
            job_key,
            {
                "status": status,
                "ultimo_erro": str(exc.detail or exc),
                "interrupted_at": datetime.now().isoformat(),
                "dias_concluidos": [d for d in dias if d in dias_concluidos],
            },
        )
        raise
    except Exception as exc:
        _vendas_sync_update_job(
            client_id,
            job_key,
            {
                "status": "erro",
                "ultimo_erro": str(exc),
                "interrupted_at": datetime.now().isoformat(),
                "dias_concluidos": [d for d in dias if d in dias_concluidos],
            },
        )
        raise

    _sync_log(client_id, f"[SYNC] Sincronizacao diaria finalizada: {len(dias)} dia(s) processados.")
    prog_final = _criar_progresso("Finalizado", len(dias), len(dias), 100, "Sincronizacao finalizada com sucesso.")
    prog_final["concluido"] = True
    prog_final["result"] = totais
    _set_progresso(client_id, prog_final)
    return {"success": True, **totais}



__all__ = [
    "_sincronizar_vendas_thread_worker",
    "sincronizar_vendas",
    "_sincronizar_vendas_impl",
]
