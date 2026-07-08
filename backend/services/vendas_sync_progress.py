"""Common imports and runtime glue for Vendas endpoint modules."""

from __future__ import annotations

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
from fastapi import Depends, HTTPException

from backend.schemas import VendasQuery, VendasSyncRequest
from backend.services import vendas_context
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.vendas_sync_state import *

_RUNTIME_NAMES = (
    "logger",
    "get_tenant_path",
    "SYNC_CANCEL_FLAGS",
    "SYNC_PROGRESS",
    "SYNC_LOGS",
    "SYNC_META",
    "SYNC_DAY_CONTEXT",
    "SYNC_MAX_ACTIVE_VENDAS",
    "SYNC_ACTIVE_LOCK",
    "SYNC_STATE_LOCK",
    "SYNC_THREAD_CONTEXT",
    "SYNC_ACTIVE",
)


def _sync_context_names() -> None:
    for name in vendas_context.CONTEXT_EXPORTS:
        globals()[name] = getattr(vendas_context, name)
    for name in _RUNTIME_NAMES:
        globals()[name] = getattr(vendas_context, name)


def _configure_runtime_globals(runtime_module=None):
    runtime = vendas_context.configure_vendas_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime

def _corrigir_texto_mojibake(valor):
    if not isinstance(valor, str):
        return valor
    texto = valor
    substituicoes = {
        "\u00c3\u00a2\u00c5\u201c\u00e2\u20ac\u00a6": "\u2705",
        "\u00c3\u00a2\u00c2\u009d\u00c5\u2019": "\u274c",
        "\u00c3\u00a2\u00c5\u00a1\u00c2\u00a0\u00c3\u00af\u00c2\u00b8\u00c2\u008f": "\u26a0\ufe0f",
        "\u00e2\u0153\u2026": "\u2705",
        "\u00e2\u009d\u0152": "\u274c",
        "\u00e2\u0161\u00a0\u00ef\u00b8\u008f": "\u26a0\ufe0f",
        "\u00e2\u20ac\u201d": "-",
        "\u00e2\u20ac\u201c": "-",
        "\u00e2\u20ac\u0153": '"',
        "\u00e2\u20ac\u009d": '"',
        "\u00e2\u20ac\u02dc": "'",
        "\u00e2\u20ac\u2122": "'",
    }
    for errado, correto in substituicoes.items():
        texto = texto.replace(errado, correto)
    marcadores = (
        "Ã", "Â", "À", "á", "â", "ã", "ç", "é", "ê", "í",
        "ó", "õ", "ú", "Â", "â€", "âœ", "â", "âš", "�", "�",
    )
    if not texto or not any(m in texto for m in marcadores):
        return texto
    for _ in range(3):
        try:
            corrigido = texto.encode("cp1252").decode("utf-8")
        except UnicodeError:
            break
        if not corrigido or corrigido == texto:
            break
        texto = corrigido
        for errado, correto in substituicoes.items():
            texto = texto.replace(errado, correto)
        if not any(m in texto for m in marcadores):
            break
    return texto

def _criar_progresso(etapa: str, lote: int, total_lotes: int, percentual: int, mensagem: str = ""):
    """Cria um dicionário de progresso padronizado"""
    etapa = _corrigir_texto_mojibake(str(etapa or ""))
    mensagem = _corrigir_texto_mojibake(str(mensagem or ""))
    return {
        "etapa": etapa,
        "lote": lote,
        "total_lotes": total_lotes,
        "percentual": percentual,
        "mensagem": mensagem or f"{etapa} - Lote {lote}/{total_lotes}",
        "timestamp": datetime.now().isoformat()
    }

def _sync_context_key(client_id: str) -> str:
    return str(getattr(SYNC_THREAD_CONTEXT, "job_id", "") or client_id)

def _sync_context_loja() -> str:
    return str(getattr(SYNC_THREAD_CONTEXT, "loja", "") or "").strip()

def _sync_active_jobs_unlocked(client_id: str) -> dict:
    active = SYNC_ACTIVE.get(client_id)
    if isinstance(active, dict):
        return active
    if active:
        return {"legacy": {"id": "legacy", "loja": ""}}
    return {}

def _sync_active_jobs(client_id: str) -> dict:
    with SYNC_ACTIVE_LOCK:
        return dict(_sync_active_jobs_unlocked(client_id))

def _sync_register_active_unlocked(client_id: str, req: "VendasSyncRequest") -> str:
    active = dict(_sync_active_jobs_unlocked(client_id))
    job_id = uuid.uuid4().hex
    agora = datetime.now().isoformat()
    meta = {
        "id": job_id,
        "client_id": client_id,
        "loja": req.loja,
        "data_inicio": req.data_inicio,
        "data_fim": req.data_fim,
        "forcar_resync": bool(req.forcar_resync),
        "started_at": agora,
        "status": "running",
    }
    active[job_id] = meta
    SYNC_ACTIVE[client_id] = active
    SYNC_META[job_id] = meta
    SYNC_META[client_id] = {
        "modo": "diario_paralelo",
        "limite_contas": SYNC_MAX_ACTIVE_VENDAS,
        "active_count": len(active),
        "started_at": agora,
        "jobs": list(active.values()),
    }
    SYNC_LOGS[job_id] = []
    SYNC_PROGRESS.pop(job_id, None)
    SYNC_CANCEL_FLAGS.pop(job_id, None)
    return job_id

def _sync_unregister_active(client_id: str, job_id: str):
    with SYNC_ACTIVE_LOCK:
        active = dict(_sync_active_jobs_unlocked(client_id))
        active.pop(job_id, None)
        if active:
            SYNC_ACTIVE[client_id] = active
            SYNC_META[client_id] = {
                **(SYNC_META.get(client_id) or {}),
                "modo": "diario_paralelo",
                "limite_contas": SYNC_MAX_ACTIVE_VENDAS,
                "active_count": len(active),
                "jobs": list(active.values()),
                "updated_at": datetime.now().isoformat(),
            }
        else:
            SYNC_ACTIVE.pop(client_id, None)
            SYNC_CANCEL_FLAGS.pop(client_id, None)
            SYNC_META[client_id] = {
                **(SYNC_META.get(client_id) or {}),
                "modo": "diario_paralelo",
                "limite_contas": SYNC_MAX_ACTIVE_VENDAS,
                "active_count": 0,
                "jobs": [],
                "finished_at": datetime.now().isoformat(),
            }
    SYNC_DAY_CONTEXT.pop(job_id, None)

def _sync_build_aggregate_progress(client_id: str, active_jobs: dict | None = None) -> dict | None:
    active_jobs = active_jobs if active_jobs is not None else _sync_active_jobs(client_id)
    if not active_jobs:
        return SYNC_PROGRESS.get(client_id)

    itens = []
    percentuais = []
    for job_id, meta in active_jobs.items():
        prog = SYNC_PROGRESS.get(job_id)
        if isinstance(prog, dict):
            percentuais.append(max(0, min(100, int(prog.get("percentual") or 0))))
        itens.append({
            "id": job_id,
            "loja": (meta or {}).get("loja") or "",
            "progress": prog,
        })

    percentual = int(sum(percentuais) / len(percentuais)) if percentuais else 0
    lojas = ", ".join(str(item.get("loja") or "").strip() for item in itens if str(item.get("loja") or "").strip())
    mensagem = f"{len(active_jobs)} conta(s) sincronizando"
    if lojas:
        mensagem += f": {lojas}"
    progresso = _criar_progresso("Sincronizando", len(active_jobs), SYNC_MAX_ACTIVE_VENDAS, percentual, mensagem)
    progresso["active_count"] = len(active_jobs)
    progresso["jobs"] = itens
    return progresso

def _set_progresso(client_id: str, data: dict):
    key = _sync_context_key(client_id)
    ctx = SYNC_DAY_CONTEXT.get(key) or SYNC_DAY_CONTEXT.get(client_id)
    if ctx and isinstance(data, dict) and not data.get("_global_percent_applied"):
        data = dict(data)
        try:
            total_dias = max(1, int(ctx.get("total_dias") or 1))
            dia_indice = max(1, int(ctx.get("dia_indice") or 1))
            percentual_local = max(0, min(100, int(data.get("percentual") or 0)))
            percentual_global = int((((dia_indice - 1) + (percentual_local / 100)) / total_dias) * 100)
            data["percentual_local"] = percentual_local
            data["percentual"] = max(0, min(99, percentual_global))
            data["dia_atual"] = ctx.get("dia_atual")
            data["dia_indice"] = dia_indice
            data["dias_total"] = total_dias
            mensagem = str(data.get("mensagem") or "")
            data["mensagem"] = f"Dia {dia_indice}/{total_dias} ({ctx.get('dia_atual')}): {mensagem}"
            data["_global_percent_applied"] = True
        except Exception:
            pass
    if isinstance(data, dict):
        loja = _sync_context_loja()
        if loja:
            data = dict(data)
            data["loja"] = loja
    SYNC_PROGRESS[key] = data
    if key != client_id:
        SYNC_PROGRESS[client_id] = _sync_build_aggregate_progress(client_id)
    else:
        SYNC_PROGRESS[client_id] = data

def _limpar_progresso(client_id: str):
    SYNC_PROGRESS.pop(client_id, None)
    SYNC_LOGS.pop(client_id, None)

def _sync_log(client_id: str, mensagem: str):
    mensagem = _corrigir_texto_mojibake(str(mensagem or ""))
    print(mensagem)
    key = _sync_context_key(client_id)
    logs = SYNC_LOGS.get(key, [])
    logs.append(mensagem)
    if len(logs) > 200:
        logs = logs[-200:]
    SYNC_LOGS[key] = logs
    if key != client_id:
        loja = _sync_context_loja()
        prefixo = f"[{loja}] " if loja else ""
        logs_cliente = SYNC_LOGS.get(client_id, [])
        logs_cliente.append(f"{prefixo}{mensagem}")
        if len(logs_cliente) > 200:
            logs_cliente = logs_cliente[-200:]
        SYNC_LOGS[client_id] = logs_cliente

def _verificar_cancelamento(client_id: str):
    key = _sync_context_key(client_id)
    if SYNC_CANCEL_FLAGS.get(client_id) or SYNC_CANCEL_FLAGS.get(key):
        SYNC_CANCEL_FLAGS.pop(key, None)
        _set_progresso(client_id, _criar_progresso("Cancelamento", 0, 0, 0, "Sincronização cancelada pelo usuário."))
        raise HTTPException(status_code=409, detail="Sincronização cancelada pelo usuário.")

async def cancelar_sincronizacao_vendas(client_id: str = Depends(vendas_context.get_tenant_id)):
    SYNC_CANCEL_FLAGS[client_id] = True
    _set_progresso(client_id, _criar_progresso("Cancelamento", 0, 0, 0, "Cancelamento solicitado."))
    return {"success": True, "message": "Cancelamento solicitado."}

async def progresso_sincronizacao_vendas(client_id: str = Depends(vendas_context.get_tenant_id)):
    active_jobs = _sync_active_jobs(client_id)
    active = bool(active_jobs)
    progress = _sync_build_aggregate_progress(client_id, active_jobs)
    state = _vendas_sync_load_state(client_id)
    active_key = state.get("active_key")
    sync_state = (state.get("jobs") or {}).get(active_key) if active_key else None
    if not active and isinstance(sync_state, dict):
        status_state = str(sync_state.get("status") or "").strip().lower()
        dias_total = int(sync_state.get("dias_total") or 0)
        dias_concluidos = [d for d in (sync_state.get("dias_concluidos") or []) if str(d or "").strip()]
        if status_state in {"running", "finished"} and (not dias_total or len(dias_concluidos) < dias_total):
            mensagem = (
                "Sincronizacao interrompida antes de concluir todos os dias. "
                "Clique em sincronizar novamente para retomar do ultimo dia concluido."
            )
            try:
                _vendas_sync_update_job(
                    client_id,
                    str(sync_state.get("id") or active_key),
                    {
                        "status": "interrompido",
                        "ultimo_erro": mensagem,
                        "interrupted_at": datetime.now().isoformat(),
                        "dias_concluidos": dias_concluidos,
                    },
                )
                state = _vendas_sync_load_state(client_id)
                sync_state = (state.get("jobs") or {}).get(active_key) if active_key else sync_state
            except Exception:
                logger.exception("[SYNC] Falha ao reconciliar estado interrompido de vendas.")
            progress = _criar_progresso("Erro", 0, 0, 0, mensagem)
    # Se a thread está ativa mas ainda não registrou progresso, retornar estado inicial
    if progress is None and active:
        progress = _criar_progresso("Preparando", 0, 0, 0, "Iniciando sincronização...")
    if isinstance(progress, dict):
        progress = {
            chave: _corrigir_texto_mojibake(valor) if isinstance(valor, str) else valor
            for chave, valor in progress.items()
        }
    active_jobs_payload = []
    for job_id, meta in active_jobs.items():
        job_progress = SYNC_PROGRESS.get(job_id)
        if isinstance(job_progress, dict):
            job_progress = {
                chave: _corrigir_texto_mojibake(valor) if isinstance(valor, str) else valor
                for chave, valor in job_progress.items()
            }
        active_jobs_payload.append({
            **(meta or {}),
            "id": job_id,
            "progress": job_progress,
        })
    return {
        "success": True,
        "progress": progress,
        "logs": [_corrigir_texto_mojibake(log) for log in SYNC_LOGS.get(client_id, [])],
        "active": active,
        "active_count": len(active_jobs),
        "active_jobs": active_jobs_payload,
        "job_progress": {
            job_id: SYNC_PROGRESS.get(job_id)
            for job_id in set(
                list(active_jobs.keys())
                + [
                    str(k) for k in SYNC_PROGRESS.keys()
                    if str(k) != client_id and (SYNC_META.get(str(k)) or {}).get("client_id") == client_id
                ]
            )
            if isinstance(SYNC_PROGRESS.get(job_id), dict)
        },
        "sync_meta": SYNC_META.get(client_id),
        "sync_state": sync_state,
    }



def configure_vendas_sync_progress_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_vendas_sync_progress_runtime()

__all__ = [
    "configure_vendas_sync_progress_runtime",
    "_corrigir_texto_mojibake",
    "_criar_progresso",
    "_sync_context_key",
    "_sync_context_loja",
    "_sync_active_jobs_unlocked",
    "_sync_active_jobs",
    "_sync_register_active_unlocked",
    "_sync_unregister_active",
    "_sync_build_aggregate_progress",
    "_set_progresso",
    "_limpar_progresso",
    "_sync_log",
    "_verificar_cancelamento",
    "cancelar_sincronizacao_vendas",
    "progresso_sincronizacao_vendas",
]
