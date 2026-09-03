"""Cadastro per-store cost persistence helpers."""

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


from typing import Any

import os
import tempfile
from datetime import datetime

import pandas as pd
from fastapi import HTTPException

from backend.services.cadastro_common import *
from backend.services.path_coordination import path_lock_for

CADASTRO_CUSTOS_LOJAS_COLS = [
    "store_id",
    "loja_sync",
    "sku",
    "produto",
    "custo",
    "preco",
    "imposto",
    "updated_at",
]
_CADASTRO_CUSTOS_COLUNAS_INTERNAS = {"store_id_key", "loja_key", "sku_key"}


def configure_cadastro_custos_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_custos_runtime()

def _cadastro_custos_lojas_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "cadastro_custos_lojas.csv")


def _cadastro_custos_lock(client_id: str):
    return path_lock_for(_cadastro_custos_lojas_path(client_id))

def _cadastro_norm_loja_custo(valor: Any) -> str:
    return _chave_loja_favoritos(str(valor or "").strip())

def _cadastro_ler_custos_lojas(client_id: str) -> pd.DataFrame:
    caminho = _cadastro_custos_lojas_path(client_id)
    if not os.path.exists(caminho):
        return pd.DataFrame(columns=CADASTRO_CUSTOS_LOJAS_COLS)
    try:
        if os.path.getsize(caminho) == 0:
            return pd.DataFrame(columns=CADASTRO_CUSTOS_LOJAS_COLS)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Nao foi possivel acessar os custos por loja.",
        ) from exc

    ultimo_erro = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            df = pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding=encoding).fillna("")
            df.columns = [str(c).strip().lower() for c in df.columns]
            df = df.loc[:, ~df.columns.duplicated()]
            df = _cadastro_canonizar_colunas_custos(df)
            if "loja_sync" not in df.columns and "loja" in df.columns:
                df["loja_sync"] = df["loja"]
            for col in CADASTRO_CUSTOS_LOJAS_COLS:
                if col not in df.columns:
                    df[col] = ""
            df["sku"] = df["sku"].astype(str).apply(_normalizar_sku_mes)
            extras = [
                col
                for col in df.columns
                if col not in CADASTRO_CUSTOS_LOJAS_COLS
                and col not in _CADASTRO_CUSTOS_COLUNAS_INTERNAS
            ]
            return df[[*CADASTRO_CUSTOS_LOJAS_COLS, *extras]]
        except Exception as exc:
            ultimo_erro = exc

    logger.warning(
        "[CADASTRO CUSTOS] Arquivo existente invalido; escrita bloqueada: %s",
        type(ultimo_erro).__name__ if ultimo_erro else "erro_desconhecido",
    )
    raise HTTPException(
        status_code=500,
        detail="O arquivo de custos por loja esta invalido; nenhuma alteracao foi gravada.",
    )

def _cadastro_salvar_custos_lojas(client_id: str, df: pd.DataFrame) -> None:
    caminho = _cadastro_custos_lojas_path(client_id)
    pasta = os.path.dirname(caminho)
    with _cadastro_custos_lock(client_id):
        os.makedirs(pasta, exist_ok=True)
        df = (df if df is not None else pd.DataFrame()).copy()
        for col in CADASTRO_CUSTOS_LOJAS_COLS:
            if col not in df.columns:
                df[col] = ""
        extras = [
            col
            for col in df.columns
            if col not in CADASTRO_CUSTOS_LOJAS_COLS
            and col not in _CADASTRO_CUSTOS_COLUNAS_INTERNAS
        ]
        df = df[[*CADASTRO_CUSTOS_LOJAS_COLS, *extras]].fillna("")
        temporario = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=".cadastro_custos_lojas.",
                suffix=".tmp",
                dir=pasta,
                delete=False,
            ) as arquivo:
                temporario = arquivo.name
                df.to_csv(arquivo, index=False, lineterminator="\n")
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

def _cadastro_mapa_custos_lojas(client_id: str) -> dict[str, dict[str, dict]]:
    df = _cadastro_ler_custos_lojas(client_id)
    if df.empty or "sku" not in df.columns:
        return {}

    mapa: dict[str, dict[str, dict]] = {}
    identidades: dict[tuple[str, str], str] = {}
    ambiguos: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        sku = _normalizar_sku_mes(row.get("sku") or "")
        loja = str(row.get("loja_sync") or "").strip()
        if not sku or not loja:
            continue
        loja_key = _cadastro_norm_loja_custo(loja)
        if not loja_key:
            continue
        item = {
            "store_id": str(row.get("store_id") or "").strip(),
            "loja_sync": loja,
            "custo": str(row.get("custo") or "").strip(),
            "preco": str(row.get("preco") or "").strip(),
            "imposto": str(row.get("imposto") or "").strip(),
            "updated_at": str(row.get("updated_at") or "").strip(),
        }
        identidade = item["store_id"]
        for sku_key in _sku_lookup_variantes(sku):
            chave = (sku_key, loja_key)
            if chave in ambiguos:
                continue
            if chave not in identidades:
                identidades[chave] = identidade
                mapa.setdefault(sku_key, {})[loja_key] = item
                continue
            anterior = identidades[chave]
            if anterior and identidade and anterior != identidade:
                ambiguos.add(chave)
                mapa.setdefault(sku_key, {}).pop(loja_key, None)
            elif anterior and not identidade:
                # Uma sombra por nome nunca substitui a identidade duravel.
                continue
            else:
                # Exato substitui legado; dentro da mesma classe, a ultima
                # linha preserva o comportamento historico do arquivo.
                identidades[chave] = identidade or anterior
                mapa.setdefault(sku_key, {})[loja_key] = item
    return mapa

def _cadastro_anexar_custos_por_loja(client_id: str, df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "sku" not in df.columns:
        return df
    mapa = _cadastro_mapa_custos_lojas(client_id)
    if not mapa:
        return df
    df = df.copy()

    def _resolver(sku_val: Any) -> dict:
        for sku_key in _sku_lookup_variantes(str(sku_val or "")):
            dados = mapa.get(sku_key)
            if dados:
                return dados
        return {}

    df["custos_por_loja"] = df["sku"].astype(str).apply(_resolver)
    return df

def _cadastro_importar_custos_loja_sem_lock(
    client_id: str,
    df_import: pd.DataFrame,
    col_sku: str,
    colunas_importadas: list[str],
    loja: str,
    store_id: str | None = None,
) -> dict:
    loja_nome = str(loja or "").strip()
    if not loja_nome:
        raise HTTPException(status_code=400, detail="Selecione a loja/conta para salvar os custos e impostos.")
    store_id_norm = str(store_id or "").strip()

    df_store = _cadastro_ler_custos_lojas(client_id)
    if df_store.empty:
        df_store = pd.DataFrame(columns=CADASTRO_CUSTOS_LOJAS_COLS)

    colunas_store = [c for c in ("produto", "custo", "preco", "imposto") if c in df_import.columns]
    if not colunas_store:
        raise HTTPException(status_code=400, detail="A planilha precisa ter pelo menos uma coluna de custo, preÃ§o, imposto ou produto.")

    loja_key = _cadastro_norm_loja_custo(loja_nome)
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    atualizados = 0
    incluidos = 0

    if "loja_key" not in df_store.columns:
        df_store["loja_key"] = df_store["loja_sync"].astype(str).apply(_cadastro_norm_loja_custo)
    if "sku_key" not in df_store.columns:
        df_store["sku_key"] = df_store["sku"].astype(str).apply(lambda v: _normalizar_sku_mes(v).upper())
    if "store_id_key" not in df_store.columns:
        df_store["store_id_key"] = df_store["store_id"].astype(str).str.strip()

    for sku_idx, row in df_import.iterrows():
        sku = _normalizar_sku_mes(sku_idx)
        if not sku:
            continue
        sku_key = sku.upper()
        if store_id_norm:
            mask = (
                (df_store["store_id_key"].astype(str) == store_id_norm)
                & (df_store["sku_key"].astype(str) == sku_key)
            )
        else:
            mask = (
                (df_store["loja_key"].astype(str) == loja_key)
                & (df_store["sku_key"].astype(str) == sku_key)
            )

        if mask.any():
            idx = df_store.loc[mask].index[0]
            atualizados += 1
        else:
            idx = len(df_store)
            nova_linha = {c: "" for c in df_store.columns}
            nova_linha["store_id"] = store_id_norm
            nova_linha["loja_sync"] = loja_nome
            nova_linha["sku"] = sku
            nova_linha["store_id_key"] = store_id_norm
            nova_linha["loja_key"] = loja_key
            nova_linha["sku_key"] = sku_key
            df_store.loc[idx] = nova_linha
            incluidos += 1

        if store_id_norm:
            df_store.at[idx, "store_id"] = store_id_norm
        df_store.at[idx, "loja_sync"] = loja_nome
        df_store.at[idx, "sku"] = sku
        for col in colunas_store:
            valor = row.get(col, "")
            df_store.at[idx, col] = "" if pd.isna(valor) else str(valor)
        df_store.at[idx, "updated_at"] = agora
        df_store.at[idx, "store_id_key"] = str(df_store.at[idx, "store_id"] or "").strip()
        df_store.at[idx, "loja_key"] = loja_key
        df_store.at[idx, "sku_key"] = sku_key

    _cadastro_salvar_custos_lojas(client_id, df_store)
    return {
        "custos_loja_atualizados": atualizados,
        "custos_loja_incluidos": incluidos,
        "custos_loja": loja_nome,
    }


def _cadastro_importar_custos_loja(
    client_id: str,
    df_import: pd.DataFrame,
    col_sku: str,
    colunas_importadas: list[str],
    loja: str,
    store_id: str | None = None,
) -> dict:
    # O lock cobre o ciclo completo read-modify-write. Assim, importacoes
    # simultaneas de lojas diferentes nao perdem as linhas uma da outra.
    with _cadastro_custos_lock(client_id):
        return _cadastro_importar_custos_loja_sem_lock(
            client_id,
            df_import,
            col_sku,
            colunas_importadas,
            loja,
            store_id=store_id,
        )


def _cadastro_salvar_custos_item_loja(
    client_id: str,
    store_id: str,
    loja: str,
    sku: str,
    valores: dict[str, Any],
) -> dict:
    campos = {
        campo: valores[campo]
        for campo in ("custo", "preco", "imposto")
        if campo in valores
    }
    if not campos:
        return {
            "custos_loja_atualizados": 0,
            "custos_loja_incluidos": 0,
            "custos_loja": str(loja or "").strip(),
        }
    frame = pd.DataFrame([campos], index=[_normalizar_sku_mes(sku)])
    return _cadastro_importar_custos_loja(
        client_id,
        frame,
        "sku",
        list(campos),
        loja,
        store_id=store_id,
    )

__all__ = ['CADASTRO_CUSTOS_LOJAS_COLS', '_cadastro_custos_lojas_path', '_cadastro_norm_loja_custo', '_cadastro_ler_custos_lojas', '_cadastro_salvar_custos_lojas', '_cadastro_mapa_custos_lojas', '_cadastro_anexar_custos_por_loja', '_cadastro_importar_custos_loja', '_cadastro_salvar_custos_item_loja', 'configure_cadastro_custos_runtime']
