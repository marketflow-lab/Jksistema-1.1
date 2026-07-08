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


def configure_favoritos_busca_runtime(runtime_module=None, peers=None):
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


configure_favoritos_busca_runtime()


def _favoritos_taxa_padrao_por_tipo_anuncio(anuncio: dict) -> float | None:
    tipo_raw = str(
        (anuncio or {}).get("listing_type_id")
        or (anuncio or {}).get("listing_type_name")
        or (anuncio or {}).get("tipo_anuncio")
        or ""
    ).strip().lower()
    if not tipo_raw:
        return None
    if "gold_pro" in tipo_raw or "premium" in tipo_raw or tipo_raw == "pro":
        return 0.17
    if (
        "gold_special" in tipo_raw
        or "classico" in tipo_raw
        or "classic" in tipo_raw
        or tipo_raw in {"gold", "silver", "bronze"}
    ):
        return 0.12
    if "free" in tipo_raw or "grat" in tipo_raw:
        return 0.0
    return None


def _favoritos_frete_existente_anuncio(anuncio: dict) -> float | None:
    if not isinstance(anuncio, dict):
        return None
    for campo in (
        "shipping_seller_cost",
        "shipping_cost",
        "frete_ml",
        "shipping_list_cost",
        "shipping_base_cost",
        "frete",
        "custo_frete",
    ):
        valor = _to_float_safe(anuncio.get(campo))
        if valor is not None:
            return valor
    shipping_info = anuncio.get("shipping") if isinstance(anuncio.get("shipping"), dict) else {}
    for campo in ("seller_cost", "shipping_cost", "base_cost", "list_cost"):
        valor = _to_float_safe((shipping_info or {}).get(campo))
        if valor is not None:
            return valor
    return None


def _favoritos_anuncio_tem_frete_gratis(anuncio: dict) -> bool:
    if not isinstance(anuncio, dict):
        return False
    shipping_info = anuncio.get("shipping") if isinstance(anuncio.get("shipping"), dict) else {}
    return bool(
        anuncio.get("free_shipping") is True
        or anuncio.get("frete_gratis") is True
        or _flag_frete_gratis(anuncio.get("Frete Gratis"))
        or _flag_frete_gratis(anuncio.get("Frete Grátis"))
        or (shipping_info or {}).get("free_shipping") is True
    )


def _favoritos_chaves_frete_anuncio(anuncio: dict, sku_hint: str = "") -> list[str]:
    if not isinstance(anuncio, dict):
        return []

    shipping_info = anuncio.get("shipping") if isinstance(anuncio.get("shipping"), dict) else {}
    logistic_type = str(anuncio.get("logistic_type") or (shipping_info or {}).get("logistic_type") or "").strip().lower()
    shipping_mode = str(anuncio.get("shipping_mode") or (shipping_info or {}).get("mode") or "").strip().lower()
    listing_type = str(
        anuncio.get("listing_type_id")
        or anuncio.get("listing_type_name")
        or anuncio.get("tipo_anuncio")
        or ""
    ).strip().lower()
    sku_base = _favoritos_resolver_sku_para_margem(anuncio, sku_hint)
    skus = _extrair_skus_para_custo(
        sku_base,
        anuncio.get("sku"),
        anuncio.get("sku_display"),
        anuncio.get("seller_sku"),
        anuncio.get("seller_custom_field"),
        anuncio.get("SELLER_SKU"),
    )

    chaves = []
    vistos = set()
    niveis = (
        (logistic_type, shipping_mode, listing_type),
        (logistic_type, shipping_mode, ""),
        (logistic_type, "", listing_type),
        ("", "", listing_type),
        (logistic_type, "", ""),
        ("", "", ""),
    )
    for sku in skus:
        sku_norm = _normalizar_sku_mes(sku).upper()
        if not sku_norm:
            continue
        for logistica, modo, tipo in niveis:
            chave = f"{sku_norm}|{logistica}|{modo}|{tipo}"
            if chave in vistos:
                continue
            vistos.add(chave)
            chaves.append(chave)
    return chaves


def _favoritos_completar_frete_pausados_por_sku(anuncios: list[dict], sku_hint: str = "") -> None:
    if not isinstance(anuncios, list) or not anuncios:
        return

    fretes_por_chave: dict[str, dict] = {}
    for anuncio in anuncios:
        if not _favoritos_anuncio_tem_frete_gratis(anuncio):
            continue
        frete = _favoritos_frete_existente_anuncio(anuncio)
        if frete is None:
            continue
        texto = (
            anuncio.get("frete_ml_text")
            or anuncio.get("shipping_text")
            or formatar_moeda_br(frete)
        )
        info = {"frete": float(frete), "texto": texto, "item_id": anuncio.get("id") or anuncio.get("mlb") or ""}
        for chave in _favoritos_chaves_frete_anuncio(anuncio, sku_hint):
            fretes_por_chave.setdefault(chave, info)

    if not fretes_por_chave:
        return

    for anuncio in anuncios:
        if not isinstance(anuncio, dict):
            continue
        if not _favoritos_anuncio_tem_frete_gratis(anuncio):
            continue
        if _favoritos_frete_existente_anuncio(anuncio) is not None:
            continue

        frete_info = None
        for chave in _favoritos_chaves_frete_anuncio(anuncio, sku_hint):
            frete_info = fretes_por_chave.get(chave)
            if frete_info:
                break
        if not frete_info:
            continue

        frete = float(frete_info.get("frete") or 0.0)
        texto = frete_info.get("texto") or formatar_moeda_br(frete)
        anuncio["shipping_cost"] = round(frete, 2)
        anuncio["shipping_seller_cost"] = round(frete, 2)
        anuncio["frete_ml"] = round(frete, 2)
        anuncio["shipping_text"] = texto
        anuncio["frete_ml_text"] = texto
        anuncio["shipping_cost_fallback_source"] = "outro_anuncio_mesmo_sku"
        origem = frete_info.get("item_id") or "mesmo SKU"
        detalhe = f"Frete reaproveitado do anuncio {origem} do mesmo SKU para calcular margem."
        breakdown_atual = str(anuncio.get("shipping_breakdown") or "").strip()
        anuncio["shipping_breakdown"] = f"{breakdown_atual} | {detalhe}" if breakdown_atual else detalhe


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    norm = unicodedata.normalize("NFKD", text)
    norm = "".join([c for c in norm if not unicodedata.combining(c)])
    return norm.lower().strip()


def _normalizar_termo_busca_ml(termo: str) -> str:
    texto = _normalize_text(termo)
    texto = re.sub(r"""[.,;:()\[\]{}"'^~!?|\\/_+=*\-]+""", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def _termos_busca_ml(termo: str, extras: list[str] | None = None) -> list[str]:
    candidatos = [
        _normalizar_termo_busca_ml(termo),
        str(termo or "").strip(),
    ]
    for extra in extras or []:
        candidatos.extend([
            _normalizar_termo_busca_ml(str(extra or "")),
            str(extra or "").strip(),
        ])
    vistos: set[str] = set()
    saida: list[str] = []
    for candidato in candidatos:
        if not candidato:
            continue
        chave = candidato.lower()
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(candidato)
    return saida


def _extrair_item_id(url: str) -> str | None:
    texto = unquote(str(url or ""))
    match = re.search(r"MLB-?(\d+)", texto, re.IGNORECASE)
    if match:
        return f"MLB{match.group(1)}"
    normalizado = unicodedata.normalize("NFD", texto)
    normalizado = "".join(ch for ch in normalizado if unicodedata.category(ch) != "Mn")
    match = re.search(r"\ban.?ncio\s*#?\s*(\d{8,})\b", normalizado, re.IGNORECASE)
    return f"MLB{match.group(1)}" if match else None


def _extrair_catalog_id(url: str) -> str | None:
    match = re.search(r"MLBU\d+", url or "")
    return match.group(0) if match else None


def _buscar_anuncios_mercadolivre_html(url: str, max_retries: int = 3, delay: int = 2):
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=_ml_headers(), timeout=10, verify=False)
            if resp.status_code != 200:
                logger.warning("Erro ao buscar %s: status %s. Tentativa %s/%s", url, resp.status_code, tentativa + 1, max_retries)
                time.sleep(delay)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            anuncios = []

            preloaded = _extrair_preloaded_results(resp.text)
            if preloaded:
                return preloaded

            ldjson = _extrair_links_ld_json(resp.text)
            if ldjson:
                return ldjson

            items = soup.select("li.ui-search-layout__item, div.poly-card, section.poly-card")
            if not items:
                items = soup.select("div.ui-search-result__content-wrapper")

            for item in items:
                link_tag = item.select_one("a.poly-component__title, h2.poly-component__title-wrapper a, a.ui-search-link")
                if not link_tag:
                    link_tag = item.select_one("a[href*='/MLB-'], a[href*='/p/MLB']")

                titulo_tag = item.select_one("a.poly-component__title, h2.poly-component__title-wrapper a, h2.ui-search-item__title")
                if not titulo_tag:
                    titulo_tag = item.select_one("span.ui-search-item__group__element.ui-search-item__title")

                preco_tag = item.select_one("span.ui-search-price__part")
                if not preco_tag:
                    preco_tag = item.select_one("span.andes-money-amount__fraction")

                vendas_tag = item.select_one("span.ui-search-item__group__element.ui-search-item__variations-text")
                if not vendas_tag:
                    vendas_tag = item.find("span", string=lambda t: t and ("vendido" in t or "vendidos" in t))

                if link_tag:
                    anuncios.append({
                        "url": link_tag.get("href"),
                        "titulo": titulo_tag.text.strip() if titulo_tag else "",
                        "preco": preco_tag.text.strip() if preco_tag else "",
                        "vendas": vendas_tag.text.strip() if vendas_tag else None
                    })

            if anuncios:
                return anuncios

            links_soup = []
            for a in soup.select("a[href*='/MLB-'], a[href*='/p/MLB']"):
                href = a.get("href")
                if not href:
                    continue
                if "mercadolivre.com.br" not in href:
                    continue
                if href not in links_soup:
                    links_soup.append(href)

            if links_soup:
                return [
                    {"url": link, "titulo": "", "preco": "", "vendas": None}
                    for link in links_soup
                ]

            # Fallback por regex quando os seletores falham
            links = re.findall(r"https?://[^\s\"']+?/(?:MLB-\d+|p/MLB\d+)[^\s\"']*", resp.text)
            links_unicos = []
            for link in links:
                if link not in links_unicos:
                    links_unicos.append(link)

            if links_unicos:
                return [
                    {"url": link, "titulo": "", "preco": "", "vendas": None}
                    for link in links_unicos
                ]

            logger.warning("Nenhum item encontrado para %s", url)
            return []
        except Exception:
            logger.exception("Erro ao processar busca HTML em %s. Tentativa %s/%s", url, tentativa + 1, max_retries)
            time.sleep(delay)
    return []


def _extrair_preloaded_results(html_text: str):
    if not html_text:
        return []
    match = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;", html_text, re.DOTALL)
    raw = None
    if match:
        raw = match.group(1)
    else:
        match = re.search(r"__PRELOADED_STATE__\s*=\s*JSON\.parse\(\"(.*?)\"\)", html_text, re.DOTALL)
        if match:
            raw = match.group(1)

    if not raw:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        try:
            unescaped = bytes(raw, "utf-8").decode("unicode_escape")
            data = json.loads(unescaped)
        except Exception:
            return []

    resultados = _buscar_lista_resultados(data)
    if not resultados:
        return []

    anuncios = []
    for item in resultados:
        if not isinstance(item, dict):
            continue
        url = item.get("permalink") or item.get("url")
        titulo = item.get("title") or ""
        preco = item.get("price")
        vendas = item.get("sold_quantity")
        data_criacao = item.get("date_created")
        if url:
            anuncios.append({
                "url": url,
                "titulo": titulo,
                "preco": preco,
                "vendas": vendas,
                "data_criacao": data_criacao
            })
    return anuncios


def _extrair_links_ld_json(html_text: str):
    if not html_text:
        return []
    try:
        soup = BeautifulSoup(html_text, "lxml")
        scripts = soup.find_all("script", {"type": "application/ld+json"})
        anuncios = []
        for script in scripts:
            try:
                data = json.loads(script.text)
            except Exception:
                continue
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                items = data.get("itemListElement") or []
                for item in items:
                    if isinstance(item, dict):
                        url = item.get("url") or (item.get("item") or {}).get("@id")
                        if url:
                            anuncios.append({
                                "url": url,
                                "titulo": "",
                                "preco": "",
                                "vendas": None
                            })
        if anuncios:
            return anuncios
    except Exception:
        logger.exception("Erro ao extrair links ld+json")
    return []


def _simplificar_termo_busca(termo: str) -> str:
    texto = _normalize_text(termo)
    if not texto:
        return ""
    texto = re.sub(r"""[.,;:()\[\]{}"'^~!?|\\/_+=*\-]+""", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    stopwords = {
        "de", "da", "do", "das", "dos", "para", "com", "sem", "e", "ou", "em",
        "novo", "nova", "original", "gen", "universal", "kit", "peca", "peça",
        "sensor", "cebolao", "cebolinha", "temperatura", "radiador"
    }

    tokens = [t for t in re.split(r"\s+", texto) if t]
    filtrados = []
    for t in tokens:
        if t in stopwords:
            continue
        if len(t) >= 3 or t.isdigit():
            filtrados.append(t)

    termo_final = " ".join(filtrados[:6])
    return termo_final.strip()


def _deduplicar_anuncios(anuncios: list):
    vistos = set()
    unicos = []
    for item in anuncios:
        url = (item or {}).get("url") or (item or {}).get("link") or (item or {}).get("permalink")
        if not url or url in vistos:
            continue
        vistos.add(url)
        normalizado = dict(item or {})
        normalizado["url"] = url
        unicos.append(normalizado)
    
    # Ordena por relevância: itens com título > sem título
    com_titulo = [a for a in unicos if (a or {}).get("titulo")]
    sem_titulo = [a for a in unicos if not (a or {}).get("titulo")]
    
    return com_titulo + sem_titulo


def _enriquecer_vendas_com_api(anuncios: list, max_workers: int = 8, client_id: str = ""):
    if not anuncios:
        return

    tarefas = []
    indice_por_id = {}

    for idx, anuncio in enumerate(anuncios):
        if not isinstance(anuncio, dict):
            continue
        item_id = _extrair_item_id(anuncio.get("url") or anuncio.get("link") or anuncio.get("permalink") or anuncio.get("id") or "")
        if not item_id:
            continue
        if item_id in indice_por_id:
            continue
        indice_por_id[item_id] = idx
        tarefas.append(item_id)

    if not tarefas:
        return

    workers = min(max_workers or 1, len(tarefas))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {
            executor.submit(_obter_info_anuncio_api, item_id, client_id or ""): item_id
            for item_id in tarefas
        }
        for futuro in as_completed(futuros):
            item_id = futuros[futuro]
            try:
                info_api = futuro.result()
            except Exception:
                logger.exception("[Favoritos] Falha ao buscar sold_quantity API para %s", item_id)
                continue
            if not info_api:
                continue
            indice = indice_por_id.get(item_id)
            if indice is None or indice >= len(anuncios):
                continue
            anuncio = anuncios[indice]
            if isinstance(anuncio, dict):
                if info_api.get("vendedor"):
                    anuncio["vendedor"] = info_api["vendedor"]
                if info_api.get("seller_id"):
                    anuncio["seller_id"] = info_api["seller_id"]
                if info_api.get("data_criacao") and not anuncio.get("data_criacao"):
                    anuncio["data_criacao"] = info_api["data_criacao"]
                if info_api.get("vendas") is not None:
                    anuncio["vendas"] = info_api["vendas"]
                if info_api.get("parcelamento_sem_juros") is not None:
                    anuncio["parcelamento_sem_juros"] = bool(info_api["parcelamento_sem_juros"])
                    anuncio["tipo_anuncio"] = "Premium" if anuncio["parcelamento_sem_juros"] else "Classico"
                if info_api.get("sku") and not anuncio.get("sku"):
                    anuncio["sku"] = info_api["sku"]
                item_condition = info_api.get("item_condition") or info_api.get("condition") or info_api.get("condicao")
                if item_condition:
                    anuncio["condicao"] = item_condition
                    anuncio["condition"] = item_condition
                    anuncio["item_condition"] = item_condition


def _normalizar_anuncio_favoritos(item: dict) -> dict:
    item = item or {}
    preco = item.get("preco")
    if preco is None:
        preco = item.get("price")
    if isinstance(preco, (int, float)):
        preco = f"R$ {float(preco):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    vendas = item.get("vendas")
    if vendas is None:
        vendas = item.get("sold_quantity")
    seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
    official_store = item.get("official_store") if isinstance(item.get("official_store"), dict) else {}
    vendedor = (
        item.get("vendedor")
        or _normalizar_nome_vendedor_ml(item)
    )

    return {
        "url": item.get("url") or item.get("link") or item.get("permalink") or "",
        "titulo": item.get("titulo") or item.get("title") or "",
        "preco": preco if preco not in (None, "") else "N/A",
        "vendas": _parse_vendas_ml(vendas),
        "data_criacao": item.get("data_criacao") or item.get("date_created") or item.get("start_time"),
        "vendedor": vendedor or "",
        "seller_id": item.get("seller_id") or item.get("seller_id") or seller.get("id"),
    }


def _buscar_lista_resultados(data):
    if isinstance(data, dict):
        if "results" in data and isinstance(data["results"], list):
            return data["results"]
        for value in data.values():
            found = _buscar_lista_resultados(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _buscar_lista_resultados(item)
            if found:
                return found
    return []

    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=_ml_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("Erro ao buscar %s: status %s. Tentativa %s/%s", url, resp.status_code, tentativa + 1, max_retries)
                time.sleep(delay)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            anuncios = []

            items = soup.select("li.ui-search-layout__item")
            if not items:
                items = soup.select("div.ui-search-result__content-wrapper")

            if not items:
                logger.warning("Nenhum item encontrado para %s", url)
                return []

            for item in items:
                link_tag = item.select_one("a.ui-search-link")
                if not link_tag:
                    link_tag = item.select_one("a[href*='/MLB-']")

                titulo_tag = item.select_one("h2.ui-search-item__title")
                if not titulo_tag:
                    titulo_tag = item.select_one("span.ui-search-item__group__element.ui-search-item__title")

                preco_tag = item.select_one("span.ui-search-price__part")
                if not preco_tag:
                    preco_tag = item.select_one("span.andes-money-amount__fraction")

                vendas_tag = item.select_one("span.ui-search-item__group__element.ui-search-item__variations-text")
                if not vendas_tag:
                    vendas_tag = item.find("span", string=lambda t: t and ("vendido" in t or "vendidos" in t))

                if link_tag and titulo_tag and preco_tag:
                    anuncios.append({
                        "url": link_tag["href"],
                        "titulo": titulo_tag.text.strip(),
                        "preco": preco_tag.text.strip(),
                        "vendas": vendas_tag.text.strip() if vendas_tag else None
                    })
            return anuncios
        except Exception:
            logger.exception("Erro ao processar busca por '%s'. Tentativa %s/%s", termo, tentativa + 1, max_retries)
            time.sleep(delay)
    return []


def _extrair_anuncio_produto(url: str, max_retries: int = 3, delay: int = 2):
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=_ml_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("Erro ao buscar %s: status %s. Tentativa %s/%s", url, resp.status_code, tentativa + 1, max_retries)
                time.sleep(delay)
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            titulo = None
            meta_title = soup.find("meta", {"property": "og:title"})
            if meta_title and meta_title.get("content"):
                titulo = meta_title.get("content").strip()
            if not titulo:
                h1_title = soup.select_one("h1.ui-pdp-title") or soup.find("h1")
                if h1_title:
                    titulo = h1_title.text.strip()

            preco = None
            meta_price = soup.find("meta", {"property": "product:price:amount"})
            if meta_price and meta_price.get("content"):
                preco = meta_price.get("content").strip()
            if not preco:
                meta_price_item = soup.find("meta", {"itemprop": "price"})
                if meta_price_item and meta_price_item.get("content"):
                    preco = meta_price_item.get("content").strip()
            if not preco:
                price_tag = soup.select_one("span.andes-money-amount__fraction")
                if price_tag:
                    preco = price_tag.text.strip()

            if not titulo and not preco:
                logger.warning("Nao foi possivel extrair dados do produto em %s", url)
                return None

            return {
                "url": url,
                "titulo": titulo or "",
                "preco": preco or "",
                "vendas": None
            }
        except Exception:
            logger.exception("Erro ao processar produto %s. Tentativa %s/%s", url, tentativa + 1, max_retries)
            time.sleep(delay)
    return None


def _extrair_dados_produto_html(url: str, max_retries: int = 3, delay: int = 2):
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=_ml_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("Erro ao buscar %s: status %s. Tentativa %s/%s", url, resp.status_code, tentativa + 1, max_retries)
                time.sleep(delay)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            titulo = None
            meta_title = soup.find("meta", {"property": "og:title"})
            if meta_title and meta_title.get("content"):
                titulo = meta_title.get("content").strip()
            if not titulo:
                h1_title = soup.select_one("h1.ui-pdp-title") or soup.find("h1")
                if h1_title:
                    titulo = h1_title.text.strip()

            atributos = _extrair_atributos_produto(resp.text, soup)
            codigo = _extrair_codigo_produto(atributos)
            veiculo = _extrair_veiculo_produto(atributos, titulo)
            anos = _extrair_anos_produto(atributos, titulo)

            return {
                "url": url,
                "titulo": titulo or "",
                "codigo": codigo,
                "veiculo": veiculo,
                "anos": anos
            }
        except Exception:
            logger.exception("Erro ao extrair dados do produto %s. Tentativa %s/%s", url, tentativa + 1, max_retries)
            time.sleep(delay)
    return None


def _extrair_atributos_produto(html_text: str, soup: BeautifulSoup):
    atributos = {}

    preloaded = _extrair_preloaded_state(html_text)
    if preloaded:
        atributos_lista = _buscar_lista_atributos(preloaded)
        for item in atributos_lista:
            nome = _normalize_text(item.get("name") or item.get("label") or "")
            valor = item.get("value_name") or item.get("value") or ""
            if nome and valor:
                atributos[nome] = str(valor).strip()

    if soup:
        for row in soup.select("tr"):
            cols = row.find_all("th") + row.find_all("td")
            if len(cols) >= 2:
                nome = _normalize_text(cols[0].get_text(" ", strip=True))
                valor = cols[1].get_text(" ", strip=True)
                if nome and valor:
                    atributos.setdefault(nome, valor)

    return atributos


def _extrair_preloaded_state(html_text: str):
    if not html_text:
        return None
    match = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;", html_text, re.DOTALL)
    raw = None
    if match:
        raw = match.group(1)
    else:
        match = re.search(r"__PRELOADED_STATE__\s*=\s*JSON\.parse\(\"(.*?)\"\)", html_text, re.DOTALL)
        if match:
            raw = match.group(1)

    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        try:
            unescaped = bytes(raw, "utf-8").decode("unicode_escape")
            return json.loads(unescaped)
        except Exception:
            return None


def _buscar_lista_atributos(data):
    if isinstance(data, dict):
        if "attributes" in data and isinstance(data["attributes"], list):
            return data["attributes"]
        for value in data.values():
            found = _buscar_lista_atributos(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _buscar_lista_atributos(item)
            if found:
                return found
    return []


def _extrair_codigo_produto(atributos: dict):
    if not atributos:
        return None
    chaves = [
        "codigo", "codigo do produto", "codigo do fabricante", "codigo de fabrica",
        "sku", "mpn", "numero de peca", "numero da peca", "numero de parte"
    ]
    for chave in chaves:
        for nome, valor in atributos.items():
            if chave == nome:
                return str(valor).strip()
    for valor in atributos.values():
        codigo = _extrair_codigo_oem(str(valor))
        if codigo:
            return codigo
    return None


def _extrair_veiculo_produto(atributos: dict, titulo: str | None):
    candidatos = []
    for chave in ["veiculo", "veiculo compativel", "aplicacao", "compatibilidade", "modelo", "marca", "linha"]:
        valor = atributos.get(chave)
        if valor:
            candidatos.append(valor)

    if titulo:
        candidatos.append(titulo)

    texto = " ".join([str(c) for c in candidatos if c])
    texto = _normalize_text(texto)
    texto = re.sub(r"\b(ano|anos|para|compativel|compativel com)\b", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def _extrair_anos_produto(atributos: dict, titulo: str | None):
    texto = " ".join([str(v) for v in atributos.values() if v])
    if titulo:
        texto += " " + titulo
    anos = re.findall(r"\b(19\d{2}|20\d{2})\b", texto)
    anos_unicos = []
    for ano in anos:
        if ano not in anos_unicos:
            anos_unicos.append(ano)
    return anos_unicos


def _extrair_codigo_oem(texto: str) -> str | None:
    if not texto:
        return None
    match = re.search(r"\b[0-9A-Z]{3,6}-[0-9A-Z]{2,5}-[0-9A-Z]{3,5}\b", texto.upper())
    return match.group(0) if match else None


def _gerar_termo_tecnico(dados: dict | None):
    if not dados:
        return None
    titulo = (dados.get("titulo") or "").lower()
    veiculo = dados.get("veiculo")
    if not veiculo:
        return None
    if "cebola" in titulo or "cebolao" in titulo or "cebolinha" in titulo:
        return f"interruptor termico radiador {veiculo}"
    return None


def _buscar_ofertas_catalogo(url: str, catalog_id: str, max_retries: int = 3, delay: int = 2):
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, headers=_ml_headers(), timeout=10)
            if resp.status_code != 200:
                logger.warning("Erro ao buscar catalogo %s: status %s. Tentativa %s/%s", url, resp.status_code, tentativa + 1, max_retries)
                time.sleep(delay)
                continue

            data = _extrair_preloaded_state(resp.text)
            if not data:
                return []

            links = _coletar_permalinks(data)
            if links:
                anuncios = []
                for link in links:
                    item_id = _extrair_item_id(link)
                    if item_id:
                        item_data = _ml_api_item(item_id)
                        if item_data:
                            anuncios.append({
                                "url": link,
                                "titulo": item_data.get("title", ""),
                                "preco": item_data.get("price"),
                                "vendas": item_data.get("sold_quantity"),
                                "data_criacao": item_data.get("date_created")
                            })
                        else:
                            item_html = _enriquecer_link_html(link, max_retries=1, delay=0)
                            anuncios.append(item_html)
                    else:
                        item_html = _enriquecer_link_html(link, max_retries=1, delay=0)
                        anuncios.append(item_html)
                return anuncios

            return []
        except Exception:
            logger.exception("Erro ao processar catalogo %s. Tentativa %s/%s", catalog_id, tentativa + 1, max_retries)
            time.sleep(delay)
    return []


def _coletar_permalinks(data):
    links = []
    if isinstance(data, dict):
        permalink = data.get("permalink") or data.get("url")
        if isinstance(permalink, str) and "MLB-" in permalink:
            links.append(permalink)
        for value in data.values():
            links.extend(_coletar_permalinks(value))
    elif isinstance(data, list):
        for item in data:
            links.extend(_coletar_permalinks(item))
    # Dedup
    unique = []
    seen = set()
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)
    return unique


def _montar_urls_busca_diretas(link_produto: str, dados: dict):
    urls = []

    item_id = _extrair_item_id(link_produto)
    if item_id:
        urls.append({
            "label": f"Link direto do produto: {item_id}",
            "url": link_produto,
            "tipo": "link_direto"
        })

    codigo = (dados or {}).get("codigo")
    if codigo:
        url_codigo = f"https://www.mercadolivre.com.br/jm/search?q={quote_plus(codigo)}"
        urls.append({
            "label": f"Pesquisar pelo código OEM: {codigo}",
            "url": url_codigo,
            "tipo": "codigo_oem"
        })

    veiculo = (dados or {}).get("veiculo")
    anos = (dados or {}).get("anos") or []

    if veiculo and anos:
        for ano in anos[:2]:
            termo = f"{veiculo} {ano}"
            url_veiculo_ano = f"https://www.mercadolivre.com.br/jm/search?q={quote_plus(termo)}"
            urls.append({
                "label": f"Pesquisar: {termo}",
                "url": url_veiculo_ano,
                "tipo": "veiculo_ano"
            })

    termo_tecnico = _gerar_termo_tecnico(dados)
    if termo_tecnico:
        url_tecnico = f"https://www.mercadolivre.com.br/jm/search?q={quote_plus(termo_tecnico)}"
        urls.append({
            "label": f"Pesquisar termo técnico: {termo_tecnico}",
            "url": url_tecnico,
            "tipo": "termo_tecnico"
        })

    if veiculo:
        url_veiculo = f"https://www.mercadolivre.com.br/jm/search?q={quote_plus(veiculo)}"
        urls.append({
            "label": f"Pesquisar pelo veículo: {veiculo}",
            "url": url_veiculo,
            "tipo": "veiculo_generico"
        })

    return urls


def _buscar_anuncios_mercadolivre_automatico(url_busca: str, max_anuncios: int = 60):
    """
    Scraping com Selenium + Chrome headless para contornar proteção do Mercado Livre.
    Com fallback para requests simples.
    """
    driver = None
    anuncios = []
    
    # Tentar com Selenium primeiro
    try:
        logger.info(f"[Selenium] Tentando buscar: {url_busca[:60]}")
        
        chrome_options = ChromeOptions()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.set_capability('unhandledPromptBehavior', 'dismiss')
        
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info("[Selenium] Navegador iniciado")
        except Exception as e:
            logger.warning(f"[Selenium] Erro ao iniciar Chrome: {e}. Usando fallback com requests...")
            driver = None
        
        if driver:
            try:
                driver.set_page_load_timeout(40)
                logger.info("[Selenium] Acessando página...")
                driver.get(url_busca)
                
                # Múltiplas tentativas de espera com seletores diferentes
                wait = WebDriverWait(driver, 15)
                seletores_teste = [
                    "li.ui-search-layout__item",
                    "div.poly-card",
                    "section.poly-card",
                    "a.poly-component__title",
                    "[data-component-type='s-search-result']",
                    ".s-result-item",
                    "div[class*='item'][class*='result']",
                    "h2[class*='title']"
                ]
                
                encontrou = False
                for seletor in seletores_teste:
                    try:
                        logger.debug(f"[Selenium] Testando seletor: {seletor}")
                        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, seletor)))
                        logger.info(f"[Selenium] Seletor encontrado: {seletor}")
                        encontrou = True
                        break
                    except Exception as e:
                        logger.debug(f"[Selenium] Seletor não encontrado: {seletor}")
                        continue
                
                if not encontrou:
                    logger.warning("[Selenium] Nenhum seletor encontrou elementos. Continuando...")
                
                # Scroll
                logger.info("[Selenium] Fazendo scroll...")
                for i in range(3):
                    driver.execute_script("window.scrollBy(0, window.innerHeight);")
                    time.sleep(0.5)
                
                # Tentar extrair com múltiplos seletores
                logger.info("[Selenium] Extraindo anuncios...")
                items = []
                
                for seletor in ["li.ui-search-layout__item", "div.poly-card", "section.poly-card", "div.ui-search-result__wrapper", "[data-component-type='s-search-result']", ".s-result-item", "div[class*='result']"]:
                    try:
                        items = driver.find_elements(By.CSS_SELECTOR, seletor)
                        if items:
                            logger.info(f"[Selenium] Encontrados {len(items)} itens com seletor: {seletor}")
                            break
                    except:
                        continue
                
                if items:
                    for idx, item in enumerate(items[:max_anuncios]):
                        try:
                            titulo = link = preco = vendas = None
                            
                            # Título
                            try:
                                for sel in ["a.poly-component__title", "h2.poly-component__title-wrapper a", "h2 a", "a[title]", ".s-item__title"]:
                                    elem = item.find_element(By.CSS_SELECTOR, sel)
                                    titulo = elem.get_attribute('title') or elem.text
                                    if titulo:
                                        break
                            except:
                                pass
                            
                            # Link
                            try:
                                for sel in ["a.poly-component__title", "h2.poly-component__title-wrapper a", "a[href*='/MLB-']", "a[href*='/p/MLB']", "a[href*='/item/MLB']", "a.s-item__link", "a[href*='mercadolivre']"]:
                                    elem = item.find_element(By.CSS_SELECTOR, sel)
                                    link = elem.get_attribute('href')
                                    if link:
                                        break
                            except:
                                pass
                            
                            # Preço
                            try:
                                elem = item.find_element(By.CSS_SELECTOR, ".s-item__price, .price, span[class*='price']")
                                preco = elem.text
                            except:
                                preco = 'N/A'
                            
                            # Vendas
                            try:
                                elem = item.find_element(By.CSS_SELECTOR, ".s-item__reviews, [class*='sold'], span[class*='review']")
                                vendas = elem.text
                            except:
                                pass
                            
                            if titulo and link:
                                anuncios.append({
                                    'titulo': str(titulo)[:120],
                                    'link': str(link).strip(),
                                    'preco': str(preco)[:50] if preco else 'N/A',
                                    'vendas': str(vendas)[:50] if vendas else None,
                                    'source': 'selenium'
                                })
                                logger.debug(f"[Selenium] Item {len(anuncios)}: {str(titulo)[:40]}")
                        except Exception as e:
                            logger.debug(f"[Selenium] Erro item {idx}: {str(e)[:60]}")
                            continue
                
                logger.info(f"[Selenium] Extraídos {len(anuncios)} anuncios")
            
            except Exception as e:
                logger.warning(f"[Selenium] Erro durante extração: {e}")
            
            finally:
                try:
                    driver.quit()
                except:
                    pass
    
    except Exception as e:
        logger.warning(f"[Selenium] Erro geral: {e}")
    
    # Se Selenium não conseguiu, usar fallback com requests + BeautifulSoup
    if not anuncios:
        logger.info("[Fallback] Tentando com requests + BeautifulSoup...")
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'pt-BR,pt;q=0.9',
            }
            
            response = requests.get(url_busca, headers=headers, timeout=20, verify=False)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Estratégia 1: Procurar por links diretos de produto
                logger.info("[Fallback] Estratégia 1: Procurando links /item/MLB...")
                for link_elem in soup.select("a.poly-component__title, h2.poly-component__title-wrapper a, a[href*='/MLB-'], a[href*='/p/MLB'], a[href*='/item/MLB']")[:max_anuncios]:
                    try:
                        link = link_elem.get('href', '').strip()
                        titulo = link_elem.get('title') or link_elem.get_text(strip=True)
                        
                        if link and titulo and len(titulo) > 5:
                            anuncios.append({
                                'titulo': str(titulo)[:120],
                                'link': link,
                                'preco': 'N/A',
                                'vendas': None,
                                'source': 'requests_fallback_v1'
                            })
                    except:
                        continue
                
                # Estratégia 2: Procurar por padrão de links com regex
                if not anuncios:
                    logger.info("[Fallback] Estratégia 2: Procurando com regex...")
                    import re as regex_module
                    links = regex_module.findall(r'href="(https://[^"]*?/(?:item/MLB|MLB-|p/MLB)[^"]*?)"', response.text)
                    
                    for link in links[:max_anuncios]:
                        try:
                            # Tentar extrair título a partir do HTML próximo
                            pattern = r'{}".*?<.*?>([^<]+)</.*?>'.format(regex_module.escape(link))
                            match = regex_module.search(pattern, response.text, regex_module.DOTALL)
                            titulo = match.group(1).strip() if match else f"Produto {len(anuncios)+1}"
                            
                            anuncios.append({
                                'titulo': str(titulo)[:120],
                                'link': link,
                                'preco': 'N/A',
                                'vendas': None,
                                'source': 'requests_fallback_v2'
                            })
                        except:
                            continue
                
                # Estratégia 3: Procurar por qualquer elemento com classe "item" ou "result"
                if not anuncios:
                    logger.info("[Fallback] Estratégia 3: Procurando por divs com classe item/result...")
                    for div in soup.select("div[class*='item'], div[class*='result']")[:max_anuncios]:
                        try:
                            # Procurar link dentro da div
                            link_elem = div.select_one("a[href*='mercadolivre']")
                            if link_elem:
                                link = link_elem.get('href', '').strip()
                                titulo = link_elem.get('title') or link_elem.get_text(strip=True)
                                
                                if link and titulo and len(titulo) > 5:
                                    anuncios.append({
                                        'titulo': str(titulo)[:120],
                                        'link': link,
                                        'preco': 'N/A',
                                        'vendas': None,
                                        'source': 'requests_fallback_v3'
                                    })
                        except:
                            continue
                
                # Remover duplicatas por link
                unique_links = {}
                for ann in anuncios:
                    if ann['link'] not in unique_links:
                        unique_links[ann['link']] = ann
                anuncios = list(unique_links.values())[:max_anuncios]
                
                logger.info(f"[Fallback] Encontrados {len(anuncios)} anuncios via requests")
        
        except Exception as e:
            logger.warning(f"[Fallback] Erro: {e}")
    
    if anuncios:
        logger.info(f"[Busca ML] Total final: {len(anuncios)} anuncios")
    else:
        logger.warning("[Busca ML] Nenhum anuncio encontrado")
    
    return anuncios

PEER_EXPORTS = ['_favoritos_taxa_padrao_por_tipo_anuncio', '_favoritos_frete_existente_anuncio', '_favoritos_anuncio_tem_frete_gratis', '_favoritos_chaves_frete_anuncio', '_favoritos_completar_frete_pausados_por_sku', '_normalize_text', '_normalizar_termo_busca_ml', '_termos_busca_ml', '_extrair_item_id', '_extrair_catalog_id', '_buscar_anuncios_mercadolivre_html', '_extrair_preloaded_results', '_extrair_links_ld_json', '_simplificar_termo_busca', '_deduplicar_anuncios', '_enriquecer_vendas_com_api', '_normalizar_anuncio_favoritos', '_buscar_lista_resultados', '_extrair_anuncio_produto', '_extrair_dados_produto_html', '_extrair_atributos_produto', '_extrair_preloaded_state', '_buscar_lista_atributos', '_extrair_codigo_produto', '_extrair_veiculo_produto', '_extrair_anos_produto', '_extrair_codigo_oem', '_gerar_termo_tecnico', '_buscar_ofertas_catalogo', '_coletar_permalinks', '_montar_urls_busca_diretas', '_buscar_anuncios_mercadolivre_automatico']
__all__ = PEER_EXPORTS + ["configure_favoritos_busca_runtime"]

configure_favoritos_busca_runtime()
