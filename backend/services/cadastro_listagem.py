"""Cadastro listing endpoint handlers."""

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


import io
import os
import re
from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import Depends, File, Form, HTTPException, UploadFile

from backend.schemas import CadastroProdutoRequest
from backend.services.cadastro_common import *
from backend.services.cadastro_custos import *
from backend.services.cadastro_fotos import *
from backend.services.cadastro_sync_ncm import *
from backend.services.integracoes import renovar_token_bling_loja


def configure_cadastro_listagem_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    configure_cadastro_custos_runtime(runtime_module)
    configure_cadastro_fotos_runtime(runtime_module)
    configure_cadastro_sync_ncm_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_listagem_runtime()

async def listar_produtos_cadastro(
    client_id: str = Depends(get_tenant_id),
    sync_fotos: bool = False,
    sync_ncm: bool = False,
):
    def _sku_lookup_keys(sku_val: str) -> tuple[str, str, str]:
        """Gera chaves de busca por SKU para melhorar match entre fontes com formatos diferentes."""
        sku_norm = _normalizar_sku_mes(str(sku_val or "").strip())
        # Tratar SKU vindo como nÃƒÂºmero decimal de planilha/CSV (ex: "123.0" -> "123").
        if re.match(r"^\d+\.0+$", sku_norm):
            sku_norm = str(int(float(sku_norm)))
        sku_compacto = re.sub(r"[^A-Z0-9]", "", sku_norm.upper())
        # Chave alternativa: remove zeros ÃƒÂ  esquerda em cada bloco numÃƒÂ©rico (ex: 008-01A -> 8-1A).
        partes = re.split(r"([0-9]+)", sku_norm.upper())
        sku_numsoft = "".join(str(int(p)) if p.isdigit() else p for p in partes)
        sku_numsoft_compacto = re.sub(r"[^A-Z0-9]", "", sku_numsoft)
        return sku_norm, sku_compacto, sku_numsoft_compacto

    def _norm_col(col_name: str) -> str:
        return re.sub(r"\s+", " ", str(col_name or "").strip().lower().replace("_", " "))

    def _obter_mapa_fotos_por_sku() -> dict:
        client = autenticar_google_sheets()
        if not client:
            return {}
        try:
            sh = client.open_by_key(SPREADSHEET_ID_FOTOS_SKU)
            ws = None
            for w in sh.worksheets():
                if int(getattr(w, "id", -1)) == int(SPREADSHEET_GID_FOTOS_SKU):
                    ws = w
                    break
            if ws is None:
                ws = sh.get_worksheet_by_id(int(SPREADSHEET_GID_FOTOS_SKU))

            # get_all_records falha quando a aba tem cabeÃƒÂ§alhos duplicados/vazios;
            # por isso usamos get_all_values e mapeamos por ÃƒÂ­ndice de coluna.
            values = ws.get_all_values()
            if not values or len(values) < 2:
                return {}

            header = [str(h or "").strip() for h in values[0]]
            header_norm = [_norm_col(h) for h in header]

            sku_idx = None
            foto_idx = None
            for i, hn in enumerate(header_norm):
                if sku_idx is None and (hn == "sku" or "sku" in hn):
                    sku_idx = i
                if foto_idx is None and ("foto" in hn or "imagem" in hn or "url" in hn or "link" in hn):
                    foto_idx = i

            if sku_idx is None or foto_idx is None:
                return {}

            fotos_por_sku = {}
            for row in values[1:]:
                sku_raw = str(row[sku_idx] if sku_idx < len(row) else "").strip()
                foto_raw = str(row[foto_idx] if foto_idx < len(row) else "").strip()
                if not sku_raw or not foto_raw:
                    continue
                sku_norm = _normalizar_sku_mes(sku_raw)
                if sku_norm:
                    fotos_por_sku[sku_norm] = foto_raw

            return fotos_por_sku
        except Exception as e:
            logger.warning(f"[CADASTRO] Não foi possível sincronizar fotos da planilha: {e}")
            return {}

    tenant_path = get_tenant_path(client_id)
    arquivo_cliente = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    alvo = arquivo_cliente

    if alvo and os.path.exists(alvo):
        try:
            df = pd.read_csv(alvo, dtype=str).fillna("")
            df.columns = [c.strip().lower() for c in df.columns]
            df = df.loc[:, ~df.columns.duplicated()]  # remove colunas com nome duplicado
            cols_remover = [
                c for c in df.columns
                if _cadastro_coluna_indesejada(c)
            ]
            precisa_salvar = False
            cols_antes_custos = list(df.columns)
            df = _cadastro_canonizar_colunas_custos(df)
            if list(df.columns) != cols_antes_custos:
                precisa_salvar = True
            cols_antes_texto = list(df.columns)
            df = _cadastro_canonizar_coluna_descricao(df)
            if list(df.columns) != cols_antes_texto:
                precisa_salvar = True
            cols_pesquisa_ausentes = [c for c in CADASTRO_PESQUISA_COLS if c not in df.columns]
            if cols_pesquisa_ausentes:
                df = _cadastro_garantir_colunas_pesquisa(df)
                precisa_salvar = True
            if cols_remover:
                df = df.drop(columns=cols_remover, errors="ignore")
                precisa_salvar = True

            # Canoniza coluna de foto para evitar duplicidade (cg_foto -> foto)
            if "foto" not in df.columns and "cg_foto" in df.columns:
                df["foto"] = df["cg_foto"]
                precisa_salvar = True
            if "cg_foto" in df.columns:
                df = df.drop(columns=["cg_foto"])
                precisa_salvar = True

            # Consolida possÃƒÂ­veis SKUs duplicados, mesclando campos preenchidos.
            df, consolidou = _consolidar_cadastro_por_sku(df)
            if consolidou:
                precisa_salvar = True

            # SincronizaÃƒÂ§ÃƒÂ£o de fotos ÃƒÂ© pesada (exporta XLSX e processa imagens).
            # Para manter a listagem rÃƒÂ¡pida, sÃƒÂ³ executa quando solicitado: ?sync_fotos=true
            if sync_fotos:
                fotos_por_sku = _obter_mapa_fotos_por_sku()
                if fotos_por_sku:
                    if "foto" not in df.columns:
                        df["foto"] = ""
                    sku_norm_series = df["sku"].astype(str).apply(_normalizar_sku_mes)
                    fotos_novas = sku_norm_series.map(fotos_por_sku).fillna("")
                    mask = fotos_novas.astype(str).str.strip() != ""
                    if mask.any():
                        atuais = df["foto"].astype(str)
                        alterou = (atuais != fotos_novas.astype(str)) & mask
                        if alterou.any():
                            df.loc[alterou, "foto"] = fotos_novas.loc[alterou]
                            precisa_salvar = True

                # Se as imagens estiverem inseridas na cÃƒÂ©lula B, exporta a planilha e salva os arquivos localmente.
                imagens_por_sku = _extrair_imagens_planilha_por_sku()
                if imagens_por_sku:
                    pasta_fotos = os.path.join(tenant_path, "cadastro_fotos")
                    os.makedirs(pasta_fotos, exist_ok=True)
                    if "foto" not in df.columns:
                        df["foto"] = ""
                    for idx, sku_val in df["sku"].astype(str).apply(_normalizar_sku_mes).items():
                        payload = imagens_por_sku.get(sku_val)
                        if not payload:
                            continue
                        nome_arquivo = _nome_arquivo_foto_sku(sku_val, payload['ext'])
                        caminho_arquivo = os.path.join(pasta_fotos, nome_arquivo)
                        try:
                            with open(caminho_arquivo, "wb") as f:
                                f.write(payload["bytes"])
                            relativo = f"cadastro_fotos/{nome_arquivo}"
                            if str(df.at[idx, "foto"] or "").strip() != relativo:
                                df.at[idx, "foto"] = relativo
                                precisa_salvar = True
                        except Exception as e:
                            logger.warning(f"[CADASTRO] NÃƒÂ£o foi possÃƒÂ­vel salvar foto do SKU {sku_val}: {e}")

            if precisa_salvar:
                df.to_csv(alvo, index=False)  # persiste limpeza/sincronizaÃƒÂ§ÃƒÂ£o no CSV

            # Enriquecimento: traz "Produto Bling" do banco de estoque (produtos_compilado.csv) por SKU.
            try:
                arquivo_estoque_cliente = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
                alvo_estoque = arquivo_estoque_cliente
                if alvo_estoque and os.path.exists(alvo_estoque):
                    df_estoque = pd.read_csv(alvo_estoque, dtype=str).fillna("")
                    df_estoque.columns = [c.strip().lower() for c in df_estoque.columns]

                    # SincronizaÃƒÂ§ÃƒÂ£o opcional de NCM (manual no mÃƒÂ³dulo Cadastro).
                    # MantÃ©m o sync de estoque rÃƒÂ¡pido e sÃƒÂ³ consulta detalhe do Bling quando solicitado.
                    if sync_ncm and "id_bling" in df_estoque.columns:
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

                        if bling_por_loja:
                            def _norm_loja_nome(v: str) -> str:
                                return str(v or "").strip().lower()

                            if "ncm_bling" not in df_estoque.columns:
                                df_estoque["ncm_bling"] = ""

                            alterou_ncm_estoque = False
                            cache_ncm = {}
                            for idx_est, row_est in df_estoque.iterrows():
                                pid = str(row_est.get("id_bling", "") or "").strip()
                                if not pid:
                                    continue
                                ncm_atual = str(row_est.get("ncm_bling", "") or "").strip()
                                if ncm_atual:
                                    continue

                                loja_sync = str(row_est.get("loja_sync", "") or "").strip()
                                loja_sync_norm = _norm_loja_nome(loja_sync)

                                candidatos = []
                                if loja_sync_norm:
                                    for nome in bling_por_loja.keys():
                                        if _norm_loja_nome(nome) == loja_sync_norm:
                                            candidatos.append(nome)
                                            break
                                if not candidatos:
                                    candidatos = list(bling_por_loja.keys())

                                ncm_novo = ""
                                for nome_loja in candidatos:
                                    chave_cache = (nome_loja, pid)
                                    if chave_cache in cache_ncm:
                                        ncm_novo = cache_ncm[chave_cache]
                                        if ncm_novo:
                                            break
                                        continue

                                    cfg_loja = bling_por_loja.get(nome_loja) or {}
                                    access_token_ncm = cfg_loja.get("access_token")
                                    cid_ncm = cfg_loja.get("id")
                                    sec_ncm = cfg_loja.get("secret")
                                    refresh_ncm = cfg_loja.get("refresh_token")
                                    if not (access_token_ncm and cid_ncm and sec_ncm):
                                        cache_ncm[chave_cache] = ""
                                        continue

                                    ncm_resp, status_ncm = _bling_obter_ncm_produto(access_token_ncm, pid)
                                    if status_ncm == 401 and refresh_ncm:
                                        try:
                                            renovado = renovar_token_bling_loja(client_id, nome_loja, cfg_loja)
                                            bling_por_loja[nome_loja] = dict(renovado)
                                            access_token_ncm = renovado.get("access_token") or access_token_ncm
                                            ncm_resp, status_ncm = _bling_obter_ncm_produto(access_token_ncm, pid)
                                        except Exception:
                                            status_ncm = 500

                                    ncm_resp = ncm_resp if (status_ncm == 200 and ncm_resp) else ""
                                    cache_ncm[chave_cache] = ncm_resp
                                    if ncm_resp:
                                        ncm_novo = ncm_resp
                                        break

                                if ncm_novo:
                                    df_estoque.at[idx_est, "ncm_bling"] = ncm_novo
                                    alterou_ncm_estoque = True

                            if alterou_ncm_estoque:
                                df_estoque.to_csv(alvo_estoque, index=False)

                    if "sku" in df_estoque.columns and "nome_bling" in df_estoque.columns:
                        mapa_bling = {}
                        mapa_bling_compacto = {}
                        mapa_bling_numsoft = {}
                        mapa_ncm = {}
                        mapa_ncm_compacto = {}
                        mapa_ncm_numsoft = {}
                        mapa_cest = {}
                        mapa_cest_compacto = {}
                        mapa_cest_numsoft = {}
                        mapa_loja = {}
                        mapa_loja_compacto = {}
                        mapa_loja_numsoft = {}
                        for _, row_est in df_estoque[["sku", "nome_bling"]].iterrows():
                            sku_est, sku_est_compacto, sku_est_numsoft = _sku_lookup_keys(row_est.get("sku", ""))
                            nome_est = str(row_est.get("nome_bling", "") or "").strip()
                            if sku_est and nome_est and sku_est not in mapa_bling:
                                mapa_bling[sku_est] = nome_est
                            if sku_est_compacto and nome_est and sku_est_compacto not in mapa_bling_compacto:
                                mapa_bling_compacto[sku_est_compacto] = nome_est
                            if sku_est_numsoft and nome_est and sku_est_numsoft not in mapa_bling_numsoft:
                                mapa_bling_numsoft[sku_est_numsoft] = nome_est

                        col_loja_sync = "loja_sync" if "loja_sync" in df_estoque.columns else ("loja" if "loja" in df_estoque.columns else None)
                        if col_loja_sync:
                            for _, row_est in df_estoque[["sku", col_loja_sync]].iterrows():
                                sku_est, sku_est_compacto, sku_est_numsoft = _sku_lookup_keys(row_est.get("sku", ""))
                                loja_est = str(row_est.get(col_loja_sync, "") or "").strip()
                                if sku_est and loja_est:
                                    atual = mapa_loja.get(sku_est, "")
                                    if not atual:
                                        mapa_loja[sku_est] = loja_est
                                    elif loja_est not in [p.strip() for p in atual.split("|")]:
                                        mapa_loja[sku_est] = f"{atual}|{loja_est}"
                                if sku_est_compacto and loja_est:
                                    atual = mapa_loja_compacto.get(sku_est_compacto, "")
                                    if not atual:
                                        mapa_loja_compacto[sku_est_compacto] = loja_est
                                    elif loja_est not in [p.strip() for p in atual.split("|")]:
                                        mapa_loja_compacto[sku_est_compacto] = f"{atual}|{loja_est}"
                                if sku_est_numsoft and loja_est:
                                    atual = mapa_loja_numsoft.get(sku_est_numsoft, "")
                                    if not atual:
                                        mapa_loja_numsoft[sku_est_numsoft] = loja_est
                                    elif loja_est not in [p.strip() for p in atual.split("|")]:
                                        mapa_loja_numsoft[sku_est_numsoft] = f"{atual}|{loja_est}"

                        col_ncm = "ncm_bling" if "ncm_bling" in df_estoque.columns else ("ncm" if "ncm" in df_estoque.columns else None)
                        if col_ncm:
                            for _, row_est in df_estoque[["sku", col_ncm]].iterrows():
                                sku_est, sku_est_compacto, sku_est_numsoft = _sku_lookup_keys(row_est.get("sku", ""))
                                ncm_est = str(row_est.get(col_ncm, "") or "").strip()
                                if sku_est and ncm_est and sku_est not in mapa_ncm:
                                    mapa_ncm[sku_est] = ncm_est
                                if sku_est_compacto and ncm_est and sku_est_compacto not in mapa_ncm_compacto:
                                    mapa_ncm_compacto[sku_est_compacto] = ncm_est
                                if sku_est_numsoft and ncm_est and sku_est_numsoft not in mapa_ncm_numsoft:
                                    mapa_ncm_numsoft[sku_est_numsoft] = ncm_est

                        col_cest = "cest_bling" if "cest_bling" in df_estoque.columns else ("cest" if "cest" in df_estoque.columns else None)
                        if col_cest:
                            for _, row_est in df_estoque[["sku", col_cest]].iterrows():
                                sku_est, sku_est_compacto, sku_est_numsoft = _sku_lookup_keys(row_est.get("sku", ""))
                                cest_est = str(row_est.get(col_cest, "") or "").strip()
                                if sku_est and cest_est and sku_est not in mapa_cest:
                                    mapa_cest[sku_est] = cest_est
                                if sku_est_compacto and cest_est and sku_est_compacto not in mapa_cest_compacto:
                                    mapa_cest_compacto[sku_est_compacto] = cest_est
                                if sku_est_numsoft and cest_est and sku_est_numsoft not in mapa_cest_numsoft:
                                    mapa_cest_numsoft[sku_est_numsoft] = cest_est

                        def _resolver_nome_bling(sku_val: str) -> str:
                            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys(sku_val)
                            return (
                                mapa_bling.get(sku_norm)
                                or mapa_bling_compacto.get(sku_compacto)
                                or mapa_bling_numsoft.get(sku_numsoft, "")
                            )

                        def _resolver_ncm(sku_val: str) -> str:
                            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys(sku_val)
                            return (
                                mapa_ncm.get(sku_norm)
                                or mapa_ncm_compacto.get(sku_compacto)
                                or mapa_ncm_numsoft.get(sku_numsoft, "")
                            )

                        def _resolver_cest(sku_val: str) -> str:
                            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys(sku_val)
                            return (
                                mapa_cest.get(sku_norm)
                                or mapa_cest_compacto.get(sku_compacto)
                                or mapa_cest_numsoft.get(sku_numsoft, "")
                            )

                        def _resolver_loja_sync(sku_val: str) -> str:
                            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys(sku_val)
                            return (
                                mapa_loja.get(sku_norm)
                                or mapa_loja_compacto.get(sku_compacto)
                                or mapa_loja_numsoft.get(sku_numsoft, "")
                            )

                        df["produto_bling"] = df["sku"].astype(str).apply(_resolver_nome_bling).fillna("")
                        if "loja_sync" not in df.columns:
                            df["loja_sync"] = ""
                        lojas_resolvidas = df["sku"].astype(str).apply(_resolver_loja_sync).fillna("")
                        mask_loja = lojas_resolvidas.astype(str).str.strip() != ""
                        df.loc[mask_loja, "loja_sync"] = lojas_resolvidas.loc[mask_loja]

                        # Preserva NCM/CEST já salvos no cadastro quando o estoque não tiver valor.
                        ncm_resolvido = df["sku"].astype(str).apply(_resolver_ncm).fillna("")
                        cest_resolvido = df["sku"].astype(str).apply(_resolver_cest).fillna("")

                        if "ncm" not in df.columns:
                            df["ncm"] = ""
                        if "cest" not in df.columns:
                            df["cest"] = ""

                        ncm_atual = df["ncm"].astype(str)
                        cest_atual = df["cest"].astype(str)
                        mask_ncm = ncm_resolvido.astype(str).str.strip() != ""
                        mask_cest = cest_resolvido.astype(str).str.strip() != ""

                        ncm_aplicados = int((mask_ncm & (ncm_atual != ncm_resolvido.astype(str))).sum())
                        cest_aplicados = int((mask_cest & (cest_atual != cest_resolvido.astype(str))).sum())
                        ncm_preservados_listagem = int((~mask_ncm & ncm_atual.str.strip().ne("")).sum())
                        cest_preservados_listagem = int((~mask_cest & cest_atual.str.strip().ne("")).sum())

                        df["ncm"] = ncm_atual
                        df["cest"] = cest_atual
                        df.loc[mask_ncm, "ncm"] = ncm_resolvido.loc[mask_ncm]
                        df.loc[mask_cest, "cest"] = cest_resolvido.loc[mask_cest]

                        if ncm_aplicados or cest_aplicados or ncm_preservados_listagem or cest_preservados_listagem:
                            logger.info(
                                "[CADASTRO LISTAGEM NCM/CEST][%s] aplicados: ncm=%d, cest=%d | preservados: ncm=%d, cest=%d",
                                client_id,
                                ncm_aplicados,
                                cest_aplicados,
                                ncm_preservados_listagem,
                                cest_preservados_listagem,
                            )

                        if sync_ncm:
                            precisa_salvar = True
            except Exception as e:
                logger.warning(f"[CADASTRO] NÃƒÂ£o foi possÃƒÂ­vel enriquecer com Produto Bling: {e}")

            classificados_alterados, _ = _classificar_monofasico_cadastro(df)
            if classificados_alterados:
                precisa_salvar = True

            if precisa_salvar:
                df.to_csv(alvo, index=False)

            cols_base = ["sku", "foto", "nome", "produto_bling", "loja_sync", "ncm", "cest", "categoria", "marca", "custo", "preco", "descricao", "updated_at"] + CADASTRO_PESQUISA_COLS
            for c in cols_base:
                if c not in df.columns:
                    df[c] = ""
            df = _cadastro_garantir_colunas_pesquisa(df)

            mapa_fotos_locais = _cadastro_mapa_fotos_locais(client_id)
            if mapa_fotos_locais:
                foto_atual = df["foto"].astype(str)
                sem_foto = foto_atual.str.strip().eq("")
                if sem_foto.any():
                    fotos_resolvidas = df.loc[sem_foto, "sku"].astype(str).apply(
                        lambda sku_val: _cadastro_resolver_foto_local(mapa_fotos_locais, sku_val)
                    )
                    mask_resolvidas = fotos_resolvidas.astype(str).str.strip().ne("")
                    if mask_resolvidas.any():
                        df.loc[fotos_resolvidas[mask_resolvidas].index, "foto"] = fotos_resolvidas[mask_resolvidas]

            # Evita que campos de nome recebam texto de descriÃ§Ã£o por erro de importaÃ§Ã£o/integraÃ§Ã£o.
            for campo in ["nome", "produto", "produto_bling", "nome_bling"]:
                if campo in df.columns:
                    df[campo] = df[campo].apply(_cadastro_limpar_nome)

            mask_nome_vazio = df["nome"].astype(str).str.strip().eq("")
            if "produto" in df.columns:
                substitui = mask_nome_vazio & df["produto"].astype(str).str.strip().ne("")
                df.loc[substitui, "nome"] = df.loc[substitui, "produto"]
                mask_nome_vazio = df["nome"].astype(str).str.strip().eq("")
            if "produto_bling" in df.columns:
                substitui = mask_nome_vazio & df["produto_bling"].astype(str).str.strip().ne("")
                df.loc[substitui, "nome"] = df.loc[substitui, "produto_bling"]

            df = _cadastro_anexar_custos_por_loja(client_id, df)

            cols_extra = [c for c in df.columns if c not in cols_base]
            return df[cols_base + cols_extra].to_dict(orient="records")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erro ao ler cadastro de produtos: {str(e)}")
    return []

__all__ = ['listar_produtos_cadastro', 'configure_cadastro_listagem_runtime']
