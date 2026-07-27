"""Internal slice for promocoes_api."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
import pandas as pd
import requests
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.schemas import (
    PromoAnaliseApiRequest,
    PromoAplicarParticipacaoRequest,
    PromoAutomacaoConfigRequest,
)
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.promocoes_common import *
from backend.services.promocoes_core import *

_PROMOCOES_RUNTIME_GET_TENANT_ID = None


def configure_promocoes_api_analise_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    runtime_get_tenant_id = getattr(runtime, "get_tenant_id", None) if runtime is not None else None
    if callable(runtime_get_tenant_id):
        globals()["_PROMOCOES_RUNTIME_GET_TENANT_ID"] = runtime_get_tenant_id
    if peers:
        globals().update({name: value for name, value in peers.items() if name != "get_tenant_id"})
    return runtime


configure_promocoes_api_analise_runtime()


logger = logging.getLogger("jk_sistema")

PROMO_DESCONTO_ML_NAO_INFORMADO = "Não informado pela API"
PROMO_DESCONTO_ML_CONFIAVEL_KEY = "_jk_desconto_ml_confiavel"
PROMO_DESCONTO_ML_FONTE_KEY = "_jk_desconto_ml_fonte"
PROMO_DESCONTO_ML_TARIFA_FIELDS = (
    "sale_fee_discount",
    "sale_fee_discount_amount",
    "selling_fee_discount",
    "selling_fee_discount_amount",
    "fee_per_sale_discount",
    "fee_per_sale_discount_amount",
    "sale_fee_reduction",
    "sale_fee_reduction_amount",
)
PROMO_DESCONTO_ML_FONTES_CALCULADAS = {
    "seller_promotions.discount_meli_boosted_percentage_calculado",
    "seller_promotions.smart_split_reconciliado",
}


def _promo_desconto_ml_parametros_calculo(raw_promocao: dict) -> tuple[Any, Any, Any]:
    """Libera o fallback apenas quando o estado de boost nao e contraditorio."""
    if not isinstance(raw_promocao, dict):
        return None, None, None

    meli_pct = raw_promocao.get("meli_percentage")
    seller_pct = raw_promocao.get("seller_percentage")
    if "boosted_offer" not in raw_promocao:
        return meli_pct, seller_pct, None

    boosted_raw = raw_promocao.get("boosted_offer")
    if isinstance(boosted_raw, bool):
        boosted_ativo = boosted_raw
        boosted_valido = True
    elif isinstance(boosted_raw, (int, float)) and not isinstance(boosted_raw, bool):
        boosted_valido = boosted_raw in {0, 1}
        boosted_ativo = boosted_raw == 1
    else:
        boosted_txt = str(boosted_raw or "").strip().lower()
        boosted_valido = boosted_txt in {"true", "1", "yes", "sim", "false", "0", "no", "nao", "não"}
        boosted_ativo = boosted_txt in {"true", "1", "yes", "sim"}

    if not boosted_valido or not boosted_ativo:
        return None, None, None

    amount_raw = raw_promocao.get("discount_meli_boost_amount")
    amount = _parse_float_flex(amount_raw)
    if amount_raw not in (None, "") and (amount is None or amount <= 0):
        return None, None, None

    boost_pct = raw_promocao.get("discount_meli_boosted_percentage")
    if amount_raw in (None, ""):
        boost_pct_val = _parse_float_flex(boost_pct)
        if boost_pct_val is None or boost_pct_val <= 0:
            return None, None, None

    return (
        meli_pct,
        seller_pct,
        boost_pct,
    )


def _promo_desconto_ml_proveniencia(
    raw_promocao: dict,
    desconto_ml: Any,
    fonte_calculo: str = "",
) -> tuple[bool, str]:
    """Identifica a evidencia da API que autorizou o desconto monetario."""
    desconto = _parse_float_flex(desconto_ml)
    if desconto is None or not isinstance(raw_promocao, dict):
        return False, ""

    boosted_raw = raw_promocao.get("boosted_offer")
    boosted_txt = str(boosted_raw or "").strip().lower()
    boosted_ativo = (
        boosted_raw is True
        or (isinstance(boosted_raw, (int, float)) and not isinstance(boosted_raw, bool) and boosted_raw == 1)
        or boosted_txt in {"true", "1", "yes", "sim"}
    )
    boosted_inativo = (
        boosted_raw is False
        or (isinstance(boosted_raw, (int, float)) and not isinstance(boosted_raw, bool) and boosted_raw == 0)
        or boosted_txt in {"false", "0", "no", "nao", "não"}
    )
    boost_amount = _parse_float_flex(raw_promocao.get("discount_meli_boost_amount"))
    if boosted_ativo and boost_amount is not None and boost_amount > 0:
        return True, "seller_promotions.discount_meli_boost_amount"
    if boosted_inativo and abs(float(desconto)) <= 0.005:
        return True, "seller_promotions.boosted_offer"

    for chave in PROMO_DESCONTO_ML_TARIFA_FIELDS:
        valor = raw_promocao.get(chave)
        if isinstance(valor, dict):
            valor = valor.get("amount") if valor.get("amount") is not None else valor.get("value")
        desconto_tarifa = _parse_float_flex(valor)
        if (
            desconto_tarifa is not None
            and desconto_tarifa > 0
            and abs(round(float(desconto_tarifa), 2) - float(desconto)) <= 0.01
        ):
            return True, f"seller_promotions.{chave}"

    fonte_calculo = str(fonte_calculo or "").strip()
    if fonte_calculo in PROMO_DESCONTO_ML_FONTES_CALCULADAS and float(desconto) >= 0:
        return True, fonte_calculo

    seller_receives = raw_promocao.get("seller_receives")
    if isinstance(seller_receives, dict):
        seller_receives = seller_receives.get("amount") or seller_receives.get("value")
    if _parse_float_flex(seller_receives) is not None and float(desconto) > 0.005:
        return True, "seller_promotions.seller_receives_reconciliado"

    return False, ""


def _promo_desconto_ml_aplicar_ajuste(
    desconto_calculado: Any,
    fonte_calculo: str,
    desconto_ajustado: Any,
) -> tuple[Any, str]:
    """Preserva o fallback calculado apenas quando o ajuste nao o contradiz."""
    fonte = str(fonte_calculo or "").strip()
    if desconto_ajustado is None:
        if fonte in PROMO_DESCONTO_ML_FONTES_CALCULADAS:
            return desconto_calculado, fonte
        return None, fonte

    calculado = _parse_float_flex(desconto_calculado)
    ajustado = _parse_float_flex(desconto_ajustado)
    if (
        fonte in PROMO_DESCONTO_ML_FONTES_CALCULADAS
        and calculado is not None
        and ajustado is not None
        and abs(float(calculado) - float(ajustado)) > 0.01
    ):
        fonte = ""
    return desconto_ajustado, fonte


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    resolver = globals().get("_PROMOCOES_RUNTIME_GET_TENANT_ID")
    if resolver is None:
        runtime = globals().get("_runtime")
        resolver = getattr(runtime, "get_tenant_id", None) if runtime is not None else None
    if not callable(resolver) or resolver is globals().get("_PROMOCOES_PLACEHOLDER_GET_TENANT_ID"):
        raise RuntimeError("Promocoes API runtime was not configured.")
    result = resolver(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


_PROMOCOES_PLACEHOLDER_GET_TENANT_ID = get_tenant_id


PROMOCOES_ENDPOINTS = ('analisar_promo_via_api', 'analisar_promo_via_api_com_arquivos', 'iniciar_analise_promo_via_api', 'iniciar_analise_promo_via_api_com_arquivos', 'progresso_analise_promo_via_api_com_arquivos', 'cancelar_analise_promo_via_api_com_arquivos', 'promo_automacao_obter', 'promo_automacao_salvar', 'aplicar_participacoes_promocoes', 'aplicar_participacoes_promocoes_start', 'aplicar_participacoes_promocoes_job', 'analisar_promo_automatico')


def _promo_sanitizar_tolerancia_margem(valor: Any) -> float:
    tolerancia = _parse_float_flex(valor)
    if tolerancia is None:
        return 0.0
    return max(0.0, min(100.0, float(tolerancia)))


def _promo_margens_aprovadas(
    margem_a: Any,
    margem_b: Any,
    margem_minima: Any = 15.0,
    margem_tolerancia: Any = 0.0,
) -> bool:
    margem_a_pct = _parse_float_flex(margem_a)
    margem_b_pct = _parse_float_flex(margem_b)
    margem_minima_pct = _parse_float_flex(margem_minima)
    minimo = float(margem_minima_pct or 0.0)
    tolerancia = _promo_sanitizar_tolerancia_margem(margem_tolerancia)
    if margem_a_pct is None or margem_b_pct is None:
        return False
    if float(margem_a_pct) < minimo or float(margem_b_pct) < minimo:
        return False
    return float(margem_b_pct) + tolerancia + 1e-9 >= float(margem_a_pct)


def _promo_obter_fretes_por_preco(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    item: dict,
    preco_a,
    preco_b,
):
    """Consulta o custo de frete separadamente para cada preco promocional."""
    cfg_atual = cfg
    resultados = {}

    def _consultar(preco):
        nonlocal cfg_atual
        preco_num = _parse_float_flex(preco)
        if preco_num is None or preco_num <= 0:
            return {}
        chave = round(float(preco_num), 2)
        if chave in resultados:
            return resultados[chave]
        try:
            dados, cfg_atual = _ml_obter_frete_detalhado(
                client_id,
                loja,
                cfg_atual,
                item_id,
                item.get("shipping") or {},
                reconsultar_zero=True,
                contexto_frete=_ml_contexto_frete_item(item, preco_num),
            )
        except Exception:
            logger.exception("[PROMO API] Falha ao consultar frete do item %s no preco %.2f", item_id, preco_num)
            dados = {}
        resultados[chave] = dados or {}
        return resultados[chave]

    return _consultar(preco_a), _consultar(preco_b), cfg_atual


def _promo_ajustar_preco_painel_seller_campaign(
    raw_campanha: dict,
    promotion_type: str,
    promocoes_item,
) -> dict:
    """Replica a escolha de preco exibida pelo painel para campanha flexivel.

    Em candidato SELLER_CAMPAIGN, a API publica devolve um limite maximo e uma
    sugestao. O painel usa a sugestao somente quando ela coincide com uma
    oferta SMART ainda candidata/elegivel do anuncio; nos demais casos exibe o
    limite maximo. Precos efetivamente ativos continuam vindo de ``price``.
    """
    if not isinstance(raw_campanha, dict):
        return raw_campanha

    tipo = str(
        promotion_type
        or raw_campanha.get("promotion_type")
        or raw_campanha.get("type")
        or ""
    ).strip().upper()
    if tipo not in {"SELLER_CAMPAIGN", "SELLER_CAMPAIGNS"}:
        return raw_campanha

    subtipo = str(raw_campanha.get("sub_type") or raw_campanha.get("subType") or "").strip().upper()
    if subtipo and subtipo != "FLEXIBLE_PERCENTAGE":
        return raw_campanha

    status = str(
        raw_campanha.get("status")
        or raw_campanha.get("status_item")
        or raw_campanha.get("_jk_status_item_consultado")
        or ""
    ).strip().lower()
    if status not in {"candidate", "eligible"}:
        return raw_campanha

    preco_maximo = _parse_float_flex(raw_campanha.get("max_discounted_price"))
    preco_sugerido = _parse_float_flex(raw_campanha.get("suggested_discounted_price"))
    if preco_maximo is None or preco_maximo <= 0:
        return raw_campanha

    preco_painel = float(preco_maximo)
    fonte = "max_discounted_price"
    if preco_sugerido is not None and preco_sugerido > 0:
        for promocao in promocoes_item or []:
            if not isinstance(promocao, dict):
                continue
            tipo_item = str(promocao.get("promotion_type") or promocao.get("type") or "").strip().upper()
            status_item = str(promocao.get("status") or promocao.get("status_item") or "").strip().lower()
            preco_item = _parse_float_flex(promocao.get("price"))
            if (
                tipo_item == "SMART"
                and status_item in {"candidate", "eligible"}
                and preco_item is not None
                and abs(float(preco_item) - float(preco_sugerido)) <= 0.01
            ):
                preco_painel = float(preco_sugerido)
                fonte = "suggested_discounted_price_smart_candidate"
                break

    resultado = dict(raw_campanha)
    resultado["_jk_preco_painel_seller_campaign"] = round(preco_painel, 2)
    resultado["_jk_preco_painel_fonte"] = fonte
    return resultado


def analisar_promo_via_api(req: PromoAnaliseApiRequest, client_id: str = Depends(get_tenant_id)):
    """
    Compara duas promocoes/campanhas existentes do Mercado Livre
    usando dados da API e custo do produto no cadastro local.
    """
    promo_a = str(req.promocao_a_id or "").strip()
    promo_a_type = str(req.promocao_a_type or "").strip()
    if promo_a_type in {"-", "None", "null"}:
        promo_a_type = ""

    promo_b_ids = []
    if isinstance(req.promocao_b_ids, list):
        promo_b_ids.extend([str(v or "").strip() for v in req.promocao_b_ids])
    if req.promocao_b_id:
        promo_b_ids.append(str(req.promocao_b_id or "").strip())
    promo_b_ids = [p for p in promo_b_ids if p and p not in {"-", "None", "null"}]
    promo_b_ids = list(dict.fromkeys(promo_b_ids))

    promo_b_types = []
    if isinstance(req.promocao_b_types, list):
        promo_b_types = [("" if str(v or "").strip() in {"-", "None", "null"} else str(v or "").strip()) for v in req.promocao_b_types]
    elif req.promocao_b_type:
        v = str(req.promocao_b_type or "").strip()
        promo_b_types = ["" if v in {"-", "None", "null"} else v]

    promo_b_files = list(req.promocao_b_files or [])

    if not req.loja or not promo_a or not promo_b_ids:
        raise HTTPException(status_code=400, detail="Informe loja, Promocao 1 e ao menos uma Promocao 2 para comparar.")
    if any(promo_a == promo_b for promo_b in promo_b_ids):
        raise HTTPException(status_code=400, detail="Selecione Promocoes 2 diferentes da Promocao 1.")
    if len(promo_b_files) != len(promo_b_ids):
        raise HTTPException(status_code=400, detail="Envie um arquivo para cada Promocao 2 selecionada.")

    inicio = time.time()
    cfg = _obter_cfg_ml(client_id, req.loja)
    itens_a_refs, raw_a, cfg = _ml_listar_itens_promocao_com_raw(
        client_id,
        req.loja,
        cfg,
        promo_a,
        promotion_type=promo_a_type,
        usar_fallback_pesado=True,
        forcar_fallback_pesado=True,
        max_items=5000,
        buscar_detalhes=False,
    )
    ids_a = {str(x.get("id") or "").strip() for x in (itens_a_refs or []) if str(x.get("id") or "").strip()}

    promocoes_b_info = []
    ids_intersecao_global = set()
    for idx_b, promo_b in enumerate(promo_b_ids):
        promo_b_type = promo_b_types[idx_b] if idx_b < len(promo_b_types) else ""
        itens_b_refs, raw_b, cfg = _ml_listar_itens_promocao_multistatus_com_raw(
            client_id,
            req.loja,
            cfg,
            promo_b,
            promotion_type=promo_b_type,
            buscar_detalhes=False,
        )
        ids_b = {str(x.get("id") or "").strip() for x in (itens_b_refs or []) if str(x.get("id") or "").strip()}
        ids_intersecao = sorted(ids_a & ids_b)
        ids_intersecao_global.update(ids_intersecao)
        logger.info(
            f"[PROMO API] Filtro Promocao 2 + Promocao 1 ativa: "
            f"promo1={len(ids_a)} promo2={len(ids_b)} intersecao={len(ids_intersecao)} promo2_id={promo_b}"
        )
        promocoes_b_info.append({
            "promo_b": promo_b,
            "promo_b_type": promo_b_type,
            "ids_b": ids_b,
            "ids_intersecao": ids_intersecao,
            "raw_b": raw_b,
            "arquivo_nome": (promo_b_files[idx_b] if idx_b < len(promo_b_files) else ""),
        })

    itens_filtrados, cfg = _ml_buscar_itens_batch(client_id, req.loja, cfg, sorted(ids_intersecao_global))
    itens_por_id = {
        str(item.get("id") or "").strip(): item
        for item in (itens_filtrados or [])
        if str(item.get("id") or "").strip()
    }

    custos_por_sku, impostos_por_sku = _carregar_custos_impostos_cadastro_por_sku_loja(client_id, req.loja)

    def _montar_linha_item(item_id: str, item: dict, promo_b: str, promo_b_type: str, ids_b: set[str], raw_b: dict, arquivo_nome_b: str):
        item = itens_por_id[item_id]
        sku = _ml_extrair_sku(item) or str(item.get("seller_custom_field") or "").strip()
        sku_display = sku
        skus_variacoes = []
        variacoes = _ml_extrair_variacoes_resumo(item, client_id=client_id, loja=req.loja)
        if variacoes:
            skus_variacoes = [str(v.get("sku") or "").strip() for v in variacoes if str(v.get("sku") or "").strip() and str(v.get("sku") or "").strip() != "-"]
            if skus_variacoes:
                sku_display = " / ".join(list(dict.fromkeys(skus_variacoes))[:4])

        cfg_local = dict(cfg)
        price_info, cfg_local = _ml_obter_preco_detalhado(client_id, req.loja, cfg_local, item_id, fallback_price=item.get("price"))
        preco_atual = _parse_float_flex(price_info.get("price")) or _parse_float_flex(item.get("price")) or 0.0
        preco_base_anuncio = (
            _parse_float_flex(price_info.get("standard_price"))
            or _parse_float_flex(price_info.get("original_price"))
            or _parse_float_flex(item.get("original_price"))
            or _parse_float_flex(item.get("base_price"))
            or preco_atual
        )
        raw_a_item = raw_a.get(item_id, {})
        raw_b_item = raw_b.get(item_id, {})
        if promo_a_type and not raw_a_item:
            raw_a_item, cfg_local = _ml_obter_item_promocao_raw(client_id, req.loja, cfg_local, promo_a, promo_a_type, item_id)
        if promo_b_type and (not raw_b_item or _promo_status_item_promocao(raw_b_item) in {"candidate", "eligible"}):
            detalhe_b, cfg_local = _ml_obter_item_promocao_raw(client_id, req.loja, cfg_local, promo_b, promo_b_type, item_id)
            if detalhe_b:
                raw_b_item = detalhe_b
        raw_b_item, cfg_local = _ml_resolver_raw_promocao_equivalente_para_analise(
            client_id,
            req.loja,
            cfg_local,
            item_id,
            raw_b_item,
            promo_b,
            promo_b_type,
            preco_base_anuncio,
        )
        promocoes_item_a = None
        try:
            promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, req.loja, cfg_local, item_id)
            raw_a_item = _promo_ajustar_preco_painel_seller_campaign(
                raw_a_item,
                promo_a_type,
                promocoes_item_a,
            )
        except Exception:
            promocoes_item_a = None
        preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item, priorizar_percentual_total_api=True)
        if preco_a_raw is None and desc_a_raw is None:
            try:
                promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, req.loja, cfg_local, item_id)
                raw_a_fallback = _ml_encontrar_promocao_raw_item(promocoes_item_a, promo_a)
                if raw_a_fallback:
                    raw_a_item = _promo_ajustar_preco_painel_seller_campaign(
                        raw_a_fallback,
                        promo_a_type,
                        promocoes_item_a,
                    )
                    preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item, priorizar_percentual_total_api=True)
            except Exception:
                promocoes_item_a = None
        preco_b_raw, desc_b_raw = _ml_extrair_preco_promocao_raw(raw_b_item, priorizar_percentual_total_api=True)
        status_promo_a = _ml_classificar_status_promocao_entry(raw_a_item)
        if str(price_info.get("promotion_id") or "").strip().lower() == promo_a.lower():
            status_promo_a = "Ativo"
        if not status_promo_a:
            try:
                if promocoes_item_a is None:
                    promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, req.loja, cfg_local, item_id)
                status_promo_a = _ml_classificar_status_promocao_por_id(promocoes_item_a, promo_a)
            except Exception:
                status_promo_a = ""
        presente_a = status_promo_a == "Ativo"
        presente_a_programado = status_promo_a == "Programado"
        status_exibicao_promo_a = _ml_status_promocao_usuario_exibicao(status_promo_a)
        desconto_tarifa_ml = _ml_extrair_desconto_tarifa_promocao_raw(raw_b_item)
        preco_a = preco_a_raw or preco_base_anuncio or preco_atual
        preco_b = preco_b_raw or preco_atual or preco_base_anuncio

        desconto_a = _ml_resolver_percentual_desconto_campanha_raw(
            raw_a_item,
            preco_base_anuncio,
            preco_a_raw or preco_a,
            desc_a_raw,
        )
        desconto_b = _ml_resolver_percentual_desconto_campanha_raw(
            raw_b_item,
            preco_base_anuncio,
            preco_b_raw or preco_b,
            desc_b_raw,
        )
        if preco_a_raw is None and desconto_a is not None and preco_base_anuncio and preco_base_anuncio > 0:
            preco_a = round(float(preco_base_anuncio) * max(0.0, 1.0 - (float(desconto_a) / 100.0)), 2)
        if desconto_a is None and preco_base_anuncio and preco_a and preco_base_anuncio > 0:
            desconto_a = max(0.0, ((preco_base_anuncio - preco_a) / preco_base_anuncio) * 100.0)
        if desconto_b is None and preco_base_anuncio and preco_b and preco_base_anuncio > 0:
            desconto_b = max(0.0, ((preco_base_anuncio - preco_b) / preco_base_anuncio) * 100.0)

        shipping_data_a, shipping_data_b, cfg_local = _promo_obter_fretes_por_preco(
            client_id,
            req.loja,
            cfg_local,
            item_id,
            item,
            preco_a,
            preco_b,
        )
        frete_a_api_val = _parse_float_flex(shipping_data_a.get("shipping_cost"))
        frete_b_api_val = _parse_float_flex(shipping_data_b.get("shipping_cost"))
        frete_a_exato = bool(shipping_data_a.get("shipping_exact_for_price"))
        frete_b_exato = bool(shipping_data_b.get("shipping_exact_for_price"))
        fretes_contextuais_confiaveis = frete_a_exato and frete_b_exato
        frete_fallback_val = 0.0
        frete_a_val = frete_a_api_val if frete_a_api_val is not None else frete_fallback_val
        frete_b_val = frete_b_api_val if frete_b_api_val is not None else frete_fallback_val
        buyer_cost_a = _parse_float_flex(shipping_data_a.get("shipping_buyer_cost"))
        buyer_cost_b = _parse_float_flex(shipping_data_b.get("shipping_buyer_cost"))
        frete_gratis_a_api = bool(shipping_data_a.get("free_shipping")) or (buyer_cost_a is not None and buyer_cost_a <= 0)
        frete_gratis_b_api = bool(shipping_data_b.get("free_shipping")) or (buyer_cost_b is not None and buyer_cost_b <= 0)

        custo = _resolver_custo_medio_por_skus(custos_por_sku, skus_variacoes or [sku, sku_display])
        imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku, sku)
        if imposto_rate is None:
            imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku, sku_display)
        item_taxa_a = dict(item)
        item_taxa_a["price"] = preco_a
        fee_a, cfg_local = _ml_obter_taxas_anuncio(client_id, req.loja, cfg_local, item_taxa_a)
        item_taxa_b = dict(item)
        item_taxa_b["price"] = preco_b
        fee_b, cfg_local = _ml_obter_taxas_anuncio(client_id, req.loja, cfg_local, item_taxa_b)
        frete_gratis_a = bool(frete_gratis_a_api or (frete_a_api_val is None and preco_a is not None and preco_a >= 79.0))
        frete_gratis_b = bool(frete_gratis_b_api or (frete_b_api_val is None and preco_b is not None and preco_b >= 79.0))
        taxa_fixa_a = _parse_float_flex(fee_a.get("fixed_fee_amount"))
        taxa_fixa_b = _parse_float_flex(fee_b.get("fixed_fee_amount"))
        if taxa_fixa_a is None:
            taxa_fixa_a = _ml_estimar_taxa_fixa_por_preco(
                preco_a,
                domain_id=item.get("domain_id") or "",
                category_id=item.get("category_id") or "",
                listing_type_id=item.get("listing_type_id") or "",
            )
        if taxa_fixa_b is None:
            taxa_fixa_b = _ml_estimar_taxa_fixa_por_preco(
                preco_b,
                domain_id=item.get("domain_id") or "",
                category_id=item.get("category_id") or "",
                listing_type_id=item.get("listing_type_id") or "",
            )
        # Frete fica com o valor da API de shipping_options.
        # A taxa fixa do ML pertence ao detalhamento da tarifa, nao substitui frete.
        tarifa_a_val = _ml_extrair_tarifa_cobrada_promocao_raw(raw_a_item)
        if tarifa_a_val is None:
            tarifa_a_val = _parse_float_flex(fee_a.get("ad_cost"))
        tarifa_b_tmp = _parse_float_flex(fee_b.get("ad_cost"))
        if desconto_tarifa_ml is not None and tarifa_b_tmp is not None and desconto_tarifa_ml >= (tarifa_b_tmp * 0.8):
            # Alguns formatos chamam a prÃ³pria tarifa de sale_fee; nesse caso nÃ£o ÃƒÂ© o desconto.
            desconto_tarifa_ml = None
        meli_pct_calculo, seller_pct_calculo, boost_pct_calculo = _promo_desconto_ml_parametros_calculo(raw_b_item)
        desconto_tarifa_ml, desconto_ml_fonte_calculo = _calcular_desconto_ml_valor(
            desconto_atual=desconto_tarifa_ml,
            ml_pct=meli_pct_calculo,
            seller_pct=seller_pct_calculo,
            boost_pct=boost_pct_calculo,
            preco_base=preco_base_anuncio,
            preco_final_ml=preco_b,
            tarifa_base=tarifa_a_val,
            tarifa_ml=tarifa_b_tmp,
            desconto_atual_confiavel=True,
            retornar_fonte=True,
        )
        desconto_tarifa_ml_ajustado = _ml_ajustar_desconto_tarifa_recebivel_promocao(
            raw_b_item,
            desconto_tarifa_ml,
            preco_b,
            desconto_b,
            tarifa_b_tmp,
            frete_b_val,
            bool(shipping_data_b.get("shipping_exact_for_price")),
            shipping_data_b.get("shipping_price_context"),
            bool(fee_b.get("ad_cost_exact_for_price")),
            fee_b.get("ad_cost_price_context"),
            fee_b.get("ad_cost_source"),
            shipping_data_b.get("shipping_cost_retry_source"),
        )
        desconto_tarifa_ml, desconto_ml_fonte_calculo = _promo_desconto_ml_aplicar_ajuste(
            desconto_tarifa_ml,
            desconto_ml_fonte_calculo,
            desconto_tarifa_ml_ajustado,
        )
        desconto_ml_confiavel, desconto_ml_fonte = _promo_desconto_ml_proveniencia(
            raw_b_item,
            desconto_tarifa_ml,
            desconto_ml_fonte_calculo,
        )
        recebe_ml = None
        if frete_b_exato:
            recebe_ml = _ml_calcular_recebivel_promocao(
                raw_b_item,
                preco_b,
                tarifa_b_tmp,
                frete_b_val,
                desconto_tarifa_ml,
            )

        valor_liquido_a = None
        valor_liquido_b = None
        margem_a = None
        margem_b = None
        imposto_a = (preco_a * imposto_rate) if imposto_rate is not None else None
        imposto_b = (preco_b * imposto_rate) if imposto_rate is not None else None
        if custo is not None and fretes_contextuais_confiaveis:
            valor_liquido_a = preco_a - float(custo) - frete_a_val - (imposto_a or 0.0) - (tarifa_a_val or 0.0)
            valor_liquido_b = preco_b - float(custo) - frete_b_val - (imposto_b or 0.0) - (_parse_float_flex(fee_b.get("ad_cost")) or 0.0)
            if desconto_tarifa_ml is not None:
                valor_liquido_b += float(desconto_tarifa_ml)
            if preco_a:
                margem_a = (valor_liquido_a * 100.0) / preco_a
            if preco_b:
                margem_b = (valor_liquido_b * 100.0) / preco_b

        presente_b = item_id in ids_b
        if not _promo_margens_aprovadas(
            margem_a,
            margem_b,
            req.margem_minima,
            req.margem_tolerancia,
        ):
            decisao = "NÃ£o participar"
        else:
            decisao = "Participar"

        status = status_exibicao_promo_a
        return {
            "Tipo": fee_b.get("listing_type_name") or fee_a.get("listing_type_name") or _ml_nome_tipo_anuncio(item.get("listing_type_id")),
            "%": _format_pct_br(
                fee_b.get("sale_fee_pct")
                if fee_b.get("sale_fee_pct") is not None
                else (fee_a.get("sale_fee_pct") if fee_a.get("sale_fee_pct") is not None else fee_b.get("meli_fee_pct"))
            ),
            "SKU": sku_display or sku,
            "TÃ­tulo": str(item.get("title") or ""),
            "Frete": formatar_moeda_br(frete_a_val) if frete_a_exato else "A calcular",
            "Frete ML": formatar_moeda_br(frete_b_val) if frete_b_exato else "A calcular",
            "frete_exato": frete_a_exato,
            "frete_ml_exato": frete_b_exato,
            "frete_fonte": shipping_data_a.get("shipping_cost_retry_source") or "",
            "frete_ml_fonte": shipping_data_b.get("shipping_cost_retry_source") or "",
            "Frete Gratis": "SIM" if frete_gratis_a else "NÃƒO",
            "Frete Gratis ML": "SIM" if frete_gratis_b else "NÃƒO",
            "Custo": formatar_moeda_br(custo) if custo is not None else "",
            "Tarifa": formatar_moeda_br(tarifa_a_val) if tarifa_a_val is not None else "",
            "Tarifa ML": formatar_moeda_br(fee_b.get("ad_cost")) if fee_b.get("ad_cost") is not None else "",
            "MLB": item_id,
            "Campanha ML": f"{promo_a} x {promo_b}",
            "Arquivo Promocao 2": arquivo_nome_b or "",
            "% Fixa": _format_pct_br(desconto_a),
            "ML % Campanha": _format_pct_br(desconto_b),
            "PreÃ§o Final": formatar_moeda_br(preco_a),
            "deal_price": round(float(preco_b), 2) if preco_b is not None else None,
            "preco_promocional_ml": round(float(preco_b), 2) if preco_b is not None else None,
            "preco_final_ml_display": recebe_ml,
            "Imposto %": _format_pct_br(imposto_rate * 100.0) if imposto_rate is not None else "",
            "Imposto": formatar_moeda_br(imposto_a) if imposto_a is not None else "",
            "PreÃ§o Final ML": formatar_moeda_br(preco_b),
            "Imposto ML": formatar_moeda_br(imposto_b) if imposto_b is not None else "",
            "Desconto ML": (
                formatar_moeda_br(desconto_tarifa_ml)
                if desconto_ml_confiavel
                else PROMO_DESCONTO_ML_NAO_INFORMADO
            ),
            PROMO_DESCONTO_ML_CONFIAVEL_KEY: desconto_ml_confiavel,
            PROMO_DESCONTO_ML_FONTE_KEY: desconto_ml_fonte,
            "Valor LÃ­quido": formatar_moeda_br(valor_liquido_a) if valor_liquido_a is not None else "",
            "Valor lÃ­quido ML": formatar_moeda_br(valor_liquido_b) if valor_liquido_b is not None else "",
            "Status": status,
            "Margem": _format_pct_br(margem_a),
            "Margem ML": _format_pct_br(margem_b),
            "AÃ§Ã£o": decisao,
            "Participar ou nÃ£o": decisao,
            "offer_id": _ml_promocao_raw_offer_id(raw_b_item),
            "promotion_type": promo_b_type,
        }

    dados_analise = []
    for promo_b_info in promocoes_b_info:
        promo_b = promo_b_info["promo_b"]
        promo_b_type = promo_b_info["promo_b_type"]
        ids_b = promo_b_info["ids_b"]
        ids_intersecao = promo_b_info["ids_intersecao"]
        raw_b = promo_b_info["raw_b"]
        arquivo_nome_b = promo_b_info["arquivo_nome"]
        item_pairs = [(item_id, itens_por_id[item_id]) for item_id in ids_intersecao if item_id in itens_por_id]
        logger.info(f"[PROMO API] Comparando promocoes {promo_a} x {promo_b}: {len(item_pairs)} anuncios encontrados")
        if item_pairs:
            max_workers = min(10, max(2, len(item_pairs)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(_montar_linha_item, item_id, item, promo_b, promo_b_type, ids_b, raw_b, arquivo_nome_b): item_id
                    for item_id, item in item_pairs
                }
                linhas_por_id = {}
                for future in as_completed(future_map):
                    item_id = future_map[future]
                    try:
                        linha = future.result()
                        if linha:
                            linhas_por_id[item_id] = linha
                    except Exception as e:
                        logger.warning(f"[PROMO API] Falha ao montar item {item_id}: {e}")
                dados_analise.extend([linhas_por_id[item_id] for item_id, _item in item_pairs if item_id in linhas_por_id])
    dados_analise = [
        row for row in dados_analise
        if row
        and _promo_linha_status_ativo_ou_programado(row)
        and _promo_linha_pct_fixa_maior_que_zero(row)
    ]

    planilha_nome = _salvar_planilha_analise_promo(client_id, dados_analise, prefixo='analise_promo_api')
    df_payload = _build_df_planilha_analise_promo(dados_analise).fillna("")
    dados_payload = df_payload.to_dict(orient="records")
    meta_por_linha = {}
    for item in dados_analise:
        chave = (str(item.get("MLB") or "").strip(), str(item.get("Campanha ML") or "").strip())
        if not chave[0]:
            continue
        meta_por_linha[chave] = {
            "offer_id": str(item.get("offer_id") or "").strip(),
            "promotion_type": str(item.get("promotion_type") or "").strip(),
            "deal_price": _parse_float_flex(item.get("deal_price") or item.get("preco_promocional_ml")),
        }
    for row in dados_payload:
        row['Frete Gratis'] = row.get('Frete GrÃ¡tis', '')
        row['Frete Gratis ML'] = row.get('Frete GrÃ¡tis ML', '')
        meta = meta_por_linha.get((str(row.get("MLB") or "").strip(), str(row.get("Campanha ML") or "").strip())) or {}
        if meta.get("offer_id"):
            row["offer_id"] = meta["offer_id"]
        if meta.get("promotion_type"):
            row["promotion_type"] = meta["promotion_type"]
        if meta.get("deal_price") is not None:
            row["deal_price"] = meta["deal_price"]
    return {
        "success": True,
        "mode": "api_comparacao_promocoes",
        "loja": req.loja,
        "promocao_a_id": promo_a,
        "promocao_b_id": promo_b_ids[0] if promo_b_ids else "",
        "promocao_b_ids": promo_b_ids,
        "total": len(dados_analise),
        "tempo_segundos": round(time.time() - inicio, 1),
        "data": dados_payload,
        "planilha_gerada": planilha_nome,
    }


async def analisar_promo_via_api_sem_arquivos(
    loja: str,
    promocao_a_id: str,
    promocao_a_type: str = "",
    margem_minima: float = 15.0,
    margem_tolerancia: float = 0.0,
    promocoes_b_meta: str = "[]",
    client_id: str = "default",
    progress_hook: Optional[Callable[[dict], None]] = None,
):
    inicio = time.time()

    def _emit_progress(progress: int, message: str, phase: str = "running", details: Optional[dict] = None):
        if not progress_hook:
            return
        payload = {
            "progress": max(0, min(99, int(progress))),
            "message": str(message or "").strip() or "Processando analise de promocoes via API...",
            "phase": phase,
        }
        if isinstance(details, dict) and details:
            payload["details"] = details
        try:
            progress_hook(payload)
        except Exception:
            pass

    promo_a = str(promocao_a_id or "").strip()
    promo_a_type = str(promocao_a_type or "").strip()
    if promo_a_type in {"-", "None", "null"}:
        promo_a_type = ""

    try:
        promocoes_meta = json.loads(promocoes_b_meta or "[]")
    except Exception:
        raise HTTPException(status_code=400, detail="Metadados das Promocoes 2 invalidos.")

    if not loja or not promo_a:
        raise HTTPException(status_code=400, detail="Informe a loja e a Promocao 1.")
    if not isinstance(promocoes_meta, list) or not promocoes_meta:
        raise HTTPException(status_code=400, detail="Selecione ao menos uma Promocao 2.")

    promocoes_processar = []
    for meta in promocoes_meta:
        if not isinstance(meta, dict):
            continue
        promo_b = str(meta.get("promo_b_id") or meta.get("value") or "").strip()
        if not promo_b:
            continue
        promo_b_type = str(meta.get("promo_b_type") or meta.get("promoType") or "").strip()
        if promo_b_type in {"-", "None", "null"}:
            promo_b_type = ""
        promo_texto = str(meta.get("promo_texto") or meta.get("text") or promo_b).strip()
        active_count = _promo_meta_contagem(meta, "active_count", "activeCount", "active", "ativos")
        eligible_count = _promo_meta_contagem(meta, "eligible_count", "eligibleCount", "eligible", "elegiveis")
        promocoes_processar.append({
            "promo_b": promo_b,
            "promo_b_type": promo_b_type,
            "promo_texto": promo_texto or promo_b,
            "active_count": active_count,
            "eligible_count": eligible_count,
        })

    if not promocoes_processar:
        raise HTTPException(status_code=400, detail="Nenhuma Promocao 2 valida foi selecionada.")

    total_solicitacoes = len(promocoes_processar)
    _emit_progress(12, "Validando promocoes selecionadas na API do Mercado Livre...")

    cfg = _obter_cfg_ml(client_id, loja)
    _emit_progress(22, "Consultando anuncios da Promocao 1...")
    itens_a_refs, raw_a, cfg = _ml_listar_itens_promocao_com_raw(
        client_id,
        loja,
        cfg,
        promo_a,
        promotion_type=promo_a_type,
        usar_fallback_pesado=True,
        forcar_fallback_pesado=True,
        max_items=5000,
        buscar_detalhes=False,
        progress_callback=lambda msg: _emit_progress(24, msg, details={"promo": promo_a}),
    )
    ids_a = {str(x.get("id") or "").strip() for x in (itens_a_refs or []) if str(x.get("id") or "").strip()}
    _emit_progress(32, f"Promocao 1 carregada com {len(ids_a)} anuncio(s).")

    custos_por_sku, impostos_por_sku = _carregar_custos_impostos_cadastro_por_sku_loja(client_id, loja)

    def _erro_legivel(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail or "Erro na API do Mercado Livre")
        texto = str(exc or "").strip()
        return texto or exc.__class__.__name__

    def _montar_linha_api(item_id: str, item: dict | None, promo_meta: dict, raw_b: dict):
        item = dict(item or {})
        item.setdefault("id", item_id)

        raw_b_item = raw_b.get(item_id, {}) if isinstance(raw_b, dict) else {}
        cfg_local = dict(cfg)
        if promo_meta.get("promo_b_type") and (not raw_b_item or _promo_status_item_promocao(raw_b_item) in {"candidate", "eligible"}):
            try:
                detalhe_b, cfg_local = _ml_obter_item_promocao_raw(
                    client_id,
                    loja,
                    cfg_local,
                    promo_meta["promo_b"],
                    promo_meta.get("promo_b_type") or "",
                    item_id,
                )
                if detalhe_b:
                    raw_b_item = detalhe_b
            except Exception:
                if not raw_b_item:
                    raw_b_item = {}
        raw_b_item, cfg_local = _ml_resolver_raw_promocao_equivalente_para_analise(
            client_id,
            loja,
            cfg_local,
            item_id,
            raw_b_item,
            promo_meta["promo_b"],
            promo_meta.get("promo_b_type") or "",
            None,
        )

        raw_a_item = raw_a.get(item_id, {}) if isinstance(raw_a, dict) else {}
        if promo_a_type and not raw_a_item:
            try:
                raw_a_item, cfg_local = _ml_obter_item_promocao_raw(client_id, loja, cfg_local, promo_a, promo_a_type, item_id)
            except Exception:
                raw_a_item = {}

        if not item.get("title"):
            item["title"] = str(raw_b_item.get("title") or raw_b_item.get("name") or item_id)

        sku = _ml_extrair_sku(item) or str(item.get("seller_custom_field") or raw_b_item.get("seller_sku") or raw_b_item.get("sku") or "").strip()
        sku_display = sku
        skus_variacoes = []
        try:
            variacoes = _ml_extrair_variacoes_resumo(item, client_id=client_id, loja=loja)
        except Exception:
            variacoes = []
        if variacoes:
            skus_variacoes = [str(v.get("sku") or "").strip() for v in variacoes if str(v.get("sku") or "").strip() and str(v.get("sku") or "").strip() != "-"]
            if skus_variacoes:
                sku_display = " / ".join(list(dict.fromkeys(skus_variacoes))[:4])

        try:
            price_info, cfg_local = _ml_obter_preco_detalhado(client_id, loja, cfg_local, item_id, fallback_price=item.get("price"))
        except Exception:
            price_info = {}
        preco_atual = _parse_float_flex(price_info.get("price")) or _parse_float_flex(item.get("price")) or 0.0
        preco_base_anuncio = (
            _parse_float_flex(raw_b_item.get("original_price"))
            or _parse_float_flex(price_info.get("standard_price"))
            or _parse_float_flex(price_info.get("original_price"))
            or _parse_float_flex(item.get("original_price"))
            or _parse_float_flex(item.get("base_price"))
            or preco_atual
        )

        promocoes_item_a = None
        try:
            promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, loja, cfg_local, item_id)
            raw_a_item = _promo_ajustar_preco_painel_seller_campaign(
                raw_a_item,
                promo_a_type,
                promocoes_item_a,
            )
        except Exception:
            promocoes_item_a = None
        preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item, priorizar_percentual_total_api=True)
        if preco_a_raw is None and desc_a_raw is None:
            try:
                promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, loja, cfg_local, item_id)
                raw_a_fallback = _ml_encontrar_promocao_raw_item(promocoes_item_a, promo_a)
                if raw_a_fallback:
                    raw_a_item = _promo_ajustar_preco_painel_seller_campaign(
                        raw_a_fallback,
                        promo_a_type,
                        promocoes_item_a,
                    )
                    preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item, priorizar_percentual_total_api=True)
            except Exception:
                promocoes_item_a = None
        preco_b_raw, desc_b_raw = _ml_extrair_preco_promocao_raw(raw_b_item, priorizar_percentual_total_api=True)
        status_promo_a = _ml_classificar_status_promocao_entry(raw_a_item)
        if str(price_info.get("promotion_id") or "").strip().lower() == promo_a.lower():
            status_promo_a = "Ativo"
        if not status_promo_a:
            try:
                if promocoes_item_a is None:
                    promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, loja, cfg_local, item_id)
                status_promo_a = _ml_classificar_status_promocao_por_id(promocoes_item_a, promo_a)
            except Exception:
                status_promo_a = ""
        presente_a = status_promo_a == "Ativo"
        presente_a_programado = status_promo_a == "Programado"
        status_exibicao_promo_a = _ml_status_promocao_usuario_exibicao(status_promo_a)
        preco_a = preco_a_raw or preco_base_anuncio or preco_atual
        preco_b = preco_b_raw or _parse_float_flex(raw_b_item.get("price")) or preco_atual or preco_base_anuncio

        desconto_a = _ml_resolver_percentual_desconto_campanha_raw(
            raw_a_item,
            preco_base_anuncio,
            preco_a_raw or preco_a,
            desc_a_raw,
        )
        if not presente_a and not presente_a_programado and desconto_a is None and preco_a is not None:
            desconto_a = 0.0
        if preco_a_raw is None and desconto_a is not None and preco_base_anuncio and preco_base_anuncio > 0:
            preco_a = round(float(preco_base_anuncio) * max(0.0, 1.0 - (float(desconto_a) / 100.0)), 2)
        if desconto_a is None and preco_a is not None and preco_base_anuncio and preco_base_anuncio > 0:
            desconto_a = max(0.0, ((preco_base_anuncio - preco_a) / preco_base_anuncio) * 100.0)

        desconto_b = _ml_resolver_percentual_desconto_campanha_raw(
            raw_b_item,
            preco_base_anuncio,
            preco_b_raw or preco_b,
            desc_b_raw,
        )
        meli_pct = _parse_float_flex(raw_b_item.get("meli_percentage"))
        seller_pct = _parse_float_flex(raw_b_item.get("seller_percentage"))
        if desconto_b is None:
            desconto_b = seller_pct if seller_pct is not None else meli_pct
        if desconto_b is None and preco_base_anuncio and preco_b and preco_base_anuncio > 0:
            desconto_b = max(0.0, ((preco_base_anuncio - preco_b) / preco_base_anuncio) * 100.0)

        shipping_data_a, shipping_data_b, cfg_local = _promo_obter_fretes_por_preco(
            client_id,
            loja,
            cfg_local,
            item_id,
            item,
            preco_a,
            preco_b,
        )
        frete_a_api_val = _parse_float_flex(shipping_data_a.get("shipping_cost"))
        frete_b_api_val = _parse_float_flex(shipping_data_b.get("shipping_cost"))
        frete_a_exato = bool(shipping_data_a.get("shipping_exact_for_price"))
        frete_b_exato = bool(shipping_data_b.get("shipping_exact_for_price"))
        buyer_cost_a = _parse_float_flex(shipping_data_a.get("shipping_buyer_cost"))
        buyer_cost_b = _parse_float_flex(shipping_data_b.get("shipping_buyer_cost"))
        frete_gratis_a_api = bool(shipping_data_a.get("free_shipping")) or (buyer_cost_a is not None and buyer_cost_a <= 0)
        frete_gratis_b_api = bool(shipping_data_b.get("free_shipping")) or (buyer_cost_b is not None and buyer_cost_b <= 0)

        custo = _resolver_custo_medio_por_skus(custos_por_sku, skus_variacoes or [sku, sku_display])
        imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku, sku)
        if imposto_rate is None:
            imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku, sku_display)

        fee_a = {}
        tarifa_a_val = None
        taxa_fixa_a = None
        tipo_anuncio = _ml_nome_tipo_anuncio(item.get("listing_type_id"))
        if preco_a is not None:
            item_taxa_a = dict(item)
            item_taxa_a["price"] = preco_a
            try:
                fee_a, cfg_local = _ml_obter_taxas_anuncio(client_id, loja, cfg_local, item_taxa_a)
            except Exception:
                fee_a = {}
            tarifa_a_val = _parse_float_flex(fee_a.get("ad_cost"))
            tarifa_promocao_a = _ml_extrair_tarifa_cobrada_promocao_raw(raw_a_item)
            if tarifa_promocao_a is not None:
                tarifa_a_val = tarifa_promocao_a
            taxa_fixa_a = _parse_float_flex(fee_a.get("fixed_fee_amount"))
            tipo_anuncio = fee_a.get("listing_type_name") or tipo_anuncio

        item_taxa_b = dict(item)
        item_taxa_b["price"] = preco_b
        try:
            fee_b, cfg_local = _ml_obter_taxas_anuncio(client_id, loja, cfg_local, item_taxa_b)
        except Exception:
            fee_b = {}
        tarifa_b_val = _parse_float_flex(fee_b.get("ad_cost"))
        taxa_fixa_b = _parse_float_flex(fee_b.get("fixed_fee_amount"))
        tipo_anuncio = fee_b.get("listing_type_name") or tipo_anuncio

        if taxa_fixa_a is None and preco_a is not None:
            taxa_fixa_a = _ml_estimar_taxa_fixa_por_preco(preco_a, domain_id=item.get("domain_id") or "", category_id=item.get("category_id") or "", listing_type_id=item.get("listing_type_id") or "")
        if taxa_fixa_b is None:
            taxa_fixa_b = _ml_estimar_taxa_fixa_por_preco(preco_b, domain_id=item.get("domain_id") or "", category_id=item.get("category_id") or "", listing_type_id=item.get("listing_type_id") or "")

        frete_a_val = None
        frete_b_val = frete_b_api_val if frete_b_api_val is not None else 0.0
        frete_gratis_a = False
        frete_gratis_b = bool(frete_gratis_b_api or (frete_b_api_val is None and preco_b is not None and preco_b >= 79.0))
        if preco_a is not None:
            frete_a_val = frete_a_api_val if frete_a_api_val is not None else 0.0
            frete_gratis_a = bool(frete_gratis_a_api or (frete_a_api_val is None and preco_a is not None and preco_a >= 79.0))
        # Frete fica com o valor da API de shipping_options.
        # A taxa fixa do ML pertence ao detalhamento da tarifa, nao substitui frete.

        desconto_tarifa_ml = _ml_extrair_desconto_tarifa_promocao_raw(raw_b_item)
        meli_pct_calculo, seller_pct_calculo, boost_pct_calculo = _promo_desconto_ml_parametros_calculo(raw_b_item)
        desconto_tarifa_ml, desconto_ml_fonte_calculo = _calcular_desconto_ml_valor(
            desconto_atual=desconto_tarifa_ml,
            ml_pct=meli_pct_calculo,
            seller_pct=seller_pct_calculo,
            boost_pct=boost_pct_calculo,
            preco_base=preco_base_anuncio,
            preco_final_ml=preco_b,
            tarifa_base=tarifa_a_val,
            tarifa_ml=tarifa_b_val,
            desconto_atual_confiavel=True,
            retornar_fonte=True,
        )
        desconto_tarifa_ml_ajustado = _ml_ajustar_desconto_tarifa_recebivel_promocao(
            raw_b_item,
            desconto_tarifa_ml,
            preco_b,
            desconto_b,
            tarifa_b_val,
            frete_b_val,
            bool(shipping_data_b.get("shipping_exact_for_price")),
            shipping_data_b.get("shipping_price_context"),
            bool(fee_b.get("ad_cost_exact_for_price")),
            fee_b.get("ad_cost_price_context"),
            fee_b.get("ad_cost_source"),
            shipping_data_b.get("shipping_cost_retry_source"),
        )
        desconto_tarifa_ml, desconto_ml_fonte_calculo = _promo_desconto_ml_aplicar_ajuste(
            desconto_tarifa_ml,
            desconto_ml_fonte_calculo,
            desconto_tarifa_ml_ajustado,
        )
        desconto_ml_confiavel, desconto_ml_fonte = _promo_desconto_ml_proveniencia(
            raw_b_item,
            desconto_tarifa_ml,
            desconto_ml_fonte_calculo,
        )
        recebe_ml = None
        if frete_b_exato:
            recebe_ml = _ml_calcular_recebivel_promocao(
                raw_b_item,
                preco_b,
                tarifa_b_val,
                frete_b_val,
                desconto_tarifa_ml,
            )
        imposto_a = (preco_a * imposto_rate) if (imposto_rate is not None and preco_a is not None) else None
        imposto_b = (preco_b * imposto_rate) if imposto_rate is not None else None
        valor_liquido_a = None
        valor_liquido_b = None
        margem_a = None
        margem_b = None
        if custo is not None and preco_a is not None and frete_a_exato:
            valor_liquido_a = preco_a - float(custo) - (frete_a_val or 0.0) - (imposto_a or 0.0) - (tarifa_a_val or 0.0)
            if preco_a:
                margem_a = (valor_liquido_a * 100.0) / preco_a
        if custo is not None and frete_b_exato:
            valor_liquido_b = preco_b - float(custo) - (frete_b_val or 0.0) - (imposto_b or 0.0) - (tarifa_b_val or 0.0)
            if desconto_tarifa_ml is not None:
                valor_liquido_b += float(desconto_tarifa_ml)
            if preco_b:
                margem_b = (valor_liquido_b * 100.0) / preco_b

        if not _promo_margens_aprovadas(
            margem_a,
            margem_b,
            margem_minima,
            margem_tolerancia,
        ):
            decisao = "Nao participar"
        else:
            decisao = "Participar"

        if presente_a:
            status = "Ativo"
        elif presente_a_programado:
            status = "Programada"
        else:
            status = status_exibicao_promo_a
        return {
            "Tipo": tipo_anuncio,
            "%": _format_pct_br(
                fee_b.get("sale_fee_pct")
                if fee_b.get("sale_fee_pct") is not None
                else (fee_a.get("sale_fee_pct") if fee_a.get("sale_fee_pct") is not None else None)
            ),
            "SKU": sku_display or sku,
            "TÃ­tulo": str(item.get("title") or raw_b_item.get("title") or ""),
            "Frete": (formatar_moeda_br(frete_a_val) if frete_a_exato else "A calcular") if preco_a is not None else "",
            "Frete ML": formatar_moeda_br(frete_b_val) if frete_b_exato else "A calcular",
            "frete_exato": frete_a_exato,
            "frete_ml_exato": frete_b_exato,
            "frete_fonte": shipping_data_a.get("shipping_cost_retry_source") or "",
            "frete_ml_fonte": shipping_data_b.get("shipping_cost_retry_source") or "",
            "Frete Gratis": ("SIM" if frete_gratis_a else "NAO") if preco_a is not None else "",
            "Frete Gratis ML": "SIM" if frete_gratis_b else "NAO",
            "Custo": formatar_moeda_br(custo) if custo is not None else "",
            "Tarifa": formatar_moeda_br(tarifa_a_val) if tarifa_a_val is not None and preco_a is not None else "",
            "Tarifa ML": formatar_moeda_br(tarifa_b_val) if tarifa_b_val is not None else "",
            "MLB": item_id,
            "Campanha ML": promo_meta["promo_texto"] or promo_meta["promo_b"],
            "Arquivo Promocao 2": "",
            "% Fixa": _format_pct_br(desconto_a) if preco_a is not None else "",
            "ML % Campanha": _format_pct_br(desconto_b),
            "PreÃ§o Final": formatar_moeda_br(preco_a) if preco_a is not None else "",
            "deal_price": round(float(preco_b), 2) if preco_b is not None else None,
            "preco_promocional_ml": round(float(preco_b), 2) if preco_b is not None else None,
            "preco_final_ml_display": recebe_ml,
            "Imposto %": _format_pct_br(imposto_rate * 100.0) if imposto_rate is not None else "",
            "Imposto": formatar_moeda_br(imposto_a) if imposto_a is not None else "",
            "PreÃ§o Final ML": formatar_moeda_br(preco_b),
            "Imposto ML": formatar_moeda_br(imposto_b) if imposto_b is not None else "",
            "Desconto ML": (
                formatar_moeda_br(desconto_tarifa_ml)
                if desconto_ml_confiavel
                else PROMO_DESCONTO_ML_NAO_INFORMADO
            ),
            PROMO_DESCONTO_ML_CONFIAVEL_KEY: desconto_ml_confiavel,
            PROMO_DESCONTO_ML_FONTE_KEY: desconto_ml_fonte,
            "Valor LÃ­quido": formatar_moeda_br(valor_liquido_a) if valor_liquido_a is not None else "",
            "Valor lÃ­quido ML": formatar_moeda_br(valor_liquido_b) if valor_liquido_b is not None else "",
            "Status": status,
            "Margem": _format_pct_br(margem_a),
            "Margem ML": _format_pct_br(margem_b),
            "AÃ§Ã£o": decisao,
            "Participar ou nÃ£o": decisao,
            "offer_id": _ml_promocao_raw_offer_id(raw_b_item),
            "promotion_type": promo_meta.get("promo_b_type") or "",
        }

    analises = []
    for idx, promo_meta in enumerate(promocoes_processar, start=1):
        _emit_progress(
            36 + int((idx / max(1, total_solicitacoes)) * 18),
            f"Consultando Promocao 2 {idx}/{total_solicitacoes}: {promo_meta['promo_texto']}",
            details={"solicitacao_atual": idx, "solicitacoes_total": total_solicitacoes, "promo_b": promo_meta["promo_b"]},
        )
        try:
            itens_b_refs, raw_b, cfg = _ml_listar_itens_promocao_multistatus_com_raw(
                client_id,
                loja,
                cfg,
                promo_meta["promo_b"],
                promotion_type=promo_meta.get("promo_b_type") or "",
                max_items=5000,
                buscar_detalhes=False,
                expected_min_items=_promo_total_esperado_por_contagens(
                    promo_meta.get("active_count"),
                    promo_meta.get("eligible_count"),
                ),
                progress_callback=lambda msg, i=idx, meta=promo_meta: _emit_progress(
                    40 + int((i / max(1, total_solicitacoes)) * 12),
                    msg,
                    details={"solicitacao_atual": i, "solicitacoes_total": total_solicitacoes, "promo_b": meta["promo_b"]},
                ),
            )
        except Exception as exc:
            erro_txt = _erro_legivel(exc)
            logger.warning("[PROMO API SEM ARQUIVOS] Promocao %s falhou ao consultar itens: %s", promo_meta["promo_b"], erro_txt)
            _emit_progress(
                48 + int((idx / max(1, total_solicitacoes)) * 10),
                f"Promocao {promo_meta['promo_texto']} falhou. Continuando as demais...",
                details={"solicitacao_atual": idx, "solicitacoes_total": total_solicitacoes, "erro": erro_txt},
            )
            analises.append({
                "promo_b_id": promo_meta["promo_b"],
                "promo_b_type": promo_meta.get("promo_b_type") or "",
                "promo_b_nome": promo_meta["promo_texto"],
                "active_count": promo_meta.get("active_count"),
                "eligible_count": promo_meta.get("eligible_count"),
                "arquivo_nome": f"analise_api_{re.sub(r'[^A-Za-z0-9]+', '_', promo_meta['promo_b'])}.xlsx",
                "tab_label": promo_meta["promo_texto"] or promo_meta["promo_b"],
                "total": 0,
                "planilha_gerada": None,
                "data": [],
                "origem": "api",
                "error": erro_txt,
            })
            continue
        ids_b = [
            str(x.get("id") or "").strip()
            for x in (itens_b_refs or [])
            if str(x.get("id") or "").strip()
        ]
        ids_b = list(dict.fromkeys(ids_b))
        _emit_progress(
            56 + int((idx / max(1, total_solicitacoes)) * 12),
            f"Consultando dados de {len(ids_b)} anuncio(s) da Promocao 2...",
            details={"solicitacao_atual": idx, "solicitacoes_total": total_solicitacoes, "itens_solicitacao": len(ids_b)},
        )

        try:
            itens_filtrados, cfg = _ml_buscar_itens_batch(
                client_id,
                loja,
                cfg,
                ids_b,
                progress_callback=lambda msg: _emit_progress(
                    60 + int((idx / max(1, total_solicitacoes)) * 8),
                    msg,
                    details={"solicitacao_atual": idx, "solicitacoes_total": total_solicitacoes, "promo_b": promo_meta["promo_b"]},
                ),
            )
        except Exception as exc:
            erro_txt = _erro_legivel(exc)
            logger.warning("[PROMO API SEM ARQUIVOS] Promocao %s falhou ao buscar detalhes: %s", promo_meta["promo_b"], erro_txt)
            _emit_progress(
                62 + int((idx / max(1, total_solicitacoes)) * 10),
                f"Promocao {promo_meta['promo_texto']} falhou ao consultar detalhes. Continuando as demais...",
                details={"solicitacao_atual": idx, "solicitacoes_total": total_solicitacoes, "erro": erro_txt},
            )
            analises.append({
                "promo_b_id": promo_meta["promo_b"],
                "promo_b_type": promo_meta.get("promo_b_type") or "",
                "promo_b_nome": promo_meta["promo_texto"],
                "active_count": promo_meta.get("active_count"),
                "eligible_count": promo_meta.get("eligible_count"),
                "arquivo_nome": f"analise_api_{re.sub(r'[^A-Za-z0-9]+', '_', promo_meta['promo_b'])}.xlsx",
                "tab_label": promo_meta["promo_texto"] or promo_meta["promo_b"],
                "total": 0,
                "planilha_gerada": None,
                "data": [],
                "origem": "api",
                "error": erro_txt,
            })
            continue
        itens_por_id = {
            str(item.get("id") or "").strip(): item
            for item in (itens_filtrados or [])
            if str(item.get("id") or "").strip()
        }
        item_pairs = [(item_id, itens_por_id.get(item_id)) for item_id in ids_b]
        _emit_progress(
            70 + int((idx / max(1, total_solicitacoes)) * 20),
            f"Montando analise {idx}/{total_solicitacoes} com dados da API...",
            details={"solicitacao_atual": idx, "solicitacoes_total": total_solicitacoes, "itens_solicitacao": len(item_pairs)},
        )

        linhas = []
        if item_pairs:
            max_workers = min(10, max(2, len(item_pairs)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(_montar_linha_api, item_id, item, promo_meta, raw_b): item_id
                    for item_id, item in item_pairs
                }
                linhas_por_id = {}
                for future in as_completed(future_map):
                    item_id = future_map[future]
                    try:
                        linha = future.result()
                        if linha:
                            linhas_por_id[item_id] = linha
                    except Exception:
                        logger.exception("[PROMO API SEM ARQUIVOS] Falha ao montar item %s", item_id)
                linhas = [linhas_por_id[item_id] for item_id, _ in item_pairs if item_id in linhas_por_id]
        total_montadas = len(linhas)
        total_status_ok = sum(1 for row in linhas if _promo_linha_status_ativo_ou_programado(row))
        total_pct_ok = sum(1 for row in linhas if _promo_linha_pct_fixa_maior_que_zero(row))
        linhas = [
            row for row in linhas
            if _promo_linha_status_ativo_ou_programado(row)
            and _promo_linha_pct_fixa_maior_que_zero(row)
        ]
        logger.info(
            "[PROMO API SEM ARQUIVOS] %s: candidatos=%s, linhas_montadas=%s, status_ok=%s, pct_fixa_ok=%s, final=%s",
            promo_meta["promo_b"],
            len(item_pairs),
            total_montadas,
            total_status_ok,
            total_pct_ok,
            len(linhas),
        )

        planilha_nome = _salvar_planilha_analise_promo(
            client_id,
            linhas,
            prefixo=f"analise_promo_api_{re.sub(r'[^A-Za-z0-9]+', '_', promo_meta['promo_b'])[:40]}",
        )
        df_payload = _build_df_planilha_analise_promo(linhas).fillna("")
        dados_payload = df_payload.to_dict(orient="records")
        meta_por_linha = {
            str(item.get("MLB") or "").strip(): {
                "offer_id": str(item.get("offer_id") or "").strip(),
                "promotion_type": str(item.get("promotion_type") or "").strip(),
                "deal_price": _parse_float_flex(item.get("deal_price") or item.get("preco_promocional_ml")),
            }
            for item in linhas
            if str(item.get("MLB") or "").strip()
        }
        for row in dados_payload:
            row["Frete Gratis"] = row.get("Frete GrÃ¡tis", row.get("Frete Gratis", ""))
            row["Frete Gratis ML"] = row.get("Frete GrÃ¡tis ML", row.get("Frete Gratis ML", ""))
            meta = meta_por_linha.get(str(row.get("MLB") or "").strip()) or {}
            if meta.get("offer_id"):
                row["offer_id"] = meta["offer_id"]
            if meta.get("promotion_type"):
                row["promotion_type"] = meta["promotion_type"]
            if meta.get("deal_price") is not None:
                row["deal_price"] = meta["deal_price"]

        arquivo_nome = f"analise_api_{re.sub(r'[^A-Za-z0-9]+', '_', promo_meta['promo_b'])}.xlsx"
        analises.append({
            "promo_b_id": promo_meta["promo_b"],
            "promo_b_type": promo_meta.get("promo_b_type") or "",
            "promo_b_nome": promo_meta["promo_texto"],
            "active_count": promo_meta.get("active_count"),
            "eligible_count": promo_meta.get("eligible_count"),
            "arquivo_nome": arquivo_nome,
            "tab_label": promo_meta["promo_texto"] or promo_meta["promo_b"],
            "total": len(dados_payload),
            "planilha_gerada": planilha_nome,
            "data": dados_payload,
            "origem": "api",
        })

    _emit_progress(98, "Finalizando resultado da analise via API...")
    return {
        "success": True,
        "mode": "api_comparacao_promocoes_sem_arquivos",
        "loja": loja,
        "promocao_a_id": promo_a,
        "total_abas": len(analises),
        "tempo_segundos": round(time.time() - inicio, 1),
        "analises": analises,
        "data": analises[0]["data"] if analises else [],
        "planilha_gerada": analises[0]["planilha_gerada"] if analises else None,
    }


async def analisar_promo_via_api_com_arquivos(
    loja: str = Form(...),
    promocao_a_id: str = Form(...),
    promocao_a_type: str = Form(""),
    margem_minima: float = Form(15.0),
    margem_tolerancia: float = Form(0.0),
    promocoes_b_meta: str = Form(...),
    files: list[UploadFile] = File(...),
    client_id: str = Depends(get_tenant_id),
    progress_hook: Any = None,
):
    inicio = time.time()

    def _emit_progress(progress: int, message: str, phase: str = "running", details: Optional[dict] = None):
        if not progress_hook:
            return
        payload = {
            "progress": max(0, min(99, int(progress))),
            "message": str(message or "").strip() or "Processando analise de promocoes...",
            "phase": phase,
        }
        if isinstance(details, dict) and details:
            payload["details"] = details
        try:
            progress_hook(payload)
        except Exception:
            pass

    promo_a = str(promocao_a_id or "").strip()
    promo_a_type = str(promocao_a_type or "").strip()
    if promo_a_type in {"-", "None", "null"}:
        promo_a_type = ""

    try:
        promocoes_meta = json.loads(promocoes_b_meta or "[]")
    except Exception:
        raise HTTPException(status_code=400, detail="Metadados das Promocoes 2 invalidos.")

    if not loja or not promo_a:
        raise HTTPException(status_code=400, detail="Informe a loja e a Promocao 1.")
    if not isinstance(promocoes_meta, list) or not promocoes_meta:
        raise HTTPException(status_code=400, detail="Anexe ao menos um arquivo vinculado a uma Promocao 2.")
    if not files:
        raise HTTPException(status_code=400, detail="Envie os arquivos das Promocoes 2.")

    _emit_progress(12, "Validando vinculos entre Promocoes 2 e arquivos enviados...")

    arquivos_por_nome = {}
    for upload in files:
        nome = str(getattr(upload, "filename", "") or "").strip()
        if nome:
            arquivos_por_nome[nome.lower()] = upload

    promocoes_processar = []
    for meta in promocoes_meta:
        if not isinstance(meta, dict):
            continue
        arquivo_nome = str(meta.get("arquivo_nome") or "").strip()
        promo_b = str(meta.get("promo_b_id") or "").strip()
        if not arquivo_nome or not promo_b:
            continue
        upload = arquivos_por_nome.get(arquivo_nome.lower())
        if upload is None:
            raise HTTPException(status_code=400, detail=f"Arquivo nao encontrado no envio: {arquivo_nome}")
        promocoes_processar.append({
            "promo_b": promo_b,
            "promo_b_type": str(meta.get("promo_b_type") or "").strip(),
            "promo_texto": str(meta.get("promo_texto") or promo_b).strip(),
            "active_count": _promo_meta_contagem(meta, "active_count", "activeCount", "active", "ativos"),
            "eligible_count": _promo_meta_contagem(meta, "eligible_count", "eligibleCount", "eligible", "elegiveis"),
            "arquivo_nome": arquivo_nome,
            "upload": upload,
        })

    if not promocoes_processar:
        raise HTTPException(status_code=400, detail="Nenhum arquivo valido de Promocao 2 foi vinculado.")

    total_solicitacoes = len(promocoes_processar)
    _emit_progress(
        18,
        f"{total_solicitacoes} solicitacao(oes) de Promocao 2 recebida(s).",
        details={"solicitacoes_total": total_solicitacoes},
    )

    cfg = _obter_cfg_ml(client_id, loja)
    _emit_progress(24, "Consultando anuncios da Promocao 1...")
    itens_a_refs, raw_a, cfg = _ml_listar_itens_promocao_com_raw(
        client_id,
        loja,
        cfg,
        promo_a,
        promotion_type=promo_a_type,
        usar_fallback_pesado=True,
        forcar_fallback_pesado=True,
        max_items=5000,
        buscar_detalhes=False,
        progress_callback=lambda msg: _emit_progress(28, msg, details={"promo": promo_a}),
    )
    ids_a = {str(x.get("id") or "").strip() for x in (itens_a_refs or []) if str(x.get("id") or "").strip()}
    _emit_progress(32, f"Promocao 1 carregada com {len(ids_a)} anuncio(s).")

    custos_por_sku, impostos_por_sku = _carregar_custos_impostos_cadastro_por_sku_loja(client_id, loja)

    analises = []
    ids_globais = set()
    for idx, promo in enumerate(promocoes_processar, start=1):
        progress_leitura = 34 + int((idx / max(1, total_solicitacoes)) * 16)
        _emit_progress(
            progress_leitura,
            f"Lendo arquivo {idx}/{total_solicitacoes}: {promo['arquivo_nome']}",
            details={
                "solicitacao_atual": idx,
                "solicitacoes_total": total_solicitacoes,
                "arquivo": promo["arquivo_nome"],
            },
        )
        content = await promo["upload"].read()
        df = await asyncio.to_thread(ler_e_tratar_arquivo, content, promo["arquivo_nome"], "Promocoes")
        mapa_promo2, ordem_ids = _extrair_mapa_promocao2_arquivo(df)
        if not mapa_promo2:
            raise HTTPException(status_code=400, detail=f"Nao foi possivel localizar os MLBs no arquivo {promo['arquivo_nome']}.")
        promo["mapa_promo2"] = mapa_promo2
        promo["ordem_ids"] = ordem_ids
        ids_globais.update(ordem_ids)

    _emit_progress(55, f"Consultando dados de {len(ids_globais)} anuncio(s) na API do ML...")

    itens_filtrados, cfg = _ml_buscar_itens_batch(
        client_id,
        loja,
        cfg,
        sorted(ids_globais),
        progress_callback=lambda msg: _emit_progress(60, msg, details={"itens": len(ids_globais)}),
    )
    itens_por_id = {
        str(item.get("id") or "").strip(): item
        for item in (itens_filtrados or [])
        if str(item.get("id") or "").strip()
    }
    _emit_progress(66, f"Dados recebidos de {len(itens_por_id)} anuncio(s). Iniciando comparacao...")

    def _montar_linha_arquivo(item_id: str, item: dict | None, entrada_b: dict, promo_meta: dict):
        item = dict(item or {})
        item.setdefault("id", item_id)
        if not item.get("title"):
            item["title"] = entrada_b.get("TÃ­tulo") or item_id

        sku_base = _promo_txt_clean(entrada_b.get("SKU"))
        sku = _ml_extrair_sku(item) or str(item.get("seller_custom_field") or "").strip() or sku_base
        sku_display = sku or sku_base
        skus_variacoes = []
        try:
            variacoes = _ml_extrair_variacoes_resumo(item, client_id=client_id, loja=loja)
        except Exception:
            variacoes = []
        if variacoes:
            skus_variacoes = [str(v.get("sku") or "").strip() for v in variacoes if str(v.get("sku") or "").strip() and str(v.get("sku") or "").strip() != "-"]
            if skus_variacoes:
                sku_display = " / ".join(list(dict.fromkeys(skus_variacoes))[:4])

        cfg_local = dict(cfg)
        try:
            price_info, cfg_local = _ml_obter_preco_detalhado(client_id, loja, cfg_local, item_id, fallback_price=item.get("price"))
        except Exception:
            price_info = {}
        preco_atual = _parse_float_flex(price_info.get("price")) or _parse_float_flex(item.get("price")) or 0.0
        preco_base_anuncio = (
            _parse_float_flex(price_info.get("standard_price"))
            or _parse_float_flex(price_info.get("original_price"))
            or _parse_float_flex(item.get("original_price"))
            or _parse_float_flex(item.get("base_price"))
            or preco_atual
        )

        raw_a_item = raw_a.get(item_id, {})
        if promo_a_type and not raw_a_item:
            try:
                raw_a_item, cfg_local = _ml_obter_item_promocao_raw(client_id, loja, cfg_local, promo_a, promo_a_type, item_id)
            except Exception:
                raw_a_item = {}
        raw_b_item = {}
        promo_b_type = str(promo_meta.get("promo_b_type") or "").strip()
        promo_b_id = str(promo_meta.get("promo_b") or "").strip()
        if promo_b_type and promo_b_id:
            try:
                raw_b_item, cfg_local = _ml_obter_item_promocao_raw(client_id, loja, cfg_local, promo_b_id, promo_b_type, item_id)
            except Exception:
                raw_b_item = {}
        raw_b_item, cfg_local = _ml_resolver_raw_promocao_equivalente_para_analise(
            client_id,
            loja,
            cfg_local,
            item_id,
            raw_b_item,
            promo_b_id,
            promo_b_type,
            None,
        )

        promocoes_item_a = None
        try:
            promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, loja, cfg_local, item_id)
            raw_a_item = _promo_ajustar_preco_painel_seller_campaign(
                raw_a_item,
                promo_a_type,
                promocoes_item_a,
            )
        except Exception:
            promocoes_item_a = None
        preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item, priorizar_percentual_total_api=True)
        if preco_a_raw is None and desc_a_raw is None:
            try:
                promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, loja, cfg_local, item_id)
                raw_a_fallback = _ml_encontrar_promocao_raw_item(promocoes_item_a, promo_a)
                if raw_a_fallback:
                    raw_a_item = _promo_ajustar_preco_painel_seller_campaign(
                        raw_a_fallback,
                        promo_a_type,
                        promocoes_item_a,
                    )
                    preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item, priorizar_percentual_total_api=True)
            except Exception:
                promocoes_item_a = None
        preco_b = _parse_float_flex(entrada_b.get("PreÃ§o Final ML")) or preco_atual or preco_base_anuncio
        status_promo_a = _ml_classificar_status_promocao_entry(raw_a_item)
        if str(price_info.get("promotion_id") or "").strip().lower() == promo_a.lower():
            status_promo_a = "Ativo"
        if not status_promo_a:
            try:
                if promocoes_item_a is None:
                    promocoes_item_a, cfg_local = _ml_obter_promocoes_item(client_id, loja, cfg_local, item_id)
                status_promo_a = _ml_classificar_status_promocao_por_id(promocoes_item_a, promo_a)
            except Exception:
                status_promo_a = ""
        presente_a_ativo = status_promo_a == "Ativo"
        presente_a_programado = status_promo_a == "Programado"
        status_exibicao_promo_a = _ml_status_promocao_usuario_exibicao(status_promo_a)
        preco_a = preco_a_raw or preco_base_anuncio or preco_atual

        desconto_a = _ml_resolver_percentual_desconto_campanha_raw(
            raw_a_item,
            preco_base_anuncio,
            preco_a_raw or preco_a,
            desc_a_raw,
        )
        desconto_b = _ml_resolver_percentual_desconto_campanha_raw(
            raw_b_item,
            preco_base_anuncio,
            preco_b,
            entrada_b.get("ML % Campanha"),
        )
        if not presente_a_ativo and not presente_a_programado and desconto_a is None and preco_a is not None:
            desconto_a = 0.0
        if preco_a_raw is None and desconto_a is not None and preco_base_anuncio and preco_base_anuncio > 0:
            preco_a = round(float(preco_base_anuncio) * max(0.0, 1.0 - (float(desconto_a) / 100.0)), 2)
        if desconto_a is None and preco_base_anuncio and preco_a and preco_base_anuncio > 0:
            desconto_a = max(0.0, ((preco_base_anuncio - preco_a) / preco_base_anuncio) * 100.0)
        if desconto_b is None and preco_base_anuncio and preco_b and preco_base_anuncio > 0:
            desconto_b = max(0.0, ((preco_base_anuncio - preco_b) / preco_base_anuncio) * 100.0)

        shipping_data_a, shipping_data_b, cfg_local = _promo_obter_fretes_por_preco(
            client_id,
            loja,
            cfg_local,
            item_id,
            item,
            preco_a,
            preco_b,
        )
        frete_a_api_val = _parse_float_flex(shipping_data_a.get("shipping_cost"))
        frete_b_api_val = _parse_float_flex(shipping_data_b.get("shipping_cost"))
        frete_a_exato = bool(shipping_data_a.get("shipping_exact_for_price"))
        frete_b_exato = bool(shipping_data_b.get("shipping_exact_for_price"))
        buyer_cost_a = _parse_float_flex(shipping_data_a.get("shipping_buyer_cost"))
        buyer_cost_b = _parse_float_flex(shipping_data_b.get("shipping_buyer_cost"))
        frete_gratis_a_api = bool(shipping_data_a.get("free_shipping")) or (buyer_cost_a is not None and buyer_cost_a <= 0)
        frete_gratis_b_api = bool(shipping_data_b.get("free_shipping")) or (buyer_cost_b is not None and buyer_cost_b <= 0)

        custo = _resolver_custo_medio_por_skus(custos_por_sku, skus_variacoes or [sku, sku_display, sku_base])
        imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku, sku)
        if imposto_rate is None:
            imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku, sku_display)

        tarifa_a_val = None
        tarifa_b_val = None
        taxa_fixa_a = None
        taxa_fixa_b = None
        tipo_anuncio = _ml_nome_tipo_anuncio(item.get("listing_type_id"))
        pct_taxa = None

        if preco_a is not None:
            item_taxa_a = dict(item)
            item_taxa_a["price"] = preco_a
            try:
                fee_a, cfg_local = _ml_obter_taxas_anuncio(client_id, loja, cfg_local, item_taxa_a)
            except Exception:
                fee_a = {}
            tarifa_a_val = _parse_float_flex(fee_a.get("ad_cost"))
            tarifa_promocao_a = _ml_extrair_tarifa_cobrada_promocao_raw(raw_a_item)
            if tarifa_promocao_a is not None:
                tarifa_a_val = tarifa_promocao_a
            taxa_fixa_a = _parse_float_flex(fee_a.get("fixed_fee_amount"))
            tipo_anuncio = fee_a.get("listing_type_name") or tipo_anuncio
            pct_taxa = fee_a.get("sale_fee_pct")
        else:
            fee_a = {}

        item_taxa_b = dict(item)
        item_taxa_b["price"] = preco_b
        try:
            fee_b, cfg_local = _ml_obter_taxas_anuncio(client_id, loja, cfg_local, item_taxa_b)
        except Exception:
            fee_b = {}
        tarifa_b_val = _parse_float_flex(fee_b.get("ad_cost"))
        taxa_fixa_b = _parse_float_flex(fee_b.get("fixed_fee_amount"))
        tipo_anuncio = fee_b.get("listing_type_name") or tipo_anuncio
        pct_taxa = fee_b.get("sale_fee_pct") if fee_b.get("sale_fee_pct") is not None else pct_taxa

        if taxa_fixa_a is None and preco_a is not None:
            taxa_fixa_a = _ml_estimar_taxa_fixa_por_preco(preco_a, domain_id=item.get("domain_id") or "", category_id=item.get("category_id") or "", listing_type_id=item.get("listing_type_id") or "")
        if taxa_fixa_b is None:
            taxa_fixa_b = _ml_estimar_taxa_fixa_por_preco(preco_b, domain_id=item.get("domain_id") or "", category_id=item.get("category_id") or "", listing_type_id=item.get("listing_type_id") or "")

        frete_a_val = None
        frete_b_val = frete_b_api_val if frete_b_api_val is not None else 0.0
        frete_gratis_a = False
        frete_gratis_b = bool(frete_gratis_b_api or (frete_b_api_val is None and preco_b is not None and preco_b >= 79.0))
        if preco_a is not None:
            frete_a_val = frete_a_api_val if frete_a_api_val is not None else 0.0
            frete_gratis_a = bool(frete_gratis_a_api or (frete_a_api_val is None and preco_a is not None and preco_a >= 79.0))

        # Frete fica com o valor da API de shipping_options.
        # A taxa fixa do ML pertence ao detalhamento da tarifa, nao substitui frete.

        meli_pct_calculo, seller_pct_calculo, boost_pct_calculo = _promo_desconto_ml_parametros_calculo(raw_b_item)
        desconto_tarifa_ml, desconto_ml_fonte_calculo = _calcular_desconto_ml_valor(
            desconto_atual=entrada_b.get("Desconto ML"),
            ml_pct=meli_pct_calculo,
            seller_pct=seller_pct_calculo,
            boost_pct=boost_pct_calculo,
            preco_base=preco_base_anuncio,
            preco_final_ml=preco_b,
            tarifa_base=tarifa_a_val,
            tarifa_ml=tarifa_b_val,
            retornar_fonte=True,
        )
        desconto_tarifa_ml_ajustado = _ml_ajustar_desconto_tarifa_recebivel_promocao(
            raw_b_item,
            desconto_tarifa_ml,
            preco_b,
            desconto_b,
            tarifa_b_val,
            frete_b_val,
            bool(shipping_data_b.get("shipping_exact_for_price")),
            shipping_data_b.get("shipping_price_context"),
            bool(fee_b.get("ad_cost_exact_for_price")),
            fee_b.get("ad_cost_price_context"),
            fee_b.get("ad_cost_source"),
            shipping_data_b.get("shipping_cost_retry_source"),
        )
        desconto_tarifa_ml, desconto_ml_fonte_calculo = _promo_desconto_ml_aplicar_ajuste(
            desconto_tarifa_ml,
            desconto_ml_fonte_calculo,
            desconto_tarifa_ml_ajustado,
        )
        desconto_ml_confiavel, desconto_ml_fonte = _promo_desconto_ml_proveniencia(
            raw_b_item,
            desconto_tarifa_ml,
            desconto_ml_fonte_calculo,
        )
        recebe_ml = None
        if frete_b_exato:
            recebe_ml = _ml_calcular_recebivel_promocao(
                raw_b_item,
                preco_b,
                tarifa_b_val,
                frete_b_val,
                desconto_tarifa_ml,
            )
        imposto_a = (preco_a * imposto_rate) if (imposto_rate is not None and preco_a is not None) else None
        imposto_b = (preco_b * imposto_rate) if imposto_rate is not None else None
        valor_liquido_a = None
        valor_liquido_b = None
        margem_a = None
        margem_b = None
        if custo is not None and preco_a is not None and frete_a_exato:
            valor_liquido_a = preco_a - float(custo) - (frete_a_val or 0.0) - (imposto_a or 0.0) - (tarifa_a_val or 0.0)
            if preco_a:
                margem_a = (valor_liquido_a * 100.0) / preco_a
        if custo is not None and frete_b_exato:
            valor_liquido_b = preco_b - float(custo) - (frete_b_val or 0.0) - (imposto_b or 0.0) - (tarifa_b_val or 0.0)
            if desconto_tarifa_ml is not None:
                valor_liquido_b += float(desconto_tarifa_ml)
            if preco_b:
                margem_b = (valor_liquido_b * 100.0) / preco_b

        if presente_a_ativo:
            status = "Ativo"
        elif presente_a_programado:
            status = "Programada"
        else:
            status = status_exibicao_promo_a

        tem_valores_comparacao = (
            custo is not None
            and preco_a not in (None, 0)
            and preco_b not in (None, 0)
            and margem_a is not None
            and tarifa_b_val is not None
            and valor_liquido_b is not None
            and margem_b is not None
        )
        if not tem_valores_comparacao:
            decisao = "NÃ£o participar"
        elif not _promo_margens_aprovadas(
            margem_a,
            margem_b,
            margem_minima,
            margem_tolerancia,
        ):
            decisao = "NÃ£o participar"
        else:
            decisao = "Participar"

        return {
            "Tipo": tipo_anuncio,
            "%": _format_pct_br(
                fee_b.get("sale_fee_pct")
                if fee_b.get("sale_fee_pct") is not None
                else (fee_a.get("sale_fee_pct") if fee_a.get("sale_fee_pct") is not None else pct_taxa)
            ),
            "SKU": sku_display or sku_base,
            "TÃ­tulo": str(item.get("title") or entrada_b.get("TÃ­tulo") or ""),
            "Frete": (formatar_moeda_br(frete_a_val) if frete_a_exato else "A calcular") if preco_a is not None else "",
            "Frete ML": formatar_moeda_br(frete_b_val) if frete_b_exato else "A calcular",
            "frete_exato": frete_a_exato,
            "frete_ml_exato": frete_b_exato,
            "frete_fonte": shipping_data_a.get("shipping_cost_retry_source") or "",
            "frete_ml_fonte": shipping_data_b.get("shipping_cost_retry_source") or "",
            "Frete Gratis": ("SIM" if frete_gratis_a else "NÃƒO") if preco_a is not None else "",
            "Frete Gratis ML": "SIM" if frete_gratis_b else "NÃƒO",
            "Custo": formatar_moeda_br(custo) if custo is not None else "",
            "Tarifa": formatar_moeda_br(tarifa_a_val) if tarifa_a_val is not None and preco_a is not None else "",
            "Tarifa ML": formatar_moeda_br(tarifa_b_val) if tarifa_b_val is not None else "",
            "MLB": item_id,
            "Campanha ML": promo_meta["promo_texto"] or promo_meta["promo_b"],
            "Arquivo Promocao 2": promo_meta["arquivo_nome"],
            "% Fixa": _format_pct_br(desconto_a) if preco_a is not None else "",
            "ML % Campanha": _format_pct_br(desconto_b),
            "PreÃ§o Final": formatar_moeda_br(preco_a) if preco_a is not None else "",
            "deal_price": round(float(preco_b), 2) if preco_b is not None else None,
            "preco_promocional_ml": round(float(preco_b), 2) if preco_b is not None else None,
            "preco_final_ml_display": recebe_ml,
            "Imposto %": _format_pct_br(imposto_rate * 100.0) if imposto_rate is not None else "",
            "Imposto": formatar_moeda_br(imposto_a) if imposto_a is not None else "",
            "PreÃ§o Final ML": formatar_moeda_br(preco_b),
            "Imposto ML": formatar_moeda_br(imposto_b) if imposto_b is not None else "",
            "Desconto ML": (
                formatar_moeda_br(desconto_tarifa_ml)
                if desconto_ml_confiavel
                else PROMO_DESCONTO_ML_NAO_INFORMADO
            ),
            PROMO_DESCONTO_ML_CONFIAVEL_KEY: desconto_ml_confiavel,
            PROMO_DESCONTO_ML_FONTE_KEY: desconto_ml_fonte,
            "Valor LÃ­quido": formatar_moeda_br(valor_liquido_a) if valor_liquido_a is not None else "",
            "Valor lÃ­quido ML": formatar_moeda_br(valor_liquido_b) if valor_liquido_b is not None else "",
            "Status": status,
            "Margem": _format_pct_br(margem_a),
            "Margem ML": _format_pct_br(margem_b),
            "AÃ§Ã£o": decisao,
            "Participar ou nÃ£o": decisao,
        }

    for idx, promo_meta in enumerate(promocoes_processar, start=1):
        mapa_promo2 = promo_meta["mapa_promo2"]
        ordem_ids = promo_meta["ordem_ids"]
        item_pairs = [(item_id, itens_por_id.get(item_id)) for item_id in ordem_ids]
        progress_comp = 68 + int((idx / max(1, total_solicitacoes)) * 25)
        _emit_progress(
            progress_comp,
            f"Processando solicitacao {idx}/{total_solicitacoes}: {promo_meta['promo_texto'] or promo_meta['promo_b']}",
            details={
                "solicitacao_atual": idx,
                "solicitacoes_total": total_solicitacoes,
                "itens_solicitacao": len(item_pairs),
                "arquivo": promo_meta["arquivo_nome"],
            },
        )
        logger.info(f"[PROMO API ARQUIVOS] Promocao 1={promo_a} Arquivo={promo_meta['arquivo_nome']} itens={len(item_pairs)}")
        linhas = []
        if item_pairs:
            max_workers = min(10, max(2, len(item_pairs)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(_montar_linha_arquivo, item_id, item, mapa_promo2.get(item_id, {}), promo_meta): item_id
                    for item_id, item in item_pairs
                }
                linhas_por_id = {}
                for future in as_completed(future_map):
                    item_id = future_map[future]
                    try:
                        linha = future.result()
                        if linha:
                            linhas_por_id[item_id] = linha
                    except Exception as e:
                        logger.warning(f"[PROMO API ARQUIVOS] Falha ao montar item {item_id}: {e}")
                linhas = [linhas_por_id[item_id] for item_id, _ in item_pairs if item_id in linhas_por_id]
        linhas = [
            row for row in linhas
            if _promo_linha_status_ativo_ou_programado(row)
            and _promo_linha_pct_fixa_maior_que_zero(row)
        ]

        planilha_nome = _salvar_planilha_analise_promo(
            client_id,
            linhas,
            prefixo=f"analise_promo_api_{re.sub(r'[^A-Za-z0-9]+', '_', promo_meta['promo_b'])[:40]}",
        )
        df_payload = _build_df_planilha_analise_promo(linhas).fillna("")
        dados_payload = df_payload.to_dict(orient="records")
        meta_por_linha = {
            str(item.get("MLB") or "").strip(): {
                "deal_price": _parse_float_flex(item.get("deal_price") or item.get("preco_promocional_ml")),
            }
            for item in linhas
            if str(item.get("MLB") or "").strip()
        }
        for row in dados_payload:
            row["Frete Gratis"] = row.get("Frete GrÃ¡tis", row.get("Frete Gratis", ""))
            row["Frete Gratis ML"] = row.get("Frete GrÃ¡tis ML", row.get("Frete Gratis ML", ""))
            meta = meta_por_linha.get(str(row.get("MLB") or "").strip()) or {}
            if meta.get("deal_price") is not None:
                row["deal_price"] = meta["deal_price"]

        analises.append({
            "promo_b_id": promo_meta["promo_b"],
            "promo_b_type": promo_meta["promo_b_type"],
            "promo_b_nome": promo_meta["promo_texto"],
            "active_count": promo_meta.get("active_count"),
            "eligible_count": promo_meta.get("eligible_count"),
            "arquivo_nome": promo_meta["arquivo_nome"],
            "tab_label": promo_meta["promo_texto"] or promo_meta["arquivo_nome"],
            "total": len(dados_payload),
            "planilha_gerada": planilha_nome,
            "data": dados_payload,
        })

    _emit_progress(98, "Finalizando resultado da analise...")

    return {
        "success": True,
        "mode": "api_comparacao_promocoes_arquivos",
        "loja": loja,
        "promocao_a_id": promo_a,
        "total_abas": len(analises),
        "tempo_segundos": round(time.time() - inicio, 1),
        "analises": analises,
        "data": analises[0]["data"] if analises else [],
        "planilha_gerada": analises[0]["planilha_gerada"] if analises else None,
    }


async def analisar_promo_automatico(
    loja: str,
    margem_minima: float = 15.0,
    offset: int = 0,
    limit: int = 100,
    client_id: str = Depends(get_tenant_id),
):
    """
    Gera analise de promocao sem upload de planilhas,
    usando anuncios ativos do Mercado Livre + custos do cadastro local.
    """
    payload = listar_anuncios_mercado_livre(client_id=client_id, loja=loja, offset=offset, limit=limit)
    anuncios = payload.get("results", []) if isinstance(payload, dict) else []

    custos_por_sku, _ = _carregar_custos_impostos_cadastro_por_sku_loja(client_id, loja)
    dados_analise = []

    for item in anuncios:
        sku = str(item.get("sku") or "").strip()
        preco_atual = _parse_float_flex(item.get("price")) or 0.0
        preco_original = _parse_float_flex(item.get("original_price"))
        if preco_original is None or preco_original <= 0:
            preco_original = preco_atual

        has_promotion = bool(item.get("has_promotion"))
        desconto_pct = _parse_float_flex(item.get("discount_pct"))
        if desconto_pct is None and preco_original > 0 and preco_atual > 0 and preco_original > preco_atual:
            desconto_pct = ((preco_original - preco_atual) / preco_original) * 100.0
        desconto_pct = desconto_pct or 0.0

        custo = _resolver_custo_medio_por_skus(custos_por_sku, sku)
        margem_pct = None
        if custo is not None and preco_atual > 0:
            margem_pct = ((preco_atual - float(custo)) / preco_atual) * 100.0

        if margem_pct is None:
            decisao = "Revisar custo"
        elif margem_pct < margem_minima:
            decisao = "NÃ£o participar"
        else:
            decisao = "Participar"

        situacao = "Promocao ativa" if has_promotion else "Sem promocao"
        dados_analise.append({
            "MLB": str(item.get("id") or ""),
            "SKU": sku,
            "TÃ­tulo": str(item.get("title") or ""),
            "SituaÃƒÂ§ÃƒÂ£o": situacao,
            "Desconto": _format_pct_br(desconto_pct),
            "Custo": formatar_moeda_br(custo) if custo is not None else "",
            "M 21 Fixa": formatar_moeda_br(preco_original),
            "M ML": formatar_moeda_br(preco_atual),
            "Margem %": _format_pct_br(margem_pct) if margem_pct is not None else "",
            "Participar ou nÃ£o": decisao,
        })

    planilha_nome = _salvar_planilha_analise_promo(client_id, dados_analise, prefixo='analise_promo_auto')

    return {
        "success": True,
        "mode": "auto_ml",
        "loja": loja,
        "margem_minima": margem_minima,
        "total": payload.get("total", len(dados_analise)) if isinstance(payload, dict) else len(dados_analise),
        "data": dados_analise,
        "planilha_gerada": planilha_nome,
    }

PEER_EXPORTS = ['logger', 'get_tenant_id', 'PROMOCOES_ENDPOINTS', 'analisar_promo_via_api', 'analisar_promo_via_api_sem_arquivos', 'analisar_promo_via_api_com_arquivos', 'analisar_promo_automatico']
__all__ = PEER_EXPORTS + ["configure_promocoes_api_analise_runtime"]

configure_promocoes_api_analise_runtime()
