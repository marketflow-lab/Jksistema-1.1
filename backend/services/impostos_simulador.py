"""Price simulator endpoint for Impostos."""

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


def configure_impostos_simulador_runtime(runtime_module=None, peers=None):
    return _configure_runtime_globals(globals(), runtime_module, peers)


async def calcular_preco_simulador(req: SimuladorCalculoRequest, client_id: str = Depends(get_tenant_id)):
    def _primeiro_numero_percentual(valor: Any) -> float | None:
        match = re.search(r"-?\d+(?:[.,]\d+)?", str(valor or "").replace(" ", ""))
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", "."))
        except Exception:
            return None

    def _resposta_sim(valor: Any) -> bool:
        txt = unicodedata.normalize("NFKD", str(valor or "").strip().lower())
        txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
        return txt.startswith("sim") or txt in {"s", "yes", "true", "1"}

    ncm = _normalizar_codigo_fiscal(req.ncm)
    ncm_vazio = len(ncm) != 8

    # AlÃƒÂ­quotas mÃƒÂ©dias usadas quando o NCM nÃ£o ÃƒÂ© informado
    ALIQ_MEDIA_II = 10.0
    ALIQ_MEDIA_IPI = 5.0
    ALIQ_MEDIA_PIS = 1.5
    ALIQ_MEDIA_COFINS = 7.0

    if ncm_vazio and len(ncm) > 0:
        raise HTTPException(status_code=400, detail="NCM invalido: informe 8 dÃƒÂ­gitos ou deixe em branco para usar alÃƒÂ­quotas mÃƒÂ©dias.")

    valor_dolar = max(0.0, _to_float(req.valor_dolar, 0.0))
    quantidade = max(0.0, _to_float(req.quantidade, 0.0))
    cubagem = max(0.0, _to_float(req.cubagem, 0.0))
    cotacao_dolar = max(0.0, _to_float(req.cotacao_dolar, 0.0))
    frete_por_m3 = max(0.0, _to_float(req.frete_por_m3, 0.0))

    if valor_dolar <= 0:
        raise HTTPException(status_code=400, detail="Informe o valor em dÃƒÂ³lar maior que zero.")
    if quantidade <= 0:
        raise HTTPException(status_code=400, detail="Informe a quantidade maior que zero.")
    if cotacao_dolar <= 0:
        raise HTTPException(status_code=400, detail="Informe a cotaÃƒÂ§ÃƒÂ£o do dÃƒÂ³lar maior que zero.")

    if ncm_vazio:
        ncm_ref = {
            "ncm": "00000000",
            "descricao": "NCM nÃ£o informado Ã¢â‚¬â€ alÃƒÂ­quotas mÃƒÂ©dias aplicadas",
            "descricao_completa": "NCM nÃ£o informado. CÃƒÂ¡lculo realizado com alÃƒÂ­quotas mÃƒÂ©dias: II 10%, IPI 5%, PIS 1,5%, COFINS 7%.",
            "ii": ALIQ_MEDIA_II,
            "ipi": ALIQ_MEDIA_IPI,
            "pis": ALIQ_MEDIA_PIS,
            "cofins": ALIQ_MEDIA_COFINS,
        }
        monofasico_info = {"status": "indeterminado", "motivo": "NCM nÃ£o informado"}
        convenio_mg_info = {}
    else:
        ncm_ref = _buscar_ncm_referencia(client_id, ncm)
        if not ncm_ref:
            raise HTTPException(status_code=404, detail=f"NCM {ncm} nÃ£o encontrado na base NCM.")
        monofasico_info = _avaliar_monofasico_ncm(client_id, ncm)
        convenio_mg_info = _resumir_convenio_mg_para_sku(
            ncm,
            str(ncm_ref.get("cest") or ""),
            _indice_ncm_referencia_por_ncm(client_id),
            _carregar_convenio_mg_referencia(client_id),
        )

    valor_produtos_usd = valor_dolar * quantidade
    valor_produtos_brl = valor_produtos_usd * cotacao_dolar
    frete_internacional = cubagem * frete_por_m3
    valor_aduaneiro = valor_produtos_brl + frete_internacional
    seguro_internacional_percentual = 0.0028
    seguro_internacional = valor_aduaneiro * seguro_internacional_percentual

    aliq_ii = max(0.0, _to_float(ncm_ref.get("ii"), 0.0)) / 100.0
    aliq_ipi = max(0.0, _to_float(ncm_ref.get("ipi"), 0.0)) / 100.0
    aliq_pis = max(0.0, _to_float(ncm_ref.get("pis"), 0.0)) / 100.0
    aliq_cofins = max(0.0, _to_float(ncm_ref.get("cofins"), 0.0)) / 100.0

    imposto_ii = valor_aduaneiro * aliq_ii
    base_ipi = valor_aduaneiro + imposto_ii
    imposto_ipi = base_ipi * aliq_ipi
    imposto_pis = valor_aduaneiro * aliq_pis
    imposto_cofins = valor_aduaneiro * aliq_cofins
    base_icms_importacao = valor_produtos_brl + imposto_ii + imposto_ipi + imposto_pis + imposto_cofins + frete_internacional + seguro_internacional
    base_icms_sem_st = imposto_ii + imposto_ipi + imposto_pis + imposto_cofins + frete_internacional + seguro_internacional
    mva_minas = _primeiro_numero_percentual(convenio_mg_info.get("convenio_mg_mva"))
    mva_ajustado = (
        (((1 + (mva_minas / 100.0)) * ((1 - 0.04) / (1 - 0.18))) - 1) * 100.0
        if mva_minas is not None
        else None
    )
    fator_ipi = 1 + aliq_ipi
    base_cst_mg = base_icms_importacao * fator_ipi * (1 + (mva_minas / 100.0)) if mva_minas is not None else None
    base_cst_pb = base_icms_importacao * fator_ipi * (1 + (mva_ajustado / 100.0)) if mva_ajustado is not None else None
    st_mg_sim = _resposta_sim(convenio_mg_info.get("convenio_mg_st_minas"))
    st_pb_sim = _resposta_sim(convenio_mg_info.get("convenio_mg_st_paraiba"))
    if st_pb_sim:
        aliq_icms = 0.18
        base_icms = base_icms_importacao
        icms_retido_nfe = base_icms * 0.04
        base_icms_calculo = base_cst_pb
    elif st_mg_sim:
        aliq_icms = 0.18
        base_icms = base_icms_importacao
        icms_retido_nfe = base_icms * 0.04
        base_icms_calculo = base_cst_pb if base_cst_pb is not None else base_cst_mg
    else:
        aliq_icms = 0.04
        base_icms = base_icms_sem_st
        icms_retido_nfe = None
        base_icms_calculo = base_icms_sem_st
    imposto_icms = ((base_icms_calculo or 0.0) * aliq_icms) - (icms_retido_nfe or 0.0) if base_icms_calculo is not None else 0.0
    taxa_trade_percentual = 0.03
    taxa_trade = (valor_aduaneiro + seguro_internacional) * taxa_trade_percentual
    total_impostos = imposto_ii + imposto_ipi + imposto_pis + imposto_cofins + imposto_icms
    preco_final_total = valor_aduaneiro + seguro_internacional + total_impostos + taxa_trade
    preco_final_unitario = preco_final_total / quantidade if quantidade > 0 else 0.0

    return {
        "success": True,
        "ncm": ncm_ref,
        "monofasico": monofasico_info,
        "convenio_mg": convenio_mg_info,
        "entradas": {
            "valor_dolar": round(valor_dolar, 6),
            "quantidade": round(quantidade, 6),
            "cubagem": round(cubagem, 6),
            "cotacao_dolar": round(cotacao_dolar, 6),
            "frete_por_m3": round(frete_por_m3, 6),
        },
        "aliquotas": {
            "ii": round(aliq_ii * 100.0, 6),
            "ipi": round(aliq_ipi * 100.0, 6),
            "pis": round(aliq_pis * 100.0, 6),
            "cofins": round(aliq_cofins * 100.0, 6),
            "icms": round(aliq_icms * 100.0, 6),
            "trade": round(taxa_trade_percentual * 100.0, 6),
        },
        "calculo": {
            "valor_produtos_usd": round(valor_produtos_usd, 2),
            "valor_produtos_brl": round(valor_produtos_brl, 2),
            "frete_internacional": round(frete_internacional, 2),
            "seguro_internacional_percentual": round(seguro_internacional_percentual * 100.0, 6),
            "seguro_internacional": round(seguro_internacional, 2),
            "valor_aduaneiro": round(valor_aduaneiro, 2),
            "base_icms": round(base_icms, 2),
            "base_icms_calculo": round(base_icms_calculo or 0.0, 2),
            "icms_retido_nfe": round(icms_retido_nfe, 2) if icms_retido_nfe is not None else None,
            "base_cst_mg": round(base_cst_mg, 2) if base_cst_mg is not None else None,
            "base_cst_pb": round(base_cst_pb, 2) if base_cst_pb is not None else None,
            "mva_minas": round(mva_minas, 6) if mva_minas is not None else None,
            "mva_ajustado": round(mva_ajustado, 6) if mva_ajustado is not None else None,
            "base_ipi": round(base_ipi, 2),
            "imposto_ii": round(imposto_ii, 2),
            "imposto_ipi": round(imposto_ipi, 2),
            "imposto_pis": round(imposto_pis, 2),
            "imposto_cofins": round(imposto_cofins, 2),
            "imposto_icms": round(imposto_icms, 2),
            "taxa_trade": round(taxa_trade, 2),
            "total_impostos": round(total_impostos, 2),
            "preco_final_total": round(preco_final_total, 2),
            "preco_final_unitario": round(preco_final_unitario, 2),
        },
    }


configure_impostos_simulador_runtime()

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
