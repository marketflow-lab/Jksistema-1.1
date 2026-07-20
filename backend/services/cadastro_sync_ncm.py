"""Cadastro NCM/CEST synchronization jobs."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Header, Request

from backend.services.runtime_bridge import bind_runtime_globals

logger = logging.getLogger("jk_sistema")


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    raise RuntimeError("Cadastro runtime was not configured.")


def get_tenant_path(client_id: str):
    raise RuntimeError("Cadastro runtime was not configured.")


def _configure_runtime_globals(target_globals, runtime_module=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        if hasattr(runtime, "get_tenant_id"):
            target_globals["get_tenant_id"] = getattr(runtime, "get_tenant_id")
        if hasattr(runtime, "get_tenant_path"):
            target_globals["get_tenant_path"] = getattr(runtime, "get_tenant_path")
    return runtime


import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import Depends

from backend.services.cadastro_common import *
from backend.services.integracoes import renovar_token_bling_loja
from backend.services.monofasico_rules import avaliar_monofasico

SYNC_NCM_JOBS: dict[str, dict] = {}

MONOFASICO_CADASTRO_COLUNAS = (
    "monofasico",
    "monofasico_status",
    "monofasico_confianca",
    "monofasico_fundamento",
    "monofasico_fonte",
    "monofasico_motivo",
    "monofasico_verificado_em",
)


def _classificar_monofasico_cadastro(df_cad: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Apply the shared conservative classifier to every Cadastro row."""
    for coluna in MONOFASICO_CADASTRO_COLUNAS:
        if coluna not in df_cad.columns:
            df_cad[coluna] = ""

    alterados = 0
    contagens: dict[str, int] = {}
    verificado_em = datetime.now(timezone.utc).isoformat()
    campos_resultado = {
        "monofasico": "rotulo",
        "monofasico_status": "status",
        "monofasico_confianca": "confianca",
        "monofasico_fundamento": "fundamento",
        "monofasico_fonte": "fonte",
        "monofasico_motivo": "motivo",
    }

    for idx, row in df_cad.iterrows():
        descricao = " | ".join(
            str(row.get(coluna, "") or "").strip()
            for coluna in ("produto_bling", "nome", "produto", "descricao", "categoria")
            if str(row.get(coluna, "") or "").strip()
        )
        ncm_principal = str(row.get("ncm", "") or "").strip()
        ncm_auditoria = str(row.get("ncm_auditoria", "") or "").strip()
        resultado = avaliar_monofasico(ncm_principal or ncm_auditoria, descricao)
        fonte_auditoria = str(row.get("ncm_fonte_auditoria", "") or "").lower()
        correspondencia = str(row.get("ncm_correspondencia", "") or "").lower()
        if (not ncm_principal and "histórico" in fonte_auditoria) or correspondencia == "ambígua":
            resultado = {
                **resultado,
                "is_monofasico": None,
                "status": "revisao",
                "rotulo": "Revisão necessária",
                "confianca": "pendente",
                "motivo": "O NCM usado na auditoria ainda precisa ser reconfirmado na fonte atual.",
            }
        status = str(resultado.get("status") or "nao_verificado")
        contagens[status] = contagens.get(status, 0) + 1
        mudou_linha = False
        for coluna, chave in campos_resultado.items():
            novo = str(resultado.get(chave) or "")
            atual = str(row.get(coluna, "") or "")
            if atual != novo:
                df_cad.at[idx, coluna] = novo
                mudou_linha = True
        if mudou_linha:
            df_cad.at[idx, "monofasico_verificado_em"] = verificado_em
            alterados += 1

    return alterados, contagens


def configure_cadastro_sync_ncm_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_sync_ncm_runtime()

def _sku_lookup_keys_sync_ncm(sku_val: str) -> tuple[str, str, str]:
    sku_norm = _normalizar_sku_mes(str(sku_val or "").strip())
    if re.match(r"^\d+\.0+$", sku_norm):
        sku_norm = str(int(float(sku_norm)))
    sku_compacto = re.sub(r"[^A-Z0-9]", "", sku_norm.upper())
    partes = re.split(r"([0-9]+)", sku_norm.upper())
    sku_numsoft = "".join(str(int(p)) if p.isdigit() else p for p in partes)
    sku_numsoft_compacto = re.sub(r"[^A-Z0-9]", "", sku_numsoft)
    return sku_norm, sku_compacto, sku_numsoft_compacto

def _set_sync_ncm_job(job_id: str, **kwargs):
    atual = SYNC_NCM_JOBS.get(job_id, {})
    atual.update(kwargs)
    atual["updated_at"] = time.time()
    SYNC_NCM_JOBS[job_id] = atual

def _sync_ncm_cadastro_worker(client_id: str, job_id: str):
    try:
        _set_sync_ncm_job(job_id, status="running", mensagem="Preparando sincronizaÃƒÂ§ÃƒÂ£o de NCM/CEST...", processados=0, total=0)

        arquivo_cadastro = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
        arquivo_estoque = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
        if not (arquivo_cadastro and os.path.exists(arquivo_cadastro) and arquivo_estoque and os.path.exists(arquivo_estoque)):
            _set_sync_ncm_job(job_id, status="error", mensagem="Arquivos de cadastro/estoque nÃ£o encontrados para o cliente.")
            return

        df_cad = pd.read_csv(arquivo_cadastro, dtype=str).fillna("")
        df_cad.columns = [c.strip().lower() for c in df_cad.columns]
        df_estoque = pd.read_csv(arquivo_estoque, dtype=str).fillna("")
        df_estoque.columns = [c.strip().lower() for c in df_estoque.columns]

        if "id_bling" not in df_estoque.columns:
            _set_sync_ncm_job(job_id, status="error", mensagem="Coluna id_bling nÃ£o encontrada no estoque.")
            return
        if "ncm_bling" not in df_estoque.columns:
            df_estoque["ncm_bling"] = ""
        if "cest_bling" not in df_estoque.columns:
            df_estoque["cest_bling"] = ""

        lojas = carregar_lojas(client_id)
        bling_por_loja = {}
        for loja in lojas:
            cfg_bling = (loja.get("integracoes") or {}).get("bling") or {}
            if cfg_bling.get("access_token") and cfg_bling.get("id") and cfg_bling.get("secret"):
                nome_loja = str(loja.get("nome") or "").strip()
                if nome_loja:
                    bling_por_loja[nome_loja] = {
                        "id": cfg_bling.get("id"),
                        "secret": cfg_bling.get("secret"),
                        "access_token": cfg_bling.get("access_token"),
                        "refresh_token": cfg_bling.get("refresh_token"),
                    }

        if not bling_por_loja:
            _set_sync_ncm_job(job_id, status="error", mensagem="Nenhuma loja Bling conectada encontrada.")
            return

        def _norm_loja_nome(v: str) -> str:
            return str(v or "").strip().lower()

        candidatos_idx = []
        for idx_est, row_est in df_estoque.iterrows():
            pid = str(row_est.get("id_bling", "") or "").strip()
            # Sempre renova NCM/CEST para todos os SKUs com id_bling quando o usuÃƒÂ¡rio dispara a sincronizaÃƒÂ§ÃƒÂ£o.
            if pid:
                candidatos_idx.append(idx_est)

        total = len(candidatos_idx)
        _set_sync_ncm_job(job_id, total=total, mensagem=f"Sincronizando NCM/CEST: 0/{total}")

        cache_ncm_cest = {}
        alterou_ncm_estoque = False
        encontrados = 0
        processados = 0

        for idx_est in candidatos_idx:
            row_est = df_estoque.loc[idx_est]
            pid = str(row_est.get("id_bling", "") or "").strip()
            loja_sync = str(row_est.get("loja_sync", "") or "").strip()
            loja_sync_norm = _norm_loja_nome(loja_sync)
            ncm_atual = str(row_est.get("ncm_bling", "") or "").strip()
            cest_atual = str(row_est.get("cest_bling", "") or "").strip()

            candidatos = []
            if loja_sync_norm:
                for nome in bling_por_loja.keys():
                    if _norm_loja_nome(nome) == loja_sync_norm:
                        candidatos.append(nome)
                        break
            if not candidatos:
                candidatos = list(bling_por_loja.keys())

            ncm_novo = ""
            cest_novo = ""
            for nome_loja in candidatos:
                chave_cache = (nome_loja, pid)
                if chave_cache in cache_ncm_cest:
                    resp_cached = cache_ncm_cest[chave_cache]
                    ncm_cache = resp_cached.get("ncm", "")
                    cest_cache = resp_cached.get("cest", "")
                    if ncm_cache:
                        ncm_novo = ncm_cache
                    if cest_cache:
                        cest_novo = cest_cache
                    if ncm_novo or cest_novo:
                        break
                    continue

                cfg_loja = bling_por_loja.get(nome_loja) or {}
                access_token_ncm = cfg_loja.get("access_token")
                cid_ncm = cfg_loja.get("id")
                sec_ncm = cfg_loja.get("secret")
                refresh_ncm = cfg_loja.get("refresh_token")
                if not (access_token_ncm and cid_ncm and sec_ncm):
                    cache_ncm_cest[chave_cache] = {"ncm": "", "cest": ""}
                    continue

                resp_ncm_cest, status_ncm = _bling_obter_ncm_cest_produto(access_token_ncm, pid)
                if status_ncm == 401 and refresh_ncm:
                    try:
                        renovado = renovar_token_bling_loja(client_id, nome_loja, cfg_loja)
                        bling_por_loja[nome_loja] = dict(renovado)
                        access_token_ncm = renovado.get("access_token") or access_token_ncm
                        resp_ncm_cest, status_ncm = _bling_obter_ncm_cest_produto(access_token_ncm, pid)
                    except Exception:
                        status_ncm = 500

                resp_ncm_cest = resp_ncm_cest if (status_ncm == 200 and resp_ncm_cest) else {"ncm": "", "cest": ""}
                cache_ncm_cest[chave_cache] = resp_ncm_cest
                ncm_resp = resp_ncm_cest.get("ncm", "")
                cest_resp = resp_ncm_cest.get("cest", "")
                if ncm_resp and not ncm_novo:
                    ncm_novo = ncm_resp
                if cest_resp and not cest_novo:
                    cest_novo = cest_resp
                if ncm_novo or cest_novo:
                    break

            processados += 1
            if ncm_novo or cest_novo:  # Atualizar se temos NCM ou CEST
                if ncm_novo and ncm_novo != ncm_atual:
                    df_estoque.at[idx_est, "ncm_bling"] = ncm_novo
                if cest_novo and cest_novo != cest_atual:
                    df_estoque.at[idx_est, "cest_bling"] = cest_novo
                alterou_ncm_estoque = True
                encontrados += 1

            _set_sync_ncm_job(
                job_id,
                processados=processados,
                encontrados=encontrados,
                mensagem=f"Sincronizando NCM/CEST: {processados}/{total}"
            )

        if alterou_ncm_estoque:
            df_estoque.to_csv(arquivo_estoque, index=False)

        # Propaga NCM e CEST para o cadastro por SKU e persiste no arquivo do usuÃƒÂ¡rio.
        mapa_ncm = {}
        mapa_ncm_compacto = {}
        mapa_ncm_numsoft = {}
        mapa_cest = {}
        mapa_cest_compacto = {}
        mapa_cest_numsoft = {}
        for _, row_est in df_estoque[["sku", "ncm_bling", "cest_bling"]].iterrows():
            sku_est, sku_est_compacto, sku_est_numsoft = _sku_lookup_keys_sync_ncm(row_est.get("sku", ""))
            ncm_est = str(row_est.get("ncm_bling", "") or "").strip()
            cest_est = str(row_est.get("cest_bling", "") or "").strip()
            if sku_est and ncm_est and sku_est not in mapa_ncm:
                mapa_ncm[sku_est] = ncm_est
            if sku_est_compacto and ncm_est and sku_est_compacto not in mapa_ncm_compacto:
                mapa_ncm_compacto[sku_est_compacto] = ncm_est
            if sku_est_numsoft and ncm_est and sku_est_numsoft not in mapa_ncm_numsoft:
                mapa_ncm_numsoft[sku_est_numsoft] = ncm_est
            if sku_est and cest_est and sku_est not in mapa_cest:
                mapa_cest[sku_est] = cest_est
            if sku_est_compacto and cest_est and sku_est_compacto not in mapa_cest_compacto:
                mapa_cest_compacto[sku_est_compacto] = cest_est
            if sku_est_numsoft and cest_est and sku_est_numsoft not in mapa_cest_numsoft:
                mapa_cest_numsoft[sku_est_numsoft] = cest_est

        def _resolver_ncm_sku(sku_val: str) -> str:
            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys_sync_ncm(sku_val)
            return (
                mapa_ncm.get(sku_norm)
                or mapa_ncm_compacto.get(sku_compacto)
                or mapa_ncm_numsoft.get(sku_numsoft, "")
            )
        
        def _resolver_cest_sku(sku_val: str) -> str:
            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys_sync_ncm(sku_val)
            return (
                mapa_cest.get(sku_norm)
                or mapa_cest_compacto.get(sku_compacto)
                or mapa_cest_numsoft.get(sku_numsoft, "")
            )

        if "sku" in df_cad.columns:
            ncm_series = df_cad["sku"].astype(str).apply(_resolver_ncm_sku).fillna("")
            cest_series = df_cad["sku"].astype(str).apply(_resolver_cest_sku).fillna("")
            criou_col_ncm = False
            criou_col_cest = False
            if "ncm" not in df_cad.columns:
                df_cad["ncm"] = ""
                criou_col_ncm = True
            if "cest" not in df_cad.columns:
                df_cad["cest"] = ""
                criou_col_cest = True

            ncm_atual_series = df_cad["ncm"].astype(str)
            cest_atual_series = df_cad["cest"].astype(str)
            ncm_series_str = ncm_series.astype(str)
            cest_series_str = cest_series.astype(str)
            mask_ncm_novo = ncm_series_str.str.strip() != ""
            mask_cest_novo = cest_series_str.str.strip() != ""

            ncm_atualizados = int((mask_ncm_novo & (ncm_atual_series != ncm_series_str)).sum())
            cest_atualizados = int((mask_cest_novo & (cest_atual_series != cest_series_str)).sum())
            ncm_preservados = int((~mask_ncm_novo & ncm_atual_series.str.strip().ne("")).sum())
            cest_preservados = int((~mask_cest_novo & cest_atual_series.str.strip().ne("")).sum())

            df_cad.loc[mask_ncm_novo, "ncm"] = ncm_series.loc[mask_ncm_novo]
            df_cad.loc[mask_cest_novo, "cest"] = cest_series.loc[mask_cest_novo]
            classificados_alterados, contagens_monofasico = _classificar_monofasico_cadastro(df_cad)

            mudou_cadastro = (
                criou_col_ncm or
                criou_col_cest or
                (ncm_atualizados > 0) or
                (cest_atualizados > 0) or
                (classificados_alterados > 0)
            )
            if mudou_cadastro:
                # SÃƒÂ³ sobrescreve quando hÃƒÂ¡ valor novo vindo do estoque/Bling;
                # evita apagar NCM/CEST jÃƒÂ¡ salvos no cadastro.
                df_cad.loc[mask_ncm_novo, "ncm"] = ncm_series.loc[mask_ncm_novo]
                df_cad.loc[mask_cest_novo, "cest"] = cest_series.loc[mask_cest_novo]
                df_cad.to_csv(arquivo_cadastro, index=False)

            logger.info(
                "[CADASTRO NCM/CEST][%s][job=%s] atualizados: ncm=%d, cest=%d | preservados: ncm=%d, cest=%d",
                client_id,
                job_id,
                ncm_atualizados,
                cest_atualizados,
                ncm_preservados,
                cest_preservados,
            )
            logger.info(
                "[CADASTRO MONOFASICO][%s][job=%s] linhas_alteradas=%d | contagens=%s",
                client_id,
                job_id,
                classificados_alterados,
                contagens_monofasico,
            )

        _set_sync_ncm_job(
            job_id,
            status="done",
            mensagem=f"SincronizaÃƒÂ§ÃƒÂ£o NCM concluida: {encontrados}/{total} preenchidos.",
            processados=processados,
            encontrados=encontrados,
        )
    except Exception as e:
        _set_sync_ncm_job(job_id, status="error", mensagem=f"Erro na sincronizaÃƒÂ§ÃƒÂ£o de NCM: {e}")

async def iniciar_sync_ncm_cadastro(client_id: str = Depends(get_tenant_id)):
    # Reaproveita job em execuÃƒÂ§ÃƒÂ£o para o mesmo cliente.
    for job_id, job in SYNC_NCM_JOBS.items():
        if job.get("client_id") == client_id and job.get("status") == "running":
            return {"job_id": job_id, "status": "running"}

    job_id = uuid.uuid4().hex
    SYNC_NCM_JOBS[job_id] = {
        "client_id": client_id,
        "status": "running",
        "mensagem": "Inicializando...",
        "processados": 0,
        "total": 0,
        "encontrados": 0,
        "updated_at": time.time(),
    }
    t = threading.Thread(target=_sync_ncm_cadastro_worker, args=(client_id, job_id), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "running"}

async def progresso_sync_ncm_cadastro(job_id: str, client_id: str = Depends(get_tenant_id)):
    job = SYNC_NCM_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job de sincronizaÃƒÂ§ÃƒÂ£o nÃ£o encontrado.")
    if job.get("client_id") != client_id:
        raise HTTPException(status_code=403, detail="Acesso negado a este job.")
    return {k: v for k, v in job.items() if k != "client_id"}

__all__ = ['SYNC_NCM_JOBS', 'MONOFASICO_CADASTRO_COLUNAS', '_classificar_monofasico_cadastro', '_sku_lookup_keys_sync_ncm', '_set_sync_ncm_job', '_sync_ncm_cadastro_worker', 'iniciar_sync_ncm_cadastro', 'progresso_sync_ncm_cadastro', 'configure_cadastro_sync_ncm_runtime']
