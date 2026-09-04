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
from contextlib import contextmanager, nullcontext
from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import Depends, File, Form, HTTPException, UploadFile

from backend.schemas import CadastroProdutoRequest
from backend.services.cadastro_common import *
from backend.services.cadastro_custos import *
from backend.services.cadastro_fotos import *
from backend.services.cadastro_lojas_produtos import (
    _bloquear_loja_cadastro_para_commit,
    _listar_produtos_loja_sync,
    resolver_loja_cadastro,
    salvar_produtos_loja_em_lote,
)
from backend.services.cadastro_sync_ncm import *


def configure_cadastro_importacao_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    configure_cadastro_custos_runtime(runtime_module)
    configure_cadastro_fotos_runtime(runtime_module)
    configure_cadastro_sync_ncm_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_importacao_runtime()


_CADASTRO_LOJA_COLUNAS_PROTEGIDAS = {
    "store_id",
    "sku_normalizado",
    "loja_sync",
    "row_version",
    "updated_at_utc",
    "deleted_at_utc",
    "scope_source",
}
_CADASTRO_LOJA_COLUNAS_CUSTOS = {"custo", "preco", "imposto"}


@contextmanager
def _cadastro_importacao_bloquear_writer_store_id(
    client_id: str,
    tenant_path: str,
):
    """Keep store validation and every related commit under the common mutex."""

    from backend.services import integracoes

    with integracoes._LOJAS_CONFIG_LOCK:
        with integracoes._integracoes_bloquear_catalogo_e_transicao_fotos(
            client_id,
            os.path.abspath(tenant_path),
        ):
            yield


def _cadastro_importacao_exigir_fotos_sem_referencia_local(
    client_id: str,
    df_import: pd.DataFrame,
) -> None:
    """Fail closed when strict store scope receives a local photo reference."""

    foto_columns = [
        column
        for column in df_import.columns
        if _cadastro_foto_coluna_candidata(column)
    ]
    if not foto_columns or not _cadastro_fotos_escopo_estrito(client_id):
        return
    skus = sorted(
        {
            str(sku or "").strip()
            for sku, row in df_import.iterrows()
            if any(
                _cadastro_foto_referencia_local_cadastro(row.get(column))
                for column in foto_columns
            )
            and str(sku or "").strip()
        }
    )
    if not any(
        _cadastro_foto_referencia_local_cadastro(row.get(column))
        for _sku, row in df_import.iterrows()
        for column in foto_columns
    ):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "store_id_required",
            "message": (
                "Referencias locais de foto exigem cadastro separado por loja "
                "e store_id."
            ),
            "skus": skus,
        },
    )


async def _ler_planilha_importacao(arquivo: UploadFile) -> pd.DataFrame:
    nome_arquivo = str(getattr(arquivo, "filename", "") or "").strip()
    if not nome_arquivo:
        raise HTTPException(status_code=400, detail="Arquivo nao informado.")

    ext = os.path.splitext(nome_arquivo.lower())[1]
    if ext not in {".xlsx", ".xls", ".csv"}:
        raise HTTPException(status_code=400, detail="Formato invalido. Use .xlsx, .xls ou .csv")

    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    if ext != ".csv":
        return pd.read_excel(io.BytesIO(conteudo), dtype=str).fillna("")

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
            candidato = pd.read_csv(
                io.BytesIO(conteudo),
                dtype=str,
                on_bad_lines="error",
                **cfg,
            ).fillna("")
            if candidato.empty:
                continue
            colunas_norm = [re.sub(r"[^a-z0-9]", "", str(c).lower()) for c in candidato.columns]
            tem_sku = any(coluna == "sku" or coluna.endswith("sku") for coluna in colunas_norm)
            score = len(candidato.columns) + (1000 if tem_sku else 0)
            if score > melhor_score:
                melhor_df = candidato
                melhor_score = score
            if tem_sku and len(candidato.columns) > 1:
                break
        except Exception as exc:
            ultimo_erro = exc

    if melhor_df is not None:
        return melhor_df
    if ultimo_erro:
        raise HTTPException(status_code=400, detail=f"Nao foi possivel ler o CSV: {ultimo_erro}")
    raise HTTPException(status_code=400, detail="Nao foi possivel ler o CSV informado.")


def _preparar_planilha_importacao(df_import: pd.DataFrame) -> tuple[pd.DataFrame, str, list[str]]:
    if df_import.empty:
        raise HTTPException(status_code=400, detail="Planilha sem dados para importar.")

    df_import = df_import.copy()
    df_import.columns = [str(coluna).strip().lower() for coluna in df_import.columns]
    df_import = df_import.loc[:, ~df_import.columns.duplicated()]
    df_import = _cadastro_canonizar_colunas_custos(df_import)

    col_sku = next(
        (
            coluna
            for coluna in df_import.columns
            if (lambda valor: valor == "sku" or valor.endswith("sku"))(
                re.sub(r"[^a-z0-9]", "", coluna.lower())
            )
        ),
        None,
    )
    if not col_sku:
        raise HTTPException(status_code=400, detail="Coluna SKU nao encontrada na planilha.")

    colunas_importadas = [
        coluna
        for coluna in df_import.columns
        if coluna != col_sku
        and coluna not in _CADASTRO_LOJA_COLUNAS_PROTEGIDAS
        and not _cadastro_coluna_indesejada(coluna)
    ]
    if not colunas_importadas:
        raise HTTPException(status_code=400, detail="Nenhuma coluna valida para importar alem do SKU.")

    df_import[col_sku] = df_import[col_sku].astype(str).apply(_normalizar_sku_mes)
    df_import = df_import[df_import[col_sku].astype(str).str.strip() != ""].copy()
    if df_import.empty:
        raise HTTPException(status_code=400, detail="Nenhum SKU valido encontrado na planilha.")
    df_import = df_import.drop_duplicates(subset=[col_sku], keep="last").set_index(col_sku)
    return df_import, col_sku, colunas_importadas


async def importar_colunas_cadastro_loja(
    store_id: str,
    arquivo: UploadFile = File(...),
    modo: Optional[str] = Form("geral"),
    client_id: str = Depends(get_tenant_id),
):
    """Importa uma planilha exclusivamente no cadastro da loja informada na rota."""

    loja = resolver_loja_cadastro(client_id, store_id)
    modo_normalizado = str(modo or "geral").strip().lower()
    aliases_modo = {
        "geral": "geral",
        "cadastro": "geral",
        "custos": "custos",
        "custo": "custos",
        "custos_impostos": "custos",
        "custos-impostos": "custos",
    }
    modo_normalizado = aliases_modo.get(modo_normalizado, "")
    if not modo_normalizado:
        raise HTTPException(status_code=400, detail="Modo invalido. Use geral ou custos.")

    try:
        df_import, col_sku, colunas_importadas = _preparar_planilha_importacao(
            await _ler_planilha_importacao(arquivo)
        )

        if modo_normalizado == "custos":
            colunas_custos = [
                coluna
                for coluna in colunas_importadas
                if coluna in (_CADASTRO_LOJA_COLUNAS_CUSTOS | {"produto"})
            ]
            if not any(coluna in _CADASTRO_LOJA_COLUNAS_CUSTOS for coluna in colunas_custos):
                raise HTTPException(
                    status_code=400,
                    detail="A planilha de custos precisa ter custo, preco ou imposto.",
                )
            with _bloquear_loja_cadastro_para_commit(client_id, loja) as loja_commit:
                resultado = _cadastro_importar_custos_loja(
                    client_id,
                    df_import,
                    col_sku,
                    colunas_custos,
                    loja_commit["nome"],
                    store_id=loja_commit["store_id"],
                )
                return {
                    "success": True,
                    "message": "Custos e impostos importados na loja selecionada.",
                    "store_id": loja_commit["store_id"],
                    "loja_sync": loja_commit["nome"],
                    "modo": "custos",
                    "colunas_criadas": [],
                    "colunas_importadas": colunas_custos,
                    "skus_atualizados": int(resultado.get("custos_loja_atualizados") or 0),
                    "skus_incluidos": int(resultado.get("custos_loja_incluidos") or 0),
                    "skus_sem_correspondencia": 0,
                    **resultado,
                }

        colunas_produto = [
            coluna for coluna in colunas_importadas if coluna not in _CADASTRO_LOJA_COLUNAS_CUSTOS
        ]
        if not colunas_produto:
            raise HTTPException(
                status_code=400,
                detail="Use o modo custos para importar custo, preco ou imposto.",
            )

        produtos_atuais = _listar_produtos_loja_sync(client_id, loja["store_id"])
        colunas_atuais = {
            str(coluna).strip().lower()
            for produto in produtos_atuais
            for coluna in produto.keys()
        }
        itens = []
        for sku, linha in df_import.iterrows():
            item = {"sku": _normalizar_sku_mes(sku)}
            for coluna in colunas_produto:
                valor = linha.get(coluna, "")
                item[coluna] = "" if pd.isna(valor) else str(valor)
            itens.append(item)

        with _bloquear_loja_cadastro_para_commit(client_id, loja) as loja_commit:
            resultado = salvar_produtos_loja_em_lote(
                client_id,
                loja_commit["store_id"],
                itens,
            )
            return {
                "success": True,
                "message": "Importacao concluida na loja selecionada.",
                "store_id": loja_commit["store_id"],
                "loja_sync": loja_commit["nome"],
                "modo": "geral",
                "colunas_criadas": [
                    coluna for coluna in colunas_produto if coluna not in colunas_atuais
                ],
                "colunas_importadas": colunas_produto,
                "skus_atualizados": int(resultado.get("atualizados") or 0),
                "skus_incluidos": int(resultado.get("incluidos") or 0),
                "skus_sem_correspondencia": 0,
            }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[CADASTRO LOJAS] Falha na importacao da loja")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao importar colunas para a loja: {exc}",
        ) from exc

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
            str(modo or "").strip().lower()
            in {"custos", "custo", "custos_impostos", "custos-impostos"}
            or bool(str(loja or "").strip())
        )

        # Esta rota e global/legada. Rejeitamos o arquivo inteiro antes de
        # qualquer escrita (inclusive custos) quando ao menos um SKU ja possui
        # identidade duravel por loja. Nunca fazemos fan-out implicito.
        from backend.services.cadastro_compatibilidade import (
            bloquear_mutacao_legada_sem_sku_controlado,
            exigir_mutacao_legada_sem_campos_loja,
            exigir_mutacao_legada_sem_sku_controlado,
        )

        exigir_mutacao_legada_sem_campos_loja(df_import.columns)
        exigir_mutacao_legada_sem_sku_controlado(client_id, df_import.index.tolist())

        integracoes_service = None
        writer_lock = nullcontext()
        tem_coluna_foto = any(
            _cadastro_foto_coluna_candidata(column)
            for column in df_import.columns
        )
        if modo_custos or tem_coluna_foto:
            from backend.services import integracoes as integracoes_service

            # Ordem canonica para este commit misto:
            # configuracao -> cadastro por loja -> custos por loja -> transicao.
            writer_lock = _cadastro_importacao_bloquear_writer_store_id(
                client_id,
                tenant_path,
            )

        with writer_lock:
            loja_custo_snapshot = None
            if modo_custos:
                nome_loja = str(loja or "").strip()
                if not nome_loja:
                    raise HTTPException(
                        status_code=400,
                        detail="Selecione a loja/conta para salvar os custos e impostos.",
                    )
                loja_custo_snapshot = integracoes_service.buscar_loja(
                    client_id,
                    nome_loja,
                )
                if not isinstance(loja_custo_snapshot, dict):
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "code": "store_not_found",
                            "message": "Loja nao encontrada para salvar custos e impostos.",
                        },
                    )
                if not str(loja_custo_snapshot.get("store_id") or "").strip():
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "store_identity_unavailable",
                            "message": "A loja nao possui store_id persistido.",
                        },
                    )

            with bloquear_mutacao_legada_sem_sku_controlado(
                client_id,
                df_import.index.tolist(),
            ):
                _cadastro_importacao_exigir_fotos_sem_referencia_local(
                    client_id,
                    df_import,
                )
                # Preserve the legacy 404 contract for uncontrolled SKUs, but only
                # after the store-identity guard has had a chance to fail closed.
                if not os.path.exists(arquivo_cliente):
                    raise HTTPException(
                        status_code=404,
                        detail="Cadastro de produtos nÃ£o encontrado para este cliente.",
                    )

                resultado_custos_loja = {}
                if modo_custos:
                    store_id_custo = str(
                        loja_custo_snapshot.get("store_id") or ""
                    ).strip()
                    loja_custo_commit = integracoes_service.buscar_loja(
                        client_id,
                        str(loja_custo_snapshot.get("nome") or ""),
                        store_id=store_id_custo,
                    )
                    if not isinstance(loja_custo_commit, dict):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "code": "store_config_changed",
                                "message": "A configuracao da loja mudou durante a importacao.",
                            },
                        )
                    resultado_custos_loja = _cadastro_importar_custos_loja(
                        client_id,
                        df_import,
                        col_sku,
                        colunas_importadas,
                        str(loja_custo_commit.get("nome") or "").strip(),
                        store_id=store_id_custo,
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

__all__ = [
    'importar_colunas_cadastro_loja',
    'importar_colunas_cadastro_por_sku',
    'configure_cadastro_importacao_runtime',
]
