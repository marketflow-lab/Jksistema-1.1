"""NCM reference import and listing helpers."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, Header, HTTPException, Request, UploadFile
from jose import JWTError

from backend.schemas import (
    ImpostoRegraRequest,
    ImpostosSimulacaoRequest,
    SimuladorCalculoRequest,
    SiscomexAliquotasRequest,
    SiscomexConfigRequest,
    SiscomexConsultaRequest,
    SiscomexFundamentoOpcionalRequest,
)
from backend.services.impostos_common import *
from backend.services.impostos_context import get_tenant_id, get_tenant_path

logger = None

def _configure_runtime_globals(target_globals, runtime_module=None, peers=None):
    if runtime_module is not None:
        runtime_logger = getattr(runtime_module, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        for name in (
            "decodificar_access_token",
            "ler_csv_seguro",
            "salvar_csv_seguro",
            "_migrar_arquivo_legado_para_tenant",
            "ARQUIVO_DB_CADASTRO_PRODUTOS",
            "ARQUIVO_DB_PRODUTOS",
            "ARQUIVO_NCM_XLSX",
            "ARQUIVO_NCM1_XLSX",
            "pd",
        ):
            if hasattr(runtime_module, name):
                target_globals[name] = getattr(runtime_module, name)
    if peers:
        target_globals.update(peers)
    return runtime_module


def configure_impostos_ncm_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _arquivo_db_ncm_impostos(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "impostos_ncm.db")


def _conexao_db_ncm_impostos(client_id: str) -> sqlite3.Connection:
    db_path = _arquivo_db_ncm_impostos(client_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ncm_referencia (
            ncm TEXT PRIMARY KEY,
            ncm_original TEXT,
            descricao TEXT,
            descricao_completa TEXT,
            cest TEXT,
            ii REAL,
            ipi REAL,
            pis REAL,
            cofins REAL,
            dados_extras_json TEXT,
            fonte_arquivo TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ncm_referencia_cest ON ncm_referencia(cest)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ncm_referencia_descricao ON ncm_referencia(descricao)")
    conn.commit()
    return conn


def _ler_registros_ncm_excel(caminho_excel: str) -> list[dict]:
    if not caminho_excel or not os.path.exists(caminho_excel):
        raise HTTPException(status_code=404, detail="Arquivo NCM.xlsx nÃ£o encontrado na pasta do projeto.")

    try:
        df = pd.read_excel(caminho_excel, dtype=str)
    except Exception as e:
        logger.exception("[IMPOSTOS][NCM] Falha ao ler Excel de NCM: %s", e)
        raise HTTPException(status_code=400, detail="Falha ao ler o Excel de NCM.")

    if df.empty:
        return []

    # Algumas versÃƒÂµes do arquivo trazem uma linha repetindo os cabeÃƒÂ§alhos dentro dos dados.
    for col in ["NCM_Clean", "ncm_clean", "NCM", "ncm"]:
        if col in df.columns:
            serie = df[col].astype(str).str.strip()
            df = df[~serie.str.lower().isin({"ncm_clean", "ncm"})]
            break

    colunas = list(df.columns)
    mapa_colunas = {_normalizar_coluna_ncm_excel(c): c for c in colunas}

    col_ncm = mapa_colunas.get("ncmclean") or mapa_colunas.get("ncm")
    col_ncm_original = mapa_colunas.get("unnamed0") if "unnamed0" in mapa_colunas else None
    col_desc = mapa_colunas.get("descricao")
    col_desc_completa = mapa_colunas.get("descricaocompleta")
    col_cest = mapa_colunas.get("cest")
    col_ii = mapa_colunas.get("ii")
    col_ipi = mapa_colunas.get("ipi")
    col_pis = mapa_colunas.get("pis")
    col_cofins = mapa_colunas.get("cofins")

    if not col_ncm:
        raise HTTPException(status_code=400, detail="Excel NCM sem coluna NCM/NCM_Clean.")

    registros: list[dict] = []
    for _, row in df.iterrows():
        ncm = _normalizar_codigo_fiscal(row.get(col_ncm, ""))
        if not ncm:
            continue

        extras = {}
        for c in colunas:
            chave = _normalizar_coluna_ncm_excel(c)
            if chave.startswith("unnamed"):
                continue
            valor = row.get(c, "")
            txt = "" if pd.isna(valor) else str(valor).strip()
            if txt:
                extras[str(c)] = txt

        registros.append({
            "ncm": ncm,
            "ncm_original": str(row.get(col_ncm_original, "") or "").strip() if col_ncm_original else "",
            "descricao": str(row.get(col_desc, "") or "").strip() if col_desc else "",
            "descricao_completa": str(row.get(col_desc_completa, "") or "").strip() if col_desc_completa else "",
            "cest": _normalizar_codigo_fiscal(row.get(col_cest, "")) if col_cest else "",
            "ii": _normalizar_aliquota_percentual(row.get(col_ii, 0)) if col_ii else 0.0,
            "ipi": _normalizar_aliquota_percentual(row.get(col_ipi, 0)) if col_ipi else 0.0,
            "pis": _normalizar_aliquota_percentual(row.get(col_pis, 0)) if col_pis else 0.0,
            "cofins": _normalizar_aliquota_percentual(row.get(col_cofins, 0)) if col_cofins else 0.0,
            "dados_extras_json": json.dumps(extras, ensure_ascii=False),
        })

    return registros


def _importar_ncm_excel_para_db(client_id: str, caminho_excel: str) -> dict:
    registros = _ler_registros_ncm_excel(caminho_excel)
    if not registros:
        return {"importados": 0, "arquivo": os.path.basename(caminho_excel)}

    conn = _conexao_db_ncm_impostos(client_id)
    try:
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = [
            (
                r["ncm"],
                r["ncm_original"],
                r["descricao"],
                r["descricao_completa"],
                r["cest"],
                r["ii"],
                r["ipi"],
                r["pis"],
                r["cofins"],
                r["dados_extras_json"],
                os.path.basename(caminho_excel),
                agora,
            )
            for r in registros
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO ncm_referencia (
                ncm, ncm_original, descricao, descricao_completa, cest,
                ii, ipi, pis, cofins, dados_extras_json, fonte_arquivo, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
        return {"importados": len(payload), "arquivo": os.path.basename(caminho_excel)}
    finally:
        conn.close()


def _garantir_base_ncm_populada(client_id: str) -> None:
    conn = _conexao_db_ncm_impostos(client_id)
    try:
        total = int(conn.execute("SELECT COUNT(1) FROM ncm_referencia").fetchone()[0] or 0)
    finally:
        conn.close()

    if total == 0 and os.path.exists(ARQUIVO_NCM_XLSX):
        try:
            _importar_ncm_excel_para_db(client_id, ARQUIVO_NCM_XLSX)
        except Exception as e:
            logger.warning("[IMPOSTOS][NCM] NÃƒÂ£o foi possÃƒÂ­vel autoimportar NCM.xlsx: %s", e)


def _buscar_ncm_referencia(client_id: str, ncm: str) -> dict | None:
    ncm_norm = _normalizar_codigo_fiscal(ncm)
    if not ncm_norm:
        return None

    _garantir_base_ncm_populada(client_id)
    conn = _conexao_db_ncm_impostos(client_id)
    try:
        row = conn.execute(
            """
            SELECT ncm, ncm_original, descricao, descricao_completa, cest,
                   ii, ipi, pis, cofins, dados_extras_json, fonte_arquivo, updated_at
            FROM ncm_referencia
            WHERE ncm = ?
            LIMIT 1
            """,
            (ncm_norm,),
        ).fetchone()
        if not row:
            return None

        extras = {}
        try:
            extras = json.loads(str(row["dados_extras_json"] or "{}"))
        except Exception:
            extras = {}

        return {
            "ncm": str(row["ncm"] or ""),
            "ncm_original": str(row["ncm_original"] or ""),
            "descricao": str(row["descricao"] or ""),
            "descricao_completa": str(row["descricao_completa"] or ""),
            "cest": str(row["cest"] or ""),
            "ii": float(row["ii"] or 0),
            "ipi": float(row["ipi"] or 0),
            "pis": float(row["pis"] or 0),
            "cofins": float(row["cofins"] or 0),
            "extras": extras,
            "fonte_arquivo": str(row["fonte_arquivo"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
    finally:
        conn.close()


def _normalizar_valor_str_excel(valor: Any) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    return str(valor).strip()


def _indice_ncm_referencia_por_ncm(client_id: str) -> dict[str, dict]:
    _garantir_base_ncm_populada(client_id)
    conn = _conexao_db_ncm_impostos(client_id)
    try:
        rows = conn.execute(
            """
            SELECT ncm, cest, descricao, descricao_completa
            FROM ncm_referencia
            """
        ).fetchall()
        idx = {}
        for r in rows:
            ncm = _normalizar_codigo_fiscal(r["ncm"])
            if not ncm:
                continue
            idx[ncm] = {
                "ncm": ncm,
                "cest": _normalizar_codigo_fiscal(r["cest"]),
                "descricao": str(r["descricao"] or "").strip(),
                "descricao_completa": str(r["descricao_completa"] or "").strip(),
            }
        return idx
    finally:
        conn.close()


async def importar_ncm_excel_impostos(client_id: str = Depends(get_tenant_id)):
    if not os.path.exists(ARQUIVO_NCM_XLSX):
        raise HTTPException(status_code=404, detail="Arquivo NCM.xlsx nÃ£o encontrado na pasta do projeto.")
    resultado = _importar_ncm_excel_para_db(client_id, ARQUIVO_NCM_XLSX)
    return {"success": True, **resultado}


async def listar_ncm_impostos(
    search: str = "",
    limit: int = 50,
    offset: int = 0,
    client_id: str = Depends(get_tenant_id),
):
    limit = max(1, min(int(limit or 50), 50))
    offset = max(0, int(offset or 0))
    termo = str(search or "").strip()
    termo_digitos = _normalizar_codigo_fiscal(termo)

    _garantir_base_ncm_populada(client_id)
    conn = _conexao_db_ncm_impostos(client_id)
    try:
        if termo:
            like = f"%{termo}%"
            like_digitos = f"%{termo_digitos}%" if termo_digitos else ""
            filtro_texto = "(ncm LIKE ? OR descricao LIKE ? OR descricao_completa LIKE ? OR cest LIKE ? OR ncm_original LIKE ?)"
            params_filtro: list[Any] = [like, like, like, like, like]

            # TambÃƒÂ©m busca com colunas normalizadas por dÃƒÂ­gitos para suportar termos com mÃƒÂ¡scara/pontuaÃƒÂ§ÃƒÂ£o.
            if termo_digitos:
                filtro_texto += (
                    " OR ("
                    "REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(ncm, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    " OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(cest, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    " OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(ncm_original, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    ")"
                )
                params_filtro.extend([like_digitos, like_digitos, like_digitos])

            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(1)
                    FROM ncm_referencia
                    WHERE {filtro_texto}
                    """,
                    tuple(params_filtro),
                ).fetchone()[0]
                or 0
            )
            rows = conn.execute(
                f"""
                SELECT ncm, ncm_original, descricao, descricao_completa, cest,
                       ii, ipi, pis, cofins, dados_extras_json, fonte_arquivo, updated_at
                FROM ncm_referencia
                WHERE {filtro_texto}
                ORDER BY ncm
                LIMIT ? OFFSET ?
                """,
                tuple(params_filtro + [limit, offset]),
            ).fetchall()
        else:
            total = int(conn.execute("SELECT COUNT(1) FROM ncm_referencia").fetchone()[0] or 0)
            rows = conn.execute(
                """
                SELECT ncm, ncm_original, descricao, descricao_completa, cest,
                       ii, ipi, pis, cofins, dados_extras_json, fonte_arquivo, updated_at
                FROM ncm_referencia
                ORDER BY ncm
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

        itens = []
        for row in rows:
            extras = {}
            try:
                extras = json.loads(str(row["dados_extras_json"] or "{}"))
            except Exception:
                extras = {}
            itens.append({
                "ncm": str(row["ncm"] or ""),
                "ncm_original": str(row["ncm_original"] or ""),
                "descricao": str(row["descricao"] or ""),
                "descricao_completa": str(row["descricao_completa"] or ""),
                "cest": str(row["cest"] or ""),
                "ii": float(row["ii"] or 0),
                "ipi": float(row["ipi"] or 0),
                "pis": float(row["pis"] or 0),
                "cofins": float(row["cofins"] or 0),
                "extras": extras,
                "fonte_arquivo": str(row["fonte_arquivo"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            })

        return {
            "success": True,
            "total": total,
            "limit": limit,
            "offset": offset,
            "itens": itens,
        }
    finally:
        conn.close()


configure_impostos_ncm_runtime()

__all__ = [
    name
    for name in globals()
    if (
        (name.startswith("_") and not name.startswith("__"))
        or name.endswith("_impostos")
        or name.startswith("consultar_")
        or name.startswith("obter_")
        or name.startswith("salvar_")
        or name.startswith("testar_")
        or name.startswith("atualizar_")
        or name.startswith("importar_")
        or name.startswith("listar_")
        or name.startswith("simular_")
        or name.startswith("aplicar_")
        or name.startswith("calcular_")
        or name.startswith("configure_")
    )
]
