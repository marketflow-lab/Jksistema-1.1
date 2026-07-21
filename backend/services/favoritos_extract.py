"""Internal slice for favoritos_core."""

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
from backend.services.transport_security import requests_tls_verify


def configure_favoritos_extract_runtime(runtime_module=None, peers=None):
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


configure_favoritos_extract_runtime()


def _normalizar_data_ml(valor):
    if not valor:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.replace("\\/", "/")
    try:
        # Mantem ISO quando possível; o frontend já formata para pt-BR.
        datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return texto
    except Exception:
        return texto


def _extrair_data_criacao_codigo_fonte(html_text: str):
    if not html_text:
        return None

    def _normalizar_codigo_texto(texto: str) -> str:
        texto = str(texto or "")
        texto = html_lib.unescape(texto)
        texto = texto.replace("\\u002F", "/").replace("\\/", "/")
        texto = texto.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
        texto = texto.replace('\\"', '"').replace("\\'", "'")
        return texto

    def _procurar_em_objeto(root):
        date_keys = {
            "dateCreated",
            "date_created",
            "releaseDate",
            "start_time",
            "startTime",
            "item_date_created",
            "creation_date",
            "creationDate",
            "listing_start_time",
            "start_date",
            "itemStartTime",
        }
        stack = [root]
        seen = set()
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                obj_id = id(cur)
                if obj_id in seen:
                    continue
                seen.add(obj_id)
                for key, value in cur.items():
                    if key in date_keys:
                        found = _normalizar_data_ml(value)
                        if found:
                            return found
                    if key == "date_created" and isinstance(value, dict):
                        found = _normalizar_data_ml(value.get("value"))
                        if found:
                            return found
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(cur, list):
                stack.extend(cur)
        return None

    textos_para_busca = [html_text, _normalizar_codigo_texto(html_text)]
    patterns = [
        r'"date_created"\s*:\s*"([^"]+)"',
        r'"dateCreated"\s*:\s*"([^"]+)"',
        r'"date_created"\s*:\s*{\s*"value"\s*:\s*"([^"]+)"',
        r'"start_time"\s*:\s*"([^"]+)"',
        r'"startTime"\s*:\s*"([^"]+)"',
        r'"item_date_created"\s*:\s*"([^"]+)"',
        r'"creation_date"\s*:\s*"([^"]+)"',
        r'"creationDate"\s*:\s*"([^"]+)"',
        r'"listing_start_time"\s*:\s*"([^"]+)"',
        r'"start_date"\s*:\s*"([^"]+)"',
        r'"itemStartTime"\s*:\s*"([^"]+)"',
    ]
    for texto in textos_para_busca:
        for pattern in patterns:
            match = re.search(pattern, texto, flags=re.IGNORECASE)
            if match:
                data = _normalizar_data_ml(match.group(1))
                if data:
                    return data

    try:
        soup = BeautifulSoup(html_text, "lxml")
        for script in soup.find_all("script"):
            script_text = _normalizar_codigo_texto(script.get_text() or "")
            if not script_text:
                continue
            for pattern in patterns:
                match = re.search(pattern, script_text, flags=re.IGNORECASE)
                if match:
                    data = _normalizar_data_ml(match.group(1))
                    if data:
                        return data
            stripped = script_text.strip()
            if not stripped or stripped[0] not in "[{":
                continue
            try:
                data = json.loads(stripped)
            except Exception:
                continue
            found = _procurar_em_objeto(data)
            if found:
                return found
    except Exception:
        logger.debug("Falha ao analisar scripts para data de criacao", exc_info=True)

    return None


def _extrair_vendedor_codigo_fonte(html_text: str):
    if not html_text:
        return None

    patterns = [
        r'"seller_name"\s*:\s*"([^"]+)"',
        r'"sellerName"\s*:\s*"([^"]+)"',
        r'"nickname"\s*:\s*"([^"]+)"',
        r'"official_store_name"\s*:\s*"([^"]+)"',
        r'"officialStoreName"\s*:\s*"([^"]+)"',
        r'"official_store"\s*:\s*\{[^{}]*"nickname"\s*:\s*"([^"]+)"',
        r'"official_store"\s*:\s*\{[^{}]*"name"\s*:\s*"([^"]+)"',
        r'"seller"\s*:\s*\{[^{}]*"nickname"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            vendedor = str(match.group(1) or "").strip()
            if vendedor:
                return vendedor

    try:
        soup = BeautifulSoup(html_text, "lxml")
        selectors = [
            ".ui-pdp-seller__header__title",
            ".ui-pdp-seller__link-trigger",
            ".ui-pdp-seller__nickname",
            ".ui-pdp-official-store-label",
            "[data-testid='seller-info']",
            "[data-testid='official-store-info']",
        ]
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag:
                texto = " ".join(tag.get_text(" ", strip=True).split())
                texto = re.sub(r"^(vendido por|loja oficial)\s+", "", texto, flags=re.IGNORECASE).strip()
                if texto:
                    return texto
    except Exception:
        logger.debug("Falha ao extrair vendedor do codigo-fonte", exc_info=True)

    return None


def _extrair_info_anuncio(
    url: str,
    item_id: str | None = None,
    client_id: str | None = None,
    imagem: str | None = None,
    dados_base: dict | None = None,
    datas_cache_local: dict | None = None,
    api_item_precarregado: dict | None = None,
    api_item_precarregado_tentado: bool = False,
):
    item_id = _extrair_item_id(item_id or "") or _extrair_item_id(url) or item_id
    info = {
        "data_criacao": None,
        "fonte": None,
        "vendedor": None,
        "fonte_vendedor": None,
        "vendas": None,
        "fonte_vendas": None,
        "visitas": None,
        "fonte_visitas": None,
        "fonte_data_criacao": None,
        "data_criacao_confianca": None,
        "sku": None,
        "listing_type_id": None,
        "listing_type_name": None,
        "tipo_anuncio": None,
        "parcelamento_sem_juros": None,
        "shipping": None,
        "logistic_type": None,
        "shipping_mode": None,
        "is_full": None,
        "condicao": None,
        "condition": None,
        "item_condition": None,
    }

    if isinstance(dados_base, dict):
        vendedor_base = str(dados_base.get("vendedor") or dados_base.get("seller") or "").strip()
        if vendedor_base and vendedor_base != "-":
            info["vendedor"] = vendedor_base
            info["fonte_vendedor"] = str(dados_base.get("fonte_vendedor") or "entrada_extensao").strip()
        vendas_base = _parse_vendas_ml(dados_base.get("vendas"))
        if vendas_base is not None:
            info["vendas"] = vendas_base
            info["fonte_vendas"] = str(dados_base.get("fonte_vendas") or "entrada_extensao").strip()
        visitas_base = _parse_vendas_ml(dados_base.get("visitas"))
        if visitas_base is not None:
            info["visitas"] = visitas_base
            info["fonte_visitas"] = str(dados_base.get("fonte_visitas") or "entrada_extensao").strip()
        data_base = _normalizar_data_ml(dados_base.get("data_criacao") or dados_base.get("dataCriacao"))
        if data_base:
            info["data_criacao"] = data_base
            info["fonte"] = str(dados_base.get("fonte_data_criacao") or dados_base.get("fonte") or "entrada_extensao").strip()
            info["fonte_data_criacao"] = info["fonte"]
            info["data_criacao_confianca"] = str(dados_base.get("data_criacao_confianca") or "media").strip()
        sku_base = str(dados_base.get("sku") or "").strip()
        if sku_base:
            info["sku"] = sku_base
        listing_type_id_base = str(dados_base.get("listing_type_id") or dados_base.get("listingTypeId") or "").strip()
        listing_type_name_base = str(dados_base.get("listing_type_name") or "").strip()
        tipo_anuncio_base = str(dados_base.get("tipo_anuncio") or "").strip()
        if listing_type_id_base:
            info["listing_type_id"] = listing_type_id_base
        if listing_type_name_base:
            info["listing_type_name"] = listing_type_name_base
        if tipo_anuncio_base:
            info["tipo_anuncio"] = tipo_anuncio_base
        parcelamento_base = dados_base.get("parcelamento_sem_juros")
        if isinstance(parcelamento_base, bool):
            info["parcelamento_sem_juros"] = parcelamento_base
        shipping_base = dados_base.get("shipping")
        if isinstance(shipping_base, dict):
            info["shipping"] = shipping_base
        logistic_type_base = str(dados_base.get("logistic_type") or dados_base.get("logisticType") or "").strip()
        shipping_mode_base = str(dados_base.get("shipping_mode") or dados_base.get("shippingMode") or "").strip()
        if logistic_type_base:
            info["logistic_type"] = logistic_type_base
        if shipping_mode_base:
            info["shipping_mode"] = shipping_mode_base
        is_full_base = dados_base.get("is_full")
        if isinstance(is_full_base, bool):
            info["is_full"] = is_full_base
        condicao_base = str(
            dados_base.get("condicao")
            or dados_base.get("condition")
            or dados_base.get("item_condition")
            or ""
        ).strip()
        if condicao_base:
            info["condicao"] = condicao_base
            info["condition"] = condicao_base
            info["item_condition"] = condicao_base

    def _vendas_da_api(dados: dict | None):
        if not isinstance(dados, dict):
            return None
        for campo in ("sold_quantity", "soldQuantity", "sold", "sold_quantity_by_state", "sold_quantity_by_country", "ventas", "quantidade_vendida"):
            if campo not in dados:
                continue
            valor = _parse_vendas_ml(dados.get(campo))
            if valor is not None:
                return valor
        return None

    def _vendedor_da_api(dados: dict | None):
        if not isinstance(dados, dict):
            return None
        seller = dados.get("seller") if isinstance(dados.get("seller"), dict) else {}
        vendedor_api = _normalizar_nome_vendedor_ml(dados)
        seller_id = dados.get("seller_id") or seller.get("id")
        if vendedor_api:
            return str(vendedor_api).strip(), seller_id
        if seller_id:
            user_data = _ml_api_user_com_oauth_tenant(client_id, str(seller_id)) or _ml_api_user(str(seller_id))
            if isinstance(user_data, dict):
                nome = (
                    user_data.get("official_store_name")
                    or (user_data.get("official_store") or {}).get("name")
                    or user_data.get("nickname")
                )
                if nome:
                    return str(nome).strip(), seller_id
        return None, seller_id

    def _fonte_vendedor_fraca(fonte: object) -> bool:
        texto = str(fonte or "").strip().lower()
        if not texto:
            return True
        return "avant" in texto or texto in {"entrada_extensao", "api_search"}

    def _vendedor_confirmado() -> bool:
        return bool(info["vendedor"]) and not _fonte_vendedor_fraca(info.get("fonte_vendedor"))

    def _aplicar_data_criacao_aproximada(permitir_rede: bool = True):
        if info["data_criacao"] or not item_id:
            return
        data_cache = (
            datas_cache_local.get(item_id)
            if isinstance(datas_cache_local, dict)
            else _ml_data_criacao_cache_local(client_id, item_id)
        )
        if data_cache:
            info["data_criacao"] = data_cache
            info["fonte"] = "cache_local_item"
            info["fonte_data_criacao"] = "cache_local_item"
            info["data_criacao_confianca"] = "alta"
            return
        data_imagem = _ml_data_criacao_por_imagem(imagem or url)
        if data_imagem:
            info["data_criacao"] = data_imagem
            info["fonte"] = "imagem_ml_mes"
            info["fonte_data_criacao"] = "imagem_ml_mes"
            info["data_criacao_confianca"] = "baixa"
            return
        if not permitir_rede:
            return
        candidatos = []
        data_wayback = _ml_wayback_primeira_captura_data(item_id, url)
        if data_wayback:
            candidatos.append(("wayback_primeira_captura", data_wayback, "media_baixa", 1))
        data_pergunta = _ml_primeira_pergunta_publica_data(item_id)
        if data_pergunta:
            candidatos.append(("primeira_pergunta_publica", data_pergunta, "baixa", 1))
        if not candidatos:
            return
        fonte, data, confianca, _prioridade = sorted(candidatos, key=lambda item: (item[3], _ml_data_sort_key(item[1])))[0]
        info["data_criacao"] = data
        info["fonte"] = fonte
        info["fonte_data_criacao"] = fonte
        info["data_criacao_confianca"] = confianca

    # Prioriza cache/imagem e as APIs oficiais. Wayback/perguntas permanecem
    # como contingencia somente depois das fontes rapidas e autoritativas.
    _aplicar_data_criacao_aproximada(permitir_rede=False)
    if info["data_criacao"] and _vendedor_confirmado() and info["vendas"] is not None:
        return info

    visitas_api_tentada = False
    if item_id:
        if isinstance(api_item_precarregado, dict) and api_item_precarregado.get("id"):
            api_data = api_item_precarregado
        elif api_item_precarregado_tentado:
            api_data = None
        else:
            api_data = _ml_api_item_com_oauth_tenant(client_id, item_id) or _ml_api_item(item_id)
        if isinstance(api_data, dict):
            data_api = _normalizar_data_ml(api_data.get("date_created") or api_data.get("start_time"))
            if data_api:
                info["data_criacao"] = data_api
                info["fonte"] = "api_item"
            vendedor_api, seller_id = _vendedor_da_api(api_data)
            if vendedor_api:
                info["vendedor"] = vendedor_api
                info["fonte_vendedor"] = "api_item"
            if seller_id:
                info["seller_id"] = str(seller_id).strip()
            sku_api = _ml_extrair_sku(api_data)
            if sku_api:
                info["sku"] = sku_api
            item_condition = _ml_extrair_item_condition(api_data)
            if item_condition:
                info["condicao"] = item_condition
                info["condition"] = item_condition
                info["item_condition"] = item_condition
            shipping_info = api_data.get("shipping") if isinstance(api_data.get("shipping"), dict) else {}
            if shipping_info:
                logistic_type = str(shipping_info.get("logistic_type") or "").strip()
                info["shipping"] = shipping_info
                info["logistic_type"] = logistic_type
                info["shipping_mode"] = str(shipping_info.get("mode") or "").strip()
                info["is_full"] = logistic_type.lower() == "fulfillment"
            listing_type_id = str(api_data.get("listing_type_id") or "").strip()
            if listing_type_id:
                info["listing_type_id"] = listing_type_id
                info["listing_type_name"] = _ml_nome_tipo_anuncio(listing_type_id)
                info["tipo_anuncio"] = info["listing_type_name"]
            sem_juros_api = _ml_parcelamento_sem_juros_api(api_data)
            if sem_juros_api is not None:
                info["parcelamento_sem_juros"] = bool(sem_juros_api)
                info["tipo_anuncio"] = "Premium" if info["parcelamento_sem_juros"] else "Classico"
            vendas_api = _vendas_da_api(api_data)
            if vendas_api is not None:
                info["vendas"] = vendas_api
                info["fonte_vendas"] = "api_item_vendas"
            if info["visitas"] is None:
                visitas_api_tentada = True
                visitas_api = _ml_api_visitas_com_oauth_tenant(client_id, item_id)
                if isinstance(visitas_api, dict) and visitas_api.get("visitas") is not None:
                    info["visitas"] = visitas_api.get("visitas")
                    info["fonte_visitas"] = visitas_api.get("fonte") or "api_visitas"

            if info["data_criacao"] and _vendedor_confirmado() and info["vendas"] is not None and info["parcelamento_sem_juros"] is not None:
                return info

    if item_id and info["visitas"] is None and not visitas_api_tentada:
        visitas_api = _ml_api_visitas_com_oauth_tenant(client_id, item_id)
        if isinstance(visitas_api, dict) and visitas_api.get("visitas") is not None:
            info["visitas"] = visitas_api.get("visitas")
            info["fonte_visitas"] = visitas_api.get("fonte") or "api_visitas"

    if not url:
        _aplicar_data_criacao_aproximada()
        return info

    try:
        headers = {
            **_ml_headers(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
        }
        resp = requests.get(
            url,
            headers=headers,
            timeout=12,
            allow_redirects=True,
            verify=requests_tls_verify(),
        )
        if resp.status_code != 200:
            logger.warning("[Favoritos][Datas] Erro ao abrir %s: status %s", url, resp.status_code)
            _aplicar_data_criacao_aproximada()
            return info

        if not info["data_criacao"]:
            data_html = _extrair_data_criacao_codigo_fonte(resp.text)
            if data_html:
                info["data_criacao"] = data_html
                info["fonte"] = "codigo_fonte"

        if not info["vendedor"] or _fonte_vendedor_fraca(info.get("fonte_vendedor")):
            vendedor_html = _extrair_vendedor_codigo_fonte(resp.text)
            if vendedor_html:
                info["vendedor"] = vendedor_html
                info["fonte_vendedor"] = "codigo_fonte"

        if info["vendas"] is None:
            vendas_match = re.search(
                r"\\b([\\d\\.]+\\,?\\d*)\\s*(?:mil|k)?\\s+vendid[oa]s?",
                resp.text,
                flags=re.IGNORECASE,
            )
            if vendas_match:
                info["vendas"] = _parse_vendas_ml(vendas_match.group(0))
                info["fonte_vendas"] = "html_text"

        if info["parcelamento_sem_juros"] is None:
            info["parcelamento_sem_juros"] = _ml_parcelamento_sem_juros_texto(resp.text)
            info["tipo_anuncio"] = "Premium" if info["parcelamento_sem_juros"] else "Classico"

        redirected_id = _extrair_item_id(resp.url)
        if redirected_id and redirected_id != item_id and (not info["data_criacao"] or not _vendedor_confirmado() or info["vendas"] is None):
            api_data = _ml_api_item_com_oauth_tenant(client_id, redirected_id) or _ml_api_item(redirected_id)
            if isinstance(api_data, dict):
                if not info["data_criacao"]:
                    data_api = _normalizar_data_ml(api_data.get("date_created") or api_data.get("start_time"))
                    if data_api:
                        info["data_criacao"] = data_api
                        info["fonte"] = "api_item_redirect"
                if not info["vendedor"] or _fonte_vendedor_fraca(info.get("fonte_vendedor")):
                    vendedor_api, seller_id = _vendedor_da_api(api_data)
                    if vendedor_api:
                        info["vendedor"] = vendedor_api
                        info["fonte_vendedor"] = "api_item_redirect"
                    if seller_id:
                        info["seller_id"] = str(seller_id).strip()
                if not info["sku"]:
                    sku_api = _ml_extrair_sku(api_data)
                    if sku_api:
                        info["sku"] = sku_api
                if not info["condicao"]:
                    item_condition = _ml_extrair_item_condition(api_data)
                    if item_condition:
                        info["condicao"] = item_condition
                        info["condition"] = item_condition
                        info["item_condition"] = item_condition
                if not info.get("shipping"):
                    shipping_info = api_data.get("shipping") if isinstance(api_data.get("shipping"), dict) else {}
                    if shipping_info:
                        logistic_type = str(shipping_info.get("logistic_type") or "").strip()
                        info["shipping"] = shipping_info
                        info["logistic_type"] = logistic_type
                        info["shipping_mode"] = str(shipping_info.get("mode") or "").strip()
                        info["is_full"] = logistic_type.lower() == "fulfillment"
                if not info["listing_type_id"]:
                    listing_type_id = str(api_data.get("listing_type_id") or "").strip()
                    if listing_type_id:
                        info["listing_type_id"] = listing_type_id
                        info["listing_type_name"] = _ml_nome_tipo_anuncio(listing_type_id)
                        info["tipo_anuncio"] = info["listing_type_name"]
                sem_juros_api = _ml_parcelamento_sem_juros_api(api_data)
                if info["parcelamento_sem_juros"] is None and sem_juros_api is not None:
                    info["parcelamento_sem_juros"] = bool(sem_juros_api)
                    info["tipo_anuncio"] = "Premium" if info["parcelamento_sem_juros"] else "Classico"
                if info["vendas"] is None:
                    vendas_api = _vendas_da_api(api_data)
                    if vendas_api is not None:
                        info["vendas"] = vendas_api
                        info["fonte_vendas"] = "api_item_redirect"
                if info["visitas"] is None:
                    visitas_api = _ml_api_visitas_com_oauth_tenant(client_id, redirected_id)
                    if isinstance(visitas_api, dict) and visitas_api.get("visitas") is not None:
                        info["visitas"] = visitas_api.get("visitas")
                        info["fonte_visitas"] = visitas_api.get("fonte") or "api_visitas"
    except Exception:
        logger.exception("[Favoritos][Datas] Erro ao consultar codigo-fonte de %s", url)

    _aplicar_data_criacao_aproximada()
    return info


def _meses_desde_date(date_str: str | None):
    if not date_str:
        return None
    try:
        safe = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(safe)
        dias = max(1, (datetime.now(dt.tzinfo) - dt).days)
        return max(1, dias // 30)
    except Exception:
        return None


def _estimar_meses_anuncio(url_anuncio: str, max_retries: int = 3, delay: int = 2):
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url_anuncio, headers=_ml_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("Erro ao buscar %s: status %s. Tentativa %s/%s", url_anuncio, resp.status_code, tentativa + 1, max_retries)
                time.sleep(delay)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            datas = []
            for tag in soup.find_all("span"):
                texto = _normalize_text(tag.text)
                if texto.startswith("ha "):
                    datas.append(texto)

            if datas:
                texto = datas[-1]
                if "ano" in texto:
                    anos = int(texto.split("ha")[1].split("ano")[0].strip())
                    meses = anos * 12
                    if "mes" in texto:
                        meses += int(texto.split("ano")[1].split("mes")[0].strip())
                    return meses
                if "mes" in texto:
                    return int(texto.split("ha")[1].split("mes")[0].strip())
                if "dia" in texto:
                    dias = int(texto.split("ha")[1].split("dia")[0].strip())
                    return max(1, dias // 30)

            perguntas = soup.find_all("div", {"class": "questions__item"})
            datas_perg = []
            for pergunta in perguntas:
                data_tag = pergunta.find("span", {"class": "questions__item-date"})
                if data_tag:
                    datas_perg.append(_normalize_text(data_tag.text))

            if datas_perg:
                texto = datas_perg[-1]
                if "ano" in texto:
                    anos = int(texto.split("ha")[1].split("ano")[0].strip())
                    meses = anos * 12
                    if "mes" in texto:
                        meses += int(texto.split("ano")[1].split("mes")[0].strip())
                    return meses
                if "mes" in texto:
                    return int(texto.split("ha")[1].split("mes")[0].strip())
                if "dia" in texto:
                    dias = int(texto.split("ha")[1].split("dia")[0].strip())
                    return max(1, dias // 30)

            logger.warning("Nenhuma data encontrada para %s", url_anuncio)
            return None
        except Exception:
            logger.exception("Erro ao processar %s. Tentativa %s/%s", url_anuncio, tentativa + 1, max_retries)
            time.sleep(delay)
    return None


def _buscar_anuncios_mercadolivre(termo: str, max_retries: int = 3, delay: int = 2):
    if termo.startswith("http"):
        url = termo
        if "lista.mercadolivre.com.br" not in url:
            return _buscar_anuncios_por_link(url, max_retries=max_retries, delay=delay)
    termos = _deduplicar_lista([termo, _simplificar_termo_busca(termo)])
    if len(termos) <= 1:
        return _buscar_anuncios_por_termo(termo, max_retries=max_retries, delay=delay)
    return _deduplicar_anuncios(_buscar_anuncios_paralelo(termos, max_retries=max_retries, delay=delay, max_workers=BUSCAS_PARALLELAS_FAVORITOS))


BUSCAS_PARALLELAS_FAVORITOS = 8


def _deduplicar_lista(valores):
    vistos = set()
    saida = []
    for valor in valores:
        if not valor:
            continue
        texto = str(valor).strip()
        if not texto or texto in vistos:
            continue
        vistos.add(texto)
        saida.append(texto)
    return saida


def _buscar_anuncios_paralelo(termos, max_retries: int = 3, delay: int = 2, max_workers: int = BUSCAS_PARALLELAS_FAVORITOS):
    if not termos:
        return []
    termos = _deduplicar_lista(termos)
    workers = min(max_workers or 1, len(termos))
    resultados = []

    def _executar_busca(termo_local: str):
        return _buscar_anuncios_por_termo(termo_local, max_retries=max_retries, delay=delay)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {executor.submit(_executar_busca, termo): termo for termo in termos}
        for futuro in as_completed(futuros):
            try:
                lote = futuro.result()
                if lote:
                    resultados.extend(lote)
            except Exception:
                logger.exception("[Favoritos] Falha em busca paralela para '%s'.", futuros[futuro])
    return resultados


def _buscar_anuncios_por_link(url: str, max_retries: int = 3, delay: int = 2):
    dados = _extrair_dados_produto_html(url, max_retries=max_retries, delay=delay)

    termos = []
    item_id = _extrair_item_id(url)
    if item_id:
        termos.append(item_id)

    codigo = (dados or {}).get("codigo")
    if codigo:
        termos.append(codigo)

    titulo = (dados or {}).get("titulo")
    if titulo:
        termos.append(titulo)

    veiculo = (dados or {}).get("veiculo")
    anos = (dados or {}).get("anos") or []
    if veiculo:
        if anos:
            for ano in anos[:3]:
                termos.append(f"{veiculo} {ano}")
        else:
            termos.append(veiculo)

    termo_tecnico = _gerar_termo_tecnico(dados)
    if termo_tecnico:
        termos.append(termo_tecnico)

    termo_limpo = _simplificar_termo_busca((dados or {}).get("titulo") or "")
    if termo_limpo:
        termos.append(termo_limpo)

    if not termos:
        anuncio = _extrair_anuncio_produto(url, max_retries=max_retries, delay=delay)
        termo_busca = (anuncio or {}).get("titulo") or ""
        if termo_busca:
            termos.append(termo_busca)

    # Estratégia 1: Catálogo (agrupa vendedores do MESMO produto)
    catalog_id = _extrair_catalog_id(url)
    if catalog_id:
        resultados_catalogo = _buscar_ofertas_catalogo(url, catalog_id, max_retries=max_retries, delay=delay)
        if resultados_catalogo:
            if len(resultados_catalogo) >= 3:
                return resultados_catalogo

    # Estratégia 2: montando termos para busca paralela em lotes (5 por vez)
    codigos = []
    if (dados or {}).get("codigo"):
        codigos.append((dados or {}).get("codigo"))
    termo_tecnico = _gerar_termo_tecnico(dados)
    termos_paralelos = codigos + ([termo_tecnico] if termo_tecnico else [])
    veiculo = (dados or {}).get("veiculo")
    anos = (dados or {}).get("anos") or []
    if veiculo and anos:
        termos_paralelos.extend([f"{veiculo} {ano}" for ano in anos[:2]])
    if veiculo:
        termos_paralelos.append(veiculo)
    termos_paralelos.extend(termos)

    termos_paralelos = _deduplicar_lista(termos_paralelos)
    resultados = []
    if resultados_catalogo:
        resultados.extend(resultados_catalogo)

    lote_size = BUSCAS_PARALLELAS_FAVORITOS
    for idx in range(0, len(termos_paralelos), lote_size):
        lote = termos_paralelos[idx: idx + lote_size]
        if not lote:
            continue
        resultados.extend(_buscar_anuncios_paralelo(lote, max_retries=max_retries, delay=delay, max_workers=BUSCAS_PARALLELAS_FAVORITOS))
        if len(resultados) >= 10:
            break

    return _deduplicar_anuncios(resultados)


def _buscar_anuncios_por_termo(termo: str, max_retries: int = 3, delay: int = 2):
    termo_original = termo
    termo_url = quote_plus(termo)
    url = f"https://lista.mercadolivre.com.br/{termo_url}"

    api_data = _ml_api_search(termo, limit=50)
    if api_data and "results" in api_data:
        anuncios = []
        for item in api_data.get("results", []):
            anuncios.append({
                "url": item.get("permalink"),
                "titulo": item.get("title"),
                "preco": item.get("price"),
                "vendas": item.get("sold_quantity"),
                "data_criacao": item.get("date_created")
            })
        return anuncios

    html_anuncios = _buscar_anuncios_mercadolivre_html(url, max_retries=max_retries, delay=delay)
    if html_anuncios and len(html_anuncios) >= 10:
        return html_anuncios

    termo_limpo = _simplificar_termo_busca(termo_original)
    if termo_limpo and termo_limpo != termo:
        termo_url_limpo = quote_plus(termo_limpo)
        url_limpo = f"https://lista.mercadolivre.com.br/{termo_url_limpo}"
        html_anuncios_limpo = _buscar_anuncios_mercadolivre_html(url_limpo, max_retries=max_retries, delay=delay)
        if html_anuncios_limpo:
            return _deduplicar_anuncios(html_anuncios + html_anuncios_limpo)

    return html_anuncios


def _parse_vendas_ml(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except Exception:
            return None
    texto = html_lib.unescape(str(v)).strip().lower()
    if not texto:
        return None

    match = re.search(r"(\d[\d\.,]*)\s*(k|mil)?\b", texto, flags=re.IGNORECASE)
    if not match:
        return None

    valor_raw = (match.group(1) or "").strip()
    sufixo = (match.group(2) or "").strip().lower()
    if not valor_raw:
        return None
    if "." in valor_raw and "," in valor_raw:
        if valor_raw.rfind(".") > valor_raw.rfind(","):
            valor_raw = re.sub(r"[^\d.,-]", "", valor_raw).replace(",", "")
        else:
            valor_raw = re.sub(r"[^\d.,-]", "", valor_raw).replace(".", "").replace(",", ".")
    elif "," in valor_raw:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", valor_raw):
            valor_raw = re.sub(r",", "", valor_raw)
        else:
            valor_raw = valor_raw.replace(",", ".")
    elif "." in valor_raw:
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", valor_raw):
            valor_raw = valor_raw.replace(".", "")

    try:
        valor = float(re.sub(r"[^\d.\-]", "", valor_raw))
    except Exception:
        digitos = re.findall(r"\d+", valor_raw)
        if not digitos:
            return None
        try:
            valor = float(digitos[0])
        except Exception:
            return None

    if sufixo in ("k", "mil"):
        valor *= 1000

    try:
        return int(round(valor))
    except Exception:
        return None


def _normalizar_nome_vendedor_ml(dados: object) -> str:
    if not isinstance(dados, dict):
        return ""
    seller = dados.get("seller") if isinstance(dados.get("seller"), dict) else {}
    official_store = dados.get("official_store") if isinstance(dados.get("official_store"), dict) else {}
    vendedor = (
        dados.get("official_store_name")
        or official_store.get("name")
        or dados.get("official_store", {}).get("name")
        or official_store.get("nickname")
        or dados.get("official_store", {}).get("nickname")
        or seller.get("nickname")
        or dados.get("seller_name")
    )
    # Mantemos somente identificadores de loja/vendedor publicados no anuncio ou loja oficial.
    if isinstance(vendedor, str):
        vendedor = vendedor.strip()
    if isinstance(vendedor, str):
        vendedor = vendedor.strip().strip('"').strip("'")
    return vendedor if vendedor else ""


def _obter_info_anuncio_api(item_id: str, client_id: str = ""):
    item_id = str(item_id or "").strip().upper().replace("-", "")
    if not item_id.startswith("MLB") or len(item_id) < 7:
        return None

    api_data = _ml_api_item_com_oauth_tenant(client_id, item_id) if client_id else None
    if not isinstance(api_data, dict):
        api_data = _ml_api_item(item_id)
    if not isinstance(api_data, dict):
        return None

    data_criacao = _normalizar_data_ml(
        api_data.get("date_created")
        or api_data.get("start_time")
        or api_data.get("start_date")
        or api_data.get("creation_date")
    )

    seller = api_data.get("seller") if isinstance(api_data.get("seller"), dict) else {}
    seller_id = api_data.get("seller_id") or seller.get("id")
    vendedor = _normalizar_nome_vendedor_ml(api_data)

    if not vendedor and seller_id:
        user_data = _ml_api_user_com_oauth_tenant(client_id, str(seller_id)) if client_id else None
        if not isinstance(user_data, dict):
            user_data = _ml_api_user(str(seller_id))
        if isinstance(user_data, dict):
            vendedor = (
                user_data.get("official_store_name")
                or (user_data.get("official_store") or {}).get("name")
                or user_data.get("nickname")
            )

    # Usa múltiplos campos comuns para evitar divergência entre payloads.
    vendas = None
    for campo in ("sold_quantity", "soldQuantity", "sold", "sold_quantity_by_state", "sold_quantity_by_country", "ventas", "quantidade_vendida"):
        if campo not in api_data:
            continue
        vendas_api = _parse_vendas_ml(api_data.get(campo))
        if vendas_api is not None:
            vendas = vendas_api
            break

    item_condition = _ml_extrair_item_condition(api_data) or None

    return {
        "vendas": vendas,
        "vendedor": str(vendedor).strip() if vendedor else None,
        "seller_id": str(seller_id).strip() if seller_id else None,
        "data_criacao": data_criacao,
        "parcelamento_sem_juros": _ml_parcelamento_sem_juros_api(api_data),
        "sku": _ml_extrair_sku(api_data) or None,
        "condicao": item_condition,
        "condition": item_condition,
        "item_condition": item_condition,
    }

PEER_EXPORTS = ['_normalizar_data_ml', '_extrair_data_criacao_codigo_fonte', '_extrair_vendedor_codigo_fonte', '_extrair_info_anuncio', '_meses_desde_date', '_estimar_meses_anuncio', '_buscar_anuncios_mercadolivre', 'BUSCAS_PARALLELAS_FAVORITOS', '_deduplicar_lista', '_buscar_anuncios_paralelo', '_buscar_anuncios_por_link', '_buscar_anuncios_por_termo', '_parse_vendas_ml', '_normalizar_nome_vendedor_ml', '_obter_info_anuncio_api']
__all__ = PEER_EXPORTS + ["configure_favoritos_extract_runtime"]

configure_favoritos_extract_runtime()
