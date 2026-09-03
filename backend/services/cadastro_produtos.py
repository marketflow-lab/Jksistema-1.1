"""Cadastro product mutation and lookup endpoint handlers."""

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
import tempfile
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

import pandas as pd
from fastapi import Depends, File, Form, HTTPException, UploadFile

from backend.schemas import CadastroProdutoRequest
from backend.services.cadastro_common import *
from backend.services.cadastro_custos import *
from backend.services.cadastro_fotos import *
from backend.services.cadastro_sync_ncm import *
from backend.services.path_coordination import path_locks_for


def configure_cadastro_produtos_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    configure_cadastro_custos_runtime(runtime_module)
    configure_cadastro_fotos_runtime(runtime_module)
    configure_cadastro_sync_ncm_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_produtos_runtime()

CADASTRO_CAMPO_M3_INDIVIDUAL = "m3 individual"


def _cadastro_salvar_dataframe_atomico(df: pd.DataFrame, caminho: str) -> None:
    pasta = os.path.dirname(os.path.abspath(caminho))
    os.makedirs(pasta, exist_ok=True)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{os.path.basename(caminho)}.",
            suffix=".tmp",
            dir=pasta,
            delete=False,
        ) as arquivo:
            temporario = arquivo.name
            df.to_csv(arquivo, index=False)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        temporario = ""
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError:
                pass


@contextmanager
def _cadastro_transacao_legada_arquivo_foto(
    client_id: str,
    tenant_path: str,
    arquivo_cliente: str,
    *,
    skus_guard: tuple[str, ...] = (),
    sku_foto: str = "",
    foto_data_url: str = "",
    referencias_foto: tuple[object, ...] = (),
) -> Iterator[list[dict[str, Any]]]:
    """Coordena CSV global e variantes de foto como uma unica transacao."""

    from backend.services.cadastro_lojas_produtos import (
        _capturar_estados_arquivos,
        _rollback_arquivos,
    )
    from backend.services.cadastro_compatibilidade import (
        exigir_mutacao_legada_sem_sku_controlado,
    )

    with _cadastro_fotos_bloquear_transicao(client_id, tenant_path):
        exigir_mutacao_legada_sem_sku_controlado(client_id, skus_guard)
        preparadas = (
            _preparar_fotos_data_url_no_tenant(
                client_id,
                sku_foto,
                foto_data_url,
                "",
            )
            if foto_data_url
            else []
        )
        if preparadas:
            _cadastro_fotos_validar_preparadas_no_lock(client_id, preparadas)
        if any(
            _cadastro_foto_referencia_local_cadastro(referencia)
            for referencia in referencias_foto
        ):
            _cadastro_fotos_exigir_mutacao_global_permitida(
                client_id,
                tenant_path,
            )

        caminhos_variantes = _cadastro_caminhos_variantes_fotos_preparadas(
            preparadas
        ) if preparadas else []
        caminhos_mutados = sorted(
            {
                os.path.realpath(arquivo_cliente),
                *(os.path.realpath(item) for item in caminhos_variantes),
            },
            key=os.path.normcase,
        )
        with path_locks_for(caminhos_mutados):
            _validar_caminhos_variantes_fotos(caminhos_variantes)
            estados = _capturar_estados_arquivos(caminhos_mutados)
            try:
                yield preparadas
            except BaseException:
                _rollback_arquivos(estados)
                raise


def _cadastro_tem_valor(v) -> bool:
    return str(v if v is not None else "").strip() != ""


def _cadastro_norm_col_compacta(coluna: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _cadastro_norm_coluna_texto(coluna))


def _cadastro_extrair_m3_individual(row_dict: dict) -> str:
    aliases = {"cgm3individual", "m3individual", "m3"}
    for col, valor in (row_dict or {}).items():
        if _cadastro_norm_col_compacta(col) in aliases and _cadastro_tem_valor(valor):
            return str(valor).strip()
    return ""


def _cadastro_caminhos_enriquecidos(tenant_path: str, arquivo_principal: str) -> list[str]:
    caminhos: list[str] = []

    def _ordem(nome: str) -> tuple[int, str]:
        n = str(nome or "").lower()
        if "classificacao_unicode" in n:
            return (0, n)
        if "classificacao" in n:
            return (1, n)
        if "backup" in n:
            return (2, n)
        return (3, n)

    try:
        for nome in sorted(os.listdir(tenant_path), key=_ordem):
            nome_txt = str(nome or "")
            if not nome_txt.lower().startswith("cadastro_produtos") or not nome_txt.lower().endswith(".csv"):
                continue
            caminho = os.path.join(tenant_path, nome_txt)
            if os.path.abspath(caminho) == os.path.abspath(arquivo_principal):
                continue
            if os.path.exists(caminho):
                caminhos.append(caminho)
    except Exception:
        return []

    return caminhos


def _cadastro_buscar_linhas_enriquecidas(client_id: str, sku_norm: str, arquivo_principal: str) -> list[dict]:
    tenant_path = get_tenant_path(client_id)
    alvos = {v.lower() for v in _sku_lookup_variantes(sku_norm)}
    alvos.add(str(sku_norm or "").strip().lower())
    linhas: list[dict] = []

    for caminho in _cadastro_caminhos_enriquecidos(tenant_path, arquivo_principal):
        try:
            df_ref = pd.read_csv(caminho, dtype=str).fillna("")
            df_ref.columns = [str(c).strip().lower() for c in df_ref.columns]
            df_ref = df_ref.loc[:, ~df_ref.columns.duplicated()]
            if "sku" not in df_ref.columns:
                continue
            for _, row in df_ref.iterrows():
                sku_row = _normalizar_sku_mes(str(row.get("sku", "") or "").strip())
                variantes = {v.lower() for v in _sku_lookup_variantes(sku_row)}
                variantes.add(sku_row.lower())
                if alvos.intersection(variantes):
                    linhas.append(row.to_dict())
                    break
        except Exception as exc:
            logger.warning("[CADASTRO] Falha ao consultar cadastro enriquecido %s: %s", caminho, exc)

    return linhas


def _cadastro_mesclar_linha_enriquecida(client_id: str, sku_norm: str, linha: dict, arquivo_principal: str) -> dict:
    merged = dict(linha or {})

    for extra in _cadastro_buscar_linhas_enriquecidas(client_id, sku_norm, arquivo_principal):
        m3_individual = _cadastro_extrair_m3_individual(extra)
        if m3_individual and not _cadastro_tem_valor(merged.get(CADASTRO_CAMPO_M3_INDIVIDUAL)):
            merged[CADASTRO_CAMPO_M3_INDIVIDUAL] = m3_individual

        for col, valor in extra.items():
            col_txt = str(col or "").strip().lower()
            if not col_txt or not _cadastro_tem_valor(valor):
                continue
            if col_txt not in merged or not _cadastro_tem_valor(merged.get(col_txt)):
                merged[col_txt] = valor

    if not _cadastro_tem_valor(merged.get(CADASTRO_CAMPO_M3_INDIVIDUAL)):
        m3_individual = _cadastro_extrair_m3_individual(merged)
        if m3_individual:
            merged[CADASTRO_CAMPO_M3_INDIVIDUAL] = m3_individual

    return merged


async def salvar_produto_cadastro(req: CadastroProdutoRequest, client_id: str = Depends(get_tenant_id)):
    sku = _normalizar_sku_mes(req.sku)
    nome = (req.nome or "").strip()

    if not sku or not nome:
        raise HTTPException(status_code=400, detail="SKU e nome sÃƒÂ£o obrigatÃƒÂ³rios.")

    from backend.services.cadastro_compatibilidade import (
        bloquear_mutacao_legada_sem_sku_controlado,
    )

    with bloquear_mutacao_legada_sem_sku_controlado(client_id, [sku]):
        tenant_path = get_tenant_path(client_id)
        arquivo_cliente = os.path.join(tenant_path, "cadastro_produtos.csv")
        try:
            with _cadastro_transacao_legada_arquivo_foto(
                client_id,
                tenant_path,
                arquivo_cliente,
                skus_guard=(sku,),
            ):
                cols_base = _cadastro_cols_base()
                if os.path.exists(arquivo_cliente):
                    try:
                        df = pd.read_csv(arquivo_cliente).fillna("")
                    except Exception as e:
                        raise HTTPException(
                            status_code=500,
                            detail=f"Erro ao abrir cadastro atual: {str(e)}",
                        )
                else:
                    df = pd.DataFrame(columns=cols_base)

                for c in cols_base:
                    if c not in df.columns:
                        df[c] = ""
                df = _cadastro_garantir_colunas_pesquisa(df)
                df, _ = _consolidar_cadastro_por_sku(df)

                agora = datetime.now().strftime("%d/%m/%Y %H:%M")
                novo = {
                    "sku": sku,
                    "nome": nome,
                    "categoria": (req.categoria or "").strip(),
                    "marca": (req.marca or "").strip(),
                    "custo": req.custo if req.custo is not None else "",
                    "preco": req.preco if req.preco is not None else "",
                    "descricao": (req.descricao or "").strip(),
                    "updated_at": agora,
                }

                mask = df["sku"].astype(str).str.strip().str.lower() == sku.lower()
                if mask.any():
                    for k, v in novo.items():
                        df.loc[mask, k] = v
                else:
                    nova_linha = {c: "" for c in df.columns}
                    nova_linha.update(novo)
                    df.loc[len(df)] = nova_linha

                df, _ = _consolidar_cadastro_por_sku(df)
                _cadastro_salvar_dataframe_atomico(df, arquivo_cliente)
                return {"success": True, "message": "Produto salvo com sucesso."}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Erro ao salvar cadastro de produtos: {str(e)}",
            ) from e

async def listar_colunas_cadastro(client_id: str = Depends(get_tenant_id)):
    tenant_path = get_tenant_path(client_id)
    arquivo_cliente = os.path.join(tenant_path, "cadastro_produtos.csv")
    cols_base = _cadastro_cols_base()

    if not os.path.exists(arquivo_cliente):
        return {"colunas": cols_base}

    try:
        df = pd.read_csv(arquivo_cliente, dtype=str).fillna("")
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        df = _cadastro_garantir_colunas_pesquisa(df)
        for c in cols_base:
            if c not in df.columns:
                df[c] = ""
        colunas = list(df.columns)
        return {"colunas": colunas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar colunas do cadastro: {str(e)}")

async def obter_produto_cadastro(sku: str, client_id: str = Depends(get_tenant_id)):
    sku_norm = _normalizar_sku_mes(sku)
    from backend.services.cadastro_compatibilidade import (
        obter_produto_controlado_para_compatibilidade,
    )

    controlado, produto_lojas = obter_produto_controlado_para_compatibilidade(
        client_id, sku_norm
    )
    if controlado:
        if produto_lojas is None:
            raise HTTPException(status_code=404, detail="SKU nao encontrado no cadastro por loja.")
        return {"produto": produto_lojas}

    tenant_path = get_tenant_path(client_id)
    arquivo_cliente = os.path.join(tenant_path, "cadastro_produtos.csv")
    if not os.path.exists(arquivo_cliente):
        raise HTTPException(status_code=404, detail="Cadastro de produtos nÃ£o encontrado para este cliente.")

    try:
        df = pd.read_csv(arquivo_cliente, dtype=str).fillna("")
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        if "sku" not in df.columns:
            raise HTTPException(status_code=404, detail="Coluna SKU nÃ£o encontrada no cadastro.")

        df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
        df, _ = _consolidar_cadastro_por_sku(df)
        mask = df["sku"].astype(str).str.strip().str.lower() == sku_norm.lower()
        if not mask.any():
            raise HTTPException(status_code=404, detail="SKU nÃ£o encontrado no cadastro.")

        linha = df.loc[mask].iloc[0].to_dict()
        linha = _cadastro_mesclar_linha_enriquecida(client_id, sku_norm, linha, arquivo_cliente)
        for campo in ("nome", "produto", "produto_bling", "nome_bling"):
            if campo in linha:
                linha[campo] = _cadastro_limpar_nome(linha[campo])
        try:
            mapa_custos = _cadastro_mapa_custos_lojas(client_id)
            custos_por_loja = {}
            for sku_key in _sku_lookup_variantes(sku_norm):
                custos_por_loja = mapa_custos.get(sku_key) or {}
                if custos_por_loja:
                    break
            linha["custos_por_loja"] = custos_por_loja
        except Exception as exc:
            logger.warning("[CADASTRO] Falha ao anexar custos por loja ao SKU %s: %s", sku_norm, exc)
        def _json_valor(v):
            if isinstance(v, (dict, list)):
                return v
            return "" if pd.isna(v) else str(v)

        return {"produto": {k: _json_valor(v) for k, v in linha.items()}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar produto no cadastro: {str(e)}")

async def atualizar_produto_cadastro_completo(sku: str, payload: dict, client_id: str = Depends(get_tenant_id)):
    sku_norm = _normalizar_sku_mes(sku)
    from backend.services.cadastro_compatibilidade import (
        bloquear_mutacao_legada_sem_sku_controlado,
        exigir_mutacao_legada_sem_campos_loja,
        exigir_mutacao_legada_sem_sku_controlado,
    )

    exigir_mutacao_legada_sem_sku_controlado(client_id, [sku_norm])
    payload = payload or {}
    data = {}
    for k, v in payload.items():
        key = str(k or "").strip().lower()
        if not key:
            continue
        data[key] = "" if v is None else str(v)
    exigir_mutacao_legada_sem_campos_loja(data)

    foto_data_url = str(data.pop("__foto_data_url", "") or "").strip()
    data.pop("__foto_filename", None)
    novo_sku = _normalizar_sku_mes(data.get("sku", sku_norm))
    if not novo_sku:
        raise HTTPException(status_code=400, detail="SKU invalido.")

    try:
        with bloquear_mutacao_legada_sem_sku_controlado(
            client_id,
            [sku_norm, novo_sku],
        ):
            tenant_path = get_tenant_path(client_id)
            arquivo_cliente = os.path.join(tenant_path, "cadastro_produtos.csv")
            with _cadastro_transacao_legada_arquivo_foto(
                client_id,
                tenant_path,
                arquivo_cliente,
                skus_guard=(sku_norm, novo_sku),
                sku_foto=novo_sku,
                foto_data_url=foto_data_url,
                referencias_foto=tuple(
                    referencia
                    for _coluna, referencia in _cadastro_foto_referencias_candidatas(
                        data
                    )
                ),
            ) as fotos_preparadas:
                if not os.path.exists(arquivo_cliente):
                    raise HTTPException(
                        status_code=404,
                        detail="Cadastro de produtos nao encontrado para este cliente.",
                    )

                df = pd.read_csv(arquivo_cliente, dtype=str).fillna("")
                df.columns = [str(c).strip().lower() for c in df.columns]
                df = df.loc[:, ~df.columns.duplicated()]
                df = _cadastro_garantir_colunas_pesquisa(df)
                if "sku" not in df.columns:
                    df["sku"] = ""

                df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
                df, _ = _consolidar_cadastro_por_sku(df)
                mask = df["sku"].astype(str).str.strip().str.lower() == sku_norm.lower()
                if not mask.any():
                    raise HTTPException(status_code=404, detail="SKU nao encontrado no cadastro.")

                if fotos_preparadas:
                    data["foto"] = str(fotos_preparadas[0]["relativo"])

                # Se mudar o SKU, valida conflito com outro registro.
                if novo_sku.lower() != sku_norm.lower():
                    conflito = df["sku"].astype(str).str.strip().str.lower() == novo_sku.lower()
                    if conflito.any() and (df.loc[conflito].index[0] != df.loc[mask].index[0]):
                        raise HTTPException(status_code=400, detail="Ja existe outro produto com o SKU informado.")

                for col in data.keys():
                    if col not in df.columns:
                        df[col] = ""

                idx = df.loc[mask].index[0]
                for col, val in data.items():
                    df.at[idx, col] = val
                df.at[idx, "sku"] = novo_sku
                if "updated_at" in df.columns:
                    df.at[idx, "updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")

                df, _ = _consolidar_cadastro_por_sku(df)
                if fotos_preparadas:
                    _salvar_fotos_preparadas_atomico(fotos_preparadas)
                _cadastro_salvar_dataframe_atomico(df, arquivo_cliente)
                return {
                    "success": True,
                    "message": "Produto atualizado com sucesso.",
                    "sku": novo_sku,
                    "foto": str(data.get("foto", "") or ""),
                }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar produto no cadastro: {str(e)}")

async def incluir_produto_cadastro_completo(payload: dict, client_id: str = Depends(get_tenant_id)):
    payload = payload or {}
    sku = _normalizar_sku_mes(payload.get("sku", ""))
    if not sku:
        raise HTTPException(status_code=400, detail="SKU ÃƒÂ© obrigatÃƒÂ³rio.")

    from backend.services.cadastro_compatibilidade import (
        bloquear_mutacao_legada_sem_sku_controlado,
        exigir_mutacao_legada_sem_campos_loja,
        exigir_mutacao_legada_sem_sku_controlado,
    )

    exigir_mutacao_legada_sem_sku_controlado(client_id, [sku])

    data = {}
    for k, v in payload.items():
        key = str(k or "").strip().lower()
        if not key:
            continue
        data[key] = "" if v is None else str(v)
    exigir_mutacao_legada_sem_campos_loja(data)

    foto_data_url = str(data.pop("__foto_data_url", "") or "").strip()
    data.pop("__foto_filename", None)
    data["sku"] = sku

    try:
        with bloquear_mutacao_legada_sem_sku_controlado(client_id, [sku]):
            tenant_path = get_tenant_path(client_id)
            arquivo_cliente = os.path.join(tenant_path, "cadastro_produtos.csv")
            with _cadastro_transacao_legada_arquivo_foto(
                client_id,
                tenant_path,
                arquivo_cliente,
                skus_guard=(sku,),
                sku_foto=sku,
                foto_data_url=foto_data_url,
                referencias_foto=tuple(
                    referencia
                    for _coluna, referencia in _cadastro_foto_referencias_candidatas(
                        data
                    )
                ),
            ) as fotos_preparadas:
                cols_base = _cadastro_cols_base()
                if os.path.exists(arquivo_cliente):
                    df = pd.read_csv(arquivo_cliente, dtype=str).fillna("")
                    df.columns = [str(c).strip().lower() for c in df.columns]
                    df = df.loc[:, ~df.columns.duplicated()]
                else:
                    df = pd.DataFrame(columns=cols_base)

                if "sku" not in df.columns:
                    df["sku"] = ""
                for c in cols_base:
                    if c not in df.columns:
                        df[c] = ""
                df = _cadastro_garantir_colunas_pesquisa(df)

                df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
                if (df["sku"].astype(str).str.strip().str.lower() == sku.lower()).any():
                    raise HTTPException(status_code=400, detail="Ja existe um produto com este SKU.")

                if fotos_preparadas:
                    data["foto"] = str(fotos_preparadas[0]["relativo"])
                if "updated_at" in df.columns:
                    data["updated_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")

                for col in data.keys():
                    if col not in df.columns:
                        df[col] = ""

                nova_linha = {c: "" for c in df.columns}
                for col, val in data.items():
                    nova_linha[col] = val
                df.loc[len(df)] = nova_linha

                df, _ = _consolidar_cadastro_por_sku(df)
                if fotos_preparadas:
                    _salvar_fotos_preparadas_atomico(fotos_preparadas)
                _cadastro_salvar_dataframe_atomico(df, arquivo_cliente)
                return {"success": True, "message": "Produto incluido com sucesso.", "sku": sku}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao incluir produto no cadastro: {str(e)}")

async def obter_produto_cadastro_query(sku: str, client_id: str = Depends(get_tenant_id)):
    # Wrapper para evitar problemas de roteamento com SKU no path.
    return await obter_produto_cadastro(sku, client_id)

async def atualizar_produto_cadastro_completo_query(
    payload: dict,
    sku_original: str,
    client_id: str = Depends(get_tenant_id)
):
    # Wrapper para evitar problemas de roteamento com SKU no path.
    return await atualizar_produto_cadastro_completo(sku_original, payload, client_id)

__all__ = ['salvar_produto_cadastro', 'listar_colunas_cadastro', 'obter_produto_cadastro', 'atualizar_produto_cadastro_completo', 'incluir_produto_cadastro_completo', 'obter_produto_cadastro_query', 'atualizar_produto_cadastro_completo_query', 'configure_cadastro_produtos_runtime']
