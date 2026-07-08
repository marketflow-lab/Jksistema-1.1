"""Convenio MG import, listing and SKU summary helpers."""

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


def configure_impostos_convenio_mg_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _arquivo_db_convenio_mg(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "impostos_convenio_mg.db")


def _conexao_db_convenio_mg(client_id: str) -> sqlite3.Connection:
    db_path = _arquivo_db_convenio_mg(client_id)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS convenio_mg_referencia (
            chave TEXT PRIMARY KEY,
            item TEXT,
            cest TEXT,
            cest_original TEXT,
            nbm_sh TEXT,
            nbm_sh_original TEXT,
            descricao TEXT,
            ambito_aplicacao TEXT,
            mva REAL,
            st_paraiba TEXT,
            st_minas TEXT,
            dados_extras_json TEXT,
            fonte_arquivo TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_convenio_mg_cest ON convenio_mg_referencia(cest)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_convenio_mg_nbmsh ON convenio_mg_referencia(nbm_sh)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_convenio_mg_desc ON convenio_mg_referencia(descricao)")
    conn.commit()
    return conn


def _ler_registros_convenio_mg_excel(caminho_excel: str) -> list[dict]:
    if not caminho_excel or not os.path.exists(caminho_excel):
        raise HTTPException(status_code=404, detail="Arquivo NCM1.xlsx nÃ£o encontrado na pasta do projeto.")

    try:
        df = pd.read_excel(caminho_excel, dtype=str)
    except Exception as e:
        logger.exception("[IMPOSTOS][CONVENIO_MG] Falha ao ler Excel NCM1: %s", e)
        raise HTTPException(status_code=400, detail="Falha ao ler o Excel NCM1.")

    if df.empty:
        return []

    colunas = list(df.columns)
    mapa_colunas = {_normalizar_coluna_ncm_excel(c): c for c in colunas}

    col_item = mapa_colunas.get("item")
    col_cest = mapa_colunas.get("cest")
    col_nbm = mapa_colunas.get("nbmsh")
    col_desc = mapa_colunas.get("descricao")
    col_ambito = mapa_colunas.get("ambitodeaplicacao")
    col_mva = mapa_colunas.get("mva")
    col_st_pb = mapa_colunas.get("stparaiba")
    col_st_mg = mapa_colunas.get("stminas")

    if not col_cest:
        raise HTTPException(status_code=400, detail="Excel NCM1 sem coluna CEST.")

    registros: list[dict] = []
    for _, row in df.iterrows():
        cest_original = _normalizar_valor_str_excel(row.get(col_cest, ""))
        cest = _normalizar_codigo_fiscal(cest_original)
        if not cest:
            continue

        item = _normalizar_valor_str_excel(row.get(col_item, "")) if col_item else ""
        nbm_original = _normalizar_valor_str_excel(row.get(col_nbm, "")) if col_nbm else ""
        nbm_sh = _normalizar_codigo_fiscal(nbm_original)
        descricao = _normalizar_valor_str_excel(row.get(col_desc, "")) if col_desc else ""
        ambito_aplicacao = _normalizar_valor_str_excel(row.get(col_ambito, "")) if col_ambito else ""
        mva = _to_float(row.get(col_mva, 0), 0.0) if col_mva else 0.0
        st_paraiba = _normalizar_valor_str_excel(row.get(col_st_pb, "")) if col_st_pb else ""
        st_minas = _normalizar_valor_str_excel(row.get(col_st_mg, "")) if col_st_mg else ""

        chave = f"{item}|{cest}|{nbm_sh}|{descricao}".strip("|")

        extras = {}
        for c in colunas:
            chave_col = _normalizar_coluna_ncm_excel(c)
            if chave_col.startswith("unnamed"):
                continue
            val = _normalizar_valor_str_excel(row.get(c, ""))
            if val:
                extras[str(c)] = val

        registros.append({
            "chave": chave or uuid.uuid4().hex,
            "item": item,
            "cest": cest,
            "cest_original": cest_original,
            "nbm_sh": nbm_sh,
            "nbm_sh_original": nbm_original,
            "descricao": descricao,
            "ambito_aplicacao": ambito_aplicacao,
            "mva": round(max(0.0, mva), 6),
            "st_paraiba": st_paraiba,
            "st_minas": st_minas,
            "dados_extras_json": json.dumps(extras, ensure_ascii=False),
        })

    return registros


def _importar_convenio_mg_excel_para_db(client_id: str, caminho_excel: str) -> dict:
    registros = _ler_registros_convenio_mg_excel(caminho_excel)
    if not registros:
        return {"importados": 0, "arquivo": os.path.basename(caminho_excel)}

    conn = _conexao_db_convenio_mg(client_id)
    try:
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = [
            (
                r["chave"], r["item"], r["cest"], r["cest_original"], r["nbm_sh"], r["nbm_sh_original"],
                r["descricao"], r["ambito_aplicacao"], r["mva"], r["st_paraiba"], r["st_minas"],
                r["dados_extras_json"], os.path.basename(caminho_excel), agora,
            )
            for r in registros
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO convenio_mg_referencia (
                chave, item, cest, cest_original, nbm_sh, nbm_sh_original,
                descricao, ambito_aplicacao, mva, st_paraiba, st_minas,
                dados_extras_json, fonte_arquivo, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        conn.commit()
        return {"importados": len(payload), "arquivo": os.path.basename(caminho_excel)}
    finally:
        conn.close()


def _garantir_base_convenio_mg_populada(client_id: str) -> None:
    conn = _conexao_db_convenio_mg(client_id)
    try:
        total = int(conn.execute("SELECT COUNT(1) FROM convenio_mg_referencia").fetchone()[0] or 0)
    finally:
        conn.close()

    if total == 0 and os.path.exists(ARQUIVO_NCM1_XLSX):
        try:
            _importar_convenio_mg_excel_para_db(client_id, ARQUIVO_NCM1_XLSX)
        except Exception as e:
            logger.warning("[IMPOSTOS][CONVENIO_MG] NÃƒÂ£o foi possÃƒÂ­vel autoimportar NCM1.xlsx: %s", e)


def _carregar_convenio_mg_referencia(client_id: str) -> list[dict]:
    _garantir_base_convenio_mg_populada(client_id)
    conn = _conexao_db_convenio_mg(client_id)
    try:
        rows = conn.execute(
            """
            SELECT item, cest, cest_original, nbm_sh, nbm_sh_original, descricao,
                   ambito_aplicacao, mva, st_paraiba, st_minas
            FROM convenio_mg_referencia
            """
        ).fetchall()
        dados = []
        for r in rows:
            nbm = _normalizar_codigo_fiscal(r["nbm_sh"])
            cest = _normalizar_codigo_fiscal(r["cest"])
            if not nbm or not cest:
                continue
            dados.append({
                "item": str(r["item"] or "").strip(),
                "cest": cest,
                "cest_original": str(r["cest_original"] or "").strip(),
                "nbm_sh": nbm,
                "nbm_sh_original": str(r["nbm_sh_original"] or "").strip(),
                "descricao": str(r["descricao"] or "").strip(),
                "ambito_aplicacao": str(r["ambito_aplicacao"] or "").strip(),
                "mva": round(max(0.0, _to_float(r["mva"], 0.0)), 6),
                "st_paraiba": str(r["st_paraiba"] or "").strip(),
                "st_minas": str(r["st_minas"] or "").strip(),
            })
        return dados
    finally:
        conn.close()


def _resumir_convenio_mg_para_sku(
    ncm_sku: str,
    cest_sku: str,
    idx_ncm_ref: dict[str, dict],
    convenio_rows: list[dict],
) -> dict:
    ncm_n = _normalizar_codigo_fiscal(ncm_sku)
    cest_sku_n = _normalizar_codigo_fiscal(cest_sku)
    if not ncm_n:
        return {
            "cest_ncm_referencia": "",
            "convenio_mg_vinculado": False,
            "convenio_mg_total_nbm": 0,
            "convenio_mg_total_compat": 0,
            "convenio_mg_nbm_sh": "",
            "convenio_mg_cest": "",
            "convenio_mg_mva": "",
            "convenio_mg_st_minas": "",
            "convenio_mg_st_paraiba": "",
            "convenio_mg_detalhes": "",
        }

    cest_ref = _normalizar_codigo_fiscal((idx_ncm_ref.get(ncm_n) or {}).get("cest", ""))
    cests_alvo = []
    if cest_ref:
        cests_alvo.append(cest_ref)
    if cest_sku_n and cest_sku_n not in cests_alvo:
        cests_alvo.append(cest_sku_n)

    candidatos_nbm = []
    for row in convenio_rows:
        nbm = row.get("nbm_sh", "")
        if nbm and ncm_n.startswith(nbm):
            candidatos_nbm.append(row)

    if cests_alvo:
        compat = [
            r for r in candidatos_nbm
            if _normalizar_codigo_fiscal(r.get("cest", "")) in cests_alvo
        ]
    else:
        compat = []

    # Quando nÃ£o hÃƒÂ¡ CEST compatÃƒÂ­vel, mantÃƒÂ©m visibilidade dos candidatos por NBM/SH
    # para facilitar auditoria e ajuste de CEST.
    exibir = compat if compat else candidatos_nbm[:5]

    def _join_unique(values: list[str]) -> str:
        out = []
        seen = set()
        for v in values:
            txt = str(v or "").strip()
            if not txt or txt in seen:
                continue
            seen.add(txt)
            out.append(txt)
        return " | ".join(out)

    nbm_join = _join_unique([r.get("nbm_sh_original") or r.get("nbm_sh") for r in exibir])
    cest_join = _join_unique([r.get("cest_original") or r.get("cest") for r in exibir])
    mva_join = _join_unique([f"{_to_float(r.get('mva', 0.0), 0.0):.2f}" for r in exibir])
    st_minas_join = _join_unique([r.get("st_minas") for r in exibir])
    st_paraiba_join = _join_unique([r.get("st_paraiba") for r in exibir])
    ambito_join = _join_unique([r.get("ambito_aplicacao") for r in exibir])

    detalhes_partes = []
    for r in exibir:
        detalhes_partes.append(
            f"NBM/SH {str(r.get('nbm_sh_original') or r.get('nbm_sh') or '').strip()}"
            f" | CEST {str(r.get('cest_original') or r.get('cest') or '').strip()}"
            f" | MVA {(_to_float(r.get('mva', 0.0), 0.0)):.2f}%"
            f" | ST MG {str(r.get('st_minas') or '').strip()}"
        )

    return {
        "cest_ncm_referencia": cest_ref,
        "convenio_mg_vinculado": len(compat) > 0,
        "convenio_mg_parcial_nbm": len(compat) == 0 and len(candidatos_nbm) > 0,
        "convenio_mg_total_nbm": len(candidatos_nbm),
        "convenio_mg_total_compat": len(compat),
        "convenio_mg_nbm_sh": nbm_join,
        "convenio_mg_cest": cest_join,
        "convenio_mg_ambito_aplicacao": ambito_join,
        "convenio_mg_mva": mva_join,
        "convenio_mg_st_minas": st_minas_join,
        "convenio_mg_st_paraiba": st_paraiba_join,
        "convenio_mg_detalhes": " || ".join(detalhes_partes),
    }


async def importar_convenio_mg_excel_impostos(client_id: str = Depends(get_tenant_id)):
    if not os.path.exists(ARQUIVO_NCM1_XLSX):
        raise HTTPException(status_code=404, detail="Arquivo NCM1.xlsx nÃ£o encontrado na pasta do projeto.")
    resultado = _importar_convenio_mg_excel_para_db(client_id, ARQUIVO_NCM1_XLSX)
    return {"success": True, **resultado}


async def listar_convenio_mg_impostos(
    search: str = "",
    limit: int = 50,
    offset: int = 0,
    client_id: str = Depends(get_tenant_id),
):
    limit = max(1, min(int(limit or 50), 50))
    offset = max(0, int(offset or 0))
    termo = str(search or "").strip()
    termo_digitos = _normalizar_codigo_fiscal(termo)

    _garantir_base_convenio_mg_populada(client_id)
    conn = _conexao_db_convenio_mg(client_id)
    try:
        if termo:
            like = f"%{termo}%"
            like_digitos = f"%{termo_digitos}%" if termo_digitos else ""
            filtro_texto = "(cest LIKE ? OR cest_original LIKE ? OR nbm_sh LIKE ? OR nbm_sh_original LIKE ? OR descricao LIKE ? OR ambito_aplicacao LIKE ? OR item LIKE ?)"
            params_filtro: list[Any] = [like, like, like, like, like, like, like]

            if termo_digitos:
                filtro_texto += (
                    " OR ("
                    "REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(cest, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    " OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(cest_original, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    " OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(nbm_sh, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    " OR REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(nbm_sh_original, ''), '.', ''), '-', ''), ' ', ''), '/', '') LIKE ?"
                    ")"
                )
                params_filtro.extend([like_digitos, like_digitos, like_digitos, like_digitos])

            total = int(
                conn.execute(
                    f"""
                    SELECT COUNT(1)
                    FROM convenio_mg_referencia
                    WHERE {filtro_texto}
                    """,
                    tuple(params_filtro),
                ).fetchone()[0]
                or 0
            )
            rows = conn.execute(
                f"""
                SELECT chave, item, cest, cest_original, nbm_sh, nbm_sh_original,
                       descricao, ambito_aplicacao, mva, st_paraiba, st_minas,
                       dados_extras_json, fonte_arquivo, updated_at
                FROM convenio_mg_referencia
                WHERE {filtro_texto}
                ORDER BY cest, nbm_sh, item
                LIMIT ? OFFSET ?
                """,
                tuple(params_filtro + [limit, offset]),
            ).fetchall()
        else:
            total = int(conn.execute("SELECT COUNT(1) FROM convenio_mg_referencia").fetchone()[0] or 0)
            rows = conn.execute(
                """
                SELECT chave, item, cest, cest_original, nbm_sh, nbm_sh_original,
                       descricao, ambito_aplicacao, mva, st_paraiba, st_minas,
                       dados_extras_json, fonte_arquivo, updated_at
                FROM convenio_mg_referencia
                ORDER BY cest, nbm_sh, item
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
                "chave": str(row["chave"] or ""),
                "item": str(row["item"] or ""),
                "cest": str(row["cest"] or ""),
                "cest_original": str(row["cest_original"] or ""),
                "nbm_sh": str(row["nbm_sh"] or ""),
                "nbm_sh_original": str(row["nbm_sh_original"] or ""),
                "descricao": str(row["descricao"] or ""),
                "ambito_aplicacao": str(row["ambito_aplicacao"] or ""),
                "mva": float(row["mva"] or 0),
                "st_paraiba": str(row["st_paraiba"] or ""),
                "st_minas": str(row["st_minas"] or ""),
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


configure_impostos_convenio_mg_runtime()

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
