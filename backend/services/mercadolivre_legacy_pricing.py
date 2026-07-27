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


def configure_mercadolivre_legacy_pricing_runtime(runtime_module=None, peers=None):
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


configure_mercadolivre_legacy_pricing_runtime()


def _ml_obter_preco_detalhado(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    fallback_price=None,
    request_fn=None,
    consultar_sale_price_sempre: bool = False,
):
    """ObtÃƒÂ©m preÃ§o real do anuncio usando /prices e /sale_price, conforme documentaÃƒÂ§ÃƒÂ£o 2026."""
    request_fn = request_fn or _ml_api_request
    info = {
        "price": fallback_price,
        "original_price": None,
        "standard_price": fallback_price,
        "has_promotion": False,
        "discount_pct": 0.0,
        "promotion_id": None,
        "promotion_type": None,
        "price_source": "item",
    }

    try:
        prices_url = f"https://api.mercadolibre.com/items/{item_id}/prices"
        prices_resp, cfg = request_fn(client_id, loja, cfg, "GET", prices_url, timeout=12)
        if prices_resp.status_code == 200:
            prices_data = prices_resp.json() or {}
            standard_price = None
            promo_candidates = []
            for price_obj in prices_data.get("prices", []) or []:
                conditions = price_obj.get("conditions") or {}
                contexts = conditions.get("context_restrictions") or []
                if contexts and "channel_marketplace" not in contexts:
                    continue
                price_type = (price_obj.get("type") or "").lower()
                amount = price_obj.get("amount")
                if price_type == "standard" and amount is not None:
                    standard_price = amount
                elif price_type == "promotion" and amount is not None:
                    promo_candidates.append(price_obj)

            if standard_price is not None:
                info["standard_price"] = standard_price
                if info["price"] is None:
                    info["price"] = standard_price
                info["price_source"] = "prices"

            if promo_candidates:
                best_promo = min(promo_candidates, key=lambda p: float(p.get("amount") or 0))
                info["price"] = best_promo.get("amount", info["price"])
                info["original_price"] = best_promo.get("regular_amount") or info.get("standard_price")
                info["has_promotion"] = True
                info["price_source"] = "prices"
    except Exception as e:
        logger.warning(f"[ML API] Falha ao consultar /prices do item {item_id}: {e}")

    need_sale_price = consultar_sale_price_sempre or info["price"] is None or info["has_promotion"]
    if need_sale_price:
        try:
            sale_url = f"https://api.mercadolibre.com/items/{item_id}/sale_price"
            sale_resp, cfg = request_fn(
                client_id,
                loja,
                cfg,
                "GET",
                sale_url,
                params={"context": "channel_marketplace"},
                timeout=12,
            )
            if sale_resp.status_code == 200:
                sale_data = sale_resp.json() or {}
                metadata = sale_data.get("metadata") or {}
                amount = sale_data.get("amount")
                regular_amount = sale_data.get("regular_amount")
                if amount is not None:
                    info["price"] = amount
                if regular_amount is not None:
                    info["original_price"] = regular_amount
                info["promotion_id"] = metadata.get("promotion_id")
                info["promotion_type"] = metadata.get("promotion_type")
                info["price_source"] = "sale_price"
                if info["original_price"] and info["price"] and float(info["original_price"]) > float(info["price"]):
                    info["has_promotion"] = True
        except Exception as e:
            logger.warning(f"[ML API] Falha ao consultar /sale_price do item {item_id}: {e}")

    if info["original_price"] and info["price"] and float(info["original_price"]) > float(info["price"]):
        info["discount_pct"] = round(((float(info["original_price"]) - float(info["price"])) / float(info["original_price"])) * 100, 1)
    else:
        info["discount_pct"] = 0.0
        if not info["has_promotion"]:
            info["original_price"] = None

    return info, cfg


def _ml_valor_embalagem_normalizado(atributo: dict, *, tipo: str) -> Optional[float]:
    if not isinstance(atributo, dict):
        return None

    candidatos = []
    value_struct = atributo.get("value_struct")
    if isinstance(value_struct, dict):
        candidatos.append((value_struct.get("number"), value_struct.get("unit")))

    value_name = str(atributo.get("value_name") or "").strip()
    if value_name:
        match = re.search(r"(-?\d+(?:[\.,]\d+)?)\s*([^\d\s]+)?", value_name)
        if match:
            candidatos.append((match.group(1), match.group(2)))

    conversoes = {
        "comprimento": {
            "mm": 0.1,
            "cm": 1.0,
            "m": 100.0,
            "in": 2.54,
            "pol": 2.54,
        },
        "peso": {
            "mg": 0.001,
            "g": 1.0,
            "kg": 1000.0,
            "oz": 28.349523125,
            "lb": 453.59237,
        },
    }
    unidades = conversoes.get(tipo) or {}
    for numero_raw, unidade_raw in candidatos:
        try:
            numero = float(str(numero_raw).strip().replace(",", "."))
        except Exception:
            continue
        unidade = unicodedata.normalize("NFKD", str(unidade_raw or "").strip().lower())
        unidade = "".join(ch for ch in unidade if not unicodedata.combining(ch)).rstrip(".")
        fator = unidades.get(unidade)
        if fator is None:
            continue
        valor = numero * fator
        if math.isfinite(valor) and valor > 0:
            return float(valor)
    return None


def _ml_formatar_numero_dimensao(valor: float) -> str:
    if abs(float(valor) - round(float(valor))) <= 1e-9:
        return str(int(round(float(valor))))
    return f"{float(valor):.3f}".rstrip("0").rstrip(".")


def _ml_extrair_dimensoes_embalagem_item(item: dict) -> str:
    atributos = item.get("attributes") if isinstance(item, dict) else None
    if not isinstance(atributos, list):
        return ""
    por_id = {
        str(atributo.get("id") or "").strip().upper(): atributo
        for atributo in atributos
        if isinstance(atributo, dict) and atributo.get("id")
    }
    comprimento = _ml_valor_embalagem_normalizado(
        por_id.get("SELLER_PACKAGE_LENGTH") or {},
        tipo="comprimento",
    )
    largura = _ml_valor_embalagem_normalizado(
        por_id.get("SELLER_PACKAGE_WIDTH") or {},
        tipo="comprimento",
    )
    altura = _ml_valor_embalagem_normalizado(
        por_id.get("SELLER_PACKAGE_HEIGHT") or {},
        tipo="comprimento",
    )
    peso = _ml_valor_embalagem_normalizado(
        por_id.get("SELLER_PACKAGE_WEIGHT") or {},
        tipo="peso",
    )
    if any(valor is None for valor in (comprimento, largura, altura, peso)):
        return ""
    return "x".join(
        _ml_formatar_numero_dimensao(valor)
        for valor in (comprimento, largura, altura)
    ) + f",{_ml_formatar_numero_dimensao(peso)}"


def _ml_contexto_frete_item(item: dict, item_price: Any = None) -> dict:
    item = item or {}
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    dimensions = str(shipping.get("dimensions") or "").strip()
    if not dimensions or dimensions.lower() == "null":
        dimensions = _ml_extrair_dimensoes_embalagem_item(item)
    contexto = {
        "item_price": item_price if item_price not in (None, "") else item.get("price"),
        "listing_type_id": item.get("listing_type_id") or "",
        "condition": item.get("condition") or "new",
        "category_id": item.get("category_id") or "",
        "mode": shipping.get("mode") or "",
        "logistic_type": shipping.get("logistic_type") or "",
        "dimensions": dimensions,
        "free_shipping": bool(shipping.get("free_shipping")),
    }
    return contexto


def _ml_obter_frete_detalhado(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    shipping_info: Optional[dict] = None,
    request_fn=None,
    reconsultar_zero: bool = False,
    contexto_frete: Optional[dict] = None,
):
    """ObtÃƒÂ©m o custo de frete cobrado pelo Mercado Livre no anuncio especÃƒÂ­fico."""
    request_fn = request_fn or _ml_api_request
    shipping_info = shipping_info or {}
    contexto_frete = contexto_frete or {}

    def _shipping_to_money(raw):
        try:
            if raw is None or raw == "":
                return None
            return float(raw)
        except Exception:
            return None

    def _shipping_to_bool(raw, default: bool = False) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return raw != 0
        texto = str(raw or "").strip().lower()
        if texto in {"true", "1", "yes", "sim"}:
            return True
        if texto in {"false", "0", "no", "nao", "não"}:
            return False
        return bool(default)

    def _deve_reconsultar_zero(payload: dict) -> bool:
        if not reconsultar_zero:
            return False
        valor = _shipping_to_money((payload or {}).get("shipping_cost"))
        if valor is None or valor > 0:
            return False
        buyer = _shipping_to_money((payload or {}).get("shipping_buyer_cost"))
        return bool((payload or {}).get("free_shipping")) or (buyer is not None and buyer <= 0)

    def _pick_contexto(*chaves):
        for chave in chaves:
            valor = contexto_frete.get(chave)
            if valor not in (None, ""):
                return valor
        return None

    contexto_preco = _shipping_to_money(_pick_contexto("item_price", "price", "preco", "preco_final"))
    contexto_listing_type = str(_pick_contexto("listing_type_id", "listing_type") or "").strip()
    contexto_mode = str(_pick_contexto("mode", "shipping_mode") or shipping_info.get("mode") or "").strip()
    contexto_logistic_type = str(_pick_contexto("logistic_type") or shipping_info.get("logistic_type") or "").strip()
    contexto_dimensions = str(_pick_contexto("dimensions") or shipping_info.get("dimensions") or "").strip()
    if contexto_dimensions.lower() == "null":
        contexto_dimensions = ""
    contexto_free_shipping = _shipping_to_bool(
        _pick_contexto("free_shipping"),
        default=_shipping_to_bool(shipping_info.get("free_shipping")),
    )
    dimensions_cache = re.sub(r"\s+", "", contexto_dimensions.lower()) or "-"
    preco_cache = round(float(contexto_preco), 2) if contexto_preco is not None else "-"
    contexto_cache = (
        f":p{preco_cache}:{contexto_listing_type}:{contexto_mode}:"
        f"{contexto_logistic_type}:d{dimensions_cache}:fs{int(contexto_free_shipping)}"
    )

    cache_key = f"v7:{client_id}:{loja}:{item_id}{contexto_cache}"
    cached = _cache_get(ML_ITEM_SHIPPING_CACHE, cache_key, ML_ITEM_SHIPPING_CACHE_TTL)
    exige_frete_exato = bool(contexto_preco is not None and contexto_preco > 0 and cfg.get("user_id"))
    cache_utilizavel = bool(
        cached
        and cached.get("shipping_cost") is not None
        and not _deve_reconsultar_zero(cached)
        and (not exige_frete_exato or cached.get("shipping_exact_for_price") is True)
    )
    if cache_utilizavel:
        return cached, cfg

    info = {
        "shipping_cost": None,
        "shipping_text": "A calcular",
        "shipping_buyer_cost": None,
        "shipping_buyer_text": "",
        "shipping_list_cost": None,
        "shipping_base_cost": None,
        "shipping_seller_cost": None,
        "shipping_breakdown": "",
        "free_shipping": contexto_free_shipping,
        "logistic_type": shipping_info.get("logistic_type") or "",
        "shipping_mode": shipping_info.get("mode") or "",
        "shipping_zip": "01310930",
        "shipping_exact_for_price": False,
        "shipping_price_context": contexto_preco,
        "shipping_dimensions_context": contexto_dimensions,
        "shipping_cost_source_path": "",
    }

    def _candidatos_positivos_frete_payload(payload) -> list[tuple[float, str]]:
        candidatos = []

        def _add(valor, caminho: str):
            val = _shipping_to_money(valor)
            if val is not None and val > 0:
                candidatos.append((float(val), caminho))

        if not isinstance(payload, dict):
            return candidatos

        coverage = payload.get("coverage")
        if isinstance(coverage, dict):
            all_country = coverage.get("all_country")
            if isinstance(all_country, dict):
                for campo in ("list_cost", "base_cost", "cost", "seller_cost", "shipping_cost"):
                    _add(all_country.get(campo), f"coverage.all_country.{campo}")
                discount = all_country.get("discount")
                if isinstance(discount, dict):
                    _add(discount.get("promoted_amount"), "coverage.all_country.discount.promoted_amount")
            for chave_cov, valor_cov in coverage.items():
                if chave_cov == "all_country":
                    continue
                if isinstance(valor_cov, dict):
                    for campo in ("list_cost", "base_cost", "cost", "seller_cost", "shipping_cost"):
                        _add(valor_cov.get(campo), f"coverage.{chave_cov}.{campo}")

        options = payload.get("options")
        if isinstance(options, dict):
            options = list(options.values())
        if isinstance(options, list):
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                for campo in ("base_cost", "list_cost", "seller_cost", "shipping_cost", "cost"):
                    _add(opt.get(campo), f"options[].{campo}")

        for campo in ("base_cost", "list_cost", "seller_cost", "shipping_cost", "cost"):
            _add(payload.get(campo), f"payload.{campo}")

        return candidatos

    def _frete_autoritativo_contextual(payload) -> tuple[float | None, str]:
        if not isinstance(payload, dict):
            return None, ""
        coverage = payload.get("coverage")
        all_country = coverage.get("all_country") if isinstance(coverage, dict) else None
        if not isinstance(all_country, dict):
            return None, ""
        for campo in ("seller_cost", "list_cost"):
            valor = _shipping_to_money(all_country.get(campo))
            if valor is not None and valor >= 0:
                return float(valor), f"coverage.all_country.{campo}"
        return None, ""

    def _payload_indica_frete_gratis(payload) -> bool:
        if contexto_free_shipping:
            return True
        if not isinstance(payload, dict):
            return False
        coverage = payload.get("coverage")
        all_country = coverage.get("all_country") if isinstance(coverage, dict) else None
        if not isinstance(all_country, dict):
            return False
        if _shipping_to_bool(all_country.get("free_shipping_by_meli")):
            return True
        discount = all_country.get("discount")
        tipo_desconto = str((discount or {}).get("type") or "").strip().lower() if isinstance(discount, dict) else ""
        return tipo_desconto in {"mandatory", "mandatory_free_shipping"}

    def _params_frete_gratis_contexto() -> dict:
        params = {"item_id": item_id}
        preco = contexto_preco
        if preco is not None and preco > 0:
            params["item_price"] = round(float(preco), 2)
        listing_type = contexto_listing_type
        if listing_type:
            params["listing_type_id"] = listing_type
        condition = str(_pick_contexto("condition") or "").strip()
        if condition:
            params["condition"] = condition
        category_id = str(_pick_contexto("category_id") or "").strip()
        if category_id:
            params["category_id"] = category_id
        mode = contexto_mode
        if mode:
            params["mode"] = mode
        logistic_type = contexto_logistic_type
        if logistic_type:
            params["logistic_type"] = logistic_type
        if contexto_dimensions:
            params["dimensions"] = contexto_dimensions
        # A API diferencia explicitamente as simulacoes com frete gratis
        # opcional e com frete por conta do comprador.
        params["free_shipping"] = "true" if contexto_free_shipping else "false"
        params["verbose"] = "true"
        return params

    def _aplicar_frete_payload(payload: dict, fonte: str, *, exato_para_preco: bool = False) -> bool:
        charged_cost = None
        caminho_origem = ""
        frete_exato = False
        if exato_para_preco:
            charged_cost, caminho_origem = _frete_autoritativo_contextual(payload)
            frete_exato = charged_cost is not None
        if charged_cost is None:
            candidatos = _candidatos_positivos_frete_payload(payload)
            if not candidatos:
                return False
            charged_cost, caminho_origem = min(candidatos, key=lambda candidato: candidato[0])
        info["shipping_cost"] = charged_cost
        info["shipping_seller_cost"] = charged_cost
        info["shipping_text"] = "Gratis" if charged_cost <= 0 else f"R$ {charged_cost:.2f}"
        info["shipping_breakdown"] = (
            f"Custo vendedor: {info['shipping_text']} | Fonte: {fonte}:{caminho_origem}"
        )
        info["shipping_cost_retry_source"] = fonte
        info["shipping_cost_source_path"] = caminho_origem
        info["shipping_exact_for_price"] = frete_exato
        if exato_para_preco:
            info["shipping_buyer_cost"] = 0.0
            info["shipping_buyer_text"] = "GrÃ¡tis"
            info["free_shipping"] = True
            coverage = payload.get("coverage") if isinstance(payload, dict) else None
            all_country = coverage.get("all_country") if isinstance(coverage, dict) else None
            list_cost = _shipping_to_money(all_country.get("list_cost")) if isinstance(all_country, dict) else None
            if list_cost is not None:
                info["shipping_list_cost"] = list_cost
            frete_gratis_comprador = _payload_indica_frete_gratis(payload)
            info["free_shipping"] = frete_gratis_comprador
            if not frete_gratis_comprador:
                info["shipping_buyer_cost"] = None
                info["shipping_buyer_text"] = ""
        return True

    item_list_cost = _shipping_to_money(shipping_info.get("list_cost"))
    item_base_cost = _shipping_to_money(shipping_info.get("base_cost"))
    item_cost = _shipping_to_money(shipping_info.get("cost"))
    info["shipping_list_cost"] = item_list_cost
    info["shipping_base_cost"] = item_base_cost

    candidatos_iniciais = []
    if info["free_shipping"]:
        candidatos_iniciais = [
            (item_base_cost, "base_cost"),
            (item_list_cost, "list_cost"),
            (item_cost, "cost"),
        ]
    else:
        candidatos_iniciais = [
            (item_list_cost, "list_cost"),
            (item_base_cost, "base_cost"),
            (item_cost, "cost"),
        ]

    valores_iniciais = [(valor, fonte) for valor, fonte in candidatos_iniciais if valor is not None]
    if valores_iniciais:
        if info["free_shipping"]:
            positivos = [(valor, fonte) for valor, fonte in valores_iniciais if valor > 0]
            val_inicial, fonte_inicial = min(positivos or valores_iniciais, key=lambda item: item[0])
        else:
            val_inicial, fonte_inicial = valores_iniciais[0]
        info["shipping_cost"] = val_inicial
        info["shipping_seller_cost"] = val_inicial
        info["shipping_text"] = "Gratis" if val_inicial <= 0 else f"R$ {val_inicial:.2f}"
        info["shipping_breakdown"] = f"Custo vendedor: {info['shipping_text']} | Fonte: item.shipping.{fonte_inicial}"

    # O custo do vendedor muda conforme o preco final mesmo quando o anuncio
    # atual esta com free_shipping=false. Consulta sempre o endpoint que aceita
    # item_price; o endpoint generico considera apenas o preco atual do anuncio.
    if contexto_preco is not None and contexto_preco > 0 and cfg.get("user_id"):
        try:
            fonte_contextual = "users/shipping_options/free/contexto"
            resp, cfg = request_fn(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/users/{cfg.get('user_id')}/shipping_options/free",
                params=_params_frete_gratis_contexto(),
                timeout=12,
            )
            if resp.status_code == 200 and _aplicar_frete_payload(
                resp.json() or {},
                fonte_contextual,
                exato_para_preco=True,
            ):
                _cache_set(ML_ITEM_SHIPPING_CACHE, cache_key, info)
                return info, cfg
        except Exception as e:
            logger.warning(f"[ML API] Falha ao consultar frete contextual do item {item_id}: {e}")

    try:
        url = f"https://api.mercadolibre.com/items/{item_id}/shipping_options"
        params = {"zip_code": info["shipping_zip"]}
        if info["free_shipping"]:
            params["free_shipping"] = "true"

        resp, cfg = request_fn(
            client_id,
            loja,
            cfg,
            "GET",
            url,
            params=params,
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            options = data.get("options", []) or []
            if options:
                best_option = next((opt for opt in options if opt.get("display") == "recommended"), options[0])

                def _to_money(raw):
                    try:
                        if raw is None or raw == "":
                            return None
                        return float(raw)
                    except Exception:
                        return None

                buyer_cost = _shipping_to_money(best_option.get("cost"))
                list_cost = _shipping_to_money(best_option.get("list_cost"))
                base_cost = _shipping_to_money(best_option.get("base_cost"))
                charged_cost = list_cost

                if info["free_shipping"]:
                    candidatos_seller = [valor for valor in (base_cost, list_cost) if valor is not None]
                    positivos_seller = [valor for valor in candidatos_seller if valor > 0]
                    if positivos_seller:
                        charged_cost = min(positivos_seller)
                    elif candidatos_seller:
                        charged_cost = min(candidatos_seller)
                    else:
                        charged_cost = None
                elif charged_cost is None:
                    charged_cost = base_cost
                if not info["free_shipping"] and charged_cost is None and buyer_cost is not None and buyer_cost > 0:
                    # Se o comprador paga frete, o custo do vendedor para a margem e zero.
                    charged_cost = 0.0

                info["shipping_buyer_cost"] = buyer_cost
                info["shipping_list_cost"] = list_cost
                info["shipping_base_cost"] = base_cost
                if charged_cost is not None:
                    info["shipping_cost"] = charged_cost
                    info["shipping_seller_cost"] = charged_cost

                if buyer_cost is not None:
                    info["shipping_buyer_text"] = "GrÃƒÂ¡tis" if buyer_cost <= 0 else f"R$ {buyer_cost:.2f}"
                if charged_cost is not None:
                    info["shipping_text"] = "Gratis" if charged_cost <= 0 else f"R$ {charged_cost:.2f}"
                if base_cost is not None and buyer_cost is not None:
                    partes_frete = [
                        f"Cliente: {info['shipping_buyer_text']}",
                        f"Custo vendedor: {info['shipping_text']}",
                        f"Base: R$ {base_cost:.2f}",
                    ]
                    if list_cost is not None:
                        partes_frete.append(f"Lista: R$ {list_cost:.2f}")
                    info["shipping_breakdown"] = " | ".join(partes_frete)
                elif charged_cost is not None:
                    info["shipping_breakdown"] = f"Custo vendedor: {info['shipping_text']}"
                elif buyer_cost is not None and buyer_cost <= 0:
                    info["shipping_breakdown"] = "Cliente: GrÃƒÆ’Ã‚Â¡tis | Custo ML: A calcular"
    except Exception as e:
        logger.warning(f"[ML API] Falha ao consultar frete do item {item_id}: {e}")

    if (info["shipping_cost"] is None or _deve_reconsultar_zero(info)) and cfg.get("user_id"):
        try:
            tentativas_frete_gratis = [
                (
                    f"https://api.mercadolibre.com/users/{cfg.get('user_id')}/shipping_options/free",
                    _params_frete_gratis_contexto(),
                    "users/shipping_options/free/contexto",
                ),
                (
                    f"https://api.mercadolibre.com/users/{cfg.get('user_id')}/shipping_options/free",
                    {"item_id": item_id},
                    "users/shipping_options/free/item_id",
                ),
                (
                    f"https://api.mercadolibre.com/items/{item_id}/shipping_options/free",
                    {},
                    "items/shipping_options/free",
                ),
            ]
            for url_retry, params_retry, fonte_retry in tentativas_frete_gratis:
                resp, cfg = request_fn(
                    client_id,
                    loja,
                    cfg,
                    "GET",
                    url_retry,
                    params=params_retry,
                    timeout=12,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json() or {}
                if _aplicar_frete_payload(
                    data,
                    fonte_retry,
                    exato_para_preco=bool(contexto_preco is not None and fonte_retry.endswith("/contexto")),
                ):
                    break
        except Exception as e:
            logger.warning(f"[ML API] Falha ao consultar frete grÃ¡tis do item {item_id}: {e}")

    _cache_set(ML_ITEM_SHIPPING_CACHE, cache_key, info)
    return info, cfg


def _ml_montar_preco_listagem(item: dict) -> dict:
    price = item.get("price")
    original_price = item.get("original_price")
    has_promotion = False
    discount_pct = 0.0

    try:
        if original_price is not None and price is not None and float(original_price) > float(price):
            has_promotion = True
            discount_pct = round(((float(original_price) - float(price)) / float(original_price)) * 100, 1)
    except Exception:
        pass

    if item.get("deal_ids"):
        has_promotion = True

    return {
        "price": price,
        "standard_price": original_price or price,
        "original_price": original_price if has_promotion else None,
        "price_source": "item",
        "has_promotion": has_promotion,
        "discount_pct": discount_pct,
        "promotion_id": None,
        "promotion_type": None,
    }


def _ml_estimar_taxa_fixa_por_preco(
    price: Any,
    domain_id: str = "",
    category_id: str = "",
    listing_type_id: str = "",
) -> float | None:
    """
    Estima taxa fixa ML para itens abaixo de R$ 79 quando a API nÃ£o retornar fixed_fee.
    Regras baseadas na tabela informada pelo usuÃƒÂ¡rio.
    """
    p = _parse_float_flex(price)
    if p is None or p <= 0:
        return None
    if p >= 79:
        return 0.0

    dom = str(domain_id or "").upper().strip()
    cat = str(category_id or "").upper().strip()
    listing_type = str(listing_type_id or "").strip().lower()
    is_books = ("BOOK" in dom) or ("LIVRO" in dom) or ("BOOK" in cat)

    if is_books:
        if p <= 12.50:
            return 3.0
        if p <= 29.00:
            return 3.5
        if p <= 50.00:
            return 4.0
        # Faixa > 50 e < 79 nÃ£o foi informada para Livros.
        return None

    # Regra especial da seÃƒÂ§ÃƒÂ£o "produtos-novos-gratis":
    # em anuncios gratuitos abaixo de R$ 12,50 a taxa fixa fica limitada a 50% do preÃ§o.
    if p < 12.50 and listing_type == "free":
        return round(p * 0.5, 2)
    if p <= 12.50:
        return 6.25
    if p <= 29.00:
        return 6.50
    if p <= 50.00:
        return 6.75
    # Exemplo prÃƒÂ¡tico enviado pelo usuÃƒÂ¡rio (~R$ 61,68 => R$ 7,75).
    return 7.75


def _ml_obter_taxas_anuncio(client_id: str, loja: str, cfg: dict, item: dict, request_fn=None):
    """ObtÃƒÂ©m o custo do anuncio e a taxa fixa usando a API de listing_prices do Mercado Livre."""
    request_fn = request_fn or _ml_api_request
    info = {
        "ad_cost": None,
        "ad_cost_text": "-",
        "fixed_fee_amount": None,
        "fixed_fee_text": "-",
        "listing_fee_amount": None,
        "listing_fee_text": "-",
        "sale_fee_pct": None,
        "meli_fee_pct": None,
        "financing_fee_pct": None,
        "listing_type_name": _ml_nome_tipo_anuncio(item.get('listing_type_id')),
        "fee_breakdown": "",
        "pct_scope": "",
        "pct_note": "",
        "ad_cost_source": "",
        "ad_cost_exact_for_price": False,
        "ad_cost_price_context": _to_float_safe(item.get("price")),
    }

    category_id = item.get("category_id")
    listing_type_id = item.get("listing_type_id")
    price = item.get("price")
    if not category_id or not listing_type_id or price in (None, ""):
        return info, cfg

    shipping_info = item.get("shipping") or {}
    logistic_type = str(shipping_info.get("logistic_type") or "").strip()
    shipping_mode = str(shipping_info.get("mode") or "").strip()

    cache_key = f"v5:{client_id}:{category_id}:{listing_type_id}:{price}:{logistic_type}:{shipping_mode}"
    cached = _cache_get(ML_LISTING_FEE_CACHE, cache_key, ML_LISTING_FEE_CACHE_TTL)
    if cached:
        return cached, cfg

    def _to_float(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    try:
        resp, cfg = request_fn(
            client_id,
            loja,
            cfg,
            "GET",
            "https://api.mercadolibre.com/sites/MLB/listing_prices",
            params={
                "price": price,
                "listing_type_id": listing_type_id,
                "category_id": category_id,
                **({"logistic_type": logistic_type} if logistic_type else {}),
                **({"shipping_mode": shipping_mode} if shipping_mode else {}),
            },
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            sale_fee_amount = _to_float(data.get("sale_fee_amount"))
            listing_fee_amount = _to_float(data.get("listing_fee_amount"))
            sale_fee_details = data.get("sale_fee_details") or {}
            listing_fee_details = data.get("listing_fee_details") or {}
            fixed_fee_amount = _to_float(sale_fee_details.get("fixed_fee"))
            if fixed_fee_amount is None:
                fixed_fee_amount = _to_float(listing_fee_details.get("fixed_fee"))
            percentage_fee = _to_float(sale_fee_details.get("percentage_fee"))
            meli_percentage_fee = _to_float(sale_fee_details.get("meli_percentage_fee"))
            financing_add_on_fee = _to_float(sale_fee_details.get("financing_add_on_fee"))
            info["listing_type_name"] = data.get("listing_type_name") or info["listing_type_name"]

            info["ad_cost"] = sale_fee_amount
            if sale_fee_amount is not None:
                info["ad_cost_source"] = "sites/MLB/listing_prices"
                info["ad_cost_exact_for_price"] = True
            info["listing_fee_amount"] = listing_fee_amount
            info["fixed_fee_amount"] = fixed_fee_amount
            # Exibe exatamente o percentual disponibilizado pela API: primeiro a tarifa ML
            # separada, quando existir, e depois o percentual total retornado.
            sale_fee_pct_display = meli_percentage_fee if meli_percentage_fee is not None else percentage_fee
            info["sale_fee_pct"] = sale_fee_pct_display
            info["meli_fee_pct"] = meli_percentage_fee
            info["financing_fee_pct"] = financing_add_on_fee
            info["ad_cost_text"] = f"R$ {sale_fee_amount:.2f}" if sale_fee_amount is not None else "-"
            info["listing_fee_text"] = f"R$ {listing_fee_amount:.2f}" if listing_fee_amount is not None else "-"
            info["fixed_fee_text"] = f"R$ {fixed_fee_amount:.2f}" if fixed_fee_amount not in (None, 0) else "-"
            if meli_percentage_fee is not None:
                info["pct_scope"] = "selling_fee"
            elif percentage_fee is not None:
                info["pct_scope"] = "total_commission"
                info["pct_note"] = (
                    "Percentual total retornado pela API oficial listing_prices."
                )

            partes = []
            if sale_fee_amount is not None:
                partes.append(f"Tarifa venda: R$ {sale_fee_amount:.2f}")
            if sale_fee_pct_display is not None:
                partes.append(f"Taxa ML: {sale_fee_pct_display:.0f}%")
            if financing_add_on_fee is not None:
                partes.append(f"Parcelamento: {financing_add_on_fee:.0f}%")
            if percentage_fee is not None:
                partes.append(f"Percentual total: {percentage_fee:.0f}%")
            if listing_fee_amount is not None:
                partes.append(f"PublicaÃƒÂ§ÃƒÂ£o: R$ {listing_fee_amount:.2f}")
            if fixed_fee_amount not in (None, 0):
                partes.append(f"Taxa fixa: R$ {fixed_fee_amount:.2f}")
            info["fee_breakdown"] = " | ".join(partes)
    except Exception as e:
        logger.warning(f"[ML API] Falha ao consultar taxas do anuncio {item.get('id')}: {e}")

    if info["fixed_fee_amount"] is None:
        estimada = _ml_estimar_taxa_fixa_por_preco(
            item.get("price"),
            domain_id=item.get("domain_id") or "",
            category_id=item.get("category_id") or "",
            listing_type_id=item.get("listing_type_id") or "",
        )
        if estimada is not None:
            info["fixed_fee_amount"] = estimada
            info["fixed_fee_text"] = f"R$ {estimada:.2f}"
            if info.get("fee_breakdown"):
                info["fee_breakdown"] = f"{info['fee_breakdown']} | Taxa fixa (estimada): R$ {estimada:.2f}"
            else:
                info["fee_breakdown"] = f"Taxa fixa (estimada): R$ {estimada:.2f}"

    _cache_set(ML_LISTING_FEE_CACHE, cache_key, info)
    return info, cfg


def _ml_extrair_recebivel_promocao_raw(entry: dict):
    """Extrai o valor que o vendedor recebe quando o payload da promocao ja traz esse campo."""
    if not isinstance(entry, dict):
        return None

    chaves_recebivel = {
        "receives",
        "receive",
        "seller_receives",
        "seller_receive",
        "seller_net",
        "net_amount",
        "net_value",
        "net_price",
        "liquid_amount",
        "liquid_value",
        "liquido",
        "valor_liquido",
    }
    for caminho, valor in _ml_iterar_campos_payload_limitado(entry, max_depth=6, max_nodes=1200):
        chave = str(caminho or "").rsplit(".", 1)[-1].strip().lower().replace("-", "_")
        chave = re.sub(r"[^a-z0-9_]", "", chave)
        caminho_norm = normalizar_texto(caminho)
        parece_recebivel = (
            chave in chaves_recebivel
            or chave.replace("_", "") in {c.replace("_", "") for c in chaves_recebivel}
            or "seller_receives" in str(caminho or "").lower()
            or "seller_receive" in str(caminho or "").lower()
            or "recebe" in caminho_norm
            or "valor_liquido" in caminho_norm
        )
        if not parece_recebivel:
            continue
        numero = _parse_float_flex(valor)
        if numero is not None and numero > 0:
            return float(numero)
    return None


def _ml_calcular_recebivel_promocao(
    raw_promocao: dict,
    preco_promocional: Any,
    tarifa_ml: Any,
    frete_ml: Any,
    desconto_tarifa_ml: Any = None,
) -> float | None:
    """Calcula o valor exibido pelo ML como recebimento do vendedor na promocao."""
    recebido_api = _ml_extrair_recebivel_promocao_raw(raw_promocao)
    if recebido_api is not None:
        return recebido_api

    preco = _to_float_safe(preco_promocional)
    if preco is None:
        return None
    tarifa = _to_float_safe(tarifa_ml) or 0.0
    frete = _to_float_safe(frete_ml) or 0.0
    desconto_tarifa = _to_float_safe(desconto_tarifa_ml) or 0.0
    recebido = float(preco) - float(tarifa) - float(frete) + float(desconto_tarifa)
    if recebido > 0:
        return round(recebido, 2)
    return None


def _ml_ajustar_desconto_tarifa_recebivel_promocao(
    raw_promocao: dict,
    desconto_tarifa_ml: Any = None,
    preco_promocional: Any = None,
    pct_desconto_campanha: Any = None,
    tarifa_ml: Any = None,
    frete_ml: Any = None,
    frete_exato: bool = False,
    frete_preco_contexto: Any = None,
    tarifa_exata: bool = False,
    tarifa_preco_contexto: Any = None,
    tarifa_fonte: Any = None,
    frete_fonte: Any = None,
) -> float | None:
    """Ajusta a reducao de tarifa usada no recebivel mostrado pelo ML."""
    estado_boost, desconto_boost = _ml_resolver_desconto_boost_tarifa_promocao_raw(raw_promocao)
    if estado_boost == "boosted":
        return round(float(desconto_boost), 2)
    if estado_boost == "sem_boost":
        return 0.0

    desconto_explicito = _ml_extrair_valor_desconto_taxa_promocao_raw(raw_promocao)
    if desconto_explicito is not None and desconto_explicito > 0:
        return round(float(desconto_explicito), 2)

    # Alguns payloads do ML ainda omitem os campos novos de boost, mas trazem
    # o recebivel final. Nesse caso a reducao de tarifa pode ser conciliada sem
    # usar meli_percentage: recebe - (preco - tarifa normal - frete).
    recebido_raw = raw_promocao.get("seller_receives") if isinstance(raw_promocao, dict) else None
    if isinstance(recebido_raw, dict):
        recebido_raw = recebido_raw.get("amount") or recebido_raw.get("value")
    recebido = _to_float_safe(recebido_raw)
    preco = _to_float_safe(preco_promocional)
    tarifa = _to_float_safe(tarifa_ml)
    frete = _to_float_safe(frete_ml)
    frete_contexto = _to_float_safe(frete_preco_contexto)
    tarifa_contexto = _to_float_safe(tarifa_preco_contexto)
    tarifa_fonte_txt = str(tarifa_fonte or "").strip()
    frete_fonte_txt = str(frete_fonte or "").strip()
    preco_raw, _ = _ml_extrair_preco_promocao_raw(raw_promocao)
    if (
        recebido is not None
        and preco is not None
        and preco_raw is not None
        and abs(float(preco_raw) - float(preco)) <= 0.02
        and tarifa is not None
        and tarifa >= 0
        and tarifa_exata is True
        and tarifa_contexto is not None
        and abs(float(tarifa_contexto) - float(preco)) <= 0.02
        and tarifa_fonte_txt == "sites/MLB/listing_prices"
        and frete is not None
        and frete >= 0
        and frete_exato is True
        and frete_contexto is not None
        and abs(float(frete_contexto) - float(preco)) <= 0.02
        and frete_fonte_txt == "users/shipping_options/free/contexto"
    ):
        desconto_conciliado = float(recebido) - (float(preco) - float(tarifa) - float(frete))
        if desconto_conciliado > 0.005 and desconto_conciliado <= float(tarifa) + 0.02:
            return round(desconto_conciliado, 2)

    # Sem evidencia atual no payload, nao reutiliza valor historico ou importado.
    # Isso impede que o antigo calculo por meli_percentage contamine a margem.
    return None


def _flag_frete_gratis(valor: Any) -> bool:
    txt = str(valor or "").strip().lower()
    if not txt:
        return False
    return txt in {"sim", "s", "yes", "true", "1"}


def _ajustar_frete_por_preco_base(frete_base: Any, preco_base: Any, preco_alvo: Any) -> float | None:
    frete_ref = _parse_float_flex(frete_base)
    p_base = _parse_float_flex(preco_base)
    p_alvo = _parse_float_flex(preco_alvo)
    if frete_ref is None or p_base is None or p_alvo is None:
        return None
    if p_base <= 0 or p_alvo <= 0:
        return None

    frete_ajustado = float(frete_ref) * (float(p_alvo) / float(p_base))
    # Regra do ML: abaixo de R$ 19, o custo nÃ£o pode ultrapassar metade do valor do produto.
    if float(p_alvo) < 19.0:
        frete_ajustado = min(frete_ajustado, float(p_alvo) * 0.5)
    return round(frete_ajustado, 2)


def _ml_price_band_index(price: Any) -> int | None:
    p = _parse_float_flex(price)
    if p is None or p < 0:
        return None
    if p <= 18.99:
        return 0
    if p <= 48.99:
        return 1
    if p <= 78.99:
        return 2
    if p <= 99.99:
        return 3
    if p <= 119.99:
        return 4
    if p <= 149.99:
        return 5
    if p <= 199.99:
        return 6
    return 7


def _recalcular_frete_por_grade_oficial(
    frete_base: Any,
    preco_base: Any,
    preco_alvo: Any,
) -> float | None:
    frete_ref = _parse_float_flex(frete_base)
    base_band = _ml_price_band_index(preco_base)
    alvo_band = _ml_price_band_index(preco_alvo)
    if frete_ref is None or base_band is None or alvo_band is None:
        return None

    row_match = None
    for _peso_label, valores in ML_FRETE_GRADE_OFICIAL:
        if base_band < len(valores) and abs(float(valores[base_band]) - float(frete_ref)) <= 0.02:
            row_match = valores
            break

    if row_match is None:
        return None
    if alvo_band >= len(row_match):
        return None
    return round(float(row_match[alvo_band]), 2)


def _recalcular_frete_por_faixa_ml(
    frete_base: Any,
    preco_base: Any,
    preco_alvo: Any,
    listing_type_id: str = "",
) -> float | None:
    """
    Quando o frete base coincide com a faixa oficial do ML para o preÃ§o-base,
    recalcula o frete do preÃ§o-alvo usando a mesma tabela/faixa.
    Se nÃ£o coincidir, retorna None para permitir fallback proporcional.
    """
    recalculado = _recalcular_frete_por_grade_oficial(frete_base, preco_base, preco_alvo)
    if recalculado is not None:
        return recalculado

    frete_ref = _parse_float_flex(frete_base)
    p_base = _parse_float_flex(preco_base)
    p_alvo = _parse_float_flex(preco_alvo)
    if frete_ref is None or p_base is None or p_alvo is None:
        return None
    if p_base <= 0 or p_alvo <= 0:
        return None

    frete_faixa_base = _ml_estimar_taxa_fixa_por_preco(p_base, listing_type_id=listing_type_id)
    frete_faixa_alvo = _ml_estimar_taxa_fixa_por_preco(p_alvo, listing_type_id=listing_type_id)
    if frete_faixa_base is None or frete_faixa_alvo is None:
        return None
    if abs(float(frete_ref) - float(frete_faixa_base)) > 0.02:
        return None
    return round(float(frete_faixa_alvo), 2)


def _ml_extrair_preco_promocao_raw(entry: dict, priorizar_percentual_total_api: bool = False):
    """Extrai somente preco final/desconto da promocao no payload do Mercado Livre."""
    if not isinstance(entry, dict):
        return None, None

    chaves_preco_ordem = [
        # A analise enriquece candidatos de SELLER_CAMPAIGN com o mesmo preco
        # escolhido pelo painel de Anuncios do ML. O endpoint publico informa
        # apenas limite e sugestao; a escolha depende das ofertas SMART do item.
        "_jk_preco_painel_seller_campaign",
    ]
    boosted_raw = entry.get("boosted_offer")
    boosted_ativo = (
        boosted_raw is True
        or (isinstance(boosted_raw, (int, float)) and not isinstance(boosted_raw, bool) and boosted_raw == 1)
        or str(boosted_raw or "").strip().lower() in {"true", "1", "yes", "sim"}
    )
    if boosted_ativo:
        # Em ofertas com boost, este e o preco final efetivamente mostrado ao
        # comprador e precisa prevalecer sobre o preco-base da oferta.
        chaves_preco_ordem.append("total_price_for_boosted_offer")
    chaves_preco_ordem.extend((
        "price",
        "deal_price",
        "promotion_price",
        "final_price",
        "campaign_price",
        "new_price",
        "loyalty_price",
        "discounted_price",
        # Fora da analise enriquecida, a sugestao continua sendo a melhor
        # aproximacao disponivel antes do limite maximo da campanha.
        "suggested_discounted_price",
        "suggested_deal_price",
        "recommended_discounted_price",
        "suggested_price",
        "recommended_price",
        "max_discounted_price",
    ))
    chaves_preco = set(chaves_preco_ordem)
    termos_preco = [
        "price",
        "preco",
        "precio",
        "deal_price",
        "promotion_price",
        "final_price",
        "loyalty_price",
        "campaign_price",
        "new_price",
        "suggested_price",
        "suggested_discounted_price",
        "suggested_deal_price",
        "recommended_discounted_price",
        "recommended_price",
        "max_discounted_price",
        "discounted_price",
    ]
    if boosted_ativo:
        termos_preco.append("total_price_for_boosted_offer")
    termos_preco = tuple(termos_preco)
    termos_excluir_preco = (
        "receive",
        "receives",
        "seller_receives",
        "seller_receive",
        "net",
        "liquid",
        "liquido",
        "margin",
        "margem",
        "contribution",
        "contribuicao",
        "fee",
        "tariff",
        "tarifa",
        "commission",
        "comissao",
        "tax",
        "imposto",
    )

    def _path_tem(caminho: str, termos: tuple[str, ...]) -> bool:
        caminho_norm = str(caminho or "").lower()
        return any(termo in caminho_norm for termo in termos)

    candidatos_preco = []
    for chave in chaves_preco_ordem:
        obj = entry.get(chave)
        if isinstance(obj, dict):
            candidatos_preco.extend([obj.get("amount"), obj.get("value")])
        else:
            candidatos_preco.append(obj)

    for caminho, valor in _ml_iterar_campos_payload_limitado(entry):
        caminho_norm = str(caminho or "").lower()
        chave_norm = caminho_norm.rsplit(".", 1)[-1]
        if _path_tem(caminho_norm, termos_excluir_preco):
            continue
        if chave_norm in chaves_preco:
            candidatos_preco.append(valor)
            continue
        if chave_norm in {"amount", "value"} and _path_tem(caminho_norm, termos_preco):
            candidatos_preco.append(valor)

    preco = next((v for v in (_parse_float_flex(x) for x in candidatos_preco) if v is not None and v > 0), None)

    chaves_desconto_total = {
        "discount_percentage",
        "discount_percent",
    }
    chaves_desconto_componentes = {
        "seller_discount_percentage",
        "meli_discount_percentage",
        "seller_percentage",
        "meli_percentage",
    }
    termos_desconto = (
        "discount",
        "desconto",
        "seller_percentage",
        "meli_percentage",
        "seller_discount",
        "meli_discount",
    )
    termos_excluir_desconto = (
        "margin",
        "margem",
        "contribution",
        "contribuicao",
        "profit",
        "lucro",
        "receive",
        "receives",
        "seller_receives",
        "net",
        "liquid",
        "liquido",
        "fee",
        "tariff",
        "tarifa",
        "tax",
        "imposto",
    )

    candidatos_total_direto = [
        entry.get("discount_percentage"),
        entry.get("discount_percent"),
    ]
    candidatos_componentes_diretos = [
        entry.get("seller_discount_percentage"),
        entry.get("meli_discount_percentage"),
    ]
    meli_pct = _parse_float_flex(entry.get("meli_percentage") or entry.get("meli_discount_percentage"))
    seller_pct = _parse_float_flex(entry.get("seller_percentage") or entry.get("seller_discount_percentage"))
    soma_componentes = None
    if meli_pct is not None or seller_pct is not None:
        soma_componentes = float(meli_pct or 0.0) + float(seller_pct or 0.0)

    candidatos_total_payload = []
    candidatos_componentes_payload = []
    candidatos_contexto_payload = []
    candidatos_payload_ordem_original = []

    for caminho, valor in _ml_iterar_campos_payload_limitado(entry):
        caminho_norm = str(caminho or "").lower()
        chave_norm = caminho_norm.rsplit(".", 1)[-1]
        if _path_tem(caminho_norm, termos_excluir_desconto):
            continue
        if chave_norm in chaves_desconto_total:
            candidatos_total_payload.append(valor)
            candidatos_payload_ordem_original.append(valor)
            continue
        if chave_norm in chaves_desconto_componentes:
            candidatos_componentes_payload.append(valor)
            candidatos_payload_ordem_original.append(valor)
            continue
        if chave_norm in {"percent", "percentage", "pct"} and _path_tem(caminho_norm, termos_desconto):
            candidatos_contexto_payload.append(valor)
            candidatos_payload_ordem_original.append(valor)

    candidatos_desconto = []
    if priorizar_percentual_total_api:
        candidatos_desconto.extend(candidatos_total_direto)
        candidatos_desconto.extend(candidatos_total_payload)
        if soma_componentes is not None:
            candidatos_desconto.append(soma_componentes)
        candidatos_desconto.extend(candidatos_componentes_diretos)
        candidatos_desconto.extend(candidatos_componentes_payload)
        candidatos_desconto.extend(candidatos_contexto_payload)
    else:
        candidatos_desconto.extend(candidatos_total_direto)
        candidatos_desconto.extend(candidatos_componentes_diretos)
        if soma_componentes is not None:
            candidatos_desconto.insert(0, soma_componentes)
        candidatos_desconto.extend(candidatos_payload_ordem_original)

    desconto = next((v for v in (_parse_float_flex(x) for x in candidatos_desconto) if v is not None), None)
    if desconto is None:
        preco_original = _parse_float_flex(entry.get("original_price"))
        if preco_original is not None and preco_original > 0 and preco is not None and preco > 0:
            desconto = max(0.0, min(100.0, ((float(preco_original) - float(preco)) / float(preco_original)) * 100.0))
    return preco, desconto


def _ml_calcular_percentual_desconto_por_preco(preco_base, preco_final):
    base_num = _parse_float_flex(preco_base)
    final_num = _parse_float_flex(preco_final)
    if base_num is None or final_num is None:
        return None
    if base_num <= 0 or final_num <= 0 or final_num > base_num:
        return None
    pct = ((float(base_num) - float(final_num)) / float(base_num)) * 100.0
    if 0 < pct <= 100:
        return pct
    return None


def _ml_resolver_desconto_boost_tarifa_promocao_raw(entry: dict):
    """Aceita boost somente no objeto da promocao/oferta ja validado."""
    if not isinstance(entry, dict):
        return "desconhecido", None

    campos = {str(chave or "").strip().lower(): valor for chave, valor in entry.items()}
    if "boosted_offer" not in campos:
        return "desconhecido", None

    boosted_raw = campos.get("boosted_offer")
    if isinstance(boosted_raw, bool):
        boosted = boosted_raw
    elif isinstance(boosted_raw, (int, float)) and not isinstance(boosted_raw, bool):
        if boosted_raw not in {0, 1}:
            return "boost_incompleto", None
        boosted = boosted_raw == 1
    else:
        boosted_txt = str(boosted_raw or "").strip().lower()
        if boosted_txt in {"true", "1", "yes", "sim"}:
            boosted = True
        elif boosted_txt in {"false", "0", "no", "nao", "não"}:
            boosted = False
        else:
            return "boost_incompleto", None

    if not boosted:
        # Somente a negacao explicita confirma ausencia do beneficio. A API pode
        # omitir temporariamente os campos novos mesmo quando o painel os exibe.
        return "sem_boost", 0.0

    amount = _parse_float_flex(campos.get("discount_meli_boost_amount"))
    if amount is not None and amount > 0:
        return "boosted", round(float(amount), 2)
    return "boost_incompleto", None


def _ml_extrair_desconto_boost_tarifa_promocao_raw(entry: dict):
    """Extrai o amount oficial ou zero quando a oferta confirma ausencia de boost."""
    estado, amount = _ml_resolver_desconto_boost_tarifa_promocao_raw(entry)
    if estado in {"boosted", "sem_boost"}:
        return amount
    return None


ML_PROMO_SALE_FEE_DISCOUNT_FIELDS = (
    "sale_fee_discount",
    "sale_fee_discount_amount",
    "selling_fee_discount",
    "selling_fee_discount_amount",
    "fee_per_sale_discount",
    "fee_per_sale_discount_amount",
    "sale_fee_reduction",
    "sale_fee_reduction_amount",
)


def _ml_extrair_desconto_tarifa_venda_direto(entry: dict):
    """Le somente campos monetarios allowlisted do objeto validado."""
    if not isinstance(entry, dict):
        return None
    campos = {str(chave or "").strip().lower(): valor for chave, valor in entry.items()}
    for chave in ML_PROMO_SALE_FEE_DISCOUNT_FIELDS:
        valor = campos.get(chave)
        if isinstance(valor, dict):
            valor = valor.get("amount") if valor.get("amount") is not None else valor.get("value")
        numero = _parse_float_flex(valor)
        if numero is not None and numero > 0:
            return float(numero)
    return None


def _ml_extrair_desconto_tarifa_promocao_raw(entry: dict):
    """Extrai o valor em R$ de reduÃ§Ã£o de tarifa da campanha, quando a API retorna esse detalhe."""
    if not isinstance(entry, dict):
        return None

    estado_boost, desconto_boost = _ml_resolver_desconto_boost_tarifa_promocao_raw(entry)
    if estado_boost == "boosted":
        return desconto_boost
    if estado_boost == "boost_incompleto":
        return None
    if estado_boost == "sem_boost":
        return 0.0

    return _ml_extrair_desconto_tarifa_venda_direto(entry)


def _ml_extrair_tarifa_cobrada_promocao_raw(entry: dict):
    """Extrai o valor cobrado de tarifa dentro do payload da promocao, quando disponivel."""
    if not isinstance(entry, dict):
        return None

    candidatos = []

    for novo_caminho, valor in _ml_iterar_campos_payload_limitado(entry):
        parece_tarifa = (
            "sale_fee" in novo_caminho
            or "fee_amount" in novo_caminho
            or "tariff_amount" in novo_caminho
            or "tarifa_valor" in novo_caminho
            or novo_caminho.endswith(".fee")
        )
        parece_desconto = (
            "discount" in novo_caminho
            or "reduction" in novo_caminho
            or "rebate" in novo_caminho
            or "desconto" in novo_caminho
            or "reducao" in novo_caminho
            or "reduÃƒÂ§ÃƒÂ£o" in novo_caminho
        )
        parece_percentual = (
            "percent" in novo_caminho
            or "percentage" in novo_caminho
            or novo_caminho.endswith("_pct")
            or novo_caminho.endswith(".pct")
        )
        if parece_tarifa and not parece_desconto and not parece_percentual:
            v = _parse_float_flex(valor)
            if v is not None and v >= 0:
                candidatos.append(float(v))
    return candidatos[0] if candidatos else None


def _ml_extrair_percentual_desconto_tarifa_promocao_raw(entry: dict):
    """Extrai percentual de reducao de tarifa quando a campanha retorna fee_discount em %."""
    if not isinstance(entry, dict):
        return None
    for caminho, valor in _ml_iterar_campos_payload_limitado(entry):
        caminho_norm = str(caminho or "").lower()
        parece_tarifa = (
            "sale_fee" in caminho_norm
            or "fee" in caminho_norm
            or "tariff" in caminho_norm
            or "tarifa" in caminho_norm
        )
        parece_desconto = (
            "discount" in caminho_norm
            or "reduction" in caminho_norm
            or "rebate" in caminho_norm
            or "desconto" in caminho_norm
            or "reducao" in caminho_norm
            or "reducao" in normalizar_texto(caminho_norm)
        )
        parece_percentual = (
            "percent" in caminho_norm
            or "percentage" in caminho_norm
            or caminho_norm.endswith("_pct")
            or caminho_norm.endswith(".pct")
        )
        if parece_tarifa and parece_desconto and parece_percentual:
            pct = _parse_float_flex(valor)
            if pct is not None and pct > 0:
                return float(pct)
    return None


def _ml_extrair_valor_desconto_taxa_promocao_raw(entry: dict):
    """Extrai apenas reducao de taxa/tarifa explicitamente indicada no payload."""
    if not isinstance(entry, dict):
        return None

    estado_boost, desconto_boost = _ml_resolver_desconto_boost_tarifa_promocao_raw(entry)
    if estado_boost == "boosted":
        return desconto_boost
    if estado_boost == "boost_incompleto":
        return None
    if estado_boost == "sem_boost":
        return 0.0

    return _ml_extrair_desconto_tarifa_venda_direto(entry)


def _ml_obter_desconto_taxa_promocao_item(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    item: Optional[dict] = None,
    price_info: Optional[dict] = None,
    fee_data: Optional[dict] = None,
    deal_ids: Optional[list] = None,
    request_fn=None,
):
    """Busca a promocao ativa do item e calcula reducao de tarifa para a margem."""
    request_fn = request_fn or _ml_api_request
    price_info = price_info or {}
    fee_data = fee_data or {}
    item = item or {}
    item_id = str(item_id or "").strip()
    info = {
        "promotion_fee_discount": None,
        "promotion_fee_discount_text": "",
        "promotion_fee_charged": None,
        "promotion_fee_charged_text": "",
        "promotion_fee_discount_source": "",
        "promotion_fee_discount_applied": False,
        "promotion_fee_discount_note": "",
        "promotion_id": price_info.get("promotion_id"),
        "promotion_type": price_info.get("promotion_type"),
        "promotion_name": "",
    }
    if not item_id:
        return info, cfg

    ids_alvo: list[str] = []

    def _add_id(valor):
        texto = str(valor or "").strip()
        if texto and texto.lower() not in {"-", "none", "null"} and texto not in ids_alvo:
            ids_alvo.append(texto)

    _add_id(price_info.get("promotion_id"))
    for deal_id in deal_ids or []:
        _add_id(deal_id)

    promocoes_item = []
    try:
        promocoes_item, cfg = _ml_obter_promocoes_item(
            client_id,
            loja,
            cfg,
            item_id,
            request_fn=request_fn,
        )
    except Exception as exc:
        logger.warning("[ML API] Falha ao consultar promocoes do item %s para desconto de tarifa: %s", item_id, exc)
        promocoes_item = []

    candidatos_ml: list[dict] = []
    candidatos_usuario: list[dict] = []
    vistos = set()

    def _add_candidato(raw, preferido: bool = False):
        if not isinstance(raw, dict):
            return
        chave = (
            _ml_promocao_raw_id(raw).lower(),
            _ml_promocao_raw_tipo(raw).lower(),
            str(raw.get("_jk_status_item_consultado") or ""),
        )
        if chave in vistos:
            return
        vistos.add(chave)
        grupo = _ml_grupo_promocao_raw(raw)
        ativo = _ml_promocao_raw_esta_ativa_ou_indefinida(raw)
        if grupo == "mercado_livre" and ativo:
            if preferido:
                candidatos_ml.insert(0, raw)
            else:
                candidatos_ml.append(raw)
        elif grupo == "usuario" and ativo:
            candidatos_usuario.append(raw)

    if ids_alvo:
        for promo_id in ids_alvo:
            raw = _ml_encontrar_promocao_raw_item(promocoes_item, promo_id)
            _add_candidato(raw, preferido=True)

    if isinstance(promocoes_item, list):
        for raw in promocoes_item:
            if not isinstance(raw, dict):
                continue
            raw_id = _ml_promocao_raw_id(raw)
            if ids_alvo and raw_id and raw_id not in ids_alvo:
                _add_candidato(raw)
                continue
            _add_candidato(raw, preferido=bool(raw_id and raw_id in ids_alvo))

    promo_id_fallback = str(price_info.get("promotion_id") or "").strip()
    promo_type_fallback = str(price_info.get("promotion_type") or "").strip()
    if promo_id_fallback and promo_type_fallback and _ml_grupo_promocao_por_meta(promo_type_fallback) == "mercado_livre":
        _add_candidato({"id": promo_id_fallback, "promotion_type": promo_type_fallback}, preferido=True)

    preco_final = _to_float_safe(price_info.get("price"))
    preco_base = (
        _to_float_safe(price_info.get("original_price"))
        or _to_float_safe(price_info.get("standard_price"))
        or _to_float_safe(item.get("original_price"))
        or _to_float_safe(item.get("base_price"))
        or preco_final
    )
    tarifa_base = _to_float_safe(fee_data.get("ad_cost"))
    melhor = dict(info)

    def _detalhar_raw_promocao(raw: dict) -> dict:
        nonlocal cfg
        promo_id = _ml_promocao_raw_id(raw)
        promo_type = _ml_promocao_raw_tipo(raw)
        if promo_id and promo_type and item_id:
            try:
                detalhe, cfg = _ml_obter_item_promocao_raw(
                    client_id,
                    loja,
                    cfg,
                    promo_id,
                    promo_type,
                    item_id,
                    request_fn=request_fn,
                )
                if isinstance(detalhe, dict) and detalhe:
                    return detalhe
            except Exception as exc:
                logger.warning(
                    "[ML API] Falha ao detalhar promocao %s/%s do item %s: %s",
                    promo_id,
                    promo_type,
                    item_id,
                    exc,
                )
        return raw if isinstance(raw, dict) else {}

    raw_a_base = _detalhar_raw_promocao(candidatos_usuario[0]) if candidatos_usuario else {}

    for raw_b in candidatos_ml:
        raw_b_item = _detalhar_raw_promocao(raw_b)
        promo_id = _ml_promocao_raw_id(raw_b_item) or _ml_promocao_raw_id(raw_b) or promo_id_fallback
        promo_type = _ml_promocao_raw_tipo(raw_b_item) or _ml_promocao_raw_tipo(raw_b) or promo_type_fallback
        promo_name = _ml_promocao_raw_nome(raw_b_item) or _ml_promocao_raw_nome(raw_b)

        raw_a_item = raw_a_base
        preco_a_raw, desc_a_raw = _ml_extrair_preco_promocao_raw(raw_a_item)
        preco_b_raw, _desc_b_raw = _ml_extrair_preco_promocao_raw(raw_b_item)
        preco_a = preco_a_raw or preco_base or preco_final
        preco_b = preco_b_raw or preco_final or preco_base
        if preco_a_raw is None and desc_a_raw is not None and preco_base and preco_base > 0:
            preco_a = round(float(preco_base) * max(0.0, 1.0 - (float(desc_a_raw) / 100.0)), 2)

        fee_a = {}
        tarifa_a_val = _ml_extrair_tarifa_cobrada_promocao_raw(raw_a_item)
        if tarifa_a_val is None and preco_a is not None:
            item_taxa_a = dict(item or {})
            item_taxa_a["price"] = preco_a
            try:
                fee_a, cfg = _ml_obter_taxas_anuncio(
                    client_id,
                    loja,
                    cfg,
                    item_taxa_a,
                    request_fn=request_fn,
                )
            except Exception:
                fee_a = {}
            tarifa_a_val = _to_float_safe(fee_a.get("ad_cost"))
        if tarifa_a_val is None:
            tarifa_a_val = tarifa_base

        tarifa_b_val = _ml_extrair_tarifa_cobrada_promocao_raw(raw_b_item)
        if tarifa_b_val is None and preco_b is not None:
            item_taxa_b = dict(item or {})
            item_taxa_b["price"] = preco_b
            try:
                fee_b, cfg = _ml_obter_taxas_anuncio(
                    client_id,
                    loja,
                    cfg,
                    item_taxa_b,
                    request_fn=request_fn,
                )
            except Exception:
                fee_b = {}
            tarifa_b_val = _to_float_safe(fee_b.get("ad_cost"))
        if tarifa_b_val is None:
            tarifa_b_val = _to_float_safe(fee_data.get("ad_cost"))

        if tarifa_a_val is None or tarifa_b_val is None:
            continue

        desconto = round(float(tarifa_a_val) - float(tarifa_b_val), 2)

        if desconto is None or desconto <= 0:
            continue

        desconto = round(float(desconto), 2)
        tarifa_promocao = max(float(tarifa_b_val), 0.0)

        melhor.update({
            "promotion_fee_discount": desconto,
            "promotion_fee_discount_text": formatar_moeda_br(desconto),
            "promotion_fee_charged": round(float(tarifa_promocao), 2),
            "promotion_fee_charged_text": formatar_moeda_br(tarifa_promocao),
            "promotion_fee_discount_source": "seller_promotions_listing_prices_comparison",
            "promotion_fee_discount_applied": False,
            "promotion_fee_discount_note": "Comparativo via API entre sale_fee_amount do preco base/usuario e sale_fee_amount do preco promocional. Nao aplicado automaticamente na margem.",
            "promotion_id": promo_id or melhor.get("promotion_id"),
            "promotion_type": promo_type or melhor.get("promotion_type"),
            "promotion_name": promo_name,
            "promotion_fee_base": round(float(tarifa_a_val), 2) if tarifa_a_val is not None else None,
            "promotion_fee_base_text": formatar_moeda_br(tarifa_a_val) if tarifa_a_val is not None else "",
            "promotion_fee_ml": round(float(tarifa_b_val), 2) if tarifa_b_val is not None else None,
            "promotion_fee_ml_text": formatar_moeda_br(tarifa_b_val) if tarifa_b_val is not None else "",
        })
        return melhor, cfg

    return melhor, cfg


def _ml_aplicar_desconto_taxa_promocao_fee_data(fee_data: dict, promo_fee_info: dict) -> dict:
    fee_data = dict(fee_data or {})
    promo_fee_info = promo_fee_info or {}
    source = str(promo_fee_info.get("promotion_fee_discount_source") or "").strip()
    if source == "seller_promotions_listing_prices_comparison":
        for campo in (
            "promotion_id",
            "promotion_type",
            "promotion_name",
            "promotion_fee_base",
            "promotion_fee_base_text",
            "promotion_fee_ml",
            "promotion_fee_ml_text",
            "promotion_fee_discount_source",
            "promotion_fee_discount_note",
        ):
            if promo_fee_info.get(campo) not in (None, ""):
                fee_data[campo] = promo_fee_info.get(campo)
        fee_data["promotion_fee_discount_applied"] = False
        partes = [fee_data.get("fee_breakdown") or ""]
        if promo_fee_info.get("promotion_fee_base_text") or promo_fee_info.get("promotion_fee_ml_text"):
            partes.append(
                "Comparativo tarifa promocao: "
                f"base {promo_fee_info.get('promotion_fee_base_text') or '-'}"
                f" -> vigente {promo_fee_info.get('promotion_fee_ml_text') or '-'}"
            )
        fee_data["fee_breakdown"] = " | ".join([parte for parte in partes if parte])
        return fee_data

    desconto = _to_float_safe(promo_fee_info.get("promotion_fee_discount"))
    tarifa_base = _to_float_safe(promo_fee_info.get("promotion_fee_base"))
    tarifa_promocao = (
        _to_float_safe(promo_fee_info.get("promotion_fee_charged"))
        if promo_fee_info.get("promotion_fee_charged") not in (None, "")
        else _to_float_safe(promo_fee_info.get("promotion_fee_ml"))
    )
    tarifa_atual = _to_float_safe(fee_data.get("ad_cost"))
    if desconto is None or desconto <= 0 or tarifa_promocao is None:
        return fee_data

    tarifa_original = tarifa_base if tarifa_base is not None else tarifa_atual
    if tarifa_original is not None:
        desconto = min(float(desconto), max(float(tarifa_original), 0.0))
        fee_data["ad_cost_original"] = round(float(tarifa_original), 2)
        fee_data["ad_cost_original_text"] = formatar_moeda_br(tarifa_original)

    tarifa_com_desconto = max(float(tarifa_promocao), 0.0)
    fee_data["ad_cost"] = round(tarifa_com_desconto, 2)
    fee_data["ad_cost_text"] = formatar_moeda_br(tarifa_com_desconto)
    fee_data["promotion_fee_discount"] = round(desconto, 2)
    fee_data["promotion_fee_discount_text"] = formatar_moeda_br(desconto)
    fee_data["promotion_fee_discount_applied"] = True
    fee_data["promotion_fee_charged"] = round(tarifa_com_desconto, 2)
    fee_data["promotion_fee_charged_text"] = formatar_moeda_br(tarifa_com_desconto)
    fee_data["promotion_fee_discount_source"] = promo_fee_info.get("promotion_fee_discount_source") or "seller_promotions"
    if promo_fee_info.get("promotion_id"):
        fee_data["promotion_id"] = promo_fee_info.get("promotion_id")
    if promo_fee_info.get("promotion_type"):
        fee_data["promotion_type"] = promo_fee_info.get("promotion_type")
    if promo_fee_info.get("promotion_name"):
        fee_data["promotion_name"] = promo_fee_info.get("promotion_name")
    for campo in (
        "promotion_fee_base",
        "promotion_fee_base_text",
        "promotion_fee_ml",
        "promotion_fee_ml_text",
    ):
        if promo_fee_info.get(campo) not in (None, ""):
            fee_data[campo] = promo_fee_info.get(campo)
    partes = [fee_data.get("fee_breakdown") or ""]
    partes.append(f"Desconto tarifa promocao: {formatar_moeda_br(desconto)}")
    partes.append(f"Tarifa com desconto: {formatar_moeda_br(tarifa_com_desconto)}")
    fee_data["fee_breakdown"] = " | ".join([parte for parte in partes if parte])
    return fee_data

PEER_EXPORTS = ['_ml_obter_preco_detalhado', '_ml_contexto_frete_item', '_ml_obter_frete_detalhado', '_ml_montar_preco_listagem', '_ml_estimar_taxa_fixa_por_preco', '_ml_obter_taxas_anuncio', '_ml_extrair_recebivel_promocao_raw', '_ml_calcular_recebivel_promocao', '_ml_ajustar_desconto_tarifa_recebivel_promocao', '_flag_frete_gratis', '_ajustar_frete_por_preco_base', '_ml_price_band_index', '_recalcular_frete_por_grade_oficial', '_recalcular_frete_por_faixa_ml', '_ml_extrair_preco_promocao_raw', '_ml_calcular_percentual_desconto_por_preco', '_ml_extrair_desconto_tarifa_promocao_raw', '_ml_extrair_tarifa_cobrada_promocao_raw', '_ml_extrair_percentual_desconto_tarifa_promocao_raw', '_ml_extrair_valor_desconto_taxa_promocao_raw', '_ml_obter_desconto_taxa_promocao_item', '_ml_aplicar_desconto_taxa_promocao_fee_data']
__all__ = PEER_EXPORTS + ["configure_mercadolivre_legacy_pricing_runtime"]

configure_mercadolivre_legacy_pricing_runtime()
