"""Cadastro import endpoint handlers."""

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


def configure_cadastro_importacao_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    configure_cadastro_custos_runtime(runtime_module)
    configure_cadastro_fotos_runtime(runtime_module)
    configure_cadastro_sync_ncm_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_importacao_runtime()

async def importar_colunas_cadastro_por_sku(
    arquivo: UploadFile = File(...),
    loja: Optional[str] = Form(None),
    modo: Optional[str] = Form(None),
    client_id: str = Depends(get_tenant_id)
):
    """Importa colunas de uma planilha e vincula os valores ao cadastro pelo SKU."""
    nome_arquivo = str(getattr(arquivo, "filename", "") or "").strip()
    if not nome_arquivo:
        raise HTTPException(status_code=400, detail="Arquivo nÃ£o informado.")

    ext = os.path.splitext(nome_arquivo.lower())[1]
    if ext not in {".xlsx", ".xls", ".csv"}:
        raise HTTPException(status_code=400, detail="Formato invalido. Use .xlsx, .xls ou .csv")

    tenant_path = get_tenant_path(client_id)
    arquivo_cliente = os.path.join(tenant_path, "cadastro_produtos.csv")
    if not os.path.exists(arquivo_cliente):
        raise HTTPException(status_code=404, detail="Cadastro de produtos nÃ£o encontrado para este cliente.")

    try:
        conteudo = await arquivo.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail="Arquivo vazio.")

        if ext == ".csv":
            # Leitura robusta para CSVs com delimitador/codificaÃƒÂ§ÃƒÂ£o variÃƒÂ¡veis.
            tentativas_csv = [
                {"sep": None, "encoding": "utf-8-sig", "engine": "python"},
                {"sep": None, "encoding": "utf-8", "engine": "python"},
                {"sep": None, "encoding": "latin1", "engine": "python"},
                {"sep": ";", "encoding": "utf-8-sig", "engine": "python"},
                {"sep": ";", "encoding": "utf-8", "engine": "python"},
                {"sep": ";", "encoding": "latin1", "engine": "python"},
                {"sep": ",", "encoding": "utf-8-sig", "engine": "python"},
                {"sep": ",", "encoding": "utf-8", "engine": "python"},
                {"sep": ",", "encoding": "latin1", "engine": "python"},
                {"sep": "\t", "encoding": "utf-8-sig", "engine": "python"},
                {"sep": "\t", "encoding": "utf-8", "engine": "python"},
                {"sep": "\t", "encoding": "latin1", "engine": "python"},
            ]

            melhor_df = None
            melhor_score = -1
            ultimo_erro = None

            for cfg in tentativas_csv:
                try:
                    df_tmp = pd.read_csv(
                        io.BytesIO(conteudo),
                        dtype=str,
                        on_bad_lines="skip",
                        **cfg,
                    ).fillna("")
                    if df_tmp.empty:
                        continue

                    cols_norm = [re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in df_tmp.columns]
                    tem_sku = any(cn == "sku" or cn.endswith("sku") for cn in cols_norm)
                    score = len(df_tmp.columns) + (1000 if tem_sku else 0)

                    if score > melhor_score:
                        melhor_df = df_tmp
                        melhor_score = score

                    if tem_sku and len(df_tmp.columns) > 1:
                        break
                except Exception as e:
                    ultimo_erro = e

            if melhor_df is None:
                if ultimo_erro:
                    raise HTTPException(status_code=400, detail=f"Não foi possível ler o CSV: {str(ultimo_erro)}")
                raise HTTPException(status_code=400, detail="Não foi possível ler o CSV informado.")

            df_import = melhor_df
        else:
            df_import = pd.read_excel(io.BytesIO(conteudo), dtype=str).fillna("")

        if df_import.empty:
            raise HTTPException(status_code=400, detail="Planilha sem dados para importar.")

        # Normaliza nomes de colunas e remove duplicadas.
        df_import.columns = [str(c).strip().lower() for c in df_import.columns]
        df_import = df_import.loc[:, ~df_import.columns.duplicated()]
        df_import = _cadastro_canonizar_colunas_custos(df_import)

        col_sku = None
        for c in df_import.columns:
            c_norm = re.sub(r"[^a-z0-9]", "", c.lower())
            if c_norm == "sku" or c_norm.endswith("sku"):
                col_sku = c
                break
        if not col_sku:
            raise HTTPException(status_code=400, detail="Coluna SKU não encontrada na planilha.")

        # Colunas importadas: todas exceto SKU.
        colunas_importadas = [c for c in df_import.columns if c != col_sku]
        colunas_importadas = [c for c in colunas_importadas if not _cadastro_coluna_indesejada(c)]
        if not colunas_importadas:
            raise HTTPException(status_code=400, detail="Nenhuma coluna valida para importar alem do SKU.")

        df_import[col_sku] = df_import[col_sku].astype(str).apply(_normalizar_sku_mes)
        df_import = df_import[df_import[col_sku].astype(str).str.strip() != ""].copy()
        if df_import.empty:
            raise HTTPException(status_code=400, detail="Nenhum SKU valido encontrado na planilha.")

        # Remove duplicidade de SKU na planilha mantendo a última ocorrência.
        df_import = df_import.drop_duplicates(subset=[col_sku], keep="last")
        df_import = df_import.set_index(col_sku)
        modo_custos = (
            str(modo or "").strip().lower() in {"custos", "custo", "custos_impostos", "custos-impostos"}
            or bool(str(loja or "").strip())
        )
        resultado_custos_loja = {}
        if modo_custos:
            resultado_custos_loja = _cadastro_importar_custos_loja(
                client_id,
                df_import,
                col_sku,
                colunas_importadas,
                str(loja or "").strip(),
            )
        colunas_importadas_cadastro = list(colunas_importadas)
        if modo_custos:
            colunas_importadas_cadastro = [
                c for c in colunas_importadas
                if c not in {"custo", "preco", "imposto"}
            ]

        df_cad = pd.read_csv(arquivo_cliente, dtype=str).fillna("")
        df_cad.columns = [str(c).strip().lower() for c in df_cad.columns]
        df_cad = df_cad.loc[:, ~df_cad.columns.duplicated()]
        df_cad = _cadastro_canonizar_colunas_custos(df_cad)
        df_cad = _cadastro_garantir_colunas_pesquisa(df_cad)
        cols_cad_remover = [c for c in df_cad.columns if c != "sku" and _cadastro_coluna_indesejada(c)]
        if cols_cad_remover:
            df_cad = df_cad.drop(columns=cols_cad_remover, errors="ignore")
        if "sku" not in df_cad.columns:
            df_cad["sku"] = ""
        df_cad["sku"] = df_cad["sku"].astype(str).apply(_normalizar_sku_mes)
        df_cad, _ = _consolidar_cadastro_por_sku(df_cad)

        colunas_criadas = []
        for c in colunas_importadas_cadastro:
            if c not in df_cad.columns:
                df_cad[c] = ""
                colunas_criadas.append(c)

        df_cad_idx = df_cad.set_index("sku", drop=False)
        skus_importados = set(df_import.index.tolist())
        skus_cadastro = set(df_cad_idx.index.tolist())
        skus_match = skus_importados & skus_cadastro
        skus_novos = skus_importados - skus_cadastro

        if skus_match:
            idx_match = sorted(skus_match)
            for c in colunas_importadas_cadastro:
                valores = df_import.loc[idx_match, c].astype(str)
                df_cad_idx.loc[idx_match, c] = valores.values

        if skus_novos:
            for sku_novo in sorted(skus_novos):
                nova_linha = {c: "" for c in df_cad_idx.columns}
                nova_linha["sku"] = sku_novo
                for c in colunas_importadas_cadastro:
                    valor = df_import.at[sku_novo, c] if c in df_import.columns else ""
                    nova_linha[c] = "" if pd.isna(valor) else str(valor)
                df_cad_idx.loc[sku_novo] = nova_linha

        df_cad_final = df_cad_idx.reset_index(drop=True)
        df_cad_final = _cadastro_garantir_colunas_pesquisa(df_cad_final)
        df_cad_final, _ = _consolidar_cadastro_por_sku(df_cad_final)
        df_cad_final.to_csv(arquivo_cliente, index=False)

        return {
            "success": True,
            "message": "ImportaÃƒÂ§ÃƒÂ£o concluida com sucesso.",
            "colunas_criadas": colunas_criadas,
            "colunas_importadas": colunas_importadas,
            "skus_atualizados": len(skus_match),
            "skus_incluidos": len(skus_novos),
            "skus_sem_correspondencia": 0,
            **resultado_custos_loja,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao importar colunas para cadastro: {str(e)}")

__all__ = ['importar_colunas_cadastro_por_sku', 'configure_cadastro_importacao_runtime']
