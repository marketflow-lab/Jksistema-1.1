"""Common Estoque endpoint helpers."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
from fastapi import Depends, HTTPException

from backend.schemas.estoque import (
    EstoqueLancamentosSyncLoteRequest,
    EstoqueLancamentosSyncRequest,
    EstoquePreferenciasColunasRequest,
    EstoqueSyncRequest,
)
from backend.services import estoque_context
from backend.services.runtime_bridge import bind_runtime_globals


def _sync_context_names() -> None:
    for name in estoque_context.CONTEXT_EXPORTS:
        globals()[name] = getattr(estoque_context, name)


def configure_estoque_common_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime


configure_estoque_common_runtime()

_ESTOQUE_SYNC_STATE_LOCK = threading.RLock()
_ESTOQUE_SYNC_TERMINAL_TTL_SECONDS = 15 * 60
_ESTOQUE_SYNC_TERMINAL_JOBS: dict[str, dict[str, dict[str, Any]]] = {}


def _estoque_podar_jobs_terminais_locked(
    client_id: str,
    *,
    agora: float | None = None,
) -> None:
    instante = time.monotonic() if agora is None else agora
    cache_tenant = _ESTOQUE_SYNC_TERMINAL_JOBS.get(client_id)
    if not cache_tenant:
        return
    expirados = [
        job_id
        for job_id, entrada in cache_tenant.items()
        if float(entrada.get("expires_at") or 0) <= instante
    ]
    for job_id in expirados:
        cache_tenant.pop(job_id, None)
    if not cache_tenant:
        _ESTOQUE_SYNC_TERMINAL_JOBS.pop(client_id, None)


def _cache_estoque_job_terminal(
    client_id: str,
    job_id: str,
    *,
    progress: dict[str, Any] | None,
    logs: list[Any],
    sync_meta: dict[str, Any],
) -> None:
    job_id_exato = str(job_id or "").strip()
    if not job_id_exato:
        return
    with _ESTOQUE_SYNC_STATE_LOCK:
        agora = time.monotonic()
        _estoque_podar_jobs_terminais_locked(client_id, agora=agora)
        cache_tenant = _ESTOQUE_SYNC_TERMINAL_JOBS.setdefault(client_id, {})
        cache_tenant[job_id_exato] = {
            "expires_at": agora + _ESTOQUE_SYNC_TERMINAL_TTL_SECONDS,
            "payload": {
                "success": True,
                "job_id": job_id_exato,
                "progress": copy.deepcopy(progress),
                "logs": copy.deepcopy(list(logs)),
                "active": False,
                "sync_meta": copy.deepcopy(sync_meta),
            },
        }


async def listar_estoque(client_id: str = Depends(get_tenant_id)):
    """Retorna o estoque compilado especÃƒÆ’Ã‚Â­fico do cliente (pasta info/<client_id>/)."""
    arquivo_cliente = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
    alvo = arquivo_cliente

    if alvo and os.path.exists(alvo):
        try:
            df = pd.read_csv(alvo).fillna("")
            for col in ["titulo_turbo", "preco_turbo", "status_turbo", "id_bling"]:
                if col in df.columns:
                    df = df.drop(columns=[col])
            return df.to_dict(orient="records")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro ao ler banco de dados: {str(e)}")
    return []

def _set_estoque_progresso(client_id: str, data: dict):
    with _ESTOQUE_SYNC_STATE_LOCK:
        ESTOQUE_SYNC_PROGRESS[client_id] = data


def _set_estoque_lanc_progresso(client_id: str, data: dict):
    ESTOQUE_LANC_SYNC_PROGRESS[client_id] = data

def _criar_progresso(etapa: str, lote: int, total_lotes: int, percentual: int, mensagem: str = ""):
    etapa = str(etapa or "")
    mensagem = str(mensagem or "")
    return {
        "etapa": etapa,
        "lote": lote,
        "total_lotes": total_lotes,
        "percentual": percentual,
        "mensagem": mensagem or f"{etapa} - Lote {lote}/{total_lotes}",
        "timestamp": datetime.now().isoformat(),
    }

def _estoque_lanc_log(client_id: str, mensagem: str):
    logger.info(mensagem)
    logs = ESTOQUE_LANC_SYNC_LOGS.get(client_id, [])
    logs.append(mensagem)
    if len(logs) > 200:
        logs = logs[-200:]
    ESTOQUE_LANC_SYNC_LOGS[client_id] = logs

def _arquivo_preferencias_colunas_estoque(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "estoque_colunas_prefs.json")

def _arquivo_preferencias_colunas_promo(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "promo_colunas_prefs.json")

def _carregar_preferencias_colunas_estoque(client_id: str) -> dict:
    caminho = _arquivo_preferencias_colunas_estoque(client_id)
    if not os.path.exists(caminho):
        return {"ordem_colunas": [], "larguras_colunas": {}}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if not isinstance(dados, dict):
            return {"ordem_colunas": [], "larguras_colunas": {}}
        ordem = dados.get("ordem_colunas")
        larguras = dados.get("larguras_colunas")
        return {
            "ordem_colunas": ordem if isinstance(ordem, list) else [],
            "larguras_colunas": larguras if isinstance(larguras, dict) else {},
        }
    except Exception:
        return {"ordem_colunas": [], "larguras_colunas": {}}

def _salvar_preferencias_colunas_estoque(client_id: str, preferencias: dict) -> dict:
    caminho = _arquivo_preferencias_colunas_estoque(client_id)
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.exists(pasta):
        os.makedirs(pasta, exist_ok=True)

    ordem_raw = (preferencias or {}).get("ordem_colunas")
    larguras_raw = (preferencias or {}).get("larguras_colunas")

    ordem: list[str] = []
    if isinstance(ordem_raw, list):
        ordem = [str(v or "").strip() for v in ordem_raw if str(v or "").strip()]

    larguras: dict[str, int] = {}
    if isinstance(larguras_raw, dict):
        for k, v in larguras_raw.items():
            key = str(k or "").strip()
            if not key:
                continue
            try:
                width = int(float(v))
            except Exception:
                continue
            larguras[key] = max(1, width)

    payload = {
        "ordem_colunas": ordem,
        "larguras_colunas": larguras,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload

def _carregar_preferencias_colunas_promo(client_id: str) -> dict:
    caminho = _arquivo_preferencias_colunas_promo(client_id)
    if not os.path.exists(caminho):
        return {"ordem_colunas": [], "colunas_visiveis": [], "larguras_colunas": {}, "versao": None}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if not isinstance(dados, dict):
            return {"ordem_colunas": [], "colunas_visiveis": [], "larguras_colunas": {}, "versao": None}
        ordem = dados.get("ordem_colunas")
        colunas_visiveis = dados.get("colunas_visiveis")
        larguras = dados.get("larguras_colunas")
        versao = dados.get("versao")
        return {
            "ordem_colunas": ordem if isinstance(ordem, list) else [],
            "colunas_visiveis": colunas_visiveis if isinstance(colunas_visiveis, list) else [],
            "larguras_colunas": larguras if isinstance(larguras, dict) else {},
            "versao": str(versao).strip() if isinstance(versao, str) and str(versao).strip() else None,
        }
    except Exception:
        return {"ordem_colunas": [], "colunas_visiveis": [], "larguras_colunas": {}, "versao": None}

def _salvar_preferencias_colunas_promo(client_id: str, preferencias: dict) -> dict:
    caminho = _arquivo_preferencias_colunas_promo(client_id)
    pasta = os.path.dirname(caminho)
    if pasta and not os.path.exists(pasta):
        os.makedirs(pasta, exist_ok=True)

    atuais = _carregar_preferencias_colunas_promo(client_id)

    ordem_raw = (preferencias or {}).get("ordem_colunas")
    colunas_visiveis_raw = (preferencias or {}).get("colunas_visiveis")
    larguras_raw = (preferencias or {}).get("larguras_colunas")

    ordem: list[str] = list(atuais.get("ordem_colunas") or [])
    if isinstance(ordem_raw, list):
        ordem = [str(v or "").strip() for v in ordem_raw if str(v or "").strip()]

    colunas_visiveis: list[str] = list(atuais.get("colunas_visiveis") or [])
    if isinstance(colunas_visiveis_raw, list):
        colunas_visiveis = [str(v or "").strip() for v in colunas_visiveis_raw if str(v or "").strip()]

    larguras: dict[str, int] = dict(atuais.get("larguras_colunas") or {})
    if isinstance(larguras_raw, dict):
        larguras = {}
        for k, v in larguras_raw.items():
            key = str(k or "").strip()
            if not key:
                continue
            try:
                width = int(float(v))
            except Exception:
                continue
            larguras[key] = max(1, width)

    payload = {
        "ordem_colunas": ordem,
        "colunas_visiveis": colunas_visiveis,
        "larguras_colunas": larguras,
        "versao": str((preferencias or {}).get("versao") or atuais.get("versao") or "").strip() or None,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload

def _estoque_log(client_id: str, mensagem: str):
    logger.info(mensagem)
    with _ESTOQUE_SYNC_STATE_LOCK:
        logs = ESTOQUE_SYNC_LOGS.get(client_id, [])
        logs.append(mensagem)
        if len(logs) > 200:
            logs = logs[-200:]
        ESTOQUE_SYNC_LOGS[client_id] = logs


async def progresso_sincronizacao_estoque(
    client_id: str = Depends(get_tenant_id),
    job_id: str | None = None,
):
    job_id_esperado = str(job_id or "").strip()
    with _ESTOQUE_SYNC_STATE_LOCK:
        _estoque_podar_jobs_terminais_locked(client_id)
        sync_meta = ESTOQUE_SYNC_META.get(client_id)
        job_id_corrente = str((sync_meta or {}).get("job_id") or "").strip()
        if job_id_esperado and job_id_esperado != job_id_corrente:
            entrada = (
                _ESTOQUE_SYNC_TERMINAL_JOBS.get(client_id, {}).get(job_id_esperado)
            )
            if entrada is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "estoque_sync_job_not_found",
                        "message": "Job de estoque nao encontrado ou expirado.",
                    },
                )
            return copy.deepcopy(entrada["payload"])

        active = bool(ESTOQUE_SYNC_ACTIVE.get(client_id)) and not bool(
            (sync_meta or {}).get("outcome")
        )
        progress = ESTOQUE_SYNC_PROGRESS.get(client_id)
        if progress is None and active:
            progress = _criar_progresso(
                "Preparando", 0, 0, 0, "Iniciando atualização de estoque..."
            )
        return {
            "success": True,
            "job_id": job_id_corrente or None,
            "progress": copy.deepcopy(progress),
            "logs": copy.deepcopy(ESTOQUE_SYNC_LOGS.get(client_id, [])),
            "active": active,
            "sync_meta": copy.deepcopy(sync_meta),
        }


async def api_estoque_preferencias_colunas_get(client_id: str = Depends(get_tenant_id)):
    prefs = _carregar_preferencias_colunas_estoque(client_id)
    return {
        "success": True,
        "ordem_colunas": prefs.get("ordem_colunas") or [],
        "larguras_colunas": prefs.get("larguras_colunas") or {},
    }

async def api_estoque_preferencias_colunas_put(
    req: EstoquePreferenciasColunasRequest,
    client_id: str = Depends(get_tenant_id),
):
    payload = _salvar_preferencias_colunas_estoque(client_id, {
        "ordem_colunas": req.ordem_colunas or [],
        "larguras_colunas": req.larguras_colunas or {},
    })
    return {
        "success": True,
        "ordem_colunas": payload.get("ordem_colunas") or [],
        "larguras_colunas": payload.get("larguras_colunas") or {},
        "updated_at": payload.get("updated_at"),
    }


__all__ = [
    "configure_estoque_common_runtime",
    "listar_estoque",
    "_set_estoque_progresso",
    "_set_estoque_lanc_progresso",
    "_estoque_lanc_log",
    "_arquivo_preferencias_colunas_estoque",
    "_arquivo_preferencias_colunas_promo",
    "_carregar_preferencias_colunas_estoque",
    "_salvar_preferencias_colunas_estoque",
    "_carregar_preferencias_colunas_promo",
    "_salvar_preferencias_colunas_promo",
    "_estoque_log",
    "progresso_sincronizacao_estoque",
    "api_estoque_preferencias_colunas_get",
    "api_estoque_preferencias_colunas_put",
]
