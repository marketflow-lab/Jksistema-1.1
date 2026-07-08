"""Common Cadastro dataframe helpers."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

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


import pandas as pd


def configure_cadastro_common_runtime(runtime_module=None):
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_common_runtime()


CADASTRO_PESQUISA_COLS = ["pesquisa_1", "pesquisa_2", "pesquisa_3"]
CADASTRO_COLS_BASE = ["sku", "nome", "categoria", "marca", "custo", "preco", "imposto", "descricao", "updated_at"] + CADASTRO_PESQUISA_COLS
CADASTRO_DESCRICAO_COL_ALIASES = {
    "descricao",
    "description",
    "descricao produto",
    "descricao do produto",
    "descricaoproduto",
    "descricaodoproduto",
}


def _normalizar_sku_mes(sku: str) -> str:
    sku_txt = str(sku or "").strip()
    m = re.match(r"^\s*(\d+)\s*[-/]\s*([A-Za-z]{3})\s*$", sku_txt)
    if m:
        mes_map = {
            "JAN": "1", "FEV": "2", "MAR": "3", "ABR": "4", "MAI": "5", "JUN": "6",
            "JUL": "7", "AGO": "8", "SET": "9", "OUT": "10", "NOV": "11", "DEZ": "12",
        }
        dia = str(int(m.group(1)))
        mes = m.group(2).upper()
        if mes in mes_map:
            sku_txt = f"{dia}-{mes_map[mes]}"
    if re.match(r"^\d+$", sku_txt) and len(sku_txt) < 2:
        sku_txt = sku_txt.zfill(3)
    return sku_txt


def _chave_loja_favoritos(nome_loja: str | None) -> str:
    texto = unicodedata.normalize("NFKD", str(nome_loja or "").strip().lower())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", texto)


def _sku_lookup_variantes(sku_val: str) -> list[str]:
    sku_norm = _normalizar_sku_mes(str(sku_val or "").strip())
    if not sku_norm:
        return []
    sku_up = sku_norm.upper()
    sku_compacto = re.sub(r"[^A-Z0-9]", "", sku_up)
    partes = re.split(r"([0-9]+)", sku_up)
    sku_numsoft = "".join(str(int(p)) if p.isdigit() else p for p in partes)
    sku_numsoft_compacto = re.sub(r"[^A-Z0-9]", "", sku_numsoft)
    return [v for v in (sku_up, sku_compacto, sku_numsoft_compacto) if v]


def _cadastro_nome_esta_suspeito(valor: str) -> bool:
    texto = str(valor or "").strip()
    if not texto:
        return True
    if "\n" in texto or "\r" in texto:
        return True
    if len(texto) > 220:
        return True
    if texto.count(";") >= 2:
        return True
    return False


def _cadastro_limpar_nome(valor: str) -> str:
    return "" if _cadastro_nome_esta_suspeito(valor) else str(valor or "").strip()


def _cadastro_cols_base(*extras: str) -> list[str]:
    cols = list(CADASTRO_COLS_BASE)
    for col in extras:
        col = str(col or "").strip()
        if col and col not in cols:
            cols.append(col)
    return cols


def _cadastro_norm_coluna_texto(coluna: str) -> str:
    texto = str(coluna or "").strip().lower()
    texto = (
        texto
        .replace("\u00e3\u00a7", "c")
        .replace("\u00e3\u00a3", "a")
        .replace("\u00e3\u00a9", "e")
        .replace("\u00e3\u00aa", "e")
        .replace("\u00e3\u00ad", "i")
        .replace("\u00e3\u00b3", "o")
        .replace("\u00e3\u00ba", "u")
    )
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _cadastro_canonizar_coluna_descricao(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    aliases = [
        col for col in list(df.columns)
        if _cadastro_norm_coluna_texto(col) in CADASTRO_DESCRICAO_COL_ALIASES
    ]
    if "descricao" not in df.columns:
        df["descricao"] = ""
    for col in aliases:
        if col == "descricao" or col not in df.columns:
            continue
        atual = df["descricao"].astype(str)
        origem = df[col].astype(str)
        mask = atual.str.strip().eq("") & origem.str.strip().ne("")
        if mask.any():
            df.loc[mask, "descricao"] = origem.loc[mask]
        df = df.drop(columns=[col], errors="ignore")
    return df


def _cadastro_garantir_colunas_pesquisa(df: pd.DataFrame) -> pd.DataFrame:
    df = _cadastro_canonizar_coluna_descricao(df)
    for col in CADASTRO_PESQUISA_COLS:
        if col not in df.columns:
            df[col] = ""
    return df


def _cadastro_norm_col_custo(valor: str) -> str:
    base = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.replace("%", " percentual ")
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()


_CADASTRO_COLS_INDESEJADAS_NORM = {
    "custoattr", "custo attr",
    "impostoattr", "imposto attr",
    "sku key", "cg sku",
}


def _cadastro_coluna_indesejada(coluna: str) -> bool:
    texto = str(coluna or "").strip()
    if not texto:
        return True
    norm = _cadastro_norm_col_custo(texto)
    if "unnamed" in norm or norm in _CADASTRO_COLS_INDESEJADAS_NORM:
        return True
    if len(texto) > 120:
        return True
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?", texto):
        return True
    return False


_CADASTRO_COL_ALIASES_CUSTOS = {
    "sku": {
        "sku", "codigo sku", "cod sku", "seller sku", "codigo do sku", "codigo produto",
        "codigo do produto", "cod produto", "referencia", "referencia sku",
    },
    "custo": {
        "custo", "custo produto", "custo do produto", "custo unitario", "valor custo",
        "valor de custo", "preco custo", "preco de custo", "custo compra",
        "custo de compra", "custo medio", "custo medio unitario",
    },
    "imposto": {
        "imposto", "percentual imposto", "imposto percentual", "percentual de imposto",
        "imposto pct", "pct imposto", "aliquota", "aliquota imposto",
        "aliquota de imposto", "taxa imposto", "taxa de imposto", "tributo",
        "percentual tributo",
    },
    "preco": {
        "preco", "preco venda", "preco de venda", "valor venda", "valor de venda",
        "preco atual", "preco ml", "preco mercado livre",
    },
}


def _cadastro_colunas_alias(df: pd.DataFrame, destino: str) -> list[str]:
    if df is None or df.empty:
        return []
    aliases = _CADASTRO_COL_ALIASES_CUSTOS.get(destino) or set()
    return [col for col in df.columns if _cadastro_norm_col_custo(col) in aliases]


def _cadastro_canonizar_colunas_custos(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    rename = {}
    for destino in ("sku", "custo", "imposto", "preco"):
        if destino not in df.columns:
            aliases = _cadastro_colunas_alias(df, destino)
            if aliases:
                rename[aliases[0]] = destino
    if rename:
        df = df.rename(columns=rename)
        df = df.loc[:, ~df.columns.duplicated()]

    for destino in ("sku", "custo", "imposto", "preco"):
        if destino not in df.columns:
            continue
        for col in _cadastro_colunas_alias(df, destino):
            if col == destino:
                continue
            try:
                atual = df[destino].astype(str)
                origem = df[col].astype(str)
                mask = atual.str.strip().eq("") & origem.str.strip().ne("")
                if mask.any():
                    df.loc[mask, destino] = origem.loc[mask]
            except Exception:
                continue
    return df


def _consolidar_cadastro_por_sku(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Consolida linhas por SKU, mesclando campos preenchidos e removendo duplicados."""
    if df is None or df.empty or "sku" not in df.columns:
        return df, False

    cols = list(df.columns)
    itens = []
    indice_por_sku = {}
    descartou_sem_sku = False

    def _tem_valor(v) -> bool:
        return str(v if v is not None else "").strip() != ""

    for _, row in df.iterrows():
        row_dict = {c: row[c] if c in row else "" for c in cols}
        sku_raw = str(row_dict.get("sku", "") or "").strip()
        sku_norm = _normalizar_sku_mes(sku_raw)
        row_dict["sku"] = sku_norm

        if not sku_norm:
            # Linhas sem SKU nÃ£o sÃƒÂ£o agrupadas entre si.
            descartou_sem_sku = True
            continue

        if sku_norm not in indice_por_sku:
            indice_por_sku[sku_norm] = len(itens)
            itens.append(row_dict)
            continue

        idx = indice_por_sku[sku_norm]
        base = itens[idx]
        for c in cols:
            if c == "sku":
                base[c] = sku_norm
                continue
            incoming = row_dict.get(c, "")
            atual = base.get(c, "")
            if _tem_valor(incoming) and (not _tem_valor(atual) or c == "updated_at"):
                base[c] = incoming

    consolidado = pd.DataFrame(itens)
    for c in cols:
        if c not in consolidado.columns:
            consolidado[c] = ""
    consolidado = consolidado[cols]

    mudou = descartou_sem_sku or len(consolidado) != len(df)
    if not mudou:
        try:
            mudou = not consolidado.fillna("").equals(df.fillna(""))
        except Exception:
            mudou = False

    return consolidado, mudou

__all__ = [
    'CADASTRO_PESQUISA_COLS',
    'CADASTRO_COLS_BASE',
    'CADASTRO_DESCRICAO_COL_ALIASES',
    '_normalizar_sku_mes',
    '_chave_loja_favoritos',
    '_sku_lookup_variantes',
    '_cadastro_nome_esta_suspeito',
    '_cadastro_limpar_nome',
    '_cadastro_cols_base',
    '_cadastro_norm_coluna_texto',
    '_cadastro_canonizar_coluna_descricao',
    '_cadastro_garantir_colunas_pesquisa',
    '_cadastro_norm_col_custo',
    '_CADASTRO_COLS_INDESEJADAS_NORM',
    '_cadastro_coluna_indesejada',
    '_CADASTRO_COL_ALIASES_CUSTOS',
    '_cadastro_colunas_alias',
    '_cadastro_canonizar_colunas_custos',
    '_consolidar_cadastro_por_sku',
    'configure_cadastro_common_runtime',
]
