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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
from backend.services.favoritos_margem import margem_calcular_anuncio
from backend.services.runtime_bridge import bind_runtime_globals


def configure_favoritos_ml_runtime(runtime_module=None, peers=None):
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


configure_favoritos_ml_runtime()


def _ml_favoritos_buscar_itens_por_sku(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    sku_norm: str,
) -> tuple[list[dict], dict]:
    """Busca até 2 páginas do endpoint de itens do seller por seller_sku, com fallback por termo."""
    sku_norm = _normalizar_sku_match_favoritos(sku_norm)
    if not sku_norm:
        return [], cfg

    cfg_local = dict(cfg or {})
    user_id = str(cfg_local.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="ID do usuário do Mercado Livre não encontrado para esta loja.")

    url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
    itens_por_id: dict[str, dict] = {}
    encontrou_por_seller_sku = False

    def _coletar_ids(resp_json: object, apenas_desta_chamada: bool = False) -> list[str]:
        ids = []
        if not isinstance(resp_json, dict):
            return ids
        for item in resp_json.get("results") or []:
            if isinstance(item, dict):
                item_id = str(item.get("id") or "").strip()
            else:
                item_id = str(item or "").strip()
            if not item_id:
                continue
            if apenas_desta_chamada and item_id in itens_por_id:
                continue
            itens_por_id[item_id] = item if isinstance(item, dict) else {"id": item_id}
            ids.append(item_id)
        return ids

    # 1) Busca direcionada pelo seller_sku (mais precisa)
    for sku_busca in _ml_favoritos_variantes_busca_sku(sku_norm):
        encontrou_nesta_variante = False
        for status_item in ML_FAVORITOS_STATUS_SKUS:
            try:
                resp, cfg_local = _ml_favoritos_api_request(
                    client_id,
                    nome_loja,
                    cfg_local,
                    "GET",
                    url,
                    params={"offset": 0, "limit": 100, "status": status_item, "seller_sku": sku_busca},
                    timeout=15,
                )
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("[Favoritos] Falha na busca seller_sku=%s loja=%s: %s", sku_busca, nome_loja, exc)
                continue

            if resp.status_code == 200:
                ids_chamada = _coletar_ids(resp.json())
                if ids_chamada:
                    encontrou_por_seller_sku = True
                    encontrou_nesta_variante = True
        if encontrou_nesta_variante:
            break

    # 2) Fallback: varre parte do catálogo do usuário e filtra por SKU quando não houver match exato.
    if not itens_por_id:
        for status_item in ML_FAVORITOS_STATUS_SKUS:
            for scan_offset in range(0, 600, 100):
                for sku_busca in _ml_favoritos_variantes_busca_sku(sku_norm):
                    try:
                        resp_scan, cfg_local = _ml_favoritos_api_request(
                            client_id,
                            nome_loja,
                            cfg_local,
                            "GET",
                            url,
                            params={"offset": scan_offset, "limit": 100, "status": status_item, "q": sku_busca},
                            timeout=15,
                        )
                    except Exception:
                        continue
                    if resp_scan.status_code != 200:
                        continue

                    data = resp_scan.json()
                    _coletar_ids(data, apenas_desta_chamada=True)
                    if not isinstance(data, dict) or "paging" not in data:
                        continue
                    paging = data.get("paging") or {}
                    total = int(paging.get("total", 0) or 0)
                    if total and scan_offset + 100 >= total:
                        break

    ids = list(itens_por_id.keys())
    if not ids:
        return [], cfg_local

    itens_detalhados, cfg_local = _ml_favoritos_buscar_itens_batch(client_id, nome_loja, cfg_local, ids)

    # Filtra por correspondência de SKU para reduzir falsos positivos em busca por termo.
    out = []
    for item in itens_detalhados:
        if not isinstance(item, dict):
            continue
        if _ml_favoritos_item_corresponde_sku(item, sku_norm):
            out.append(item)

    if not out and encontrou_por_seller_sku:
        # A busca direta por seller_sku e confiavel mesmo quando o detalhe do item
        # nao devolve o atributo SKU da variacao.
        return itens_detalhados, cfg_local
    return out, cfg_local


def _ml_favoritos_buscar_primeiros_itens_por_skus(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    skus: set[str],
    max_por_sku: int = 3,
) -> tuple[dict[str, list[dict]], dict]:
    """Busca poucos itens por SKU para enriquecer descrições em lote."""
    skus_limpos = [str(sku or "").strip() for sku in (skus or []) if str(sku or "").strip()]
    skus_limpos = list(dict.fromkeys(skus_limpos))
    if not skus_limpos:
        return {}, cfg

    resultado: dict[str, list[dict]] = {}

    def _buscar(sku_norm: str):
        itens, novo_cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg_local_cache, sku_norm)
        limite = max(1, int(max_por_sku))
        return sku_norm, itens[:limite], novo_cfg

    cfg_local_cache = dict(cfg or {})
    if len(skus_limpos) == 1:
        sku_norm = _normalizar_sku_match_favoritos(skus_limpos[0])
        itens, cfg_local_cache = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg_local_cache, sku_norm)
        if itens:
            resultado[sku_norm] = itens[:max(1, int(max_por_sku))]
        return resultado, cfg_local_cache

    max_workers = min(len(skus_limpos), 4)
    if max_workers <= 1:
        for sku in skus_limpos:
            sku_norm = _normalizar_sku_match_favoritos(sku)
            itens, cfg_local_cache = _ml_favoritos_buscar_itens_por_sku(
                client_id,
                nome_loja,
                cfg_local_cache,
                sku_norm,
            )
            if itens:
                resultado[sku_norm] = itens[: max(1, int(max_por_sku))]
        return resultado, cfg_local_cache

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuras = {
            executor.submit(
                _ml_favoritos_buscar_itens_por_sku,
                client_id,
                nome_loja,
                dict(cfg_local_cache),
                _normalizar_sku_match_favoritos(sku),
            ): _normalizar_sku_match_favoritos(sku)
            for sku in skus_limpos
        }
        for futura in as_completed(futuras):
            sku_norm = futuras[futura]
            try:
                itens, cfg_novo = futura.result()
            except Exception as exc:
                logger.warning("[Favoritos] Falha ao buscar itens do SKU %s: %s", sku_norm, exc)
                continue
            if cfg_novo:
                cfg_local_cache = cfg_novo
            if itens:
                resultado[sku_norm] = itens[: max(1, int(max_por_sku))]

    return resultado, cfg_local_cache


def _ml_favoritos_listar_itens_ativos_loja(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    limite: int = 3000,
) -> tuple[list[dict], dict]:
    cfg_local = dict(cfg or {})
    user_id = str(cfg_local.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="ID do usuario do Mercado Livre nao encontrado para esta loja.")

    cache_key = f"favoritos:v5:ml_itens_ativos_pausados:{client_id}:{nome_loja}:{int(limite or 0)}"
    cached = _ml_cache_get(cache_key, ttl=900)
    if cached is not None:
        return cached, cfg_local

    url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
    ids: list[str] = []
    vistos: set[str] = set()
    offset = 0
    page_limit = 100
    # O endpoint de busca por usuário do ML rejeita offset/limit acima da janela 1000.
    # Para os SKUs que ficarem fora da varredura, Favoritos usa busca direta por seller_sku.
    limite = max(100, min(int(limite or 3000), 2000))

    while len(ids) < limite:
        if offset >= 1000:
            break
        limite_chamada = min(page_limit, limite - len(ids), 1000 - offset)
        if limite_chamada <= 0:
            break
        resp, cfg_local = _ml_favoritos_api_request(
            client_id,
            nome_loja,
            cfg_local,
            "GET",
            url,
            params={"offset": offset, "limit": limite_chamada, "status": "active"},
            timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao listar anuncios ativos do Mercado Livre"))

        data = resp.json() or {}
        batch = data.get("results") or []
        if not batch:
            break
        for item_id_raw in batch:
            item_id = str(item_id_raw or "").strip()
            if item_id and item_id not in vistos:
                vistos.add(item_id)
                ids.append(item_id)
                if len(ids) >= limite:
                    break

        paging = data.get("paging") or {}
        total = int(paging.get("total", len(ids)) or len(ids))
        offset += limite_chamada
        if offset >= total:
            break

    if len(ids) < limite:
        offset = 0
        while len(ids) < limite:
            if offset >= 1000:
                break
            limite_chamada = min(page_limit, limite - len(ids), 1000 - offset)
            if limite_chamada <= 0:
                break
            resp, cfg_local = _ml_favoritos_api_request(
                client_id,
                nome_loja,
                cfg_local,
                "GET",
                url,
                params={"offset": offset, "limit": limite_chamada, "status": "paused"},
                timeout=15,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao listar anuncios pausados do Mercado Livre"))

            data = resp.json() or {}
            batch = data.get("results") or []
            if not batch:
                break
            for item_id_raw in batch:
                item_id = str(item_id_raw or "").strip()
                if item_id and item_id not in vistos:
                    vistos.add(item_id)
                    ids.append(item_id)
                    if len(ids) >= limite:
                        break

            paging = data.get("paging") or {}
            total = int(paging.get("total", len(ids)) or len(ids))
            offset += limite_chamada
            if offset >= total:
                break

    itens, cfg_local = _ml_favoritos_buscar_itens_batch(client_id, nome_loja, cfg_local, ids)
    _ml_cache_set(cache_key, itens)
    return itens, cfg_local


def _ml_favoritos_listar_todos_itens_ativos_loja(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    limite: int = 10000,
) -> tuple[list[dict], dict]:
    """Lista os anúncios ativos da loja usando scan quando disponível para não parar na janela de 1000."""
    cfg_local = dict(cfg or {})
    user_id = str(cfg_local.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="ID do usuario do Mercado Livre nao encontrado para esta loja.")

    limite = max(100, min(int(limite or 10000), 20000))
    cache_key = f"favoritos:v5:ml_todos_itens_ativos_pausados:{client_id}:{nome_loja}:{limite}"
    cached = _ml_cache_get(cache_key, ttl=900)
    if cached is not None:
        return cached, cfg_local

    url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
    ids: list[str] = []
    vistos: set[str] = set()

    def _registrar_ids(batch) -> int:
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
            resp, cfg_local = _ml_favoritos_api_request(
                client_id,
                nome_loja,
                cfg_local,
                "GET",
                url,
                params=params,
                timeout=20,
            )
            if resp.status_code != 200:
                break
            data = resp.json() or {}
            batch = data.get("results") or []
            if not batch:
                scan_ok = True
                break
            _registrar_ids(batch)
            scan_ok = True
            scroll_id = str(data.get("scroll_id") or "").strip()
            if not scroll_id or len(ids) >= limite:
                break
    except Exception as exc:
        logger.warning("[Favoritos ML] Falha no scan de itens ativos da loja %s: %s", nome_loja, exc)

    if scan_ok and len(ids) < limite:
        scroll_id = ""
        try:
            params = {"search_type": "scan", "limit": 100, "status": "paused"}
            for _pagina in range(max(1, math.ceil((limite - len(ids)) / 100))):
                if scroll_id:
                    params = {"search_type": "scan", "scroll_id": scroll_id, "limit": 100}
                resp, cfg_local = _ml_favoritos_api_request(
                    client_id,
                    nome_loja,
                    cfg_local,
                    "GET",
                    url,
                    params=params,
                    timeout=20,
                )
                if resp.status_code != 200:
                    break
                data = resp.json() or {}
                batch = data.get("results") or []
                if not batch:
                    break
                _registrar_ids(batch)
                scroll_id = str(data.get("scroll_id") or "").strip()
                if not scroll_id or len(ids) >= limite:
                    break
        except Exception as exc:
            logger.warning("[Favoritos ML] Falha no scan de itens pausados da loja %s: %s", nome_loja, exc)

    if not scan_ok or not ids:
        ids = []
        vistos = set()
        page_limit = 100
        for status_item in ML_FAVORITOS_STATUS_SKUS:
            offset = 0
            while len(ids) < limite and offset < 1000:
                limite_chamada = min(page_limit, limite - len(ids), 1000 - offset)
                if limite_chamada <= 0:
                    break
                resp, cfg_local = _ml_favoritos_api_request(
                    client_id,
                    nome_loja,
                    cfg_local,
                    "GET",
                    url,
                    params={"offset": offset, "limit": limite_chamada, "status": status_item},
                    timeout=15,
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao listar anuncios ativos/pausados do Mercado Livre"))
                data = resp.json() or {}
                batch = data.get("results") or []
                if not batch:
                    break
                _registrar_ids(batch)
                paging = data.get("paging") or {}
                total = int(paging.get("total", len(ids)) or len(ids))
                offset += limite_chamada
                if offset >= total:
                    break

    itens, cfg_local = _ml_favoritos_buscar_itens_batch(client_id, nome_loja, cfg_local, ids)
    _ml_cache_set(cache_key, itens)
    return itens, cfg_local


def _favoritos_ml_dividir_skus(valor: str) -> list[str]:
    return [
        str(sku or "").strip()
        for sku in re.split(r"[,;|\n]+", str(valor or ""))
        if str(sku or "").strip()
    ]


def _favoritos_ml_imagem_item(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    imagem = str(item.get("secure_thumbnail") or item.get("thumbnail") or "").strip()
    if imagem:
        return imagem
    pictures = item.get("pictures") if isinstance(item.get("pictures"), list) else []
    for picture in pictures:
        if not isinstance(picture, dict):
            continue
        imagem = str(picture.get("secure_url") or picture.get("url") or picture.get("thumbnail") or "").strip()
        if imagem:
            return imagem
    return ""


def _favoritos_ml_url_item_id(item_id: str) -> str:
    item_id_txt = re.sub(r"[^A-Za-z0-9]", "", str(item_id or "").strip()).upper()
    digitos = item_id_txt[3:] if item_id_txt.startswith("MLB") else ""
    if not item_id_txt.startswith("MLB") or len(digitos) < 8:
        return ""
    return f"https://produto.mercadolivre.com.br/{item_id_txt.replace('MLB', 'MLB-')}-_JM"


def _favoritos_ml_resumo_anuncio_sku(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    item_id = str(item.get("id") or "").strip()
    permalink = str(item.get("permalink") or "").strip()
    if not permalink and item_id:
        permalink = _favoritos_ml_url_item_id(item_id)
    imagem = _favoritos_ml_imagem_item(item)
    return {
        "id": item_id,
        "mlb": item_id,
        "titulo": str(item.get("title") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "url": permalink,
        "permalink": permalink,
        "link": permalink,
        "imagem": imagem,
        "thumbnail": imagem,
        "status": str(item.get("status") or "").strip(),
    }


def _favoritos_ml_skus_unicos_itens(itens: list[dict]) -> list[dict]:
    mapa: dict[str, dict] = {}
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        titulo = str(item.get("title") or "").strip()
        permalink = str(item.get("permalink") or "").strip()
        imagem_item = _favoritos_ml_imagem_item(item)
        resumo_anuncio = _favoritos_ml_resumo_anuncio_sku(item)
        status_item = str(item.get("status") or "").strip()
        try:
            estoque_item = int(float(str(item.get("available_quantity") or 0).replace(",", ".")))
        except Exception:
            estoque_item = 0
        skus_item = _favoritos_ml_dividir_skus(_ml_extrair_sku(item))
        for sku_extra in _ml_favoritos_extrair_skus_item(item):
            sku_extra_txt = str(sku_extra or "").strip()
            if sku_extra_txt and not any(sku_extra_txt.lower() == sku_existente.lower() for sku_existente in skus_item):
                skus_item.append(sku_extra_txt)
        for sku in skus_item:
            chave = _normalizar_sku_compacto_favoritos(sku) or sku.lower()
            if not chave:
                continue
            atual = mapa.get(chave)
            if not atual:
                atual = {
                    "sku": sku,
                    "titulo": titulo,
                    "produto": titulo,
                    "loja": "",
                    "saldo_loja": 0,
                    "estoque_loja": 0,
                    "item_ids": [],
                    "links": [],
                    "url": permalink,
                    "permalink": permalink,
                    "link": permalink,
                    "imagem": imagem_item,
                    "thumbnail": imagem_item,
                    "anuncios": [],
                    "total_anuncios": 0,
                    "status_anuncio": status_item,
                    "status_anuncios": [],
                    "fonte": "mercadolivre_api",
                }
                mapa[chave] = atual
            if item_id and item_id not in atual["item_ids"]:
                atual["item_ids"].append(item_id)
                atual["total_anuncios"] = len(atual["item_ids"])
                atual["saldo_loja"] = int(atual.get("saldo_loja") or 0) + estoque_item
                atual["estoque_loja"] = atual["saldo_loja"]
                if resumo_anuncio and len(atual.get("anuncios") or []) < 12:
                    atual["anuncios"].append(resumo_anuncio)
            if permalink and permalink not in atual["links"]:
                atual["links"].append(permalink)
            if permalink and not atual.get("url"):
                atual["url"] = permalink
                atual["permalink"] = permalink
                atual["link"] = permalink
            if imagem_item and not atual.get("imagem"):
                atual["imagem"] = imagem_item
                atual["thumbnail"] = imagem_item
            if status_item and status_item not in atual["status_anuncios"]:
                atual["status_anuncios"].append(status_item)
                if not atual.get("status_anuncio") or atual.get("status_anuncio") != "active":
                    atual["status_anuncio"] = status_item
            if not atual.get("titulo") and titulo:
                atual["titulo"] = titulo
                atual["produto"] = titulo

    def _numero_sku(sku: str):
        grupos = re.findall(r"\d+", str(sku or ""))
        if not grupos:
            return None
        try:
            return int("".join(grupos)[:15])
        except Exception:
            return None

    def _sort_key(item: dict):
        numero = _numero_sku(item.get("sku") or "")
        return (0 if numero is not None else 1, numero or 0, str(item.get("sku") or "").lower())

    return sorted(mapa.values(), key=_sort_key)


def _favoritos_ml_garantir_sku_busca(skus: list[dict], sku_busca: str, itens: list[dict]) -> list[dict]:
    sku_txt = str(sku_busca or "").strip()
    if not sku_txt or not itens:
        return skus
    chave_busca = _normalizar_sku_compacto_favoritos(sku_txt) or sku_txt.lower()
    sku_match_busca = _normalizar_sku_match_favoritos(sku_txt).lower()
    for item in skus or []:
        sku_item = str((item or {}).get("sku") or "").strip()
        if _normalizar_sku_match_favoritos(sku_item).lower() == sku_match_busca:
            return skus

    for item in skus or []:
        sku_item = str((item or {}).get("sku") or "").strip()
        if (_normalizar_sku_compacto_favoritos(sku_item) or sku_item.lower()) == chave_busca:
            item["sku_api_original"] = sku_item
            item["sku"] = sku_txt
            item["sku_forcado_busca"] = True
            return sorted(skus or [], key=lambda item: (
                0 if (_normalizar_sku_compacto_favoritos(item.get("sku") or "") or "").isdigit() else 1,
                int(_normalizar_sku_compacto_favoritos(item.get("sku") or "")[:15] or 0)
                if (_normalizar_sku_compacto_favoritos(item.get("sku") or "") or "").isdigit() else 0,
                str(item.get("sku") or "").lower(),
            ))

    item_ids = []
    titulo = ""
    permalink = ""
    imagem = ""
    anuncios = []
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id not in item_ids:
            item_ids.append(item_id)
            resumo = _favoritos_ml_resumo_anuncio_sku(item)
            if resumo and len(anuncios) < 12:
                anuncios.append(resumo)
        if not titulo:
            titulo = str(item.get("title") or "").strip()
        if not permalink:
            permalink = str(item.get("permalink") or "").strip() or _favoritos_ml_url_item_id(item_id)
        if not imagem:
            imagem = _favoritos_ml_imagem_item(item)

    if not item_ids:
        return skus

    extra = {
        "sku": sku_txt,
        "titulo": titulo,
        "item_ids": item_ids,
        "links": [permalink] if permalink else [],
        "url": permalink,
        "permalink": permalink,
        "link": permalink,
        "imagem": imagem,
        "thumbnail": imagem,
        "anuncios": anuncios,
        "total_anuncios": len(item_ids),
        "sku_forcado_busca": True,
    }
    return sorted([extra, *(skus or [])], key=lambda item: (
        0 if (_normalizar_sku_compacto_favoritos(item.get("sku") or "") or "").isdigit() else 1,
        int(_normalizar_sku_compacto_favoritos(item.get("sku") or "")[:15] or 0)
        if (_normalizar_sku_compacto_favoritos(item.get("sku") or "") or "").isdigit() else 0,
        str(item.get("sku") or "").lower(),
    ))


def _ml_favoritos_mapear_itens_ativos_por_skus(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    skus: set[str],
    max_por_sku: int = 3,
) -> tuple[dict[str, list[dict]], dict]:
    alvos = {_normalizar_sku_match_favoritos(sku) for sku in (skus or []) if str(sku or "").strip()}
    alvos = {sku for sku in alvos if sku}
    if not alvos:
        return {}, cfg

    itens, cfg_local = _ml_favoritos_listar_itens_ativos_loja(client_id, nome_loja, cfg)
    resultado: dict[str, list[dict]] = {sku: [] for sku in alvos}
    limite = max(1, int(max_por_sku or 3))

    def _adicionar(sku_norm: str, item: dict):
        lista = resultado.setdefault(sku_norm, [])
        item_id = str((item or {}).get("id") or "").strip()
        if not item_id or any(str(x.get("id") or "").strip() == item_id for x in lista):
            return
        if len(lista) < limite:
            lista.append(item)

    alvos_compactos = {_normalizar_sku_compacto_favoritos(sku): sku for sku in alvos}
    for item in itens:
        if not isinstance(item, dict):
            continue
        for sku_raw in _ml_favoritos_extrair_skus_item(item):
            sku_norm = _normalizar_sku_match_favoritos(sku_raw)
            if sku_norm in alvos:
                _adicionar(sku_norm, item)
                continue
            sku_comp = _normalizar_sku_compacto_favoritos(sku_norm)
            sku_alvo = alvos_compactos.get(sku_comp)
            if sku_alvo:
                _adicionar(sku_alvo, item)

    pendentes = [sku for sku in alvos if not resultado.get(sku)]
    if pendentes:
        for item in itens:
            if not isinstance(item, dict):
                continue
            for sku_norm in list(pendentes):
                if _ml_favoritos_item_corresponde_sku(item, sku_norm):
                    _adicionar(sku_norm, item)
                    if resultado.get(sku_norm):
                        pendentes.remove(sku_norm)
                if not pendentes:
                    break
            if not pendentes:
                break

    return {sku: lista for sku, lista in resultado.items() if lista}, cfg_local


def _ml_favoritos_extrair_texto_descricao(payload: object) -> str:
    def _normalizar_texto(valor: str) -> str:
        if not valor:
            return ""
        txt = str(valor).strip().replace("\\\\n", " ").replace("\\n", " ")
        txt = html_lib.unescape(txt)
        if "<" in txt and ">" in txt:
            try:
                soup = BeautifulSoup(txt, "lxml")
                txt = soup.get_text(" ", strip=True)
            except Exception:
                pass
        return re.sub(r"\s+", " ", txt).strip()

    if payload is None:
        return ""
    if isinstance(payload, str):
        return _normalizar_texto(payload)
    if not isinstance(payload, dict):
        return ""

    # Formato padrão da API de descrição do ML.
    for campo in ("plain_text", "text", "content"):
        valor = payload.get(campo)
        if isinstance(valor, str):
            texto = _normalizar_texto(valor)
            if texto:
                return texto

    # Alguns retornos podem vir aninhados em `description`.
    descricao = payload.get("description")
    if isinstance(descricao, str):
        texto = _normalizar_texto(descricao)
        if texto:
            return texto
    if isinstance(descricao, dict):
        for campo in ("plain_text", "text", "content"):
            subvalor = descricao.get(campo)
            if isinstance(subvalor, str):
                texto = _normalizar_texto(subvalor)
                if texto:
                    return texto

    # Algumas estruturas retornam lista dentro desses campos.
    for campo in ("text", "plain_text", "content"):
        valor = payload.get(campo)
        if isinstance(valor, list):
            for item in valor:
                if not isinstance(item, dict):
                    continue
                for subcampo in ("plain_text", "text", "content"):
                    subvalor = item.get(subcampo)
                    if isinstance(subvalor, str):
                        texto = _normalizar_texto(subvalor)
                        if texto:
                            return texto
        elif isinstance(valor, str):
            texto = _normalizar_texto(valor)
            if texto:
                return texto

    # Fallback para texto serializado (último recurso).
    if isinstance(payload.get("text"), dict):
        texto = _normalizar_texto(json.dumps(payload.get("text")))
        if texto:
            return texto
    return ""


def _ml_favoritos_montar_descricao_por_item(item_payload: dict) -> str:
    item = item_payload if isinstance(item_payload, dict) else {}
    if not item:
        return ""

    partes: list[str] = []
    titulo = str(item.get("title") or item.get("name") or "").strip()
    if titulo:
        partes.append(f"Produto: {titulo}.")

    vistos: set[str] = set()

    def _valor_attr(attr: dict) -> str:
        if not isinstance(attr, dict):
            return ""
        valor = attr.get("value_name")
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
        valores = attr.get("values")
        if isinstance(valores, list):
            saida = []
            for v in valores:
                if isinstance(v, dict):
                    txt = str(v.get("name") or v.get("value_name") or "").strip()
                    if txt and txt not in saida:
                        saida.append(txt)
            if saida:
                return ", ".join(saida[:4])
        valor_id = attr.get("value_id")
        return str(valor_id or "").strip()

    def _add_attr(attr: dict):
        nome = str((attr or {}).get("name") or (attr or {}).get("id") or "").strip()
        valor = _valor_attr(attr or {})
        if not nome or not valor:
            return
        chave = f"{_favoritos_normalizar_sem_acentos(nome).lower()}:{_favoritos_normalizar_sem_acentos(valor).lower()}"
        if chave in vistos:
            return
        vistos.add(chave)
        partes.append(f"{nome}: {valor}.")

    for attr in item.get("attributes") or []:
        _add_attr(attr)
        if len(partes) >= 18:
            break

    for variacao in item.get("variations") or []:
        if len(partes) >= 22:
            break
        if not isinstance(variacao, dict):
            continue
        for attr in (variacao.get("attribute_combinations") or []):
            _add_attr(attr)
            if len(partes) >= 22:
                break

    descricao = " ".join(partes)
    descricao = re.sub(r"\s+", " ", descricao).strip()
    return descricao[:3000]


def _ml_favoritos_obter_descricao_item(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    item_id: str,
    item_payload: Optional[dict] = None,
) -> dict[str, object]:
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"success": False, "descricao": "", "description_info": {}, "erro": "Item id vazio."}

    cfg_local = dict(cfg or {})
    info = {"success": False, "descricao": "", "description_info": {}, "erro": ""}
    item_url = f"https://api.mercadolibre.com/items/{item_id}"
    descricao_url = f"https://api.mercadolibre.com/items/{item_id}/description"
    item_payload_local = dict(item_payload or {}) if isinstance(item_payload, dict) else {}

    if not item_payload_local:
        try:
            resp_item, cfg_local = _ml_favoritos_api_request(
                client_id,
                nome_loja,
                cfg_local,
                "GET",
                item_url,
                timeout=12,
            )
        except HTTPException as exc:
            info["erro"] = str(exc.detail or exc)
            return info
        except Exception as exc:
            info["erro"] = f"Falha ao consultar anuncio: {exc}"
            return info

        if resp_item.status_code == 404:
            info["erro"] = "Anuncio nao encontrado."
            return info
        if resp_item.status_code != 200:
            info["erro"] = f"Erro ao consultar anuncio ({resp_item.status_code})."
            return info

        try:
            item_payload_local = resp_item.json() or {}
        except Exception:
            item_payload_local = {}

    descricao_info = {}
    descricao = ""
    try:
        resp_desc, cfg_local = _ml_favoritos_api_request(
            client_id,
            nome_loja,
            cfg_local,
            "GET",
            descricao_url,
            timeout=12,
        )
        if resp_desc.status_code == 200:
            descricao_info = resp_desc.json() or {}
            descricao = _ml_favoritos_extrair_texto_descricao(descricao_info)
        elif resp_desc.status_code == 404:
            if isinstance(item_payload_local, dict):
                bloco = item_payload_local.get("description")
                if isinstance(bloco, dict):
                    descricao_info = bloco
                    descricao = _ml_favoritos_extrair_texto_descricao(bloco)
                elif isinstance(bloco, str):
                    descricao = bloco.strip()
                    descricao_info = {"content": descricao}
        else:
            descricao = ""
    except Exception as exc:
        logger.warning("[Favoritos] Falha ao buscar descricao do item %s: %s", item_id, exc)

    if not descricao and isinstance(item_payload_local, dict):
        descricao_fallback = _ml_favoritos_montar_descricao_por_item(item_payload_local)
        if descricao_fallback:
            descricao = descricao_fallback
            descricao_info = {"fonte": "item_payload", "fallback": True}

    descricao = (descricao or "").strip()
    if descricao:
        info["success"] = True
        info["descricao"] = descricao
        info["description_info"] = descricao_info
    else:
        info["erro"] = "Anuncio encontrado, mas sem descricao publicada e sem atributos suficientes no Mercado Livre."

    return info


def _ml_favoritos_obter_descricao_item_rapida(
    client_id: str,
    nome_loja: str,
    cfg: dict,
    item_id: str,
    item_payload: Optional[dict] = None,
) -> dict[str, object]:
    item_id = str(item_id or "").strip()
    if not item_id:
        return {"success": False, "descricao": "", "description_info": {}, "erro": "Item id vazio."}

    cfg_local = dict(cfg or {})
    descricao_url = f"https://api.mercadolibre.com/items/{item_id}/description"
    item_payload_local = dict(item_payload or {}) if isinstance(item_payload, dict) else {}
    try:
        resp_desc, cfg_local = _ml_favoritos_api_request(
            client_id,
            nome_loja,
            cfg_local,
            "GET",
            descricao_url,
            timeout=12,
        )
        if resp_desc.status_code == 200:
            descricao_info = resp_desc.json() or {}
            descricao = _ml_favoritos_extrair_texto_descricao(descricao_info)
            if descricao:
                return {"success": True, "descricao": descricao, "description_info": descricao_info, "erro": ""}
            if item_payload_local:
                descricao_fallback = _ml_favoritos_montar_descricao_por_item(item_payload_local)
                if descricao_fallback:
                    return {
                        "success": True,
                        "descricao": descricao_fallback,
                        "description_info": {"fonte": "item_payload", "fallback": True},
                        "erro": "",
                    }
            fallback = _ml_favoritos_obter_descricao_item(client_id, nome_loja, cfg_local, item_id, item_payload_local)
            if fallback.get("success"):
                return fallback
            return {"success": False, "descricao": "", "description_info": descricao_info, "erro": fallback.get("erro") or "Anuncio encontrado, mas sem descricao publicada na API do Mercado Livre."}
        if resp_desc.status_code == 404:
            return _ml_favoritos_obter_descricao_item(client_id, nome_loja, cfg_local, item_id, item_payload_local)
        return {"success": False, "descricao": "", "description_info": {}, "erro": f"Erro ao consultar descricao ({resp_desc.status_code})."}
    except Exception as exc:
        if item_payload_local:
            descricao_fallback = _ml_favoritos_montar_descricao_por_item(item_payload_local)
            if descricao_fallback:
                return {
                    "success": True,
                    "descricao": descricao_fallback,
                    "description_info": {"fonte": "item_payload", "fallback": True, "description_error": str(exc)},
                    "erro": "",
                }
        return {"success": False, "descricao": "", "description_info": {}, "erro": str(exc)}


def _favoritos_ml_float_close(valor, esperado, tolerancia: float = 0.08) -> bool:
    valor_num = _parse_float_flex(valor)
    esperado_num = _parse_float_flex(esperado)
    if valor_num is None or esperado_num is None:
        return False
    return abs(float(valor_num) - float(esperado_num)) <= float(tolerancia)


def _favoritos_ml_preco_ideal_req(req: FavoritosEfetivarPromocaoRequest) -> Optional[float]:
    for valor in (req.preco_ideal, req.preco_promocional, req.preco_competitivo):
        preco = _parse_float_flex(valor)
        if preco is not None and preco > 0:
            return round(float(preco), 2)
    return None


def _favoritos_ml_calcular_preco_cheio_centavos(preco_ideal: Any, percentual: Any) -> Optional[float]:
    ideal_num = _parse_float_flex(preco_ideal)
    percentual_num = _parse_float_flex(percentual)
    if ideal_num is None or ideal_num <= 0 or percentual_num is None or not (0 < percentual_num < 100):
        return None

    centavo = Decimal("0.01")
    ideal = Decimal(str(ideal_num)).quantize(centavo, rounding=ROUND_HALF_UP)
    fator = (Decimal("100") - Decimal(str(percentual_num))) / Decimal("100")
    bruto_centavos = (ideal / fator) * Decimal("100")
    centro = int(bruto_centavos.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    offsets = sorted(range(-12, 13), key=lambda valor: (abs(valor), valor))
    for offset in offsets:
        centavos = centro + offset
        if centavos <= 0:
            continue
        candidato = (Decimal(centavos) / Decimal("100")).quantize(centavo)
        final_calculado = (candidato * fator).quantize(centavo, rounding=ROUND_HALF_UP)
        if final_calculado == ideal:
            return float(candidato)

    return float((Decimal(centro) / Decimal("100")).quantize(centavo, rounding=ROUND_HALF_UP))


def _favoritos_ml_preco_minimo_margem_simulado(req: FavoritosEfetivarPromocaoRequest) -> Optional[float]:
    sim = req.simulacao if isinstance(req.simulacao, dict) else {}
    for campo in (
        "precoMinimoMargem",
        "preco_minimo_margem",
        "minimum_margin_price",
        "preco_minimo_15",
    ):
        valor = _parse_float_flex(sim.get(campo))
        if valor is not None and valor > 0:
            return math.ceil(float(valor) * 100.0) / 100.0
    return None


def _favoritos_ml_preco_final_verificacao(verificacao: Optional[dict]) -> Optional[float]:
    if not isinstance(verificacao, dict):
        return None
    price_info = verificacao.get("price_info") if isinstance(verificacao.get("price_info"), dict) else {}
    for valor in (
        verificacao.get("promotion_price_raw"),
        price_info.get("price"),
        price_info.get("sale_price"),
        price_info.get("promotional_price"),
        verificacao.get("item_price"),
    ):
        preco = _parse_float_flex(valor)
        if preco is not None and preco > 0:
            return float(preco)
    return None


def _favoritos_ml_margem_estimada(req: FavoritosEfetivarPromocaoRequest, preco_venda: Optional[float]) -> Optional[float]:
    preco = _parse_float_flex(preco_venda)
    if preco is None or preco <= 0:
        return None
    sim = req.simulacao if isinstance(req.simulacao, dict) else {}
    custo = _parse_float_flex(sim.get("custo"))
    frete = _parse_float_flex(sim.get("frete"))
    tarifa_ref = _parse_float_flex(sim.get("tarifa"))
    imposto_ref = _parse_float_flex(sim.get("impostoValor") or sim.get("imposto_valor"))
    preco_ref = _parse_float_flex(
        sim.get("precoCompetitivo")
        or sim.get("precoPromocionalCalculado")
        or sim.get("precoPromocional")
        or req.preco_ideal
        or req.preco_promocional
        or req.preco_competitivo
    )
    if custo is None or preco_ref is None or preco_ref <= 0:
        return None
    frete = frete or 0.0
    taxa_tarifa = max(0.0, float(tarifa_ref or 0.0)) / float(preco_ref)
    taxa_imposto = max(0.0, float(imposto_ref or 0.0)) / float(preco_ref)
    liquido = float(preco) - float(custo) - float(frete) - (float(preco) * taxa_tarifa) - (float(preco) * taxa_imposto)
    return (liquido * 100.0) / float(preco)


def _favoritos_ml_preco_contingencia_sem_promocao(
    req: FavoritosEfetivarPromocaoRequest,
    desconto_maximo: float = 1.5,
) -> Optional[float]:
    preco_final_previsto = _favoritos_ml_preco_ideal_req(req)
    if preco_final_previsto is not None and preco_final_previsto > 0:
        margem_prevista = _favoritos_ml_margem_estimada(req, preco_final_previsto)
        if margem_prevista is None or margem_prevista >= 15.0:
            return round(math.ceil(float(preco_final_previsto) * 100.0) / 100.0, 2)

    preco_ranking = _favoritos_ml_preco_ranking_simulado(req)
    if preco_ranking is None or preco_ranking <= 0:
        return None
    preco_teto = math.floor((float(preco_ranking) - 0.01) * 100.0) / 100.0
    if preco_teto <= 0:
        return None
    preco_piso_competitivo = math.ceil(max(0.01, float(preco_ranking) - float(desconto_maximo)) * 100.0) / 100.0
    preco_minimo_margem = _favoritos_ml_preco_minimo_margem_simulado(req)
    preco_contingencia = max(preco_piso_competitivo, preco_minimo_margem or 0.01)
    preco_contingencia = math.ceil(preco_contingencia * 100.0) / 100.0
    if preco_contingencia > preco_teto:
        if preco_minimo_margem is not None and preco_minimo_margem > 0:
            return round(float(preco_minimo_margem), 2)
        return None
    return round(float(preco_contingencia), 2)


def _favoritos_ml_verificacao_exige_contingencia_por_margem(
    req: FavoritosEfetivarPromocaoRequest,
    verificacao: Optional[dict],
) -> tuple[bool, str, dict]:
    preco_final = _favoritos_ml_preco_final_verificacao(verificacao)
    preco_simulado = _favoritos_ml_preco_ideal_req(req)
    preco_minimo_margem = _favoritos_ml_preco_minimo_margem_simulado(req)
    margem = _favoritos_ml_margem_estimada(req, preco_final)
    preco_proximo = bool(
        (isinstance(verificacao, dict) and verificacao.get("preco_promocional_ok"))
        or _favoritos_ml_float_close(preco_final, preco_simulado)
    )
    margem_baixa = bool(margem is not None and margem < 15.0)
    abaixo_minimo = bool(
        preco_final is not None
        and preco_minimo_margem is not None
        and float(preco_final) + 0.005 < float(preco_minimo_margem)
    )
    detalhes = {
        "preco_final_verificado": round(float(preco_final), 2) if preco_final is not None else None,
        "preco_promocional_simulado": round(float(preco_simulado), 2) if preco_simulado is not None else None,
        "preco_minimo_margem": round(float(preco_minimo_margem), 2) if preco_minimo_margem is not None else None,
        "margem_estimada": round(float(margem), 2) if margem is not None else None,
        "preco_proximo_simulado": preco_proximo,
    }
    if margem_baixa or abaixo_minimo:
        motivo = (
            "Preco final aplicado ficou com margem abaixo de 15% "
            f"(preco final {detalhes['preco_final_verificado']}, "
            f"margem estimada {detalhes['margem_estimada']}, "
            f"minimo seguro {detalhes['preco_minimo_margem']})."
        )
        return True, motivo, detalhes
    if not preco_proximo and (margem_baixa or abaixo_minimo):
        motivo = (
            "Preco final aplicado nao ficou proximo do simulado e entrou em faixa de margem insegura. "
            f"Detalhes: {detalhes}"
        )
        return True, motivo, detalhes
    return False, "", detalhes


def _favoritos_ml_falha_por_percentual_promocao(
    erro_texto: str = "",
    verificacao: Optional[dict] = None,
) -> bool:
    erro_norm = normalizar_texto(erro_texto or "")
    gatilhos = (
        "error_credibility_discounted_price",
        "error_credibility_price",
        "final_price_lower_than_zero",
        "discounted price is not credible",
        "new deal_price must be lower than current deal_price",
        "new top_deal_price must be lower than current top_deal_price",
        "something went wrong",
    )
    if any(g in erro_norm for g in gatilhos):
        return True

    if isinstance(verificacao, dict):
        if verificacao.get("preco_anuncio_ok") and (
            not verificacao.get("promocao_ok")
            or not verificacao.get("promocao_por_preco_ok")
            or not verificacao.get("preco_promocional_ok")
            or not verificacao.get("desconto_ok")
        ):
            return True

        info_preco = verificacao.get("price_info") if isinstance(verificacao.get("price_info"), dict) else {}
        desconto_info = _parse_float_flex((info_preco or {}).get("discount_pct") or verificacao.get("desconto_info"))
        desconto_esperado = _parse_float_flex(verificacao.get("desconto_esperado"))
        if desconto_info is not None and desconto_esperado is not None:
            if abs(float(desconto_info) - float(desconto_esperado)) > 0.25:
                return True
    return False


def _favoritos_ml_aplicar_contingencia_sem_promocao(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    req: FavoritosEfetivarPromocaoRequest,
    motivo: str,
) -> tuple[dict, dict]:
    preco_ranking = _favoritos_ml_preco_ranking_simulado(req)
    preco_contingencia = _favoritos_ml_preco_contingencia_sem_promocao(req)
    preco_teto_competitivo = None
    if preco_ranking is not None and preco_ranking > 0:
        preco_teto_competitivo = round(math.floor((float(preco_ranking) - 0.01) * 100.0) / 100.0, 2)
    if preco_contingencia is None:
        preco_ranking_txt = round(float(preco_ranking), 2) if preco_ranking is not None else None
        preco_minimo_txt = _favoritos_ml_preco_minimo_margem_simulado(req)
        preco_piso_txt = None
        preco_teto_txt = None
        if preco_ranking is not None and preco_ranking > 0:
            preco_piso_txt = round(math.ceil(max(0.01, float(preco_ranking) - 1.5) * 100.0) / 100.0, 2)
            preco_teto_txt = round(math.floor((float(preco_ranking) - 0.01) * 100.0) / 100.0, 2)
        raise HTTPException(
            status_code=409,
            detail=(
                "A campanha foi recusada pelo Mercado Livre ou o preco final ficou abaixo da margem minima, "
                "mas nao existe preco de contingencia seguro: o anuncio precisa ficar abaixo do concorrente "
                "com diferenca maxima de R$ 1,50 e margem minima de 15%. "
                f"Preco concorrente: {preco_ranking_txt}. Faixa aceita sem campanha: {preco_piso_txt} a {preco_teto_txt}. "
                f"Preco minimo para margem: {preco_minimo_txt}. Motivo original: "
                f"{motivo}"
            ),
        )
    margem_contingencia = _favoritos_ml_margem_estimada(req, preco_contingencia)
    if margem_contingencia is not None and margem_contingencia < 15.0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Contingencia sem promocao bloqueada: o preco calculado ficaria com margem abaixo de 15%. "
                f"Preco calculado: {preco_contingencia}. Margem estimada: {round(float(margem_contingencia), 2)}%. "
                f"Motivo original: {motivo}"
            ),
        )

    removidas, cfg = _favoritos_ml_remover_promocoes_atuais(client_id, loja, cfg, item_id, req)
    estado_pos_clear, cfg = _favoritos_ml_obter_estado_item(
        client_id,
        loja,
        cfg,
        item_id,
        preco_contingencia,
    )
    preflight = estado_pos_clear.get("price_preflight") or {}
    if estado_pos_clear.get("mutation_blocked") or not preflight.get("ok", True):
        raise HTTPException(
            status_code=409,
            detail=(
                estado_pos_clear.get("block_reason")
                or preflight.get("message")
                or "O preco ideal direto ficou bloqueado depois da remocao da promocao."
            ),
        )

    preco_update, cfg = _favoritos_ml_atualizar_preco_item(client_id, loja, cfg, item_id, preco_contingencia)
    preco_confirmacao, cfg = _favoritos_ml_aguardar_preco_anuncio(
        client_id,
        loja,
        cfg,
        item_id,
        float(preco_contingencia),
        tentativas=8,
    )
    if not preco_confirmacao.get("success"):
        raise HTTPException(
            status_code=409,
            detail=(
                "A campanha foi recusada e o fallback sem campanha tambem nao confirmou o preco final. "
                f"Motivo original: {motivo} | Conferencia fallback: {preco_confirmacao}"
            ),
        )

    confirmacao_sem_promocao_final, cfg = _favoritos_ml_confirmar_sem_promocoes(
        client_id,
        loja,
        cfg,
        item_id,
        preco_direto=preco_contingencia,
    )
    removidas_reconciliacao = []
    preco_update_reconciliacao = None
    preco_confirmacao_reconciliacao = None
    promocao_reapareceu = bool(
        confirmacao_sem_promocao_final.get("active_promotions")
        or confirmacao_sem_promocao_final.get("sale_price_has_promotion")
    )
    if not confirmacao_sem_promocao_final.get("success") and promocao_reapareceu:
        try:
            removidas_reconciliacao, cfg = _favoritos_ml_remover_promocoes_atuais(
                client_id,
                loja,
                cfg,
                item_id,
                req,
            )
            removidas.extend(removidas_reconciliacao)
            preco_update_reconciliacao, cfg = _favoritos_ml_atualizar_preco_item(
                client_id,
                loja,
                cfg,
                item_id,
                preco_contingencia,
            )
            preco_confirmacao_reconciliacao, cfg = _favoritos_ml_aguardar_preco_anuncio(
                client_id,
                loja,
                cfg,
                item_id,
                float(preco_contingencia),
                tentativas=8,
            )
            if not preco_confirmacao_reconciliacao.get("success"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A promocao reapareceu, foi removida novamente, mas o preco ideal direto "
                        f"nao foi reconfirmado: {preco_confirmacao_reconciliacao}"
                    ),
                )
            confirmacao_sem_promocao_final, cfg = _favoritos_ml_confirmar_sem_promocoes(
                client_id,
                loja,
                cfg,
                item_id,
                preco_direto=preco_contingencia,
            )
        except Exception as exc:
            detalhe = getattr(exc, "detail", None) or str(exc or "falha desconhecida")
            raise HTTPException(
                status_code=getattr(exc, "status_code", 409) or 409,
                detail=(
                    "A promocao reapareceu depois do preco ideal e a reconciliacao final falhou: "
                    f"{detalhe}"
                ),
            ) from exc
    if not confirmacao_sem_promocao_final.get("success"):
        raise HTTPException(
            status_code=409,
            detail=(
                "O preco ideal direto foi confirmado, mas o fallback nao provou ausencia estavel de promocao "
                "nas consultas seller-promotions e sale_price: "
                f"{confirmacao_sem_promocao_final}"
            ),
        )

    return {
        "fallback_sem_promocao_aplicado": True,
        "fallback_motivo": motivo,
        "preco_ranking_referencia": round(float(preco_ranking), 2) if preco_ranking is not None else None,
        "preco_anuncio_contingencia": round(float(preco_contingencia), 2),
        "preco_teto_competitivo": preco_teto_competitivo,
        "fallback_sem_competir": bool(
            preco_teto_competitivo is not None
            and float(preco_contingencia) > float(preco_teto_competitivo) + 0.005
        ),
        "margem_estimada_contingencia": round(float(margem_contingencia), 2) if margem_contingencia is not None else None,
        "promocoes_removidas_fallback": removidas,
        "preco_update_fallback": preco_update,
        "preco_confirmacao_fallback": preco_confirmacao,
        "price_preflight_fallback": preflight,
        "promocao_clear_confirmada_fallback": confirmacao_sem_promocao_final,
        "promocoes_removidas_reconciliacao": removidas_reconciliacao,
        "preco_update_reconciliacao": preco_update_reconciliacao,
        "preco_confirmacao_reconciliacao": preco_confirmacao_reconciliacao,
    }, cfg


def _favoritos_ml_texto_promocao(entry: dict, chaves: tuple[str, ...]) -> str:
    texto = _ml_promocao_raw_texto(entry, chaves)
    if texto:
        return texto
    if not isinstance(entry, dict):
        return ""
    for _caminho, valor in _ml_iterar_campos_payload_limitado(entry, max_depth=5, max_nodes=900):
        chave = str(_caminho or "").rsplit(".", 1)[-1].strip().lower()
        if chave in {str(c or "").strip().lower() for c in chaves}:
            valor_txt = str(valor or "").strip()
            if valor_txt and valor_txt.lower() not in {"none", "null", "-"}:
                return valor_txt
    return ""


def _favoritos_ml_promocao_para_remocao(entry: dict, item_id: str) -> dict | None:
    if not isinstance(entry, dict):
        return None
    status = _ml_classificar_status_promocao_entry(entry)
    if status != "Ativo":
        return None

    promotion_type = (
        _favoritos_ml_texto_promocao(entry, ("promotion_type", "promotionType", "type", "campaign_type", "campaignType"))
        or "SELLER_CAMPAIGN"
    )
    promotion_id = _favoritos_ml_texto_promocao(
        entry,
        ("promotion_id", "promotionId", "campaign_id", "campaignId", "deal_id", "dealId"),
    )
    if not promotion_id:
        candidato_id = _favoritos_ml_texto_promocao(entry, ("id",))
        if candidato_id and candidato_id.upper() != str(item_id or "").upper():
            promotion_id = candidato_id
    offer_id = _favoritos_ml_texto_promocao(entry, ("offer_id", "offerId", "ref_id", "refId"))

    if not promotion_type and not promotion_id:
        return None
    return {
        "promotion_id": promotion_id,
        "promotion_type": promotion_type or "SELLER_CAMPAIGN",
        "offer_id": offer_id,
        "status": status or "Ativo",
    }


def _favoritos_ml_promocoes_remocao_fallback(req: FavoritosEfetivarPromocaoRequest, item_id: str) -> list[dict]:
    anuncio = req.anuncio if isinstance(req.anuncio, dict) else {}
    promotion_type = str(
        anuncio.get("promotion_type")
        or anuncio.get("promotionType")
        or req.promotion_type
        or "SELLER_CAMPAIGN"
    ).strip() or "SELLER_CAMPAIGN"
    candidatos = []
    for valor in [
        anuncio.get("promotion_id"),
        anuncio.get("promotionId"),
        anuncio.get("campaign_id"),
        anuncio.get("deal_id"),
    ]:
        texto = str(valor or "").strip()
        if texto:
            candidatos.append(texto)
    deal_ids = anuncio.get("deal_ids") or anuncio.get("dealIds") or []
    if isinstance(deal_ids, str):
        deal_ids = [deal_ids]
    if isinstance(deal_ids, (list, tuple, set)):
        for valor in deal_ids:
            texto = str(valor or "").strip()
            if texto:
                candidatos.append(texto)

    retorno = []
    vistos = set()
    for promotion_id in candidatos:
        chave = (promotion_id.lower(), promotion_type.lower())
        if chave in vistos:
            continue
        vistos.add(chave)
        retorno.append({
            "promotion_id": promotion_id,
            "promotion_type": promotion_type,
        "offer_id": str(
            anuncio.get("offer_id")
            or anuncio.get("offerId")
            or anuncio.get("ref_id")
            or anuncio.get("refId")
            or ""
        ).strip(),
            "status": "Ativo",
        })
    return retorno


def _favoritos_ml_remocao_max_attempts() -> int:
    try:
        tentativas = int(float(str(os.getenv("ML_PROMO_REMOVE_MAX_ATTEMPTS", "4") or "4").replace(",", ".")))
    except Exception:
        tentativas = 4
    return max(1, min(tentativas, 6))


def _favoritos_ml_textos_resposta_remocao(resp, body_resp: Any, erros_payload: Optional[list[str]] = None) -> list[str]:
    textos = list(erros_payload or [])
    if isinstance(body_resp, dict):
        for chave in ("message", "error", "detail", "warning"):
            valor = body_resp.get(chave)
            if valor not in (None, ""):
                textos.append(str(valor))
    if not textos:
        try:
            textos.append(resp.text or "")
        except Exception:
            pass
    return textos


def _favoritos_ml_remocao_erro_transitorio(resp, textos_resposta: list[str]) -> bool:
    status_code = getattr(resp, "status_code", None)
    if status_code in (408, 429, 500, 502, 503, 504):
        return True

    texto = " ".join(str(t or "") for t in (textos_resposta or []))
    texto_norm = normalizar_texto(texto).replace("_", " ")
    gatilhos = (
        "service temporarily overloaded",
        "temporarily overloaded",
        "please try again later",
        "temporarily unavailable",
        "temporary unavailable",
        "upstream request timeout",
        "gateway timeout",
        "read timed out",
    )
    return any(gatilho in texto_norm for gatilho in gatilhos)


def _favoritos_ml_remocao_retry_delay(resp, tentativa: int) -> float:
    padrao = min(16.0, 1.5 * (2 ** max(0, int(tentativa) - 1)))
    if resp is not None:
        padrao = _ml_retry_after_seconds(resp, padrao=padrao)
    return min(18.0, max(1.0, float(padrao)) + random.uniform(0.2, 1.0))


def _favoritos_ml_invalidar_cache_promocoes_item(client_id: str, loja: str, item_id: str) -> None:
    cache = globals().get("ML_ITEM_PROMOTIONS_CACHE")
    if not isinstance(cache, dict):
        return
    for versao in ("v2", "v3-strict"):
        cache.pop(f"{versao}:{client_id}:{loja}:{item_id}", None)


def _favoritos_ml_confirmar_sem_promocoes(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    *,
    preco_direto: Optional[float] = None,
    tentativas: int = 8,
    leituras_estaveis: int = 3,
) -> tuple[dict, dict]:
    preco_alvo = _parse_float_flex(preco_direto)
    estabilidade = 0
    assinatura_anterior = None
    ultimo = {
        "success": False,
        "attempt": 0,
        "active_promotions": [],
        "strict": True,
        "sale_price_observable": False,
        "sale_price_has_promotion": False,
        "sale_price_amount": None,
        "sale_price_regular_amount": None,
        "sale_price_promotion_id": None,
        "sale_price_campaign_id": None,
        "direct_price_ok": False if preco_alvo is not None else True,
        "stable_reads": 0,
        "required_stable_reads": max(2, int(leituras_estaveis)),
    }
    for tentativa in range(max(1, int(tentativas))):
        if tentativa:
            time.sleep(2.0)
        _favoritos_ml_invalidar_cache_promocoes_item(client_id, loja, item_id)
        promocoes_item, cfg = _ml_obter_promocoes_item(
            client_id,
            loja,
            cfg,
            item_id,
            strict=True,
        )
        ativas = []
        for raw in promocoes_item if isinstance(promocoes_item, list) else []:
            info = _favoritos_ml_promocao_para_remocao(raw, item_id)
            if info:
                ativas.append(info)

        sale_price = {}
        sale_price_observable = False
        try:
            resp_sale_price, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/items/{item_id}/sale_price",
                params={"context": "channel_marketplace"},
                timeout=15,
            )
            if resp_sale_price.status_code == 200:
                payload_sale_price = resp_sale_price.json() or {}
                if isinstance(payload_sale_price, dict):
                    sale_price = payload_sale_price
                    sale_price_observable = True
        except Exception as exc:
            logger.warning(
                "[Favoritos ML] Falha ao reconciliar sale_price sem promocao de %s: %s",
                item_id,
                exc,
            )

        metadata_sale_price = sale_price.get("metadata") if isinstance(sale_price.get("metadata"), dict) else {}
        sale_amount = _parse_float_flex(sale_price.get("amount"))
        regular_amount = _parse_float_flex(sale_price.get("regular_amount"))
        sale_promotion_id = str(metadata_sale_price.get("promotion_id") or "").strip()
        sale_campaign_id = str(metadata_sale_price.get("campaign_id") or "").strip()
        sale_promotion_type = str(metadata_sale_price.get("promotion_type") or "").strip()
        desconto_por_preco = bool(
            sale_amount is not None
            and regular_amount is not None
            and float(regular_amount) > float(sale_amount) + 0.005
        )
        sale_price_has_promotion = bool(
            sale_promotion_id
            or sale_campaign_id
            or sale_promotion_type
            or desconto_por_preco
        )
        direct_price_ok = bool(
            preco_alvo is None
            or (
                sale_amount is not None
                and _favoritos_ml_float_close(sale_amount, preco_alvo, tolerancia=0.009)
            )
        )
        leitura_limpa = bool(
            not ativas
            and sale_price_observable
            and not sale_price_has_promotion
            and direct_price_ok
        )
        assinatura = (
            round(float(sale_amount), 2) if sale_amount is not None else None,
            round(float(regular_amount), 2) if regular_amount is not None else None,
            sale_promotion_id.lower(),
            sale_campaign_id.lower(),
            sale_promotion_type.lower(),
        )
        if leitura_limpa:
            estabilidade = estabilidade + 1 if assinatura == assinatura_anterior else 1
            assinatura_anterior = assinatura
        else:
            estabilidade = 0
            assinatura_anterior = None
        ultimo = {
            "success": False,
            "attempt": tentativa + 1,
            "active_promotions": ativas,
            "strict": True,
            "sale_price_observable": sale_price_observable,
            "sale_price_has_promotion": sale_price_has_promotion,
            "sale_price_amount": round(float(sale_amount), 2) if sale_amount is not None else None,
            "sale_price_regular_amount": round(float(regular_amount), 2) if regular_amount is not None else None,
            "sale_price_promotion_id": sale_promotion_id or None,
            "sale_price_campaign_id": sale_campaign_id or None,
            "sale_price_promotion_type": sale_promotion_type or None,
            "direct_price_ok": direct_price_ok,
            "stable_reads": estabilidade,
            "required_stable_reads": max(2, int(leituras_estaveis)),
        }
        if estabilidade >= max(2, int(leituras_estaveis)):
            ultimo["success"] = True
            return ultimo, cfg
    return ultimo, cfg


def _favoritos_ml_remover_promocoes_atuais(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    req: FavoritosEfetivarPromocaoRequest,
) -> tuple[list[dict], dict]:
    _favoritos_ml_invalidar_cache_promocoes_item(client_id, loja, item_id)
    promocoes_item, cfg = _ml_obter_promocoes_item(
        client_id,
        loja,
        cfg,
        item_id,
        strict=True,
    )

    remover = []
    vistos = set()
    for raw in promocoes_item if isinstance(promocoes_item, list) else []:
        info = _favoritos_ml_promocao_para_remocao(raw, item_id)
        if not info:
            continue
        chave = (
            str(info.get("promotion_id") or "").lower(),
            str(info.get("promotion_type") or "").lower(),
            str(info.get("offer_id") or "").lower(),
        )
        if chave in vistos:
            continue
        vistos.add(chave)
        remover.append(info)

    resultados = []

    def _erro_remocao_com_resultados(status_code: int, detail: str) -> HTTPException:
        erro = HTTPException(status_code=status_code, detail=detail)
        erro.favoritos_remocoes = list(resultados)
        erro.favoritos_cfg = cfg
        return erro

    max_attempts = _favoritos_ml_remocao_max_attempts()
    for promo in remover:
        params = {"app_version": "v2"}
        if promo.get("promotion_type"):
            params["promotion_type"] = promo["promotion_type"]
        if promo.get("promotion_id"):
            params["promotion_id"] = promo["promotion_id"]
        if promo.get("offer_id"):
            params["offer_id"] = promo["offer_id"]

        for tentativa in range(1, max_attempts + 1):
            try:
                resp, cfg = _ml_api_request(
                    client_id,
                    loja,
                    cfg,
                    "DELETE",
                    f"https://api.mercadolibre.com/seller-promotions/items/{item_id}",
                    params=params,
                    timeout=20,
                )
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if tentativa < max_attempts:
                    espera = _favoritos_ml_remocao_retry_delay(None, tentativa)
                    logger.warning(
                        "[Favoritos ML] Falha transitoria ao sair da promocao atual de %s (%s/%s): %s. Nova tentativa em %.1fs.",
                        item_id,
                        tentativa,
                        max_attempts,
                        exc,
                        espera,
                    )
                    time.sleep(espera)
                    continue
                detalhe_timeout = f"Mercado Livre nao respondeu ao remover promocao atual: {exc}"
                resultados.append({
                    **promo,
                    "success": False,
                    "status_code": 503,
                    "detail": detalhe_timeout,
                    "response": {},
                    "attempts": tentativa,
                })
                raise _erro_remocao_com_resultados(
                    503,
                    f"Erro ao sair da promocao atual: {detalhe_timeout}",
                )
            except Exception as exc:
                status_code = getattr(exc, "status_code", 502) or 502
                detalhe_excecao = getattr(exc, "detail", None) or str(exc or "Falha inesperada na API do Mercado Livre")
                resultados.append({
                    **promo,
                    "success": False,
                    "status_code": status_code,
                    "detail": str(detalhe_excecao),
                    "response": {},
                    "attempts": tentativa,
                })
                raise _erro_remocao_com_resultados(
                    status_code,
                    f"Erro ao sair da promocao atual: {detalhe_excecao}",
                ) from exc

            body_resp = {}
            try:
                body_resp = resp.json() or {}
            except Exception:
                body_resp = {}
            erros_payload = []
            if isinstance(body_resp, dict):
                for erro in body_resp.get("errors") or []:
                    if isinstance(erro, dict):
                        erros_payload.append(str(erro.get("error") or erro.get("message") or erro))
                    else:
                        erros_payload.append(str(erro))
                for item_sucesso in body_resp.get("successful_ids") or []:
                    if isinstance(item_sucesso, dict) and item_sucesso.get("error"):
                        erros_payload.append(str(item_sucesso.get("error")))
            textos_resposta = _favoritos_ml_textos_resposta_remocao(resp, body_resp, erros_payload)
            textos_normalizados = [normalizar_texto(e).replace("_", " ") for e in textos_resposta]
            erro_sem_oferta = any("no offers found" in e for e in textos_normalizados)
            erro_tipo_promocao_invalido = any("invalid promotion type" in e for e in textos_normalizados)
            erro_promocao_invalida = any("invalid promotion id" in e for e in textos_normalizados)
            erro_recurso_inexistente = any("the resource you are trying to access does not exist" in e for e in textos_normalizados)
            ok_status = resp.status_code in (200, 202, 204, 404)
            if erro_sem_oferta or erro_tipo_promocao_invalido or erro_promocao_invalida or erro_recurso_inexistente:
                erros_payload = []
                ok_status = True
                if erro_sem_oferta:
                    aviso = "No offers found for item"
                elif erro_tipo_promocao_invalido:
                    aviso = "Invalid promotion type ignored while removing stale promotion reference"
                elif erro_promocao_invalida:
                    aviso = "Invalid promotion id ignored while removing stale promotion reference"
                else:
                    aviso = "Resource does not exist ignored while removing stale promotion reference"
                if not body_resp:
                    body_resp = {"warning": aviso}
                elif isinstance(body_resp, dict):
                    body_resp["warning"] = aviso
            ok = ok_status and not erros_payload
            detalhe = ""
            if not ok:
                detalhe = " | ".join([e for e in erros_payload if e][:5]) or _ml_parse_error_detail(resp, "Erro ao remover promocao atual")

            if not ok and tentativa < max_attempts and _favoritos_ml_remocao_erro_transitorio(resp, textos_resposta):
                espera = _favoritos_ml_remocao_retry_delay(resp, tentativa)
                logger.warning(
                    "[Favoritos ML] Mercado Livre falhou ao sair da promocao atual de %s (%s/%s, HTTP %s): %s. Nova tentativa em %.1fs.",
                    item_id,
                    tentativa,
                    max_attempts,
                    resp.status_code,
                    detalhe,
                    espera,
                )
                time.sleep(espera)
                continue

            resultados.append({
                **promo,
                "success": ok,
                "status_code": resp.status_code,
                "detail": detalhe,
                "response": body_resp,
                "attempts": tentativa,
            })
            if not ok:
                raise _erro_remocao_com_resultados(
                    resp.status_code,
                    f"Erro ao sair da promocao atual: {detalhe}",
                )
            break

    if resultados:
        time.sleep(1.0)
    confirmacao, cfg = _favoritos_ml_confirmar_sem_promocoes(
        client_id,
        loja,
        cfg,
        item_id,
    )
    if not confirmacao.get("success"):
        erro = _erro_remocao_com_resultados(
            409,
            (
                "Mercado Livre respondeu a remocao, mas a reconciliacao ainda encontrou promocao ativa "
                "ou sale_price promocional: "
                f"promocoes={confirmacao.get('active_promotions') or []}; "
                f"sale_price_has_promotion={bool(confirmacao.get('sale_price_has_promotion'))}; "
                f"sale_price_amount={confirmacao.get('sale_price_amount')}; "
                f"sale_price_regular_amount={confirmacao.get('sale_price_regular_amount')}"
            ),
        )
        erro.favoritos_clear_confirmation = confirmacao
        raise erro

    return resultados, cfg


def _favoritos_ml_listing_type_id(valor: Any) -> str:
    if isinstance(valor, dict):
        valor = valor.get("id") or valor.get("name") or valor.get("label") or valor.get("title") or ""
    texto_raw = str(valor or "").strip()
    if not texto_raw:
        return ""
    texto = normalizar_texto(texto_raw).replace("_", " ").replace("-", " ")
    compacto = re.sub(r"[^a-z0-9]+", "", texto)
    if "gold pro" in texto or "goldpro" in compacto or "premium" in texto or texto == "pro":
        return "gold_pro"
    if (
        "gold special" in texto
        or "goldspecial" in compacto
        or "classico" in texto
        or "classic" in texto
        or texto == "gold"
    ):
        return "gold_special"
    if texto == "free" or "gratis" in texto or "gratuito" in texto:
        return "free"
    return ""


def _favoritos_ml_nome_listing_type(listing_type_id: str) -> str:
    listing_type = _favoritos_ml_listing_type_id(listing_type_id)
    if listing_type == "gold_pro":
        return "Premium"
    if listing_type == "gold_special":
        return "Classico"
    if listing_type == "free":
        return "Gratis"
    return str(listing_type_id or "").strip()


def _favoritos_ml_tags_item(item_data: Any) -> list[str]:
    item = item_data if isinstance(item_data, dict) else {}
    tags_raw = item.get("tags")
    if isinstance(tags_raw, str):
        tags_raw = [tags_raw]
    tags = []
    for valor in tags_raw if isinstance(tags_raw, list) else []:
        tag = str(valor or "").strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _favoritos_ml_preco_cheio_estado(estado: Any) -> tuple[Optional[float], str]:
    dados = estado if isinstance(estado, dict) else {}
    for campo in ("original_price", "base_price", "price"):
        valor = _parse_float_flex(dados.get(campo))
        if valor is not None:
            return float(valor), campo
    return None, ""


def _favoritos_ml_obter_automacao_preco(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
) -> tuple[dict, dict]:
    resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/pricing-automation/items/{item_id}/automation",
        timeout=15,
    )
    try:
        data = resp.json() or {}
    except Exception:
        data = {}
    if resp.status_code == 200:
        if not isinstance(data, dict) or not data.get("item_id"):
            raise HTTPException(
                status_code=502,
                detail="Mercado Livre retornou uma automacao de precos invalida.",
            )
        return {
            "configured": True,
            "status": str(data.get("status") or "").strip().upper(),
            "rule_id": str((data.get("item_rule") or {}).get("rule_id") or "").strip()
            if isinstance(data.get("item_rule"), dict)
            else "",
        }, cfg

    erro = str(data.get("error") or data.get("code") or "").strip().lower() if isinstance(data, dict) else ""
    if resp.status_code == 404 and erro == "automation_not_found":
        return {"configured": False, "status": "", "rule_id": ""}, cfg

    detalhe = _ml_parse_error_detail(resp, "Nao foi possivel verificar a automacao de precos do anuncio")
    raise HTTPException(status_code=resp.status_code or 502, detail=detalhe)


def _favoritos_ml_validar_preco_mutavel(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    estado: dict,
    preco_alvo: Any,
) -> tuple[dict, dict]:
    alvo = _parse_float_flex(preco_alvo)
    atual, campo_atual = _favoritos_ml_preco_cheio_estado(estado)
    base = {
        "checked": alvo is not None,
        "ok": True,
        "price_update_required": None,
        "target_price": round(float(alvo), 2) if alvo is not None else None,
        "current_price": atual,
        "current_price_field": campo_atual,
        "dynamic_standard_price": bool((estado or {}).get("dynamic_standard_price")),
        "automation_configured": False,
        "automation_status": "",
        "automation_rule_id": "",
        "retryable": False,
        "reason": "",
        "message": "Preco-alvo nao informado; verificacao de editabilidade adiada para a execucao."
        if alvo is None
        else "Preco do anuncio apto para a alteracao solicitada.",
    }
    if alvo is None:
        return base, cfg

    update_required = bool(atual is None or not _favoritos_ml_float_close(atual, alvo))
    base["price_update_required"] = update_required
    if not update_required:
        base["message"] = "O preco cheio ja esta no valor alvo; nenhuma edicao de preco e necessaria."
        return base, cfg

    if base["dynamic_standard_price"]:
        base.update({
            "ok": False,
            "reason": "dynamic_standard_price",
            "message": (
                "O anuncio possui automatizacao de precos ativa no Mercado Livre. "
                "Desative ou ajuste a automacao antes de alterar tipo, preco ou campanha."
            ),
        })
        return base, cfg

    automacao, cfg = _favoritos_ml_obter_automacao_preco(client_id, loja, cfg, item_id)
    base.update({
        "automation_configured": bool(automacao.get("configured")),
        "automation_status": str(automacao.get("status") or ""),
        "automation_rule_id": str(automacao.get("rule_id") or ""),
    })
    if automacao.get("configured"):
        base.update({
            "ok": False,
            "reason": "pricing_automation_configured",
            "message": (
                "O anuncio possui uma automacao de precos configurada no Mercado Livre"
                + (f" (status {automacao.get('status')})." if automacao.get("status") else ".")
                + " Desative ou ajuste a automacao antes de alterar tipo, preco ou campanha."
            ),
        })
    return base, cfg


def _favoritos_ml_resumir_estado_item(item_id: str, item_data: Any) -> dict:
    item = item_data if isinstance(item_data, dict) else {}
    status = str(item.get("status") or "").strip().lower()
    sub_status_raw = item.get("sub_status")
    if isinstance(sub_status_raw, str):
        sub_status_raw = [sub_status_raw]
    sub_status = [
        str(valor or "").strip().lower()
        for valor in (sub_status_raw if isinstance(sub_status_raw, list) else [])
        if str(valor or "").strip()
    ]
    status_bloqueado = not status or status in {"under_review", "closed", "inactive"}
    sub_status_bloqueado = any(
        valor in {"forbidden", "suspended", "deleted"}
        for valor in sub_status
    )
    mutacao_bloqueada = bool(status_bloqueado or sub_status_bloqueado)
    retryable = bool(status == "under_review" or "forbidden" in sub_status)
    if not status:
        motivo = "O Mercado Livre nao informou o status atual do anuncio. Nenhuma alteracao foi enviada por seguranca."
    elif status == "under_review" or "forbidden" in sub_status:
        motivo = (
            "O anuncio esta em revisao no Mercado Livre e as alteracoes de preco "
            "ficam temporariamente bloqueadas. Aguarde o anuncio voltar a ativo."
        )
    elif mutacao_bloqueada:
        marcador = ", ".join([status or "sem status", *sub_status])
        motivo = f"O anuncio esta em um estado que bloqueia alteracoes no Mercado Livre: {marcador}."
    else:
        motivo = ""
    listing_type_id = _favoritos_ml_listing_type_id(item.get("listing_type_id"))
    tags = _favoritos_ml_tags_item(item)
    sold_quantity = _parse_float_flex(item.get("sold_quantity"))
    has_bids_raw = item.get("has_bids")
    return {
        "item_id": str(item.get("id") or item_id or "").strip(),
        "status": status,
        "sub_status": sub_status,
        "listing_type_id": listing_type_id,
        "listing_type_name": _favoritos_ml_nome_listing_type(listing_type_id),
        "price": _parse_float_flex(item.get("price")),
        "base_price": _parse_float_flex(item.get("base_price")),
        "original_price": _parse_float_flex(item.get("original_price")),
        "tags": tags,
        "dynamic_standard_price": "dynamic_standard_price" in tags,
        "has_bids": has_bids_raw if isinstance(has_bids_raw, bool) else None,
        "sold_quantity": int(sold_quantity) if sold_quantity is not None else None,
        "catalog_listing": bool(item.get("catalog_listing")),
        "catalog_product_id": str(item.get("catalog_product_id") or "").strip(),
        "last_updated": str(item.get("last_updated") or "").strip(),
        "mutation_blocked": mutacao_bloqueada,
        "retryable": retryable,
        "block_reason": motivo,
    }


def _favoritos_ml_obter_estado_item(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    preco_alvo: Any = None,
) -> tuple[dict, dict]:
    resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/items/{item_id}",
        timeout=15,
    )
    if resp.status_code != 200:
        detalhe = _ml_parse_error_detail(resp, "Nao foi possivel conferir o estado atual do anuncio")
        raise HTTPException(status_code=resp.status_code or 502, detail=detalhe)
    try:
        item_data = resp.json() or {}
    except Exception:
        item_data = {}
    if not isinstance(item_data, dict):
        raise HTTPException(status_code=502, detail="Mercado Livre retornou um estado de anuncio invalido.")
    estado = _favoritos_ml_resumir_estado_item(item_id, item_data)
    price_preflight, cfg = _favoritos_ml_validar_preco_mutavel(
        client_id,
        loja,
        cfg,
        item_id,
        estado,
        preco_alvo,
    )
    estado["price_preflight"] = price_preflight
    return estado, cfg


def _favoritos_ml_troca_listing_type_favoritos_suportada(atual: str, alvo: str) -> bool:
    atual_id = _favoritos_ml_listing_type_id(atual)
    alvo_id = _favoritos_ml_listing_type_id(alvo)
    return bool(
        atual_id
        and alvo_id
        and atual_id != alvo_id
        and atual_id in {"gold_pro", "gold_special"}
        and alvo_id in {"gold_pro", "gold_special"}
    )


def _favoritos_ml_listing_type_alvo_req(req: FavoritosEfetivarPromocaoRequest) -> str:
    candidatos = [
        req.listing_type_id_alvo,
        req.tipo_anuncio_alvo,
    ]
    if isinstance(req.simulacao, dict):
        candidatos.extend([
            req.simulacao.get("listingTypeIdAlvo"),
            req.simulacao.get("listing_type_id_alvo"),
            req.simulacao.get("tipoAnuncioAlvo"),
            req.simulacao.get("tipo_anuncio_alvo"),
        ])
    for candidato in candidatos:
        listing_type = _favoritos_ml_listing_type_id(candidato)
        if listing_type:
            return listing_type
    return ""


def _favoritos_ml_obter_listing_type_atual_e_disponiveis(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
) -> tuple[str, list[str], dict, dict, dict]:
    item_resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/items/{item_id}",
        timeout=15,
    )
    item_data = {}
    try:
        item_data = item_resp.json() or {}
    except Exception:
        item_data = {}
    if item_resp.status_code != 200:
        detalhe = _ml_parse_error_detail(item_resp, "Nao foi possivel consultar o anuncio")
        raise HTTPException(
            status_code=item_resp.status_code,
            detail=f"Nao foi possivel consultar o anuncio {item_id} na loja {loja}: {detalhe}",
        )

    atual = _favoritos_ml_listing_type_id(item_data.get("listing_type_id"))
    detalhe_disponibilidade: dict[str, list[str]] = {}

    def _consultar_tipos_disponiveis(sufixo: str) -> list[str]:
        nonlocal cfg
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}/{sufixo}",
            timeout=15,
        )
        data = []
        try:
            data = resp.json() or []
        except Exception:
            data = []
        saida: list[str] = []
        if resp.status_code == 200 and isinstance(data, list):
            for entrada in data:
                tipo = _favoritos_ml_listing_type_id(entrada)
                if tipo and tipo not in saida:
                    saida.append(tipo)
        elif resp.status_code not in (404, 403):
            logger.warning(
                "[Favoritos ML] Falha ao consultar %s de %s/%s: HTTP %s %s",
                sufixo,
                loja,
                item_id,
                resp.status_code,
                _ml_parse_error_detail(resp, "erro"),
            )
        return saida

    for sufixo in ("available_listing_types", "available_upgrades", "available_downgrades"):
        detalhe_disponibilidade[sufixo] = _consultar_tipos_disponiveis(sufixo)

    disponiveis: list[str] = []
    for lista in detalhe_disponibilidade.values():
        for tipo in lista:
            if tipo not in disponiveis:
                disponiveis.append(tipo)
    return atual, disponiveis, item_data, cfg, detalhe_disponibilidade


def _favoritos_ml_validar_listing_type_disponivel(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    listing_type_alvo: str,
) -> tuple[dict, dict]:
    alvo = _favoritos_ml_listing_type_id(listing_type_alvo)
    atual, disponiveis, item_data, cfg, detalhe_disponibilidade = _favoritos_ml_obter_listing_type_atual_e_disponiveis(
        client_id,
        loja,
        cfg,
        item_id,
    )
    atual_nome = _favoritos_ml_nome_listing_type(atual)
    alvo_nome = _favoritos_ml_nome_listing_type(alvo)
    if not alvo or atual == alvo:
        return {
            "ok": True,
            "item_id": item_id,
            "loja": loja,
            "current": atual,
            "target": alvo,
            "current_name": atual_nome,
            "target_name": alvo_nome,
            "available_listing_types": disponiveis,
            "available_listing_types_detail": detalhe_disponibilidade,
            "item": item_data,
            "message": "Sem troca de tipo necessaria.",
        }, cfg
    if alvo not in disponiveis:
        return {
            "ok": False,
            "item_id": item_id,
            "loja": loja,
            "current": atual,
            "target": alvo,
            "current_name": atual_nome,
            "target_name": alvo_nome,
            "available_listing_types": disponiveis,
            "available_listing_types_detail": detalhe_disponibilidade,
            "item": item_data,
            "message": (
                f"Mercado Livre nao disponibiliza a troca {atual_nome or atual or '-'} -> "
                f"{alvo_nome or alvo or '-'} para este anuncio agora."
            ),
            "item_status": item_data.get("status") if isinstance(item_data, dict) else "",
        }, cfg
    return {
        "ok": True,
        "item_id": item_id,
        "loja": loja,
        "current": atual,
        "target": alvo,
        "current_name": atual_nome,
        "target_name": alvo_nome,
        "available_listing_types": disponiveis,
        "available_listing_types_detail": detalhe_disponibilidade,
        "item": item_data,
        "message": "Troca de tipo disponivel.",
    }, cfg


def _favoritos_ml_atualizar_tipo_listing_item(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    listing_type_alvo: str,
) -> tuple[dict, dict]:
    alvo = _favoritos_ml_listing_type_id(listing_type_alvo)
    if not alvo:
        return {"success": True, "changed": False, "target": "", "skipped": True}, cfg
    if alvo not in {"gold_pro", "gold_special"}:
        raise HTTPException(status_code=400, detail=f"Tipo de anuncio alvo nao suportado para Favoritos: {listing_type_alvo}")

    def _json_response(resp) -> dict:
        try:
            data = resp.json() or {}
            return data if isinstance(data, dict) else {"response": data}
        except Exception:
            return {}

    max_validacoes = 1
    try:
        max_validacoes = int(float(str(os.getenv("ML_LISTING_TYPE_VALIDATION_MAX_ATTEMPTS", "6") or "6").replace(",", ".")))
    except Exception:
        max_validacoes = 6
    max_validacoes = max(1, min(max_validacoes, 10))

    validacao_tipo = {}
    for tentativa_validacao in range(1, max_validacoes + 1):
        validacao_tipo, cfg = _favoritos_ml_validar_listing_type_disponivel(client_id, loja, cfg, item_id, alvo)
        if validacao_tipo.get("ok"):
            break
        if _favoritos_ml_troca_listing_type_favoritos_suportada(validacao_tipo.get("current"), alvo):
            break
        if tentativa_validacao < max_validacoes and alvo == "gold_special":
            logger.info(
                "[Favoritos ML] Troca de tipo ainda indisponivel para %s/%s (%s/%s). Aguardando liberacao do Mercado Livre apos remocao de promocoes.",
                loja,
                item_id,
                tentativa_validacao,
                max_validacoes,
            )
            time.sleep(2.0)
            continue
        break
    item_data = validacao_tipo.get("item") or {}
    atual = _favoritos_ml_listing_type_id(validacao_tipo.get("current"))
    if atual == alvo:
        return {
            "success": True,
            "changed": False,
            "current": atual,
            "target": alvo,
            "current_name": _favoritos_ml_nome_listing_type(atual),
            "target_name": _favoritos_ml_nome_listing_type(alvo),
            "response": item_data,
            "validacao": validacao_tipo,
        }, cfg
    if not validacao_tipo.get("ok"):
        if _favoritos_ml_troca_listing_type_favoritos_suportada(atual, alvo):
            logger.info(
                "[Favoritos ML] %s/%s nao listou %s em available_*; tentando POST /listing_type mesmo assim. Detalhe: %s",
                loja,
                item_id,
                alvo,
                validacao_tipo.get("message") or "",
            )
        else:
            raise HTTPException(status_code=409, detail=validacao_tipo.get("message") or "Troca de tipo indisponivel no Mercado Livre.")

    ultimo_resp = None
    ultimo_body = {}
    ultimo_method = ""
    max_tentativas_update = 1
    try:
        max_tentativas_update = int(float(str(os.getenv("ML_LISTING_TYPE_UPDATE_MAX_ATTEMPTS", "3") or "3").replace(",", ".")))
    except Exception:
        max_tentativas_update = 3
    max_tentativas_update = max(1, min(max_tentativas_update, 5))

    for tentativa_update in range(1, max_tentativas_update + 1):
        for metodo in ("POST", "PUT"):
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                metodo,
                f"https://api.mercadolibre.com/items/{item_id}/listing_type",
                json={"id": alvo},
                timeout=25,
            )
            ultimo_resp = resp
            ultimo_body = _json_response(resp)
            ultimo_method = metodo
            if resp.status_code in (200, 201, 202, 204):
                break
            if not (metodo == "POST" and resp.status_code == 405):
                break
        if ultimo_resp and ultimo_resp.status_code in (200, 201, 202, 204):
            break
        if tentativa_update < max_tentativas_update and _favoritos_ml_troca_listing_type_favoritos_suportada(atual, alvo):
            detalhe_retry = _ml_parse_error_detail(ultimo_resp, "Erro ao alterar tipo do anuncio no Mercado Livre")
            logger.info(
                "[Favoritos ML] Tentativa %s/%s falhou ao trocar tipo de %s/%s para %s: %s. Nova tentativa.",
                tentativa_update,
                max_tentativas_update,
                loja,
                item_id,
                alvo,
                detalhe_retry,
            )
            time.sleep(2.0)
            continue
        break

    if not ultimo_resp or ultimo_resp.status_code not in (200, 201, 202, 204):
        detalhe = _ml_parse_error_detail(ultimo_resp, "Erro ao alterar tipo do anuncio no Mercado Livre")
        detalhe = (
            f"Erro ao alterar tipo Premium/Classico para {_favoritos_ml_nome_listing_type(alvo)} "
            f"no anuncio {item_id}: {detalhe}"
        )
        raise HTTPException(status_code=getattr(ultimo_resp, "status_code", 500) or 500, detail=detalhe)

    confirmacao = {}
    confirmado = False
    for tentativa in range(6):
        if tentativa:
            time.sleep(1.2)
        resp_conf, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}",
            timeout=15,
        )
        item_conf = _json_response(resp_conf) if resp_conf.status_code == 200 else {}
        atual_conf = _favoritos_ml_listing_type_id(item_conf.get("listing_type_id"))
        confirmacao = {
            "attempt": tentativa + 1,
            "status_code": resp_conf.status_code,
            "listing_type_id": atual_conf,
            "listing_type_name": _favoritos_ml_nome_listing_type(atual_conf),
        }
        if atual_conf == alvo:
            confirmado = True
            break

    if not confirmado:
        raise HTTPException(
            status_code=409,
            detail=(
                "O tipo do anuncio ainda nao ficou igual ao ranking no Mercado Livre. "
                f"Tipo esperado: {_favoritos_ml_nome_listing_type(alvo)}. Conferencia: {confirmacao}"
            ),
        )

    return {
        "success": True,
        "changed": True,
        "method": ultimo_method,
        "current": atual,
        "target": alvo,
        "current_name": _favoritos_ml_nome_listing_type(atual),
        "target_name": _favoritos_ml_nome_listing_type(alvo),
        "payload": {"id": alvo},
        "response": ultimo_body,
        "confirmacao": confirmacao,
    }, cfg


def _favoritos_ml_atualizar_preco_item(client_id: str, loja: str, cfg: dict, item_id: str, preco: float) -> tuple[dict, dict]:
    preco_num = round(float(preco), 2)
    ultimo_resp = None
    ultima_conferencia_apos_erro = None

    def _json_response(resp) -> Any:
        try:
            return resp.json() or {}
        except Exception:
            return {}

    def _preco_no_payload(data: Any) -> Optional[float]:
        if not isinstance(data, dict):
            return None
        return _parse_float_flex(
            data.get("price")
            or data.get("base_price")
            or data.get("amount")
            or data.get("standard_price")
        )

    def _resposta_indica_preco_ignorado(resp, data: Any) -> bool:
        preco_retornado = _preco_no_payload(data)
        if preco_retornado is not None and _favoritos_ml_float_close(preco_retornado, preco_num):
            return False

        partes_risco = []
        if isinstance(data, dict):
            for chave in ("warnings", "warning", "errors", "error", "message", "cause", "detail"):
                valor = data.get(chave)
                if valor:
                    try:
                        partes_risco.append(json.dumps(valor, ensure_ascii=False))
                    except Exception:
                        partes_risco.append(str(valor))
        if not partes_risco:
            return False

        try:
            texto = " ".join(partes_risco)
        except Exception:
            texto = " ".join(str(p) for p in partes_risco)
        texto_norm = normalizar_texto(texto)
        termos_preco = ("price", "preco", "amount")
        termos_ignorado = ("ignored", "ignore", "not modifiable", "not_modifiable", "automat", "readonly", "read only")
        if any(t in texto_norm for t in termos_preco) and any(t in texto_norm for t in termos_ignorado):
            return True
        return False

    def _erro_preco_regra_negocio(resp, detalhe: str = "") -> bool:
        texto = f"{detalhe or ''} {getattr(resp, 'text', '') or ''}"
        texto_norm = normalizar_texto(texto).replace("_", " ")
        gatilhos = (
            "item.price.not modifiable",
            "price is not modifiable",
            "cannot modify price",
            "dynamic pricing",
            "dynamic standard price",
            "pricing automation",
            "automatizacao de precos",
            "automacao de precos",
            "field not updatable",
            "catalog listing",
            "catalog item",
            "business rule",
        )
        return any(gatilho in texto_norm for gatilho in gatilhos)

    def _erro_preco_transitorio(resp, detalhe: str = "") -> bool:
        if _erro_preco_regra_negocio(resp, detalhe):
            return False
        status = getattr(resp, "status_code", None)
        texto = f"{detalhe or ''} {getattr(resp, 'text', '') or ''}"
        texto_norm = normalizar_texto(texto)
        gatilhos = (
            "oops",
            "something went wrong",
            "temporarily unavailable",
            "temporary unavailable",
            "temporarily overloaded",
            "service temporarily overloaded",
            "internal error",
            "internal server error",
            "bad gateway",
            "gateway timeout",
            "read timed out",
            "request timeout",
            "try again",
        )
        return bool(
            status in (408, 425, 429, 500, 502, 503, 504)
            or any(gatilho in texto_norm for gatilho in gatilhos)
        )

    def _resumir_conferencia_preco(conferencia: Any) -> str:
        if not isinstance(conferencia, dict):
            return ""
        partes = []
        if conferencia.get("preco_alvo") not in (None, ""):
            partes.append(f"preco esperado {conferencia.get('preco_alvo')}")
        for label, chave in (
            ("price", "item_price"),
            ("base_price", "base_price"),
            ("standard_price", "standard_price"),
            ("original_price", "original_price"),
        ):
            valor = conferencia.get(chave)
            if valor not in (None, ""):
                partes.append(f"{label} {valor}")
        if conferencia.get("attempt"):
            partes.append(f"tentativa de conferencia {conferencia.get('attempt')}")
        return ", ".join(partes)

    def _confirmar_preco_apos_erro(motivo: str, tentativas: int = 3) -> tuple[dict, dict]:
        try:
            conferencia, cfg_conf = _favoritos_ml_aguardar_preco_anuncio(
                client_id,
                loja,
                cfg,
                item_id,
                float(preco_num),
                tentativas=tentativas,
            )
        except Exception as exc:
            logger.warning(
                "[Favoritos ML] Falha ao conferir preco de %s/%s apos erro da API de preco: %s",
                loja,
                item_id,
                exc,
            )
            return {
                "success": False,
                "preco_alvo": preco_num,
                "error": f"Falha ao conferir preco apos erro: {exc}",
            }, cfg
        if conferencia.get("success"):
            logger.info(
                "[Favoritos ML] Preco de %s/%s confirmado apos erro da API de preco. Motivo anterior: %s",
                loja,
                item_id,
                motivo,
            )
        return conferencia, cfg_conf

    def _montar_erro_preco_final(detalhe_principal: str, conferencia: Optional[dict] = None) -> str:
        partes = [
            (
                "Mercado Livre nao confirmou a atualizacao do preco cheio. "
                "O sistema tentou atualizar o preco e conferiu o anuncio depois do erro."
            )
        ]
        if detalhe_principal:
            partes.append(f"Resposta do Mercado Livre: {detalhe_principal}")
        resumo_conf = _resumir_conferencia_preco(conferencia or ultima_conferencia_apos_erro)
        if resumo_conf:
            partes.append(f"Conferencia pos-erro: {resumo_conf}.")
        return " ".join(partes)

    max_tentativas_put = 5
    for tentativa in range(max_tentativas_put):
        if tentativa:
            espera = min(8.0, 1.5 * tentativa)
            if ultimo_resp is not None:
                espera = _ml_retry_after_seconds(ultimo_resp, padrao=espera)
            time.sleep(espera)
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "PUT",
            f"https://api.mercadolibre.com/items/{item_id}",
            json={"price": preco_num},
            timeout=25,
        )
        ultimo_resp = resp
        if resp.status_code in (200, 201):
            data = _json_response(resp)
            if _resposta_indica_preco_ignorado(resp, data):
                detalhe_ignorado = (
                    "Mercado Livre respondeu ao PUT /items, mas o retorno nao refletiu o preco cheio simulado. "
                    f"Preco esperado: {preco_num}. Retorno: {data}"
                )
                conferencia, cfg = _confirmar_preco_apos_erro(detalhe_ignorado, tentativas=3)
                ultima_conferencia_apos_erro = conferencia
                if conferencia.get("success"):
                    return {
                        "method": "items_put_confirmed_after_warning",
                        "payload": {"price": preco_num},
                        "response": data,
                        "warning": detalhe_ignorado,
                        "preco_confirmacao_apos_erro": conferencia,
                    }, cfg
                erro = HTTPException(status_code=409, detail=_montar_erro_preco_final(detalhe_ignorado, conferencia))
                erro.favoritos_retryable = False
                erro.favoritos_price_business_rule = True
                raise erro
            return {
                "method": "items_put",
                "payload": {"price": preco_num},
                "response": data,
            }, cfg

        detalhe_tentativa = _ml_parse_error_detail(resp, "Erro ao atualizar preco do anuncio no Mercado Livre")
        conferencia, cfg = _confirmar_preco_apos_erro(detalhe_tentativa, tentativas=2)
        ultima_conferencia_apos_erro = conferencia
        if conferencia.get("success"):
            return {
                "method": "items_put_confirmed_after_error",
                "payload": {"price": preco_num},
                "response_error": _json_response(resp),
                "error_status_code": resp.status_code,
                "error_detail": detalhe_tentativa,
                "preco_confirmacao_apos_erro": conferencia,
            }, cfg
        if not _erro_preco_transitorio(resp, detalhe_tentativa):
            break

    detalhe = _ml_parse_error_detail(ultimo_resp, "Erro ao atualizar preco do anuncio no Mercado Livre")
    erro = HTTPException(
        status_code=getattr(ultimo_resp, "status_code", 500) or 500,
        detail=_montar_erro_preco_final(detalhe, ultima_conferencia_apos_erro),
    )
    erro.favoritos_retryable = _erro_preco_transitorio(ultimo_resp, detalhe)
    erro.favoritos_price_business_rule = _erro_preco_regra_negocio(ultimo_resp, detalhe)
    raise erro


def _favoritos_ml_preco_base_autoritativo(item: Any, price_info: Any) -> tuple[Optional[float], str]:
    item_data = item if isinstance(item, dict) else {}
    price_data = price_info if isinstance(price_info, dict) else {}
    standard_price = _parse_float_flex(price_data.get("standard_price"))
    if standard_price is not None:
        return round(float(standard_price), 2), "standard_price"
    base_price = _parse_float_flex(item_data.get("base_price"))
    if base_price is not None:
        return round(float(base_price), 2), "base_price"
    return None, ""


def _favoritos_ml_aguardar_preco_anuncio(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    preco_anuncio: float,
    tentativas: int = 8,
) -> tuple[dict, dict]:
    ultimo = {}
    preco_alvo = round(float(preco_anuncio), 2)
    for tentativa in range(max(1, tentativas)):
        if tentativa:
            time.sleep(1.5)

        item = {}
        resp_item, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}",
            timeout=15,
        )
        if resp_item.status_code == 200:
            try:
                item = resp_item.json() or {}
            except Exception:
                item = {}

        price_info, cfg = _ml_obter_preco_detalhado(
            client_id,
            loja,
            cfg,
            item_id,
            fallback_price=item.get("price"),
            request_fn=_ml_api_request,
            consultar_sale_price_sempre=True,
        )

        preco_base, campo_base = _favoritos_ml_preco_base_autoritativo(item, price_info)
        ok = _favoritos_ml_float_close(preco_base, preco_alvo, tolerancia=0.009)
        ultimo = {
            "success": ok,
            "attempt": tentativa + 1,
            "preco_alvo": preco_alvo,
            "item_price": item.get("price"),
            "base_price": item.get("base_price"),
            "standard_price": price_info.get("standard_price"),
            "original_price": price_info.get("original_price"),
            "authoritative_price": preco_base,
            "authoritative_price_field": campo_base,
            "price_info": price_info,
        }
        if ok:
            return ultimo, cfg

    ultimo["success"] = False
    return ultimo, cfg


def _favoritos_ml_verificar_efetivacao(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    campanha_id: str,
    promotion_type: str,
    preco_anuncio: float,
    preco_promocional: Optional[float],
    percentual_promocao: Optional[float] = None,
    *,
    tentativas: int = 8,
    leituras_estaveis: int = 3,
) -> tuple[dict, dict]:
    ultimo = {}
    assinatura_anterior = None
    estabilidade = 0
    assinatura_divergente_anterior = None
    estabilidade_divergente = 0
    observacoes = []
    percentual_esperado = _parse_float_flex(percentual_promocao)
    if percentual_esperado is None and preco_anuncio and preco_promocional:
        percentual_esperado = max(
            0.0,
            ((float(preco_anuncio) - float(preco_promocional)) / float(preco_anuncio)) * 100.0,
        )

    for tentativa in range(max(2, int(tentativas))):
        if tentativa:
            time.sleep(1.8)

        resp_item, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}",
            timeout=15,
        )
        if resp_item.status_code != 200:
            detalhe = _ml_parse_error_detail(resp_item, "Nao foi possivel reconciliar o item depois da promocao")
            raise HTTPException(status_code=resp_item.status_code or 502, detail=detalhe)
        try:
            item = resp_item.json() or {}
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Mercado Livre retornou um item invalido na reconciliacao.") from exc
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="Mercado Livre retornou um item invalido na reconciliacao.")

        price_info, cfg = _ml_obter_preco_detalhado(
            client_id,
            loja,
            cfg,
            item_id,
            fallback_price=item.get("price"),
            request_fn=_ml_api_request,
            consultar_sale_price_sempre=True,
        )
        _favoritos_ml_invalidar_cache_promocoes_item(client_id, loja, item_id)
        promocoes_item, cfg = _ml_obter_promocoes_item(
            client_id,
            loja,
            cfg,
            item_id,
            strict=True,
        )
        raw_promocao = _ml_encontrar_promocao_raw_item(promocoes_item, campanha_id)
        tipo_observado = _favoritos_ml_texto_promocao(
            raw_promocao or {},
            ("promotion_type", "promotionType", "campaign_type", "campaignType", "type"),
        )
        tipo_esperado = str(promotion_type or "").strip().lower()
        tipo_ok = bool(tipo_observado) and str(tipo_observado).strip().lower() == tipo_esperado
        promocao_ok = bool(raw_promocao) and tipo_ok

        preco_base, campo_base = _favoritos_ml_preco_base_autoritativo(item, price_info)
        preco_raw_promocao, desconto_raw = _ml_extrair_preco_promocao_raw(raw_promocao or {})
        preco_final = _parse_float_flex(preco_raw_promocao)
        if preco_final is None and raw_promocao:
            preco_final = _parse_float_flex(
                price_info.get("price")
                or price_info.get("sale_price")
                or price_info.get("promotional_price")
            )

        preco_anuncio_ok = _favoritos_ml_float_close(preco_base, preco_anuncio, tolerancia=0.009)
        preco_promocional_ok = _favoritos_ml_float_close(preco_final, preco_promocional, tolerancia=0.009)
        desconto_info = _parse_float_flex(price_info.get("discount_pct"))
        if desconto_info is None:
            desconto_info = _parse_float_flex(desconto_raw)
        if desconto_info is None and preco_base and preco_final is not None:
            desconto_info = max(0.0, ((float(preco_base) - float(preco_final)) / float(preco_base)) * 100.0)
        desconto_ok = bool(
            percentual_esperado is not None
            and desconto_info is not None
            and abs(float(desconto_info) - float(percentual_esperado)) <= 0.35
        )
        leitura_ok = bool(promocao_ok and preco_anuncio_ok and preco_promocional_ok and desconto_ok)
        ids_promocoes_observadas = tuple(sorted(
            str(valor or "").strip().lower()
            for valor in _ml_extrair_ids_promocoes_item(promocoes_item)
            if str(valor or "").strip()
        ))
        observacao_completa = bool(
            preco_base is not None
            and (
                not raw_promocao
                or (tipo_observado and preco_final is not None and desconto_info is not None)
            )
        )
        assinatura = (
            str(campanha_id or "").strip().lower(),
            str(tipo_observado or "").strip().lower(),
            round(float(preco_base), 2) if preco_base is not None else None,
            round(float(preco_final), 2) if preco_final is not None else None,
            round(float(desconto_info), 2) if desconto_info is not None else None,
            ids_promocoes_observadas,
        )
        if leitura_ok:
            estabilidade = estabilidade + 1 if assinatura == assinatura_anterior else 1
            assinatura_anterior = assinatura
            estabilidade_divergente = 0
            assinatura_divergente_anterior = None
        else:
            estabilidade = 0
            assinatura_anterior = None
            if observacao_completa:
                estabilidade_divergente = (
                    estabilidade_divergente + 1
                    if assinatura == assinatura_divergente_anterior
                    else 1
                )
                assinatura_divergente_anterior = assinatura
            else:
                estabilidade_divergente = 0
                assinatura_divergente_anterior = None

        ultimo = {
            "promotion_id": campanha_id,
            "promotion_type": promotion_type,
            "promocao_ok": promocao_ok,
            "promotion_type_observed": tipo_observado,
            "promotion_type_ok": tipo_ok,
            "promocao_por_preco_ok": False,
            "preco_anuncio_ok": preco_anuncio_ok,
            "preco_promocional_ok": preco_promocional_ok,
            "desconto_ok": desconto_ok,
            "desconto_esperado": percentual_esperado,
            "desconto_info": desconto_info,
            "price_info": price_info,
            "item_price": item.get("price"),
            "standard_price": price_info.get("standard_price"),
            "base_price": preco_base,
            "base_price_field": campo_base,
            "promotion_price_raw": preco_raw_promocao,
            "final_price": round(float(preco_final), 2) if preco_final is not None else None,
            "promotion_discount_raw": desconto_raw,
            "attempt": tentativa + 1,
            "stable_reads": estabilidade,
            "divergent_stable_reads": estabilidade_divergente,
            "required_stable_reads": max(2, int(leituras_estaveis)),
            "observable": observacao_completa,
            "confirmed_divergence": False,
        }
        observacoes.append({
            "attempt": tentativa + 1,
            "promotion_id_ok": bool(raw_promocao),
            "promotion_type_ok": tipo_ok,
            "base_price": ultimo["base_price"],
            "final_price": round(float(preco_final), 2) if preco_final is not None else None,
            "discount_pct": round(float(desconto_info), 2) if desconto_info is not None else None,
            "valid": leitura_ok,
            "complete": observacao_completa,
            "divergent_stable_reads": estabilidade_divergente,
        })
        ultimo["observations"] = observacoes[-4:]
        if leitura_ok and estabilidade >= max(2, int(leituras_estaveis)):
            ultimo["success"] = True
            return ultimo, cfg
        if not leitura_ok and estabilidade_divergente >= max(2, int(leituras_estaveis)):
            ultimo["success"] = False
            ultimo["confirmed_divergence"] = True
            return ultimo, cfg

    ultimo["success"] = False
    ultimo["confirmed_divergence"] = False
    return ultimo, cfg


def _favoritos_resolver_sku_para_margem(anuncio: dict, sku_hint: str = "") -> str:
    candidatos = [
        sku_hint,
        (anuncio or {}).get("sku"),
        (anuncio or {}).get("sku_display"),
        (anuncio or {}).get("seller_sku"),
        (anuncio or {}).get("seller_custom_field"),
        (anuncio or {}).get("SELLER_SKU"),
    ]
    for candidato in candidatos:
        texto = str(candidato or "").strip()
        if texto:
            return texto
    return ""


def _favoritos_aplicar_margem_anuncio_ml(
    anuncio: dict,
    sku_hint: str,
    custos_por_sku: dict,
    impostos_por_sku: dict,
) -> dict:
    if not isinstance(anuncio, dict):
        return anuncio

    sku_margem = _favoritos_resolver_sku_para_margem(anuncio, sku_hint)
    preco_final = None
    for campo in (
        "preco_promocional",
        "promotional_price",
        "promotion_price",
        "sale_price",
        "price",
        "preco",
        "valor",
    ):
        preco_final = _to_float_safe(anuncio.get(campo))
        if preco_final is not None and preco_final > 0:
            break

    custo = _resolver_custo_por_sku(custos_por_sku or {}, sku_margem)
    imposto_rate = _resolver_imposto_rate_por_sku(impostos_por_sku or {}, sku_margem)

    tarifa = None
    for campo in ("ad_cost", "tarifa", "tarifa_ml", "fee_per_sale", "sale_fee_amount"):
        tarifa = _to_float_safe(anuncio.get(campo))
        if tarifa is not None:
            break

    taxa_pct = _to_rate_safe(anuncio.get("sale_fee_pct"))
    if taxa_pct is None:
        taxa_pct = _favoritos_taxa_padrao_por_tipo_anuncio(anuncio)
    if tarifa is None and preco_final is not None and taxa_pct is not None:
        tarifa = round(float(preco_final) * float(taxa_pct), 2)

    frete = None
    for campo in (
        "shipping_seller_cost",
        "shipping_cost",
        "frete_ml",
        "shipping_list_cost",
        "shipping_base_cost",
        "frete",
        "custo_frete",
    ):
        frete = _to_float_safe(anuncio.get(campo))
        if frete is not None:
            break
    free_shipping = bool(
        anuncio.get("free_shipping") is True
        or anuncio.get("frete_gratis") is True
        or _flag_frete_gratis(anuncio.get("Frete Gratis"))
        or _flag_frete_gratis(anuncio.get("Frete Grátis"))
    )
    if frete is None and isinstance(anuncio.get("shipping"), dict):
        shipping_info = anuncio.get("shipping") or {}
        free_shipping = free_shipping or bool(shipping_info.get("free_shipping") is True)
        campos_frete_shipping = ("seller_cost", "shipping_cost", "base_cost", "list_cost")
        if not free_shipping:
            campos_frete_shipping = campos_frete_shipping + ("cost",)
        for campo in campos_frete_shipping:
            frete = _to_float_safe(shipping_info.get(campo))
            if frete is not None:
                break

    dados_margem = dict(anuncio)
    if preco_final is not None:
        dados_margem["preco_final_margem"] = preco_final
    if tarifa is not None:
        dados_margem["tarifa_ml"] = tarifa
    if frete is not None:
        dados_margem["frete_ml"] = frete
    if free_shipping:
        dados_margem["free_shipping"] = True
    if taxa_pct is not None:
        dados_margem["sale_fee_pct"] = taxa_pct
    anuncio.update(
        margem_calcular_anuncio(
            dados_margem,
            sku_hint=sku_margem,
            custo=custo,
            imposto_rate=imposto_rate,
            taxa_padrao=taxa_pct,
        )
    )
    return anuncio


def _ml_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }


def _ml_api_get(url: str, params: dict | None = None, max_retries: int = 3, delay: int = 1):
    headers = {
        **_ml_headers(),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.mercadolivre.com.br/"
    }
    for tentativa in range(max_retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            status = int(resp.status_code or 0)
            retryable = status in {408, 425, 429} or 500 <= status <= 599
            logger.warning(
                "ML API erro %s: status %s. Tentativa %s/%s retry=%s",
                url,
                status,
                tentativa + 1,
                max_retries,
                retryable,
            )
            if not retryable:
                return None
            if tentativa + 1 < max_retries:
                time.sleep(delay)
        except Exception:
            logger.exception("ML API erro %s. Tentativa %s/%s", url, tentativa + 1, max_retries)
            if tentativa + 1 < max_retries:
                time.sleep(delay)
    return None


def _ml_api_item(item_id: str):
    item_id = _extrair_item_id(item_id) or str(item_id or "").strip().upper().replace("-", "")
    if not item_id:
        return None
    return _ml_api_get(f"https://api.mercadolibre.com/items/{item_id}")


def _ml_api_items_multiget_tenant(client_id: str | None, item_ids: list[str] | tuple[str, ...] | set[str]):
    ids = []
    vistos = set()
    for valor in item_ids or []:
        item_id = _extrair_item_id(valor) or str(valor or "").strip().upper().replace("-", "")
        if not item_id or item_id in vistos:
            continue
        vistos.add(item_id)
        ids.append(item_id)
        if len(ids) >= 200:
            break
    if not ids:
        return {}

    try:
        lojas = carregar_lojas(client_id) if client_id else []
    except Exception:
        lojas = []
    lojas_oauth = []
    for loja in lojas or []:
        nome_loja = str((loja or {}).get("nome") or "").strip()
        integracoes = (loja or {}).get("integracoes") or {}
        cfg = dict(integracoes.get("mercadolivre") or {})
        if not nome_loja or not cfg.get("access_token"):
            continue
        cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
        cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
        lojas_oauth.append((nome_loja, cfg))

    def _itens_payload(payload):
        itens = {}
        entradas = payload if isinstance(payload, list) else []
        for entrada in entradas:
            if not isinstance(entrada, dict):
                continue
            body = entrada.get("body") if isinstance(entrada.get("body"), dict) else entrada
            code = entrada.get("code")
            if code is not None:
                try:
                    if int(code) != 200:
                        continue
                except Exception:
                    continue
            item_id = _extrair_item_id(body.get("id") or "") if isinstance(body, dict) else None
            if item_id:
                itens[item_id] = body
        return itens

    resultados = {}
    inicio_total = time.perf_counter()
    chamadas_oauth = 0
    chamadas_publicas = 0
    for inicio in range(0, len(ids), 20):
        lote = ids[inicio:inicio + 20]
        pendentes = [item_id for item_id in lote if item_id not in resultados]
        resultados_lojas = {}

        def _consultar_lote_loja(indice_loja: int, nome_loja: str, cfg: dict, ids_lote: list[str]):
            try:
                resp, _cfg = _ml_api_request(
                    client_id,
                    nome_loja,
                    dict(cfg),
                    "GET",
                    "https://api.mercadolibre.com/items",
                    params={"ids": ",".join(ids_lote)},
                    timeout=20,
                )
                if resp.status_code == 200:
                    return indice_loja, nome_loja, resp.status_code, _itens_payload(resp.json() or []), None
                return indice_loja, nome_loja, resp.status_code, {}, None
            except Exception as exc:
                return indice_loja, nome_loja, None, {}, exc

        if pendentes and lojas_oauth:
            chamadas_oauth += len(lojas_oauth)
            with ThreadPoolExecutor(max_workers=min(4, len(lojas_oauth))) as executor:
                futuros_lojas = [
                    executor.submit(_consultar_lote_loja, indice, nome_loja, dict(cfg), list(pendentes))
                    for indice, (nome_loja, cfg) in enumerate(lojas_oauth)
                ]
                for futuro in as_completed(futuros_lojas):
                    indice_loja, nome_loja, status_code, itens_loja, erro_loja = futuro.result()
                    resultados_lojas[indice_loja] = itens_loja
                    if erro_loja is not None:
                        logger.warning(
                            "[Favoritos][Datas] Falha OAuth multiget loja=%s itens=%s: %s",
                            nome_loja,
                            len(pendentes),
                            erro_loja,
                        )
                    elif status_code != 200:
                        logger.info(
                            "[Favoritos][Datas] ML OAuth multiget loja=%s itens=%s status=%s",
                            nome_loja,
                            len(pendentes),
                            status_code,
                        )
            for indice_loja in range(len(lojas_oauth)):
                for item_id, body in (resultados_lojas.get(indice_loja) or {}).items():
                    if item_id not in resultados:
                        resultados[item_id] = body
            pendentes = [item_id for item_id in pendentes if item_id not in resultados]
        if pendentes:
            chamadas_publicas += 1
            payload_publico = _ml_api_get(
                "https://api.mercadolibre.com/items",
                params={"ids": ",".join(pendentes)},
            )
            resultados.update(_itens_payload(payload_publico))

    logger.info(
        "[Favoritos][Datas] multiget tenant=%s solicitados=%s resolvidos=%s oauth_calls=%s public_calls=%s ms=%s",
        client_id,
        len(ids),
        len(resultados),
        chamadas_oauth,
        chamadas_publicas,
        int((time.perf_counter() - inicio_total) * 1000),
    )
    return resultados


def _ml_api_item_com_oauth_tenant(client_id: str | None, item_id: str):
    item_id = _extrair_item_id(item_id) or str(item_id or "").strip().upper().replace("-", "")
    if not client_id or not item_id:
        return None
    try:
        lojas = carregar_lojas(client_id)
    except Exception:
        lojas = []

    for loja in lojas or []:
        nome_loja = str((loja or {}).get("nome") or "").strip()
        integracoes = (loja or {}).get("integracoes") or {}
        cfg = dict(integracoes.get("mercadolivre") or {})
        if not nome_loja or not cfg.get("access_token"):
            continue
        cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
        cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
        try:
            resp, _cfg = _ml_api_request(
                client_id,
                nome_loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/items/{item_id}",
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                if isinstance(data, dict) and data.get("id"):
                    return data
            logger.info("[Favoritos][Datas] ML OAuth loja=%s item=%s status=%s", nome_loja, item_id, resp.status_code)
        except Exception as exc:
            logger.warning("[Favoritos][Datas] Falha OAuth loja=%s item=%s: %s", nome_loja, item_id, exc)
    return None


def _ml_api_user(user_id: str):
    if not user_id:
        return None
    return _ml_api_get(f"https://api.mercadolibre.com/users/{user_id}")


def _ml_api_user_com_oauth_tenant(client_id: str | None, user_id: str | None):
    user_id = str(user_id or "").strip()
    if not client_id or not user_id:
        return None
    try:
        lojas = carregar_lojas(client_id)
    except Exception:
        lojas = []

    for loja in lojas or []:
        nome_loja = str((loja or {}).get("nome") or "").strip()
        integracoes = (loja or {}).get("integracoes") or {}
        cfg = dict(integracoes.get("mercadolivre") or {})
        if not nome_loja or not cfg.get("access_token"):
            continue
        cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
        cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
        try:
            resp, _cfg = _ml_api_request(
                client_id,
                nome_loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/users/{user_id}",
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                if isinstance(data, dict) and data.get("id"):
                    return data
            logger.info("[Favoritos][Vendedor] ML OAuth loja=%s user=%s status=%s", nome_loja, user_id, resp.status_code)
        except Exception as exc:
            logger.warning("[Favoritos][Vendedor] Falha OAuth loja=%s user=%s: %s", nome_loja, user_id, exc)
    return None


def _ml_total_visitas_payload(data: dict | None):
    if not isinstance(data, dict):
        return None
    for campo in ("total_visits", "totalVisits", "total", "visits"):
        if campo in data:
            valor = _parse_vendas_ml(data.get(campo))
            if valor is not None:
                return valor
    total = 0
    encontrou = False
    for entry in data.get("results") or []:
        if not isinstance(entry, dict):
            continue
        valor = _parse_vendas_ml(entry.get("total") or entry.get("visits") or entry.get("total_visits"))
        if valor is None:
            continue
        encontrou = True
        total += max(0, int(valor))
    return total if encontrou else None


def _ml_api_visitas_com_oauth_tenant(client_id: str | None, item_id: str | None, dias: int = 30):
    item_id = _extrair_item_id(item_id or "") or str(item_id or "").strip().upper().replace("-", "")
    if not client_id or not item_id:
        return None
    dias = max(1, min(int(dias or 30), 150))
    ending = dt.datetime.now().strftime("%Y-%m-%d")
    chave_cache = f"favoritos_visitas:{client_id}:{item_id}:{dias}:{ending}"
    cached = _ml_cache_get(chave_cache, 900)
    if cached is not None:
        return cached
    try:
        lojas = carregar_lojas(client_id)
    except Exception:
        lojas = []

    lojas_oauth = []
    for loja in lojas or []:
        nome_loja = str((loja or {}).get("nome") or "").strip()
        integracoes = (loja or {}).get("integracoes") or {}
        cfg = dict(integracoes.get("mercadolivre") or {})
        if not nome_loja or not cfg.get("access_token"):
            continue
        cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
        cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
        lojas_oauth.append((nome_loja, cfg))

    def _consultar_visitas_loja(nome_loja, cfg):
        return _ml_api_request(
            client_id,
            nome_loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}/visits/time_window",
            params={"last": dias, "unit": "day", "ending": ending},
            timeout=12,
        )

    respostas_por_indice = {}
    if lojas_oauth:
        with ThreadPoolExecutor(max_workers=min(4, len(lojas_oauth))) as executor:
            futuros = {
                executor.submit(_consultar_visitas_loja, nome_loja, cfg): indice
                for indice, (nome_loja, cfg) in enumerate(lojas_oauth)
            }
            for futuro in as_completed(futuros):
                respostas_por_indice[futuros[futuro]] = futuro

    for indice, (nome_loja, _cfg) in enumerate(lojas_oauth):
        futuro = respostas_por_indice.get(indice)
        if futuro is None:
            continue
        try:
            resp, _cfg_atualizada = futuro.result()
            if resp.status_code == 200:
                data = resp.json() or {}
                total = _ml_total_visitas_payload(data)
                if total is not None:
                    payload = {"visitas": int(total), "fonte": "api_visitas", "loja": nome_loja}
                    _ml_cache_set(chave_cache, payload)
                    return payload
            logger.info("[Favoritos][Visitas] ML OAuth loja=%s item=%s status=%s", nome_loja, item_id, resp.status_code)
        except Exception as exc:
            logger.warning("[Favoritos][Visitas] Falha OAuth loja=%s item=%s: %s", nome_loja, item_id, exc)
    return None


def _ml_api_search(termo: str, limit: int = 50, offset: int = 0):
    params = {
        "q": termo,
        "limit": limit,
        "offset": offset
    }
    return _ml_api_get("https://api.mercadolibre.com/sites/MLB/search", params=params)


def _ml_api_search_paginated(termo: str, limit: int = 60) -> list[dict]:
    limite = max(1, min(int(limit or 60), 100))
    resultados: list[dict] = []
    vistos: set[str] = set()
    offset = 0

    while len(resultados) < limite:
        page_limit = min(50, limite - len(resultados))
        if page_limit <= 0:
            break
        api_data = _ml_api_search(termo, limit=page_limit, offset=offset) or {}
        pagina = api_data.get("results") or []
        if not pagina:
            break

        adicionados = 0
        for item in pagina:
            if not isinstance(item, dict):
                continue
            chave = str(item.get("id") or item.get("permalink") or "").strip()
            if chave and chave in vistos:
                continue
            if chave:
                vistos.add(chave)
            resultados.append(item)
            adicionados += 1
            if len(resultados) >= limite:
                break

        if len(pagina) < page_limit or adicionados <= 0:
            break
        offset += page_limit

    return resultados[:limite]


def _ml_parcelamento_sem_juros_api(item: dict | None):
    if not isinstance(item, dict):
        return None
    installments = item.get("installments") if isinstance(item.get("installments"), dict) else None
    if not installments:
        return None
    try:
        raw_rate = installments.get("rate", installments.get("interest_rate", installments.get("interestRate")))
        rate = float(str(raw_rate).replace("%", "").replace(",", ".")) if raw_rate not in (None, "") else None
    except Exception:
        rate = None
    try:
        quantity = int(float(str(installments.get("quantity") or installments.get("installments") or 0).replace(",", ".")))
    except Exception:
        quantity = 0
    if rate is not None:
        return rate == 0 and quantity > 1
    if installments.get("no_interest") is True or installments.get("noInterest") is True:
        return True
    return False


def _ml_parcelamento_sem_juros_texto(texto: str) -> bool:
    normalizado = unicodedata.normalize("NFKD", str(texto or ""))
    normalizado = "".join(c for c in normalizado if not unicodedata.combining(c)).lower()
    normalizado = re.sub(r"\s+", " ", normalizado)
    return bool(re.search(r"\bsem\s+juros\b|\b0\s*%?\s*de?\s*juros\b", normalizado))


def _ml_data_sort_key(valor):
    texto = str(valor or "").strip()
    if not texto:
        return float("inf")
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()
    except Exception:
        return texto


def _ml_primeira_pergunta_publica_data(item_id: str):
    item_id = _extrair_item_id(item_id) or str(item_id or "").strip().upper().replace("-", "")
    if not item_id:
        return None
    payload = _ml_api_get(
        "https://api.mercadolibre.com/questions/search",
        params={
            "item": item_id,
            "api_version": 4,
            "sort_fields": "date_created",
            "sort_types": "ASC",
            "limit": 50,
            "offset": 0,
        },
        max_retries=1,
        delay=0,
    )
    perguntas = []
    if isinstance(payload, dict):
        for key in ("questions", "results"):
            valores = payload.get(key)
            if isinstance(valores, list):
                perguntas.extend(v for v in valores if isinstance(v, dict))
    elif isinstance(payload, list):
        perguntas = [v for v in payload if isinstance(v, dict)]

    datas = []
    for pergunta in perguntas:
        data = _normalizar_data_ml(
            pergunta.get("date_created")
            or pergunta.get("dateCreated")
            or pergunta.get("created_at")
            or pergunta.get("createdAt")
            or pergunta.get("creation_date")
        )
        if data:
            datas.append(data)
    if not datas:
        return None
    datas.sort(key=_ml_data_sort_key)
    return datas[0]


def _ml_wayback_timestamp_iso(timestamp: str):
    digits = re.sub(r"\D+", "", str(timestamp or ""))
    if not re.fullmatch(r"\d{14}", digits):
        return None
    try:
        data = datetime.strptime(digits, "%Y%m%d%H%M%S")
        return data.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _ml_wayback_primeira_captura_data(item_id: str | None, url: str | None = None):
    item_id = _extrair_item_id(item_id or "") or _extrair_item_id(url or "")
    if item_id:
        url_pattern = f"https://produto.mercadolivre.com.br/{item_id.replace('MLB', 'MLB-')}-*"
    else:
        url_pattern = str(url or "").split("#", 1)[0].split("?", 1)[0].strip()
    if not url_pattern:
        return None
    try:
        resp = requests.get(
            "https://web.archive.org/cdx/search/cdx",
            params={
                "url": url_pattern,
                "output": "json",
                "fl": "timestamp,original,statuscode,mimetype",
                "filter": "statuscode:200",
                "limit": 1,
                "sort": "ascending",
            },
            headers={
                **_ml_headers(),
                "Accept": "application/json, text/plain, */*",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except Exception:
        logger.debug("[Favoritos][Datas] Falha ao consultar Wayback para %s", item_id or url, exc_info=True)
        return None

    if not isinstance(payload, list):
        return None
    for row in payload:
        if not isinstance(row, list) or not row:
            continue
        if re.search(r"timestamp", str(row[0] or ""), flags=re.IGNORECASE):
            continue
        data = _ml_wayback_timestamp_iso(str(row[0] or ""))
        if data:
            return data
    return None


def _ml_normalizar_data_cache_local(valor):
    texto = str(valor or "").strip()
    if not texto or texto in {"-", "null", "None"}:
        return None
    match_br = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", texto)
    if match_br:
        try:
            dia = int(match_br.group(1))
            mes = int(match_br.group(2))
            ano = int(match_br.group(3))
            if ano < 100:
                ano += 2000
            return dt.datetime(ano, mes, dia, 12, 0, 0).isoformat()
        except Exception:
            return None
    return _normalizar_data_ml(texto)


def _ml_data_criacao_por_imagem(valor):
    texto = str(valor or "")
    if not texto:
        return None
    patterns = [
        r"[_-]([01]\d)((?:20)\d{2})(?=[^0-9]|$)",
        r"[_-]((?:20)\d{2})([01]\d)(?=[^0-9]|$)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, texto):
            if pattern.startswith("[_-]([01]"):
                mes = int(match.group(1))
                ano = int(match.group(2))
            else:
                ano = int(match.group(1))
                mes = int(match.group(2))
            if 1 <= mes <= 12:
                try:
                    return dt.datetime(ano, mes, 15, 12, 0, 0).isoformat()
                except Exception:
                    continue
    return None


def _ml_datas_cache_local(client_id: str | None):
    client = str(client_id or "").strip()
    if not client:
        return {}
    tenant_path = get_tenant_path(client)
    datas = {}
    patterns = [
        "favoritos_historico_*.json",
        "favoritos_*.json",
        "favoritos_anuncios_*.json",
        "favoritos_anuncios_ignorados_*.json",
    ]

    def _registrar(item_id, data):
        item_id = _extrair_item_id(item_id or "")
        data = _ml_normalizar_data_cache_local(data)
        if not item_id or not data:
            return
        atual = datas.get(item_id)
        if not atual or _ml_data_sort_key(data) < _ml_data_sort_key(atual):
            datas[item_id] = data

    def _walk(obj, contador):
        if contador[0] > 80000:
            return
        contador[0] += 1
        if isinstance(obj, list):
            for value in obj:
                _walk(value, contador)
            return
        if not isinstance(obj, dict):
            return
        raw_id = (
            obj.get("id")
            or obj.get("item_id")
            or obj.get("itemId")
            or obj.get("mlb")
            or obj.get("codigo_ml")
            or obj.get("url")
        )
        data = (
            obj.get("data_criacao")
            or obj.get("dataCriacao")
            or obj.get("date_created")
            or obj.get("dateCreated")
            or obj.get("created_at")
            or obj.get("createdAt")
            or obj.get("createdDate")
            or obj.get("creation_date")
            or obj.get("listing_start_time")
            or obj.get("start_time")
        )
        _registrar(raw_id, data)
        for value in obj.values():
            if isinstance(value, (dict, list)):
                _walk(value, contador)

    try:
        nomes = os.listdir(tenant_path)
        filenames = sorted({
            filename
            for pattern in patterns
            for filename in fnmatch.filter(nomes, pattern)
        })
        for filename in filenames:
            caminho = os.path.join(tenant_path, filename)
            if not os.path.isfile(caminho):
                continue
            try:
                with open(caminho, "r", encoding="utf-8") as f:
                    _walk(json.load(f), [0])
            except Exception:
                logger.debug("[Favoritos][Datas] Falha ao ler cache local %s", caminho, exc_info=True)
    except Exception:
        logger.debug("[Favoritos][Datas] Falha ao varrer cache local tenant=%s", client, exc_info=True)
    return datas


def _ml_data_criacao_cache_local(client_id: str | None, item_id: str | None):
    item_id = _extrair_item_id(item_id or "")
    if not item_id:
        return None
    return _ml_datas_cache_local(client_id).get(item_id)

PEER_EXPORTS = ['_ml_favoritos_buscar_itens_por_sku', '_ml_favoritos_buscar_primeiros_itens_por_skus', '_ml_favoritos_listar_itens_ativos_loja', '_ml_favoritos_listar_todos_itens_ativos_loja', '_favoritos_ml_dividir_skus', '_favoritos_ml_imagem_item', '_favoritos_ml_url_item_id', '_favoritos_ml_resumo_anuncio_sku', '_favoritos_ml_skus_unicos_itens', '_favoritos_ml_garantir_sku_busca', '_ml_favoritos_mapear_itens_ativos_por_skus', '_ml_favoritos_extrair_texto_descricao', '_ml_favoritos_montar_descricao_por_item', '_ml_favoritos_obter_descricao_item', '_ml_favoritos_obter_descricao_item_rapida', '_favoritos_ml_float_close', '_favoritos_ml_preco_minimo_margem_simulado', '_favoritos_ml_preco_final_verificacao', '_favoritos_ml_margem_estimada', '_favoritos_ml_preco_contingencia_sem_promocao', '_favoritos_ml_verificacao_exige_contingencia_por_margem', '_favoritos_ml_falha_por_percentual_promocao', '_favoritos_ml_aplicar_contingencia_sem_promocao', '_favoritos_ml_texto_promocao', '_favoritos_ml_promocao_para_remocao', '_favoritos_ml_promocoes_remocao_fallback', '_favoritos_ml_remocao_max_attempts', '_favoritos_ml_textos_resposta_remocao', '_favoritos_ml_remocao_erro_transitorio', '_favoritos_ml_remocao_retry_delay', '_favoritos_ml_remover_promocoes_atuais', '_favoritos_ml_listing_type_id', '_favoritos_ml_nome_listing_type', '_favoritos_ml_resumir_estado_item', '_favoritos_ml_obter_estado_item', '_favoritos_ml_troca_listing_type_favoritos_suportada', '_favoritos_ml_listing_type_alvo_req', '_favoritos_ml_obter_listing_type_atual_e_disponiveis', '_favoritos_ml_validar_listing_type_disponivel', '_favoritos_ml_atualizar_tipo_listing_item', '_favoritos_ml_atualizar_preco_item', '_favoritos_ml_aguardar_preco_anuncio', '_favoritos_ml_verificar_efetivacao', '_favoritos_resolver_sku_para_margem', '_favoritos_aplicar_margem_anuncio_ml', '_ml_headers', '_ml_api_get', '_ml_api_item', '_ml_api_items_multiget_tenant', '_ml_api_item_com_oauth_tenant', '_ml_api_user', '_ml_api_user_com_oauth_tenant', '_ml_total_visitas_payload', '_ml_api_visitas_com_oauth_tenant', '_ml_api_search', '_ml_api_search_paginated', '_ml_parcelamento_sem_juros_api', '_ml_parcelamento_sem_juros_texto', '_ml_data_sort_key', '_ml_primeira_pergunta_publica_data', '_ml_wayback_timestamp_iso', '_ml_wayback_primeira_captura_data', '_ml_normalizar_data_cache_local', '_ml_data_criacao_por_imagem', '_ml_datas_cache_local', '_ml_data_criacao_cache_local']
PEER_EXPORTS.extend([
    '_favoritos_ml_preco_ideal_req',
    '_favoritos_ml_calcular_preco_cheio_centavos',
    '_favoritos_ml_invalidar_cache_promocoes_item',
    '_favoritos_ml_confirmar_sem_promocoes',
    '_favoritos_ml_preco_base_autoritativo',
])
__all__ = PEER_EXPORTS + ["configure_favoritos_ml_runtime"]

configure_favoritos_ml_runtime()
