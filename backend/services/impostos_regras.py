"""Tax rule persistence, product simulation and cadastro application."""

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
from backend.services.path_coordination import path_lock_for

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


def configure_impostos_regras_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


def _arquivo_regras_impostos(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "impostos_regras.json")


def _to_float(valor, padrao: float = 0.0) -> float:
    txt = str(valor if valor is not None else "").strip()
    if not txt:
        return float(padrao)
    txt = txt.replace("R$", "").replace(" ", "")
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except Exception:
        return float(padrao)


def _normalizar_codigo_fiscal(valor: str) -> str:
    return re.sub(r"\D", "", str(valor or "").strip())


def _normalizar_regra_imposto(item: dict) -> dict:
    ncm = _normalizar_codigo_fiscal(item.get("ncm", ""))
    cest = _normalizar_codigo_fiscal(item.get("cest", ""))
    aliquota_federal = max(0.0, _to_float(item.get("aliquota_federal", 0.0)))
    aliquota_estadual = max(0.0, _to_float(item.get("aliquota_estadual", 0.0)))
    aliquota_municipal = max(0.0, _to_float(item.get("aliquota_municipal", 0.0)))
    return {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "ncm": ncm,
        "cest": cest,
        "descricao": str(item.get("descricao") or "").strip(),
        "aliquota_federal": round(aliquota_federal, 4),
        "aliquota_estadual": round(aliquota_estadual, 4),
        "aliquota_municipal": round(aliquota_municipal, 4),
        "observacoes": str(item.get("observacoes") or "").strip(),
        "updated_at": str(item.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    }


def _aliquota_total_regra(regra: dict) -> float:
    return round(
        max(0.0, _to_float(regra.get("aliquota_federal", 0.0)))
        + max(0.0, _to_float(regra.get("aliquota_estadual", 0.0)))
        + max(0.0, _to_float(regra.get("aliquota_municipal", 0.0))),
        4,
    )


def _carregar_regras_impostos(client_id: str) -> list[dict]:
    arquivo = _arquivo_regras_impostos(client_id)
    if not os.path.exists(arquivo):
        return []
    try:
        with open(arquivo, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            payload = payload.get("regras", [])
        if not isinstance(payload, list):
            return []
        regras = [_normalizar_regra_imposto(item if isinstance(item, dict) else {}) for item in payload]
        regras.sort(key=lambda x: (x.get("ncm", ""), x.get("cest", ""), x.get("id", "")))
        return regras
    except Exception as e:
        logger.warning(f"[IMPOSTOS] Falha ao carregar regras do cliente {client_id}: {e}")
        return []


def _salvar_regras_impostos(client_id: str, regras: list[dict]) -> None:
    arquivo = _arquivo_regras_impostos(client_id)
    os.makedirs(os.path.dirname(arquivo), exist_ok=True)
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(regras, f, ensure_ascii=False, indent=2)


def _indice_regras_impostos(regras: list[dict]) -> dict:
    idx = {}
    for regra in regras:
        ncm = _normalizar_codigo_fiscal(regra.get("ncm", ""))
        cest = _normalizar_codigo_fiscal(regra.get("cest", ""))
        if ncm:
            idx[f"{ncm}|{cest}"] = regra
            if cest:
                idx.setdefault(f"{ncm}|", regra)
    return idx


def _resolver_regra_impostos(ncm: str, cest: str, idx_regras: dict) -> dict | None:
    ncm_n = _normalizar_codigo_fiscal(ncm)
    cest_n = _normalizar_codigo_fiscal(cest)
    if not ncm_n:
        return None
    return idx_regras.get(f"{ncm_n}|{cest_n}") or idx_regras.get(f"{ncm_n}|")


def _sku_keys_impostos(valor: Any) -> set[str]:
    sku = str(valor or "").strip().upper()
    if not sku:
        return set()
    if re.fullmatch(r"\d+\.0+", sku):
        sku = str(int(float(sku)))
    compacto = re.sub(r"[^A-Z0-9]", "", sku)
    partes = re.split(r"(\d+)", sku)
    sem_zeros = "".join(str(int(p)) if p.isdigit() else p for p in partes)
    sem_zeros_compacto = re.sub(r"[^A-Z0-9]", "", sem_zeros)
    return {k for k in (sku, compacto, sem_zeros, sem_zeros_compacto) if k}


def _arquivos_classificacao_cadastro(client_id: str) -> list[str]:
    tenant_path = get_tenant_path(client_id)
    if not tenant_path or not os.path.isdir(tenant_path):
        return []
    arquivos = []
    for nome in os.listdir(tenant_path):
        nome_l = str(nome or "").lower()
        if nome_l.startswith("cadastro_produtos.backup_classificacao") and nome_l.endswith(".csv"):
            arquivos.append(os.path.join(tenant_path, nome))
    arquivos.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return arquivos


def _ler_csv_classificacao_cadastro(caminho: str) -> pd.DataFrame:
    ultimo_erro = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        try:
            df = pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding=encoding).fillna("")
            df.columns = [str(c or "").strip().lower() for c in df.columns]
            df = df.loc[:, ~df.columns.duplicated()]
            return df
        except Exception as exc:
            ultimo_erro = exc
    if logger:
        logger.warning("[IMPOSTOS] Falha ao ler backup de classificacao %s: %s", caminho, ultimo_erro)
    return pd.DataFrame()


def _mapa_classificacao_fiscal_cadastro(client_id: str) -> dict[str, dict]:
    mapa: dict[str, dict] = {}
    for caminho in _arquivos_classificacao_cadastro(client_id):
        df = _ler_csv_classificacao_cadastro(caminho)
        if df.empty or "sku" not in df.columns:
            continue
        ncm_col = "ncm" if "ncm" in df.columns else ""
        cest_col = "cest" if "cest" in df.columns else ""
        if not ncm_col and not cest_col:
            continue
        fonte = os.path.basename(caminho)
        for _, row in df.iterrows():
            ncm = _normalizar_codigo_fiscal(row.get(ncm_col, "")) if ncm_col else ""
            cest = _normalizar_codigo_fiscal(row.get(cest_col, "")) if cest_col else ""
            if not ncm and not cest:
                continue
            registro = {
                "ncm": ncm,
                "cest": cest,
                "fonte": fonte,
                "updated_at": str(row.get("updated_at", "") or "").strip(),
            }
            for sku_key in _sku_keys_impostos(row.get("sku", "")):
                mapa.setdefault(sku_key, registro)
    return mapa


def _aplicar_classificacao_fiscal_fallback(df: pd.DataFrame, client_id: str) -> pd.DataFrame:
    if df is None or df.empty or "sku" not in df.columns:
        return df
    mapa = _mapa_classificacao_fiscal_cadastro(client_id)
    if not mapa:
        return df
    df = df.copy()
    for col in ("ncm", "cest", "ncm_fonte_impostos", "cest_fonte_impostos"):
        if col not in df.columns:
            df[col] = ""
    for idx, row in df.iterrows():
        registro = None
        for sku_key in _sku_keys_impostos(row.get("sku", "")):
            registro = mapa.get(sku_key)
            if registro:
                break
        if not registro:
            continue
        if not _normalizar_codigo_fiscal(row.get("ncm", "")) and registro.get("ncm"):
            df.at[idx, "ncm"] = registro["ncm"]
            df.at[idx, "ncm_fonte_impostos"] = registro.get("fonte", "")
        if not _normalizar_codigo_fiscal(row.get("cest", "")) and registro.get("cest"):
            df.at[idx, "cest"] = registro["cest"]
            df.at[idx, "cest_fonte_impostos"] = registro.get("fonte", "")
    return df


def _carregar_cadastro_para_impostos(client_id: str) -> tuple[pd.DataFrame, str]:
    arquivo = _migrar_arquivo_legado_para_tenant(client_id, "cadastro_produtos.csv", ARQUIVO_DB_CADASTRO_PRODUTOS)
    if not arquivo or not os.path.exists(arquivo):
        return pd.DataFrame(), arquivo
    df = pd.read_csv(arquivo, dtype=str).fillna("")
    df.columns = [str(c or "").strip().lower() for c in df.columns]
    for c in ["sku", "nome", "ncm", "cest", "custo", "preco", "imposto"]:
        if c not in df.columns:
            df[c] = ""
    df = _aplicar_classificacao_fiscal_fallback(df, client_id)
    return df, arquivo



def _montar_simulacao_impostos(client_id: str, reserva_percentual: float = 0.0) -> dict:
    regras = _carregar_regras_impostos(client_id)
    idx_regras = _indice_regras_impostos(regras)
    idx_ncm_ref = _indice_ncm_referencia_por_ncm(client_id)
    convenio_rows = _carregar_convenio_mg_referencia(client_id)
    df, _ = _carregar_cadastro_para_impostos(client_id)
    if df.empty:
        return {"reserva_percentual": max(0.0, float(reserva_percentual)), "produtos": [], "resumo": {"qtd_produtos": 0, "qtd_com_regra": 0, "imposto_total_estimado": 0.0, "faturamento_total": 0.0, "margem_liquida_total": 0.0}}

    reserva = max(0.0, float(reserva_percentual))
    produtos = []
    faturamento_total = 0.0
    imposto_total_estimado = 0.0
    margem_liquida_total = 0.0
    qtd_com_regra = 0
    qtd_com_convenio = 0

    for _, row in df.iterrows():
        sku = str(row.get("sku", "")).strip()
        nome = str(row.get("nome", "")).strip()
        ncm = _normalizar_codigo_fiscal(row.get("ncm", ""))
        cest = _normalizar_codigo_fiscal(row.get("cest", ""))
        custo = max(0.0, _to_float(row.get("custo", 0.0)))
        preco = max(0.0, _to_float(row.get("preco", 0.0)))
        regra = _resolver_regra_impostos(ncm, cest, idx_regras)

        aliquota_total = _aliquota_total_regra(regra) if regra else 0.0
        imposto_estimado = round(preco * (aliquota_total / 100.0), 2)
        reserva_valor = round(preco * (reserva / 100.0), 2)
        margem_bruta = round(preco - custo, 2)
        margem_liquida = round(preco - custo - imposto_estimado - reserva_valor, 2)
        convenio = _resumir_convenio_mg_para_sku(ncm, cest, idx_ncm_ref, convenio_rows)

        if regra:
            qtd_com_regra += 1
        if convenio.get("convenio_mg_vinculado"):
            qtd_com_convenio += 1

        faturamento_total += preco
        imposto_total_estimado += imposto_estimado
        margem_liquida_total += margem_liquida

        produtos.append({
            "sku": sku,
            "nome": nome,
            "ncm": ncm,
            "cest": cest,
            "ncm_fonte_impostos": str(row.get("ncm_fonte_impostos", "") or "").strip(),
            "cest_fonte_impostos": str(row.get("cest_fonte_impostos", "") or "").strip(),
            "custo": round(custo, 2),
            "preco": round(preco, 2),
            "aliquota_total": round(aliquota_total, 4),
            "imposto_estimado": imposto_estimado,
            "margem_bruta": margem_bruta,
            "margem_liquida": margem_liquida,
            "regra_id": regra.get("id") if regra else "",
            "regra_descricao": regra.get("descricao", "") if regra else "",
            "cest_ncm_referencia": convenio.get("cest_ncm_referencia", ""),
            "convenio_mg_vinculado": bool(convenio.get("convenio_mg_vinculado", False)),
            "convenio_mg_parcial_nbm": bool(convenio.get("convenio_mg_parcial_nbm", False)),
            "convenio_mg_total_nbm": int(convenio.get("convenio_mg_total_nbm", 0) or 0),
            "convenio_mg_total_compat": int(convenio.get("convenio_mg_total_compat", 0) or 0),
            "convenio_mg_nbm_sh": convenio.get("convenio_mg_nbm_sh", ""),
            "convenio_mg_cest": convenio.get("convenio_mg_cest", ""),
            "convenio_mg_ambito_aplicacao": convenio.get("convenio_mg_ambito_aplicacao", ""),
            "convenio_mg_mva": convenio.get("convenio_mg_mva", ""),
            "convenio_mg_st_minas": convenio.get("convenio_mg_st_minas", ""),
            "convenio_mg_st_paraiba": convenio.get("convenio_mg_st_paraiba", ""),
            "convenio_mg_detalhes": convenio.get("convenio_mg_detalhes", ""),
        })

    produtos.sort(key=lambda x: (x.get("sku", ""), x.get("nome", "")))
    return {
        "reserva_percentual": reserva,
        "produtos": produtos,
        "resumo": {
            "qtd_produtos": len(produtos),
            "qtd_com_regra": qtd_com_regra,
            "qtd_com_convenio": qtd_com_convenio,
            "imposto_total_estimado": round(imposto_total_estimado, 2),
            "faturamento_total": round(faturamento_total, 2),
            "margem_liquida_total": round(margem_liquida_total, 2),
        }
    }


async def listar_regras_impostos(client_id: str = Depends(get_tenant_id)):
    regras = _carregar_regras_impostos(client_id)
    for r in regras:
        r["aliquota_total"] = _aliquota_total_regra(r)
    return regras


async def salvar_regra_impostos(req: ImpostoRegraRequest, client_id: str = Depends(get_tenant_id)):
    ncm = _normalizar_codigo_fiscal(req.ncm)
    cest = _normalizar_codigo_fiscal(req.cest)
    if not ncm:
        raise HTTPException(status_code=400, detail="Informe o NCM para salvar a regra.")

    regras = _carregar_regras_impostos(client_id)
    regra_nova = _normalizar_regra_imposto({
        "id": req.id or "",
        "ncm": ncm,
        "cest": cest,
        "descricao": req.descricao,
        "aliquota_federal": req.aliquota_federal,
        "aliquota_estadual": req.aliquota_estadual,
        "aliquota_municipal": req.aliquota_municipal,
        "observacoes": req.observacoes,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    atualizou = False
    for i, regra in enumerate(regras):
        mesma_chave = (
            _normalizar_codigo_fiscal(regra.get("ncm", "")) == ncm
            and _normalizar_codigo_fiscal(regra.get("cest", "")) == cest
        )
        mesmo_id = bool(req.id) and str(regra.get("id", "")) == str(req.id)
        if mesma_chave or mesmo_id:
            regra_nova["id"] = str(regra.get("id") or regra_nova["id"])
            regras[i] = regra_nova
            atualizou = True
            break

    if not atualizou:
        regras.append(regra_nova)

    regras.sort(key=lambda x: (x.get("ncm", ""), x.get("cest", ""), x.get("id", "")))
    _salvar_regras_impostos(client_id, regras)
    return {"success": True, "regra": regra_nova, "total": len(regras)}


async def excluir_regra_impostos(regra_id: str, client_id: str = Depends(get_tenant_id)):
    regras = _carregar_regras_impostos(client_id)
    antes = len(regras)
    regras = [r for r in regras if str(r.get("id", "")) != str(regra_id)]
    removidos = antes - len(regras)
    if removidos <= 0:
        raise HTTPException(status_code=404, detail="Regra nÃ£o encontrada.")
    _salvar_regras_impostos(client_id, regras)
    return {"success": True, "removidos": removidos, "total": len(regras)}


async def listar_produtos_impostos(client_id: str = Depends(get_tenant_id)):
    return _montar_simulacao_impostos(client_id, 0.0)


async def simular_impostos(req: ImpostosSimulacaoRequest, client_id: str = Depends(get_tenant_id)):
    return _montar_simulacao_impostos(client_id, req.reserva_percentual)


async def _aplicar_impostos_no_cadastro_sem_lock(client_id: str):
    df, arquivo = _carregar_cadastro_para_impostos(client_id)
    if df.empty or not arquivo or not os.path.exists(arquivo):
        raise HTTPException(status_code=404, detail="Cadastro nÃ£o encontrado para aplicar impostos.")

    regras = _carregar_regras_impostos(client_id)
    idx_regras = _indice_regras_impostos(regras)

    if "imposto" not in df.columns:
        df["imposto"] = ""

    atualizados = 0
    preservados = 0
    for idx, row in df.iterrows():
        ncm = _normalizar_codigo_fiscal(row.get("ncm", ""))
        cest = _normalizar_codigo_fiscal(row.get("cest", ""))
        regra = _resolver_regra_impostos(ncm, cest, idx_regras)
        if not regra:
            if str(row.get("imposto", "")).strip():
                preservados += 1
            continue
        novo = f"{_aliquota_total_regra(regra):.4f}".rstrip("0").rstrip(".")
        atual = str(row.get("imposto", "")).strip()
        if atual != novo:
            df.at[idx, "imposto"] = novo
            atualizados += 1

    if atualizados > 0:
        df.to_csv(arquivo, index=False)

    logger.info(
        "[IMPOSTOS][%s] aplicaÃƒÂ§ÃƒÂ£o no cadastro: atualizados=%d preservados=%d",
        client_id,
        atualizados,
        preservados,
    )
    return {"success": True, "atualizados": atualizados, "preservados": preservados, "qtd_regras": len(regras)}


async def aplicar_impostos_no_cadastro(
    client_id: str = Depends(get_tenant_id),
):
    # Materialize candidates independently, then re-read under the canonical
    # store lock.  A scoped SKU created between both reads is therefore seen
    # before any global fiscal value can be persisted.
    preview, arquivo = _carregar_cadastro_para_impostos(client_id)
    if preview.empty or not arquivo or not os.path.exists(arquivo):
        raise HTTPException(
            status_code=404,
            detail="Cadastro nÃ£o encontrado para aplicar impostos.",
        )
    skus_preview = preview["sku"].astype(str).tolist() if "sku" in preview.columns else []
    from backend.services.cadastro_compatibilidade import (
        bloquear_mutacao_legada_sem_sku_controlado,
        exigir_mutacao_legada_sem_sku_controlado,
    )

    with bloquear_mutacao_legada_sem_sku_controlado(client_id, skus_preview):
        with path_lock_for(arquivo):
            atual, arquivo_atual = _carregar_cadastro_para_impostos(client_id)
            if (
                atual.empty
                or not arquivo_atual
                or os.path.realpath(arquivo_atual) != os.path.realpath(arquivo)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "legacy_catalog_changed",
                        "message": "O cadastro global mudou antes da aplicacao dos impostos.",
                    },
                )
            exigir_mutacao_legada_sem_sku_controlado(
                client_id,
                atual["sku"].astype(str).tolist()
                if "sku" in atual.columns
                else [],
            )
            return await _aplicar_impostos_no_cadastro_sem_lock(client_id)


configure_impostos_regras_runtime()

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
