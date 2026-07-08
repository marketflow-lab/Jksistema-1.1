"""Compiled stock synchronization for Estoque."""

from __future__ import annotations

import asyncio
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


def configure_estoque_sync_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime


configure_estoque_sync_runtime()

from backend.services.estoque_common import (
    _criar_progresso,
    _estoque_log,
    _set_estoque_progresso,
)
from backend.services.estoque_historico import _registrar_snapshot_historico_estoque

def _estoque_verificar_cancelamento(client_id: str):
    if ESTOQUE_SYNC_CANCEL_FLAGS.get(client_id):
        ESTOQUE_SYNC_CANCEL_FLAGS.pop(client_id, None)
        _set_estoque_progresso(client_id, _criar_progresso("Cancelamento", 0, 0, 0, "Sincronização de estoque cancelada."))
        raise HTTPException(status_code=409, detail="Sincronização de estoque cancelada pelo usuário.")

def _sincronizar_estoque_thread_worker(req: "EstoqueSyncRequest", client_id: str):
    ESTOQUE_SYNC_ACTIVE[client_id] = True
    try:
        asyncio.run(_sincronizar_estoque_impl(req, client_id))
    except HTTPException as e:
        msg = e.detail or "Erro na sincronização de estoque"
        _estoque_log(client_id, f"[ESTOQUE] Erro: {msg}")
        _set_estoque_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, str(msg)[:140]))
    except requests.RequestException:
        msg = "Falha de conexão com a API do Bling. Verifique internet/firewall e tente novamente em alguns minutos."
        logger.exception(f"[ESTOQUE] Erro: {msg}")
        _set_estoque_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, msg[:140]))
    except Exception as e:
        texto = str(e)
        if "HTTPSConnectionPool" in texto and "api.bling.com.br" in texto:
            msg = "Erro de conectividade com a API do Bling. Tente novamente em alguns minutos."
        else:
            msg = f"Erro inesperado: {texto}"
        logger.exception(f"[ESTOQUE] Erro: {msg}")
        _set_estoque_progresso(client_id, _criar_progresso("Erro", 0, 0, 0, msg[:140]))
    finally:
        ESTOQUE_SYNC_ACTIVE.pop(client_id, None)

async def sincronizar_estoque(req: EstoqueSyncRequest, client_id: str = Depends(get_tenant_id)):
    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")

    if ESTOQUE_SYNC_ACTIVE.get(client_id):
        return {"started": False, "already_running": True, "message": "Atualização de estoque já em andamento."}

    ESTOQUE_SYNC_META[client_id] = {
        "loja": req.loja,
        "started_at": datetime.now().isoformat(),
    }
    ESTOQUE_SYNC_LOGS[client_id] = []
    ESTOQUE_SYNC_ACTIVE[client_id] = True
    _set_estoque_progresso(
        client_id,
        _criar_progresso("Preparando", 0, 0, 0, "Solicitacao recebida. Iniciando atualizacao de estoque..."),
    )

    t = threading.Thread(
        target=_sincronizar_estoque_thread_worker,
        args=(req, client_id),
        daemon=True
    )
    try:
        t.start()
    except Exception:
        ESTOQUE_SYNC_ACTIVE.pop(client_id, None)
        _set_estoque_progresso(
            client_id,
            _criar_progresso("Erro", 0, 0, 0, "Nao foi possivel iniciar a atualizacao de estoque."),
        )
        raise HTTPException(status_code=500, detail="Nao foi possivel iniciar a atualizacao de estoque.")
    return {"started": True, "message": "Atualização de estoque iniciada em background."}

async def _sincronizar_estoque_impl(req: EstoqueSyncRequest, client_id: str):
    ESTOQUE_SYNC_CANCEL_FLAGS.pop(client_id, None)
    ESTOQUE_SYNC_LOGS[client_id] = []
    _set_estoque_progresso(client_id, _criar_progresso("Preparando", 0, 0, 0, "Iniciando atualização de estoque."))
    _estoque_log(client_id, f"[ESTOQUE] Iniciando sincronização da loja {req.loja}")

    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")

    loja = buscar_loja(client_id, req.loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja não encontrada para o cliente.")

    integracoes = loja.get("integracoes", {})
    bling_cfg = integracoes.get("bling")
    if not bling_cfg:
        raise HTTPException(status_code=400, detail="Loja não possui integração Bling conectada.")

    access_token = bling_cfg.get("access_token")
    cid = bling_cfg.get("id")
    sec = bling_cfg.get("secret")

    if not (access_token and cid and sec):
        raise HTTPException(status_code=400, detail="Credenciais Bling incompletas para esta loja.")

    _estoque_verificar_cancelamento(client_id)
    _set_estoque_progresso(client_id, _criar_progresso("Bling", 1, 4, 15, "Buscando produtos no Bling..."))
    _estoque_log(client_id, "[ESTOQUE] Etapa 1/4: listando produtos")
    produtos, status_prod, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        _bling_listar_produtos,
        on_refresh=lambda: _set_estoque_progresso(client_id, _criar_progresso("Bling", 1, 4, 20, "Renovando token Bling...")),
    )
    access_token = bling_cfg.get("access_token")

    if status_prod != 200:
        status_err = int(status_prod or 502)
        _estoque_log(client_id, f"[ESTOQUE] Falha ao listar produtos no Bling: {status_err}")
        if status_err == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
        if status_err == 503:
            raise HTTPException(status_code=503, detail="Falha de conexão com Bling ao listar produtos. Verifique a conexão e tente novamente.")
        raise HTTPException(
            status_code=503,
            detail="Erro inesperado ao consultar produtos no Bling. Tente novamente em alguns minutos."
        )

    _estoque_verificar_cancelamento(client_id)
    _set_estoque_progresso(client_id, _criar_progresso("Bling", 2, 4, 45, "Mapeando depósitos da loja..."))
    _estoque_log(client_id, "[ESTOQUE] Etapa 2/4: mapeando depósitos")
    mapa_dep, status_dep, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        _bling_map_depositos,
        on_refresh=lambda: _set_estoque_progresso(client_id, _criar_progresso("Bling", 2, 4, 48, "Renovando token Bling...")),
    )
    access_token = bling_cfg.get("access_token")
    if status_dep != 200:
        status_err = int(status_dep or 502)
        _estoque_log(client_id, f"[ESTOQUE] Falha ao mapear depósitos no Bling: {status_err}")
        if status_err == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
        if status_err == 503:
            raise HTTPException(status_code=503, detail="Falha de conexão com Bling ao consultar depósitos. Tente novamente em alguns minutos.")
        raise HTTPException(status_code=503, detail="Erro inesperado ao consultar depósitos no Bling. Tente novamente em alguns minutos.")

    _estoque_verificar_cancelamento(client_id)
    _set_estoque_progresso(client_id, _criar_progresso("Bling", 3, 4, 70, "Calculando saldos de estoque..."))
    _estoque_log(client_id, "[ESTOQUE] Etapa 3/4: consultando saldos")
    ids_prod = [str(p.get("id_bling")).strip() for p in produtos if p.get("id_bling")]
    saldos, status_saldo, bling_cfg = _bling_executar_com_refresh(
        client_id,
        req.loja,
        bling_cfg,
        lambda token: _bling_saldos(token, ids_prod, mapa_dep),
        on_refresh=lambda: _set_estoque_progresso(client_id, _criar_progresso("Bling", 3, 4, 72, "Renovando token Bling...")),
    )
    access_token = bling_cfg.get("access_token")
    if status_saldo != 200:
        status_err = int(status_saldo or 502)
        _estoque_log(client_id, f"[ESTOQUE] Falha ao consultar saldos no Bling: {status_err}")
        if status_err == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
        if status_err == 503:
            raise HTTPException(status_code=503, detail="Falha de conexão com Bling ao consultar saldos. Tente novamente em alguns minutos.")
        raise HTTPException(status_code=503, detail="Erro inesperado ao consultar saldos no Bling. Tente novamente em alguns minutos.")

    _estoque_verificar_cancelamento(client_id)
    _set_estoque_progresso(client_id, _criar_progresso("Processamento", 4, 4, 85, "Consolidando dados do estoque..."))
    registros = []
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    for p in produtos:
        pid = str(p.get("id_bling") or "").strip()
        saldo = saldos.get(pid, {"loja": 0, "full": 0}) if saldos else {"loja": 0, "full": 0}
        registros.append({
            "sku": p.get("sku"),
            "loja_sync": req.loja,
            "last_update": agora,
            "id_bling": pid,
            "nome_bling": p.get("nome_bling"),
            "situacao_bling": p.get("situacao_bling"),
            "ncm_bling": p.get("ncm_bling"),
            "saldo_loja": saldo.get("loja", 0),
            "saldo_full": saldo.get("full", 0)
        })

    _estoque_verificar_cancelamento(client_id)
    _set_estoque_progresso(client_id, _criar_progresso("Finalizando", 4, 4, 95, "Salvando estoque atualizado..."))
    tenant_path = get_tenant_path(client_id)
    arquivo_cliente = os.path.join(tenant_path, "produtos_compilado.csv")

    df_loja_atual = pd.DataFrame(registros)
    df_existente = pd.DataFrame()
    if os.path.exists(arquivo_cliente):
        try:
            df_existente = pd.read_csv(arquivo_cliente).fillna("")
        except Exception as e:
            logger.warning(f"[ESTOQUE] Falha ao ler estoque existente do tenant {client_id}: {e}")
            df_existente = pd.DataFrame()

    if not df_existente.empty and "loja_sync" in df_existente.columns:
        loja_alvo_norm = str(req.loja or "").strip().lower()
        mask_outras_lojas = df_existente["loja_sync"].astype(str).str.strip().str.lower() != loja_alvo_norm
        df_existente = df_existente.loc[mask_outras_lojas].copy()

    if df_existente.empty:
        df_saida = df_loja_atual
    elif df_loja_atual.empty:
        df_saida = df_existente
    else:
        df_saida = pd.concat([df_existente, df_loja_atual], ignore_index=True)

    df_saida.to_csv(arquivo_cliente, index=False)

    _set_estoque_progresso(client_id, _criar_progresso("Finalizando", 4, 4, 97, "Registrando histórico diário de estoque..."))
    total_hist = _registrar_snapshot_historico_estoque(client_id, req.loja, registros)

    _estoque_log(client_id, f"[ESTOQUE] Sincronização concluída: {len(registros)} SKUs")
    _estoque_log(client_id, f"[ESTOQUE] Histórico atualizado ({total_hist} registros processados; último snapshot do dia por SKU)")
    _set_estoque_progresso(client_id, _criar_progresso("Concluído", 4, 4, 100, f"Estoque atualizado com {len(registros)} SKUs."))
    return {"success": True, "total": len(registros)}


__all__ = [
    "configure_estoque_sync_runtime",
    "_estoque_verificar_cancelamento",
    "_sincronizar_estoque_thread_worker",
    "sincronizar_estoque",
    "_sincronizar_estoque_impl",
]
