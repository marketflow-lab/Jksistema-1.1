"""Internal slice for mercadolivre_legacy_core."""

from __future__ import annotations

from __future__ import annotations
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


def configure_mercadolivre_legacy_promocoes_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_mercadolivre_legacy_promocoes_runtime()


def _ml_obter_promocoes_item(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    request_fn=None,
    *,
    strict: bool = False,
    force_refresh: bool = False,
):
    request_fn = request_fn or _ml_api_request
    cache_key = f"v3-strict:{client_id}:{loja}:{item_id}" if strict else f"v2:{client_id}:{loja}:{item_id}"
    # O endpoint por item e a fonte que associa uma oferta ativa a campanha
    # correspondente. Fluxos financeiros podem exigir uma fotografia atual
    # dessa associacao, pois o cache normal dura varios minutos. A atualizacao
    # forcada ignora somente a leitura: o resultado continua sendo gravado na
    # mesma chave para que as demais leituras do mesmo item no lote o reutilizem.
    if not force_refresh:
        cached = _cache_get(ML_ITEM_PROMOTIONS_CACHE, cache_key, ML_ITEM_PROMOTIONS_CACHE_TTL)
        if cached is not None:
            cached = [
                _promo_remover_contexto_consultado(entry)
                if isinstance(entry, dict)
                else entry
                for entry in cached
            ] if isinstance(cached, list) else cached
            return cached, cfg

    url = f"https://api.mercadolibre.com/seller-promotions/items/{item_id}"
    try:
        resp, cfg = request_fn(client_id, loja, cfg, "GET", url, params={"app_version": "v2"}, timeout=12)
    except Exception:
        if force_refresh:
            _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, [])
        raise
    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, dict):
                data = data.get("results")
            if not isinstance(data, list):
                if strict:
                    if force_refresh:
                        _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, [])
                    raise ValueError("payload de promocoes sem lista results")
                data = []
        except Exception as exc:
            if strict:
                raise HTTPException(
                    status_code=502,
                    detail=f"Mercado Livre retornou uma lista de promocoes invalida para {item_id}: {exc}",
                ) from exc
            data = []
        # O payload externo nunca pode fornecer os marcadores internos usados
        # para autorizar um contexto financeiro. Esses marcadores so sao
        # materializados pelo adapter do endpoint de campanha, depois das
        # validacoes de campanha/tipo/item/status/offer.
        data = [
            _promo_remover_contexto_consultado(entry)
            if isinstance(entry, dict)
            else entry
            for entry in data
        ]
        _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, data)
        return data, cfg

    if strict:
        body = {}
        try:
            body = resp.json() or {}
        except Exception:
            body = {}
        partes = []
        if isinstance(body, dict):
            for chave in ("error", "code", "message", "detail"):
                valor = body.get(chave)
                if valor not in (None, ""):
                    partes.append(str(valor))
        try:
            if resp.text:
                partes.append(str(resp.text))
        except Exception:
            pass
        detalhe = " | ".join(dict.fromkeys(partes))[:600]
        detalhe_norm = re.sub(r"\s+", " ", detalhe).strip().lower().replace("_", " ")
        if resp.status_code == 404 and "no offers found" in detalhe_norm:
            _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, [])
            return [], cfg
        if force_refresh:
            _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, [])
        raise HTTPException(
            status_code=resp.status_code or 502,
            detail=(
                f"Nao foi possivel confirmar as promocoes atuais do anuncio {item_id}"
                + (f": {detalhe}" if detalhe else ".")
            ),
        )

    if resp.status_code in (400, 404, 500):
        _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, [])
        return [], cfg

    if force_refresh:
        _cache_set(ML_ITEM_PROMOTIONS_CACHE, cache_key, [])
    logger.warning(f"[ML API] Falha ao consultar promocoes do item {item_id}: {resp.status_code} - {resp.text[:300]}")
    return [], cfg


def _ml_extrair_ids_promocoes_item(promocoes_item) -> set[str]:
    ids: set[str] = set()
    chaves_id_promocao = {
        "promotion_id",
        "promotionid",
        "campaign_id",
        "campaignid",
        "deal_id",
        "dealid",
        "offer_id",
        "offerid",
    }
    chaves_indicam_promocao = chaves_id_promocao | {
        "promotion_type",
        "promotiontype",
        "type",
        "status",
        "name",
        "title",
        "start_date",
        "finish_date",
    }

    def registrar(valor):
        if valor is None:
            return
        texto = str(valor).strip()
        if texto and texto.lower() not in {"-", "none", "null"}:
            ids.add(texto.lower())

    def parece_promocao(obj: dict) -> bool:
        chaves = {str(chave or "").strip().lower() for chave in obj.keys()}
        return bool(chaves & chaves_indicam_promocao)

    vistos = set()
    nodes = 0

    def visitar(obj, em_lista_promocoes: bool = False, depth: int = 0):
        nonlocal nodes
        nodes += 1
        if nodes > 4000 or depth > 8:
            return
        if isinstance(obj, (dict, list)):
            obj_id = id(obj)
            if obj_id in vistos:
                return
            vistos.add(obj_id)
        if isinstance(obj, list):
            for item in obj[:250]:
                if isinstance(item, (dict, list)):
                    visitar(item, True, depth + 1)
                else:
                    registrar(item)
            return
        if not isinstance(obj, dict):
            return

        dict_parece_promocao = em_lista_promocoes or parece_promocao(obj)
        for chave, valor in obj.items():
            chave_norm = str(chave or "").strip().lower()
            if chave_norm in chaves_id_promocao:
                registrar(valor)
            elif chave_norm == "id" and dict_parece_promocao:
                registrar(valor)

        for chave, valor in obj.items():
            chave_norm = str(chave or "").strip().lower()
            if chave_norm == "item":
                continue
            if isinstance(valor, (dict, list)):
                visitar(valor, chave_norm in {"results", "campaign", "campaigns", "promotion", "promotions", "deal", "deals", "offer", "offers", "available_promotions"}, depth + 1)

    visitar(promocoes_item)
    return ids


def _ml_encontrar_promocao_raw_item(promocoes_item, campaign_id: str):
    campaign_norm = str(campaign_id or "").strip().lower()
    if not campaign_norm:
        return {}
    chaves_id_promocao = {
        "id",
        "promotion_id",
        "promotionid",
        "campaign_id",
        "campaignid",
        "deal_id",
        "dealid",
        "offer_id",
        "offerid",
    }
    listas_promocoes = {"results", "campaign", "campaigns", "promotion", "promotions", "deal", "deals", "offer", "offers", "available_promotions"}
    stack = [(promocoes_item, False, 0)]
    vistos = set()
    nodes = 0

    while stack:
        obj, em_lista_promocoes, depth = stack.pop()
        nodes += 1
        if nodes > 4000 or depth > 8:
            break
        if isinstance(obj, (dict, list)):
            obj_id = id(obj)
            if obj_id in vistos:
                continue
            vistos.add(obj_id)
        if isinstance(obj, list):
            for item in reversed(obj[:250]):
                if isinstance(item, (dict, list)):
                    stack.append((item, True, depth + 1))
            continue
        if not isinstance(obj, dict):
            continue

        for chave, valor in obj.items():
            chave_norm = str(chave or "").strip().lower()
            if chave_norm in chaves_id_promocao or (em_lista_promocoes and chave_norm == "id"):
                if str(valor or "").strip().lower() == campaign_norm:
                    return obj

        for chave, valor in obj.items():
            chave_norm = str(chave or "").strip().lower()
            if chave_norm == "item":
                continue
            if isinstance(valor, (dict, list)):
                stack.append((valor, chave_norm in listas_promocoes, depth + 1))
    return {}


def _promo_status_item_promocao(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    status = (
        entry.get("status")
        or entry.get("status_item")
        or entry.get("statusItem")
        or entry.get("_jk_status_item_consultado")
    )
    if isinstance(status, dict):
        status = status.get("id") or status.get("name") or status.get("status")
    return str(status or "").strip().lower()


def _promo_prioridade_status_item(entry: Any) -> int:
    status = _promo_status_item_promocao(entry)
    return {
        "pending": 60,
        "programmed": 60,
        "programada": 60,
        "scheduled": 60,
        "started": 50,
        "active": 50,
        "approved": 45,
        "candidate": 20,
        "eligible": 10,
    }.get(status, 0)


def _promo_normalizar_mlb_contexto(valor: Any) -> str:
    normalizar = globals().get("_promo_normalizar_mlb")
    if callable(normalizar):
        return str(normalizar(valor) or "").strip().upper()
    texto = str(valor or "").strip().upper()
    match = re.search(r"MLB\s*([0-9]+)", texto)
    return f"MLB{match.group(1)}" if match else ""


def _promo_entry_item_id(entry: Any) -> str:
    if not isinstance(entry, dict):
        return _promo_normalizar_mlb_contexto(entry)
    raw_item = entry.get("item")
    raw_item_id = raw_item.get("id") if isinstance(raw_item, dict) else raw_item
    return _promo_normalizar_mlb_contexto(
        entry.get("item_id")
        or entry.get("itemId")
        or entry.get("item_id_to")
        or raw_item_id
        or entry.get("id")
        or ""
    )


def _promo_remover_contexto_consultado(entry: Any) -> dict:
    """Remove marcadores internos que nunca podem ser aceitos da API externa."""
    if not isinstance(entry, dict):
        return {}
    sanitizado = copy.deepcopy(entry)
    pendentes = [sanitizado]
    while pendentes:
        atual = pendentes.pop()
        if isinstance(atual, dict):
            for chave in list(atual):
                valor = atual.get(chave)
                if str(chave or "").startswith("_jk_"):
                    atual.pop(chave, None)
                    continue
                if isinstance(valor, (dict, list)):
                    pendentes.append(valor)
        elif isinstance(atual, list):
            pendentes.extend(valor for valor in atual if isinstance(valor, (dict, list)))
    return sanitizado


def _promo_status_contexto_compativel(status_entry: str, status_consultado: str) -> bool:
    status_entry = str(status_entry or "").strip().lower()
    status_consultado = str(status_consultado or "").strip().lower()
    if not status_consultado or not status_entry:
        return True
    grupos = (
        {"candidate", "eligible"},
        {"pending", "programmed", "programada", "scheduled"},
        {"started", "active", "approved"},
        {"paused"},
    )
    for grupo in grupos:
        if status_entry in grupo or status_consultado in grupo:
            return status_entry in grupo and status_consultado in grupo
    return status_entry == status_consultado


def _promo_valores_texto_contexto(entry: dict, chaves: tuple[str, ...]) -> set[str]:
    if not isinstance(entry, dict):
        return set()
    containers = [
        entry,
        entry.get("promotion") if isinstance(entry.get("promotion"), dict) else {},
        entry.get("campaign") if isinstance(entry.get("campaign"), dict) else {},
        entry.get("deal") if isinstance(entry.get("deal"), dict) else {},
        entry.get("offer") if isinstance(entry.get("offer"), dict) else {},
    ]
    valores = set()
    for container in containers:
        for chave in chaves:
            valor = container.get(chave)
            if isinstance(valor, dict):
                valor = valor.get("id") or valor.get("name") or valor.get("status")
            texto = str(valor or "").strip()
            if texto and texto.lower() not in {"-", "none", "null"}:
                valores.add(texto)
    return valores


def _promo_contextualizar_entry_campanha(
    entry: Any,
    *,
    campaign_id: str,
    promotion_type_consultado: str,
    item_id: str,
    status_consultado: str = "",
    status_param_consultado: str = "",
) -> dict:
    """Carimba a origem da consulta somente apos validar o registro retornado."""
    sanitizado = _promo_remover_contexto_consultado(entry)
    if not sanitizado:
        return {}

    campaign_id = str(campaign_id or "").strip()
    promotion_type_consultado = str(promotion_type_consultado or "").strip()
    item_id = _promo_normalizar_mlb_contexto(item_id)
    if not campaign_id or not item_id:
        return {}

    campaigns_explicitas = _promo_valores_texto_contexto(
        sanitizado,
        ("promotion_id", "promotionId", "campaign_id", "campaignId", "deal_id", "dealId"),
    )
    for container_key in ("promotion", "campaign", "deal"):
        container = sanitizado.get(container_key)
        if isinstance(container, dict):
            valor_id = str(container.get("id") or "").strip()
            if valor_id:
                campaigns_explicitas.add(valor_id)
    raw_id = str(sanitizado.get("id") or "").strip()
    if raw_id.upper().startswith(("P-", "DEAL-")):
        campaigns_explicitas.add(raw_id)
    if any(valor.lower() != campaign_id.lower() for valor in campaigns_explicitas):
        return {}

    tipos_explicitos = _promo_valores_texto_contexto(
        sanitizado,
        ("promotion_type", "promotionType", "campaign_type", "campaignType", "type"),
    )
    if (
        promotion_type_consultado
        and any(valor.upper() != promotion_type_consultado.upper() for valor in tipos_explicitos)
    ):
        return {}

    item_entry = _promo_entry_item_id(sanitizado)
    if not item_entry or item_entry.upper() != item_id.upper():
        return {}
    itens_explicitos = _promo_valores_texto_contexto(
        sanitizado,
        ("item_id", "itemId", "item_id_to"),
    )
    raw_item = sanitizado.get("item")
    if isinstance(raw_item, dict) and raw_item.get("id"):
        itens_explicitos.add(str(raw_item.get("id") or "").strip())
    elif isinstance(raw_item, str):
        itens_explicitos.add(raw_item.strip())
    if raw_id.upper().startswith("MLB"):
        itens_explicitos.add(raw_id)
    if any(_promo_normalizar_mlb_contexto(valor) != item_id for valor in itens_explicitos):
        return {}

    status_entry = _promo_status_item_promocao(sanitizado)
    status_explicitos = set()
    for chave in ("status", "state", "status_item", "statusItem"):
        valor = sanitizado.get(chave)
        if isinstance(valor, dict):
            valor = valor.get("id") or valor.get("name") or valor.get("status")
        texto = str(valor or "").strip().lower()
        if texto and texto not in {"-", "none", "null"}:
            status_explicitos.add(texto)
    if any(
        not _promo_status_contexto_compativel(status_entry, valor)
        for valor in status_explicitos
    ):
        return {}
    if not _promo_status_contexto_compativel(status_entry, status_consultado):
        return {}
    status_resolvido = str(status_entry or status_consultado or "").strip().lower()
    offers_explicitas = _promo_valores_texto_contexto(
        sanitizado,
        ("offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId"),
    )
    offers_normalizadas = {valor.strip().upper() for valor in offers_explicitas if valor.strip()}
    if len(offers_normalizadas) > 1:
        return {}
    offer_id = next(iter(offers_normalizadas), "") or _ml_promocao_raw_offer_id(sanitizado).strip().upper()
    if not status_resolvido:
        return sanitizado
    if offer_id and status_resolvido in {"candidate", "eligible"}:
        offer_coerente = offer_id.startswith("CANDIDATE-")
    elif offer_id and status_resolvido in {"started", "active", "approved", "paused"}:
        offer_coerente = offer_id.startswith("OFFER-")
    elif offer_id and status_resolvido in {"pending", "programmed", "programada", "scheduled"}:
        offer_coerente = offer_id.startswith(("CANDIDATE-", "OFFER-"))
    elif not offer_id and status_resolvido in {"candidate", "eligible"}:
        # A API SMART pode omitir offer_id no candidate. A proveniencia da
        # precificacao continua valida; o gate da acao exige CANDIDATE-{MLB}-.
        offer_coerente = True
    else:
        offer_coerente = False
    if not offer_coerente:
        return {}

    if offer_id:
        match_item_offer = re.match(r"^(?:CANDIDATE|OFFER)-(MLB\d+)-", offer_id, flags=re.IGNORECASE)
        if not match_item_offer or match_item_offer.group(1).upper() != item_id.upper():
            return {}

    contextualizado = dict(sanitizado)
    if not status_entry and status_resolvido:
        contextualizado["_jk_status_item_consultado"] = status_resolvido
        contextualizado["_jk_status_param_consultado"] = str(status_param_consultado or "").strip()

    # O ID vem do path e o tipo vem exclusivamente do query param efetivamente
    # enviado. Sem os dois, preservamos o raw, mas ele nao autoriza financas.
    if promotion_type_consultado:
        contextualizado["_jk_campaign_id_consultado"] = campaign_id
        contextualizado["_jk_promotion_type_consultado"] = promotion_type_consultado
        contextualizado["_jk_item_id_consultado"] = item_id
        contextualizado["_jk_contexto_promocao_confirmado"] = True
    return contextualizado


def _promo_mesclar_raw_multistatus(raw_atual: Any, raw_novo: Any) -> dict:
    if not isinstance(raw_novo, dict):
        return dict(raw_atual) if isinstance(raw_atual, dict) else {}
    if not isinstance(raw_atual, dict) or not raw_atual:
        return dict(raw_novo)
    prioridade_atual = _promo_prioridade_status_item(raw_atual)
    prioridade_nova = _promo_prioridade_status_item(raw_novo)
    if prioridade_nova > prioridade_atual:
        return dict(raw_novo)
    if prioridade_nova < prioridade_atual:
        return dict(raw_atual)

    offer_atual = _ml_promocao_raw_offer_id(raw_atual).strip().lower()
    offer_novo = _ml_promocao_raw_offer_id(raw_novo).strip().lower()
    status_atual = _promo_status_item_promocao(raw_atual)
    status_novo = _promo_status_item_promocao(raw_novo)
    ambiguo = bool(
        (offer_atual and offer_novo and offer_atual != offer_novo)
        or (
            status_atual
            and status_novo
            and not _promo_status_contexto_compativel(status_atual, status_novo)
        )
    )
    if ambiguo:
        return _promo_remover_contexto_consultado(raw_atual)

    atual_confirmado = raw_atual.get("_jk_contexto_promocao_confirmado") is True
    novo_confirmado = raw_novo.get("_jk_contexto_promocao_confirmado") is True
    if atual_confirmado and novo_confirmado:
        identidade_atual = (
            str(raw_atual.get("_jk_campaign_id_consultado") or "").strip().lower(),
            str(raw_atual.get("_jk_promotion_type_consultado") or "").strip().upper(),
            str(raw_atual.get("_jk_item_id_consultado") or "").strip().upper(),
        )
        identidade_nova = (
            str(raw_novo.get("_jk_campaign_id_consultado") or "").strip().lower(),
            str(raw_novo.get("_jk_promotion_type_consultado") or "").strip().upper(),
            str(raw_novo.get("_jk_item_id_consultado") or "").strip().upper(),
        )
        if identidade_atual != identidade_nova:
            return _promo_remover_contexto_consultado(raw_atual)
    if novo_confirmado and not atual_confirmado:
        return dict(raw_novo)
    return dict(raw_atual)


def _promo_erro_candidate_not_found(texto: str) -> bool:
    texto_norm = normalizar_texto(texto or "").replace("_", " ")
    return (
        "candidate not found" in texto_norm
        or "candidate was not found" in texto_norm
        or "candidato nao encontrado" in texto_norm
    )


def _promo_consultar_item_na_campanha(
    client_id: str,
    loja: str,
    cfg: dict,
    promotion_id: str,
    promotion_type: str,
    item_id: str,
) -> tuple[dict, dict]:
    promotion_id = str(promotion_id or "").strip()
    promotion_type = str(promotion_type or "").strip()
    item_id = _promo_normalizar_mlb_contexto(item_id)
    if not promotion_id or not item_id:
        return {"success": False, "found": False, "detail": "Promocao ou MLB ausente."}, cfg

    base_params = {
        "app_version": "v2",
        "item_id": item_id,
    }
    if promotion_type:
        base_params["promotion_type"] = promotion_type

    tentativas = [dict(base_params)]
    for status_consulta in ("candidate", "pending", "started"):
        tentativas.append({**base_params, "status": status_consulta})
    for status_consulta in ("active", "paused"):
        tentativas.append({**base_params, "status_item": status_consulta})
    ultimo_erro = ""
    for params in tentativas:
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/seller-promotions/promotions/{promotion_id}/items",
            params=params,
            timeout=15,
        )
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            ultimo_erro = _ml_parse_error_detail(resp, f"Erro {resp.status_code} ao consultar candidato")
            continue
        try:
            data = resp.json() or {}
        except Exception:
            data = {}
        entries = data.get("results") if isinstance(data, dict) else []
        if not isinstance(entries, list):
            entries = []
        status_param = "status" if params.get("status") else ("status_item" if params.get("status_item") else "")
        status_consultado = str(params.get(status_param) or "").strip().lower() if status_param else ""
        encontrados = []
        for entry in entries:
            entry_contextualizado = _promo_contextualizar_entry_campanha(
                entry,
                campaign_id=promotion_id,
                promotion_type_consultado=str(params.get("promotion_type") or ""),
                item_id=item_id,
                status_consultado=status_consultado,
                status_param_consultado=status_param,
            )
            if entry_contextualizado:
                encontrados.append(entry_contextualizado)
        assinaturas = {
            (
                _promo_status_item_promocao(entry),
                _ml_promocao_raw_offer_id(entry).strip().lower(),
            )
            for entry in encontrados
        }
        if len(assinaturas) == 1 and encontrados:
            entry = encontrados[0]
            status = _promo_status_item_promocao(entry) or status_consultado
            return {
                "success": True,
                "found": True,
                "status": status,
                "entry": entry,
                "can_participate": status in {"candidate", "eligible"},
                "already_participating": status in {"started", "active", "pending", "programmed"},
            }, cfg

    return {
        "success": not bool(ultimo_erro),
        "found": False,
        "status": "",
        "detail": ultimo_erro or "MLB nao aparece como candidato/elegivel nessa campanha.",
    }, cfg


def _calcular_desconto_ml_valor(
    desconto_atual: Any = None,
    ml_pct: Any = None,
    preco_base: Any = None,
    preco_final_ml: Any = None,
    tarifa_base: Any = None,
    tarifa_ml: Any = None,
    desconto_atual_confiavel: bool = False,
    seller_pct: Any = None,
    boost_pct: Any = None,
    retornar_fonte: bool = False,
) -> float | None | tuple[float | None, str]:
    """Resolve o beneficio ML sem confundir percentual isolado com dinheiro.

    Prioriza o valor absoluto confiavel publicado pela API. Na ausencia dele,
    aceita somente percentuais que possam ser reconciliados com o desconto total
    observado entre ``preco_base`` e ``preco_final_ml``. Tarifa e frete nao
    participam do split percentual; os parametros de tarifa permanecem apenas
    para compatibilidade com os chamadores legados.
    """

    def _resultado(valor: float | None, fonte: str = ""):
        if retornar_fonte:
            return valor, fonte
        return valor

    desconto = _to_float_safe(desconto_atual)
    if desconto_atual_confiavel and desconto is not None and desconto >= 0:
        return _resultado(desconto, "seller_promotions.valor_direto")

    preco_base_val = _to_float_safe(preco_base)
    preco_final_val = _to_float_safe(preco_final_ml)
    if (
        preco_base_val is None
        or preco_base_val <= 0
        or preco_final_val is None
        or preco_final_val < 0
    ):
        return _resultado(None)

    desconto_total = round(float(preco_base_val) - float(preco_final_val), 2)
    if desconto_total < 0:
        return _resultado(None)

    boost_pct_val = _to_float_safe(boost_pct)
    if boost_pct_val is not None:
        if not 0 <= boost_pct_val <= 100:
            return _resultado(None)
        boost_valor = round(float(preco_base_val) * float(boost_pct_val) / 100.0, 2)
        # Um centavo de tolerancia cobre apenas o arredondamento monetario. Um
        # percentual que ultrapasse materialmente todo o desconto e inconsistente.
        if boost_valor > desconto_total + 0.01:
            return _resultado(None)
        boost_valor = min(boost_valor, desconto_total)
        return _resultado(
            boost_valor,
            "seller_promotions.discount_meli_boosted_percentage_calculado",
        )

    ml_pct_val = _to_float_safe(ml_pct)
    seller_pct_val = _to_float_safe(seller_pct)
    if ml_pct_val is None or seller_pct_val is None:
        return _resultado(None)
    if (
        not 0 <= ml_pct_val <= 100
        or not 0 <= seller_pct_val <= 100
        or ml_pct_val + seller_pct_val > 100
    ):
        return _resultado(None)

    percentual_total_observado = desconto_total * 100.0 / float(preco_base_val)
    percentual_total_informado = float(ml_pct_val) + float(seller_pct_val)
    # Os percentuais da seller-promotions sao exibidos com uma casa decimal.
    # A tolerancia cobre o arredondamento de ambos e mais um centavo no preco.
    tolerancia_pct = 0.1 + (0.01 * 100.0 / float(preco_base_val))
    if abs(percentual_total_informado - percentual_total_observado) > tolerancia_pct:
        return _resultado(None)

    parcela_vendedor = round(float(preco_base_val) * float(seller_pct_val) / 100.0, 2)
    desconto_ml = round(desconto_total - parcela_vendedor, 2)
    if desconto_ml < 0 or desconto_ml > desconto_total:
        return _resultado(None)
    return _resultado(
        desconto_ml,
        "seller_promotions.smart_split_reconciliado",
    )


def _ml_extrair_percentual_total_direto_promocao_raw(entry: dict):
    if not isinstance(entry, dict):
        return None

    chaves_diretas = {
        "discount_percentage",
        "discount_percent",
        "discountpercentage",
        "discountpercent",
    }

    for chave, valor in entry.items():
        chave_norm = str(chave or "").strip().lower().replace("-", "_")
        chave_norm = re.sub(r"[^a-z0-9_]", "", chave_norm)
        if chave_norm in chaves_diretas:
            pct = _ml_parse_percentual_promocao_texto(valor)
            if pct is not None and 0 < float(pct) <= 100:
                return float(pct)

    termos_excluir = (
        "seller",
        "meli",
        "fee",
        "tariff",
        "tarifa",
        "tax",
        "imposto",
        "margin",
        "margem",
        "contribution",
        "receive",
        "receives",
        "net",
        "liquid",
    )
    for caminho, valor in _ml_iterar_campos_payload_limitado(entry):
        caminho_norm = str(caminho or "").lower().replace("-", "_")
        chave_norm = re.sub(r"[^a-z0-9_]", "", caminho_norm.rsplit(".", 1)[-1])
        if chave_norm not in chaves_diretas:
            continue
        if any(termo in caminho_norm for termo in termos_excluir):
            continue
        pct = _ml_parse_percentual_promocao_texto(valor)
        if pct is not None and 0 < float(pct) <= 100:
            return float(pct)
    return None


def _ml_resolver_percentual_desconto_campanha_raw(entry: dict, preco_base=None, preco_final=None, fallback=None):
    direto = _ml_extrair_percentual_total_direto_promocao_raw(entry)
    por_preco = _ml_calcular_percentual_desconto_por_preco(preco_base, preco_final)
    if direto is not None and por_preco is not None:
        if abs(float(direto) - float(por_preco)) <= 1.0:
            return direto
        return por_preco
    if por_preco is not None:
        return por_preco
    if direto is not None:
        return direto

    sugerido = _ml_extrair_percentual_sugerido_campanha_raw(entry, preco_base)
    if sugerido is not None:
        return sugerido

    pct_fallback = _ml_parse_percentual_promocao_texto(fallback)
    if pct_fallback is not None:
        return pct_fallback
    return None


def _ml_parse_percentual_promocao_texto(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    match_pct = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", texto)
    if match_pct:
        return _parse_float_flex(match_pct.group(1))
    return _parse_float_flex(valor)


def _ml_extrair_percentual_sugerido_campanha_raw(entry: dict, preco_base=None):
    """Extrai o percentual total sugerido/ofertado pela campanha ML."""
    if not isinstance(entry, dict):
        return None

    def _normalizar_chave(chave: str) -> str:
        chave_txt = str(chave or "").strip().lower().replace("-", "_")
        return re.sub(r"[^a-z0-9_]", "", chave_txt)

    def _numero_campo(valor, chaves: tuple[str, ...] = ("percentage", "percent", "pct", "amount", "value", "price")):
        if isinstance(valor, dict):
            for chave in chaves:
                numero = _parse_float_flex(valor.get(chave))
                if numero is not None:
                    return numero
            return None
        return _parse_float_flex(valor)

    def _percentual_valido(valor):
        pct = _ml_parse_percentual_promocao_texto(valor)
        if pct is None:
            pct = _numero_campo(valor, ("percentage", "percent", "pct", "value", "amount"))
        if pct is None:
            return None
        pct = float(pct)
        if 0 < pct <= 100:
            return pct
        return None

    def _primeiro_percentual(candidatos):
        for valor in candidatos:
            pct = _percentual_valido(valor)
            if pct is not None:
                return pct
        return None

    def _percentual_por_valor(valor, base):
        numero = _numero_campo(valor, ("amount", "value", "discount", "price"))
        base_num = _parse_float_flex(base)
        if numero is None or base_num is None or base_num <= 0:
            return None
        pct = (float(numero) / float(base_num)) * 100.0
        if 0 < pct <= 100:
            return pct
        return None

    def _percentual_por_preco(preco_sugerido, base):
        preco_num = _numero_campo(preco_sugerido, ("amount", "value", "price"))
        base_num = _parse_float_flex(base)
        if preco_num is None or base_num is None or base_num <= 0 or preco_num <= 0:
            return None
        pct = ((float(base_num) - float(preco_num)) / float(base_num)) * 100.0
        if 0 < pct <= 100:
            inteiro = round(pct)
            if abs(pct - inteiro) <= 0.25:
                return float(inteiro)
            return pct
        return None

    def _preco_base_campanha():
        for valor in (
            entry.get("original_price"),
            entry.get("regular_price"),
            entry.get("base_price"),
            entry.get("standard_price"),
            preco_base,
        ):
            numero = _parse_float_flex(valor)
            if numero is not None and numero > 0:
                return numero
        return None

    def _soma_percentuais_componentes():
        seller_pct = _percentual_valido(
            entry.get("seller_percentage")
            if entry.get("seller_percentage") is not None
            else entry.get("seller_discount_percentage")
        )
        meli_pct = _percentual_valido(
            entry.get("meli_percentage")
            if entry.get("meli_percentage") is not None
            else entry.get("meli_discount_percentage")
        )
        if seller_pct is None and meli_pct is None:
            return None
        soma = float(seller_pct or 0.0) + float(meli_pct or 0.0)
        if 0 < soma <= 100:
            return soma
        return None

    chaves_seller_pct = {
        "seller_percentage",
        "seller_discount_percentage",
        "seller_discount_percent",
        "sellerpercentage",
        "sellerdiscountpercentage",
    }
    chaves_meli_pct = {
        "meli_percentage",
        "meli_discount_percentage",
        "meli_discount_percent",
        "melipercentage",
        "melidiscountpercentage",
    }
    chaves_sugeridas_pct = {
        "suggested_discount_percentage",
        "suggested_discount_percent",
        "recommended_discount_percentage",
        "recommended_discount_percent",
        "campaign_discount_percentage",
        "campaign_discount_percent",
        "deal_discount_percentage",
        "offer_discount_percentage",
        "min_discount_percentage",
        "minimum_discount_percentage",
        "max_discount_percentage",
        "maximum_discount_percentage",
    }
    chaves_diretas_pct = {
        "discount_percentage",
        "discount_percent",
    }
    chaves_valor_desconto = {
        "seller_discount_amount",
        "seller_discount_value",
        "suggested_discount_amount",
        "suggested_discount_value",
        "recommended_discount_amount",
        "recommended_discount_value",
        "campaign_discount_amount",
        "campaign_discount_value",
        "discount_amount",
        "discount_value",
    }
    chaves_preco_sugerido = {
        "price",
        "deal_price",
        "promotion_price",
        "final_price",
        "discounted_price",
        "suggested_discounted_price",
        "suggested_price",
        "suggested_deal_price",
        "recommended_discounted_price",
        "recommended_price",
        "campaign_price",
    }

    seller_direto = []
    meli_direto = []
    sugerido_direto = []
    direto_pct = []
    valor_desconto = []
    preco_sugerido = []
    preco_base_item = _preco_base_campanha()

    for chave, valor in entry.items():
        chave_norm = _normalizar_chave(chave)
        if chave_norm in chaves_seller_pct:
            seller_direto.append(valor)
        elif chave_norm in chaves_meli_pct:
            meli_direto.append(valor)
        elif chave_norm in chaves_sugeridas_pct:
            sugerido_direto.append(valor)
        elif chave_norm in chaves_diretas_pct:
            direto_pct.append(valor)
        elif chave_norm in chaves_valor_desconto:
            valor_desconto.append(valor)
        elif chave_norm in chaves_preco_sugerido:
            preco_sugerido.append(valor)

    seller_payload = []
    meli_payload = []
    sugerido_payload = []
    direto_payload = []
    valor_payload = []
    preco_payload = []
    termos_excluir = ("fee", "tariff", "tarifa", "tax", "imposto", "margin", "margem", "contribution", "receive")

    for caminho, valor in _ml_iterar_campos_payload_limitado(entry):
        caminho_norm = str(caminho or "").lower()
        if any(termo in caminho_norm for termo in termos_excluir):
            continue
        chave_norm = _normalizar_chave(caminho_norm.rsplit(".", 1)[-1])
        tem_percentual = "percent" in caminho_norm or "percentage" in caminho_norm or chave_norm.endswith("_pct")
        if chave_norm in chaves_seller_pct or ("seller" in caminho_norm and tem_percentual):
            seller_payload.append(valor)
            continue
        if chave_norm in chaves_meli_pct or ("meli" in caminho_norm and tem_percentual):
            meli_payload.append(valor)
            continue
        if chave_norm in chaves_sugeridas_pct or (tem_percentual and any(t in caminho_norm for t in ("suggest", "recommend", "campaign", "deal", "offer"))):
            sugerido_payload.append(valor)
            continue
        if chave_norm in chaves_diretas_pct:
            direto_payload.append(valor)
            continue
        if tem_percentual and "discount" in caminho_norm:
            direto_payload.append(valor)
            continue
        if chave_norm in chaves_valor_desconto or ("discount" in caminho_norm and any(t in caminho_norm for t in ("seller", "suggest", "recommend", "campaign", "deal", "offer")) and not tem_percentual):
            valor_payload.append(valor)
            continue
        if chave_norm in chaves_preco_sugerido:
            preco_payload.append(valor)

    for grupo in (sugerido_direto, sugerido_payload, direto_pct, direto_payload):
        pct = _primeiro_percentual(grupo)
        if pct is not None:
            return pct

    for grupo in (preco_sugerido, preco_payload):
        for valor in grupo:
            pct = _percentual_por_preco(valor, preco_base_item)
            if pct is not None:
                return pct

    componentes = _soma_percentuais_componentes()
    if componentes is not None:
        return componentes

    meli_pct = _primeiro_percentual(meli_direto) or _primeiro_percentual(meli_payload)
    seller_pct = _primeiro_percentual(seller_direto) or _primeiro_percentual(seller_payload)
    if meli_pct is not None and seller_pct is not None:
        soma = float(meli_pct) + float(seller_pct)
        if 0 < soma <= 100:
            return soma

    if seller_pct is not None:
        return seller_pct
    if meli_pct is not None:
        return meli_pct

    for grupo in (valor_desconto, valor_payload):
        for valor in grupo:
            pct = _percentual_por_valor(valor, preco_base_item)
            if pct is not None:
                return pct

    return None


def _ml_obter_item_promocao_raw(client_id: str, loja: str, cfg: dict, campaign_id: str, promotion_type: str, item_id: str, request_fn=None):
    request_fn = request_fn or _ml_api_request
    campaign_id = str(campaign_id or "").strip()
    promotion_type = str(promotion_type or "").strip()
    if promotion_type in {"-", "None", "null"}:
        promotion_type = ""
    item_id = str(item_id or "").strip()
    if not campaign_id or not promotion_type or not item_id:
        return {}, cfg

    # O endpoint campaign-items pode devolver registros de outra campanha no
    # mesmo resultado. Resolve antes a identidade exata divulgada pelo endpoint
    # por item para filtrar localmente por offer/ref e status.
    raw_referencia = {}
    try:
        promocoes_item, cfg = _ml_obter_promocoes_item(
            client_id,
            loja,
            cfg,
            item_id,
            request_fn=request_fn,
        )
        raw_referencia = _ml_encontrar_promocao_raw_item(promocoes_item, campaign_id) or {}
    except Exception:
        raw_referencia = {}
    offer_id_esperado = _ml_promocao_raw_offer_id(raw_referencia).strip().lower()
    status_esperado = _promo_status_item_promocao(raw_referencia)

    urls = [
        f"https://api.mercadolibre.com/seller-promotions/promotions/{campaign_id}/items",
    ]
    consultas_status = []

    def _adicionar_consulta_status(parametro: str, status: str):
        chave = (str(parametro or ""), str(status or ""))
        if chave not in consultas_status:
            consultas_status.append(chave)

    if status_esperado:
        parametro = "status_item" if status_esperado in {"active", "paused"} else "status"
        _adicionar_consulta_status(parametro, status_esperado)
    for status in ("candidate", "eligible", "pending", "started"):
        _adicionar_consulta_status("status", status)
    for status in ("active", "paused"):
        _adicionar_consulta_status("status_item", status)
    _adicionar_consulta_status("", "")
    for status_param, status_item in consultas_status:
        params = {
            "app_version": "v2",
            "promotion_type": promotion_type,
            "item_id": item_id,
        }
        if status_param and status_item:
            params[status_param] = status_item
        for url in urls:
            offset = 0
            limit = 50
            paginas = 0
            while True:
                paginas += 1
                params_chamada = dict(params)
                params_chamada.update({"offset": offset, "limit": limit})
                if request_fn is _ml_api_request:
                    resp, cfg = _ml_api_request_com_retry(
                        client_id,
                        loja,
                        cfg,
                        "GET",
                        url,
                        params=params_chamada,
                        timeout=30,
                        max_attempts=3,
                    )
                else:
                    resp, cfg = request_fn(client_id, loja, cfg, "GET", url, params=params_chamada, timeout=30)
                if resp.status_code != 200:
                    break
                try:
                    data = resp.json() or {}
                except Exception:
                    data = {}
                entries = data.get("results") or data.get("items") or []
                entry_selecionado = {}
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    entry_id = _promo_entry_item_id(entry)
                    if entry_id == _promo_normalizar_mlb_contexto(item_id):
                        entry_contextualizado = _promo_contextualizar_entry_campanha(
                            entry,
                            campaign_id=campaign_id,
                            promotion_type_consultado=str(params_chamada.get("promotion_type") or ""),
                            item_id=item_id,
                            status_consultado=status_item,
                            status_param_consultado=status_param,
                        )
                        if not entry_contextualizado:
                            continue
                        offer_id_entry = _ml_promocao_raw_offer_id(entry_contextualizado).strip().lower()
                        if offer_id_esperado and offer_id_entry != offer_id_esperado:
                            continue
                        entry_selecionado = _promo_mesclar_raw_multistatus(
                            entry_selecionado,
                            entry_contextualizado,
                        )
                if entry_selecionado:
                    return entry_selecionado, cfg
                paging = data.get("paging") or {}
                total = int(paging.get("total") or 0)
                offset += limit
                if not entries or len(entries) < limit or (total and offset >= total) or paginas >= 100:
                    break
    return {}, cfg


def _ml_promocao_raw_texto(entry: dict, chaves: tuple[str, ...]) -> str:
    if not isinstance(entry, dict):
        return ""
    containers = [
        entry,
        entry.get("promotion") if isinstance(entry.get("promotion"), dict) else {},
        entry.get("campaign") if isinstance(entry.get("campaign"), dict) else {},
        entry.get("deal") if isinstance(entry.get("deal"), dict) else {},
        entry.get("offer") if isinstance(entry.get("offer"), dict) else {},
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for chave in chaves:
            valor = container.get(chave)
            texto = str(valor or "").strip()
            if texto and texto.lower() not in {"-", "none", "null"}:
                return texto
    return ""


def _ml_promocao_raw_id(entry: dict) -> str:
    campaign_id = _ml_promocao_raw_texto(
        entry,
        (
            "promotion_id",
            "promotionId",
            "campaign_id",
            "campaignId",
            "deal_id",
            "dealId",
        ),
    )
    if campaign_id:
        return campaign_id
    if isinstance(entry, dict) and entry.get("_jk_contexto_promocao_confirmado") is True:
        campaign_id = str(entry.get("_jk_campaign_id_consultado") or "").strip()
        if campaign_id:
            return campaign_id
    return _ml_promocao_raw_texto(entry, ("offer_id", "offerId", "id"))


def _ml_promocao_raw_tipo(entry: dict) -> str:
    promotion_type = _ml_promocao_raw_texto(
        entry,
        ("promotion_type", "promotionType", "type", "campaign_type", "campaignType"),
    )
    if promotion_type:
        return promotion_type
    if isinstance(entry, dict) and entry.get("_jk_contexto_promocao_confirmado") is True:
        return str(entry.get("_jk_promotion_type_consultado") or "").strip()
    return ""


def _ml_promocao_raw_nome(entry: dict) -> str:
    return _ml_promocao_raw_texto(entry, ("name", "title", "label"))


def _ml_promocao_raw_offer_id(entry: dict) -> str:
    texto = _ml_promocao_raw_texto(
        entry,
        ("offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId"),
    )
    if texto:
        return texto
    if not isinstance(entry, dict):
        return ""
    chaves_offer = {"offer_id", "offerid", "ref_id", "refid", "candidate_offer_id", "candidateofferid"}
    for caminho, valor in _ml_iterar_campos_payload_limitado(entry, max_depth=5, max_nodes=900):
        ultimo = str(caminho or "").rsplit(".", 1)[-1].strip().lower().replace("-", "_")
        ultimo = re.sub(r"[^a-z0-9_]", "", ultimo)
        if ultimo in chaves_offer or ultimo.replace("_", "") in chaves_offer:
            valor_txt = str(valor or "").strip()
            if valor_txt and valor_txt.lower() not in {"-", "none", "null"}:
                return valor_txt
    return ""


def _ml_grupo_promocao_por_meta(promotion_type: str = "", nome: str = "") -> str:
    tipo = str(promotion_type or "").strip().upper()
    nome_norm = normalizar_texto(str(nome or ""))
    if tipo in {"SELLER_CAMPAIGN", "SELLER_COUPON_CAMPAIGN"}:
        return "usuario"
    if tipo in {"SMART", "PRICE_MATCHING", "PRICE_MATCHING_MELI_ALL", "MARKETPLACE_CAMPAIGN", "PRE_NEGOTIATED"}:
        return "mercado_livre"
    if any(chave in nome_norm for chave in ["aceler", "tarifa", "menos tarifa", "reduzimos", "aumente suas vendas"]):
        return "mercado_livre"
    return "outras"


def _ml_grupo_promocao_raw(entry: dict) -> str:
    return _ml_grupo_promocao_por_meta(_ml_promocao_raw_tipo(entry), _ml_promocao_raw_nome(entry))


def _ml_resolver_raw_promocao_equivalente_para_analise(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    raw_atual: dict,
    campaign_id: str,
    promotion_type: str,
    preco_base=None,
    request_fn=None,
):
    """Enriquece a analise somente com o registro da campanha selecionada."""
    campaign_id = str(campaign_id or "").strip()
    if not item_id or not campaign_id or not isinstance(raw_atual, dict):
        return raw_atual, cfg

    request_fn = request_fn or _ml_api_request
    try:
        promocoes_item, cfg = _ml_obter_promocoes_item(client_id, loja, cfg, item_id, request_fn=request_fn)
    except Exception:
        return raw_atual, cfg
    if not promocoes_item:
        return raw_atual, cfg

    raw_exato = _ml_encontrar_promocao_raw_item(promocoes_item, campaign_id) or {}
    if not raw_exato:
        return raw_atual, cfg

    # O endpoint por item pode trazer os campos condicionais de boost que nao
    # vieram no endpoint da campanha. Mescla apenas o mesmo ID: nome e tipo nao
    # sao identidade suficiente, pois o ML pode publicar campanhas homonimas.
    offer_atual = _ml_promocao_raw_offer_id(raw_atual).strip().lower()
    offer_exato = _ml_promocao_raw_offer_id(raw_exato).strip().lower()
    status_atual = _promo_status_item_promocao(raw_atual)
    status_exato = _promo_status_item_promocao(raw_exato)
    if (
        (offer_exato and offer_atual != offer_exato)
        or (status_exato and status_atual and status_atual != status_exato)
    ):
        return dict(raw_exato), cfg

    enriquecido = dict(raw_exato)
    enriquecido.update(raw_atual)
    for chave in (
        "boosted_offer",
        "discount_meli_boosted_percentage",
        "discount_meli_boost_amount",
        "total_price_for_boosted_offer",
    ):
        if chave in raw_exato:
            enriquecido[chave] = raw_exato[chave]
    return enriquecido, cfg


def _ml_promocao_raw_esta_ativa_ou_indefinida(entry: dict) -> bool:
    status = _ml_classificar_status_promocao_entry(entry)
    if status in {"Ativo", ""}:
        return True
    return False


def _ml_listar_ids_anuncios_ativos(
    client_id: str,
    loja: str,
    cfg: dict,
    user_id: str,
    limite: int = 5000,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> tuple[list[str], dict]:
    cfg_local = dict(cfg or {})
    user = str(user_id or "").strip()
    if not user:
        raise HTTPException(status_code=400, detail="ID do usuario Mercado Livre nao encontrado")

    limite = max(100, min(int(limite or 5000), 20000))
    cache_key = f"promo:ml_ids_ativos:{client_id}:{loja}:{user}:{limite}"
    cached = _ml_cache_get(cache_key)
    if cached is not None:
        return cached, cfg_local

    url = f"https://api.mercadolibre.com/users/{user}/items/search"
    ids: list[str] = []
    vistos: set[str] = set()

    def registrar(batch) -> int:
        adicionados = 0
        for item_raw in batch or []:
            item_id = str((item_raw or {}).get("id") if isinstance(item_raw, dict) else item_raw or "").strip()
            if item_id and item_id not in vistos:
                vistos.add(item_id)
                ids.append(item_id)
                adicionados += 1
                if len(ids) >= limite:
                    break
        return adicionados

    scan_ok = False
    scroll_id = ""
    try:
        params = {"search_type": "scan", "limit": 100, "status": "active"}
        for _pagina in range(max(1, math.ceil(limite / 100))):
            if scroll_id:
                params = {"search_type": "scan", "scroll_id": scroll_id, "limit": 100}
            resp, cfg_local = _ml_api_request_com_retry(
                client_id,
                loja,
                cfg_local,
                "GET",
                url,
                params=params,
                timeout=35,
                max_attempts=3,
                progress_callback=progress_callback,
            )
            if resp.status_code != 200:
                break
            data = resp.json() or {}
            batch = data.get("results") or []
            if not batch:
                scan_ok = True
                break
            registrar(batch)
            scan_ok = True
            scroll_id = str(data.get("scroll_id") or "").strip()
            if not scroll_id or len(ids) >= limite:
                break
    except Exception as exc:
        logger.warning("[PROMO API] Falha no scan de anuncios ativos da loja %s: %s", loja, exc)

    if not scan_ok or not ids:
        ids = []
        vistos = set()
        offset = 0
        page_limit = 100
        max_offset = min(limite, 1000)
        while len(ids) < max_offset and offset < 1000:
            limite_chamada = min(page_limit, max_offset - len(ids), 1000 - offset)
            if limite_chamada <= 0:
                break
            resp, cfg_local = _ml_api_request_com_retry(
                client_id,
                loja,
                cfg_local,
                "GET",
                url,
                params={"offset": offset, "limit": limite_chamada, "status": "active"},
                timeout=30,
                max_attempts=3,
                progress_callback=progress_callback,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao listar anuncios ativos do Mercado Livre"))
            data = resp.json() or {}
            batch = data.get("results") or []
            if not batch:
                break
            registrar(batch)
            paging = data.get("paging") or {}
            total = int(paging.get("total", len(ids)) or len(ids))
            offset += limite_chamada
            if offset >= total:
                break

    _ml_cache_set(cache_key, ids)
    return ids, cfg_local


def _ml_listar_itens_promocao_com_raw(
    client_id: str,
    loja: str,
    cfg: dict,
    campaign_id: str,
    *,
    promotion_type: str = "",
    usar_fallback_pesado: bool = False,
    max_items: int = 300,
    buscar_detalhes: bool = True,
    status_promocao: str = "",
    status_item_preferencial: str = "active",
    forcar_fallback_pesado: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
):
    campaign_id = str(campaign_id or "").strip()
    if not campaign_id:
        return [], {}, cfg

    item_ids = []
    raw_por_item = {}
    promotion_type = str(promotion_type or "").strip()
    status_promocao = str(status_promocao or "").strip()
    status_item_preferencial = str(status_item_preferencial or "").strip()
    promo_url_tpls = []
    if promotion_type:
        if status_promocao:
            promo_url_tpls.append(
                f"https://api.mercadolibre.com/seller-promotions/promotions/{campaign_id}/items?app_version=v2&promotion_type={quote_plus(promotion_type)}&status={quote_plus(status_promocao)}"
            )
        if status_item_preferencial:
            promo_url_tpls.append(
                f"https://api.mercadolibre.com/seller-promotions/promotions/{campaign_id}/items?app_version=v2&promotion_type={quote_plus(promotion_type)}&status_item={quote_plus(status_item_preferencial)}"
            )
        promo_url_tpls.append(
            f"https://api.mercadolibre.com/seller-promotions/promotions/{campaign_id}/items?app_version=v2&promotion_type={quote_plus(promotion_type)}"
        )
    elif status_promocao:
        promo_url_tpls.append(
            f"https://api.mercadolibre.com/seller-promotions/{campaign_id}/items?app_version=v2&status={quote_plus(status_promocao)}"
        )
    promo_url_tpls.extend([
        f"https://api.mercadolibre.com/seller-promotions/{campaign_id}/items?app_version=v2",
        f"https://api.mercadolibre.com/seller-promotions/{campaign_id}/items",
    ])

    for promo_url_tpl in promo_url_tpls:
        params_url_tpl = parse_qs(urlparse(promo_url_tpl).query)
        promotion_type_consultado = str(
            (params_url_tpl.get("promotion_type") or [""])[0] or ""
        ).strip()
        status_consultado = ""
        status_param_consultado = ""
        if params_url_tpl.get("status_item"):
            status_consultado = str((params_url_tpl.get("status_item") or [""])[0] or "").strip()
            status_param_consultado = "status_item"
        elif params_url_tpl.get("status"):
            status_consultado = str((params_url_tpl.get("status") or [""])[0] or "").strip()
            status_param_consultado = "status"
        offset = 0
        limit = 50
        encontrou_endpoint = False
        paginas_lidas = 0
        max_paginas = max(1, math.ceil(max_items / limit) + 1)
        assinaturas_paginas = set()
        while True:
            paginas_lidas += 1
            if paginas_lidas > max_paginas:
                logger.warning(f"[PROMO API] Interrompendo paginacao da promocao {campaign_id}: limite de paginas atingido")
                break
            sep = "&" if "?" in promo_url_tpl else "?"
            url = f"{promo_url_tpl}{sep}offset={offset}&limit={limit}"
            try:
                resp, cfg = _ml_api_request_com_retry(
                    client_id,
                    loja,
                    cfg,
                    "GET",
                    url,
                    timeout=35,
                    max_attempts=4,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                logger.warning(f"[ML API] Timeout/falha ao buscar itens da promocao {campaign_id}: {exc}")
                if progress_callback:
                    try:
                        progress_callback(f"Mercado Livre nao respondeu para a promocao {campaign_id}; tentando outro caminho de consulta.")
                    except Exception:
                        pass
                break
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                logger.warning(f"[ML API] Falha ao buscar itens da promocao {campaign_id}: {resp.status_code}")
                break
            encontrou_endpoint = True
            try:
                data = resp.json() or {}
            except Exception:
                data = {}
            entries = data.get("results") or data.get("items") or []
            def _entry_assinatura(entry):
                if isinstance(entry, dict):
                    raw_item = entry.get("item")
                    item_obj_id = raw_item.get("id") if isinstance(raw_item, dict) else raw_item
                    return str(entry.get("item_id") or entry.get("itemId") or item_obj_id or entry.get("id") or "")
                return str(entry or "")

            assinatura = tuple(_entry_assinatura(entry) for entry in entries[:20])
            if assinatura and assinatura in assinaturas_paginas:
                logger.warning(f"[PROMO API] Interrompendo paginacao repetida da promocao {campaign_id}")
                break
            if assinatura:
                assinaturas_paginas.add(assinatura)
            for entry in entries:
                item_id = ""
                if isinstance(entry, dict):
                    raw_id = str(entry.get("id") or "").strip()
                    raw_item = entry.get("item")
                    item_obj_id = raw_item.get("id") if isinstance(raw_item, dict) else raw_item
                    item_id = str(
                        entry.get("item_id")
                        or entry.get("itemId")
                        or entry.get("item_id_to")
                        or item_obj_id
                        or (raw_id if raw_id.upper().startswith("MLB") else "")
                        or ""
                    ).strip()
                    if item_id:
                        entry_contextualizado = _promo_contextualizar_entry_campanha(
                            entry,
                            campaign_id=campaign_id,
                            promotion_type_consultado=promotion_type_consultado,
                            item_id=item_id,
                            status_consultado=status_consultado,
                            status_param_consultado=status_param_consultado,
                        )
                        if not entry_contextualizado:
                            item_id = ""
                        else:
                            raw_por_item[item_id] = _promo_mesclar_raw_multistatus(
                                raw_por_item.get(item_id),
                                entry_contextualizado,
                            )
                else:
                    item_id = str(entry or "").strip()
                if item_id:
                    item_ids.append(item_id)
                if len(item_ids) >= max_items:
                    logger.warning(f"[PROMO API] Promocao {campaign_id} limitada a {max_items} anuncios para evitar travamento")
                    break
            if len(item_ids) >= max_items:
                break
            paging = data.get("paging") or {}
            total = int(paging.get("total") or 0)
            offset += limit
            if not entries or len(entries) < limit or (total and offset >= total):
                break
        if encontrou_endpoint and item_ids:
            break

    if item_ids and not forcar_fallback_pesado:
        item_ids = list(dict.fromkeys(item_ids))
        if not buscar_detalhes:
            itens = [{"id": item_id} for item_id in item_ids]
            return itens, raw_por_item, cfg
        itens, cfg = _ml_buscar_itens_batch(client_id, loja, cfg, item_ids, progress_callback=progress_callback)
        return itens, raw_por_item, cfg

    if not usar_fallback_pesado:
        logger.warning(f"[PROMO API] Promocao {campaign_id} sem itens no endpoint direto; fallback pesado desativado")
        if item_ids:
            ids_unicos = list(dict.fromkeys(item_ids))[:max_items]
            if not buscar_detalhes:
                itens = [{"id": item_id} for item_id in ids_unicos]
            else:
                itens, cfg = _ml_buscar_itens_batch(client_id, loja, cfg, ids_unicos, progress_callback=progress_callback)
            return itens, raw_por_item, cfg
        return [], raw_por_item, cfg

    user_id = cfg.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="ID do usuario Mercado Livre nao encontrado")

    campaign_norm = campaign_id.lower()
    try:
        all_ids, cfg = _ml_listar_ids_anuncios_ativos(
            client_id,
            loja,
            cfg,
            user_id,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        logger.warning("[PROMO API] Falha na varredura complementar da promocao %s: %s", campaign_id, exc)
        if progress_callback:
            try:
                progress_callback(
                    f"Mercado Livre demorou para responder na varredura complementar da promocao {campaign_id}; seguindo com os dados ja encontrados."
                )
            except Exception:
                pass
        all_ids = []
    logger.info(
        "[PROMO API] Promocao %s: iniciando varredura complementar de %s anuncios ativos%s",
        campaign_id,
        len(all_ids),
        f" (endpoint direto trouxe {len(set(item_ids))})" if item_ids else "",
    )
    participantes = []
    max_workers = min(6, max(2, len(all_ids))) if all_ids else 0
    if max_workers:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_ml_obter_promocoes_item, client_id, loja, dict(cfg), str(item_id)): str(item_id)
                for item_id in all_ids if item_id
            }
            for future in as_completed(future_map):
                item_id = future_map[future]
                try:
                    promocoes_item, _cfg_tmp = future.result()
                except Exception:
                    promocoes_item = []
                if campaign_norm in _ml_extrair_ids_promocoes_item(promocoes_item):
                    participantes.append({"id": item_id})
                    raw_encontrado = _ml_encontrar_promocao_raw_item(promocoes_item, campaign_id)
                    if raw_encontrado:
                        raw_por_item.setdefault(item_id, raw_encontrado)
                    if len(participantes) >= max_items:
                        logger.warning(f"[PROMO API] Varredura da promocao {campaign_id} limitada a {max_items} anuncios")
                        break
    ids_finais = list(dict.fromkeys(
        [str(item_id or "").strip() for item_id in item_ids if str(item_id or "").strip()]
        + [str(item.get("id") or "").strip() for item in participantes if str(item.get("id") or "").strip()]
    ))[:max_items]
    logger.info(
        "[PROMO API] Promocao %s: varredura complementar encontrou %s participante(s); total final=%s",
        campaign_id,
        len(participantes),
        len(ids_finais),
    )
    if buscar_detalhes and ids_finais:
        itens_finais, cfg = _ml_buscar_itens_batch(client_id, loja, cfg, ids_finais, progress_callback=progress_callback)
        return itens_finais, raw_por_item, cfg
    return [{"id": item_id} for item_id in ids_finais], raw_por_item, cfg


def _ml_listar_itens_promocao_multistatus_com_raw(
    client_id: str,
    loja: str,
    cfg: dict,
    campaign_id: str,
    *,
    promotion_type: str = "",
    max_items: int = 5000,
    buscar_detalhes: bool = False,
    expected_min_items: Optional[int] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
):
    consultas = [
        {"status_promocao": "pending", "status_item_preferencial": ""},
        {"status_promocao": "started", "status_item_preferencial": ""},
        {"status_promocao": "", "status_item_preferencial": "active"},
        {"status_promocao": "", "status_item_preferencial": ""},
        {"status_promocao": "candidate", "status_item_preferencial": ""},
        {"status_promocao": "eligible", "status_item_preferencial": ""},
    ]
    ids = []
    raw_total = {}
    for consulta in consultas:
        itens, raw_por_item, cfg = _ml_listar_itens_promocao_com_raw(
            client_id,
            loja,
            cfg,
            campaign_id,
            promotion_type=promotion_type,
            usar_fallback_pesado=False,
            max_items=max_items,
            buscar_detalhes=False,
            status_promocao=consulta["status_promocao"],
            status_item_preferencial=consulta["status_item_preferencial"],
            progress_callback=progress_callback,
        )
        for item in itens or []:
            item_id = str((item or {}).get("id") or "").strip()
            if item_id and item_id not in ids:
                ids.append(item_id)
        for item_id, raw in (raw_por_item or {}).items():
            item_key = str(item_id or "").strip()
            if item_key:
                raw_atual = raw_total.get(item_key)
                raw_total[item_key] = _promo_mesclar_raw_multistatus(raw_atual, raw)
        if len(ids) >= max_items:
            break

    expected_min = None
    try:
        if expected_min_items is not None:
            expected_min = max(0, min(int(expected_min_items), int(max_items)))
    except Exception:
        expected_min = None

    if ids and expected_min and len(ids) < expected_min:
        logger.warning(
            "[PROMO API] Promocao %s parece truncada: %s/%s itens. Acionando varredura complementar.",
            campaign_id,
            len(ids),
            expected_min,
        )
        itens_extra, raw_extra, cfg = _ml_listar_itens_promocao_com_raw(
            client_id,
            loja,
            cfg,
            campaign_id,
            promotion_type=promotion_type,
            usar_fallback_pesado=True,
            forcar_fallback_pesado=True,
            max_items=max_items,
            buscar_detalhes=False,
            status_promocao="",
            status_item_preferencial="",
            progress_callback=progress_callback,
        )
        for item in itens_extra or []:
            item_id = str((item or {}).get("id") or "").strip()
            if item_id and item_id not in ids:
                ids.append(item_id)
        for item_id, raw in (raw_extra or {}).items():
            item_key = str(item_id or "").strip()
            if item_key:
                raw_atual = raw_total.get(item_key)
                raw_total[item_key] = _promo_mesclar_raw_multistatus(raw_atual, raw)

    if not ids:
        return _ml_listar_itens_promocao_com_raw(
            client_id,
            loja,
            cfg,
            campaign_id,
            promotion_type=promotion_type,
            usar_fallback_pesado=True,
            max_items=max_items,
            buscar_detalhes=buscar_detalhes,
            status_promocao="",
            status_item_preferencial="",
            progress_callback=progress_callback,
        )

    ids = list(dict.fromkeys(ids))[:max_items]
    if buscar_detalhes:
        itens_detalhados, cfg = _ml_buscar_itens_batch(client_id, loja, cfg, ids, progress_callback=progress_callback)
        return itens_detalhados, raw_total, cfg
    return [{"id": item_id} for item_id in ids], raw_total, cfg

PEER_EXPORTS = ['_ml_obter_promocoes_item', '_ml_extrair_ids_promocoes_item', '_ml_encontrar_promocao_raw_item', '_promo_status_item_promocao', '_promo_prioridade_status_item', '_promo_entry_item_id', '_promo_erro_candidate_not_found', '_promo_consultar_item_na_campanha', '_calcular_desconto_ml_valor', '_ml_extrair_percentual_total_direto_promocao_raw', '_ml_resolver_percentual_desconto_campanha_raw', '_ml_parse_percentual_promocao_texto', '_ml_extrair_percentual_sugerido_campanha_raw', '_ml_obter_item_promocao_raw', '_ml_promocao_raw_texto', '_ml_promocao_raw_id', '_ml_promocao_raw_tipo', '_ml_promocao_raw_nome', '_ml_promocao_raw_offer_id', '_ml_grupo_promocao_por_meta', '_ml_grupo_promocao_raw', '_ml_resolver_raw_promocao_equivalente_para_analise', '_ml_promocao_raw_esta_ativa_ou_indefinida', '_ml_listar_ids_anuncios_ativos', '_ml_listar_itens_promocao_com_raw', '_ml_listar_itens_promocao_multistatus_com_raw']
__all__ = PEER_EXPORTS + ["configure_mercadolivre_legacy_promocoes_runtime"]

configure_mercadolivre_legacy_promocoes_runtime()
