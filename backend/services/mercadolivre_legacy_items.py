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


def configure_mercadolivre_legacy_items_runtime(runtime_module=None, peers=None):
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


configure_mercadolivre_legacy_items_runtime()


def _ml_retry_after_seconds(resp, padrao: float = 2.0) -> float:
    try:
        raw = (getattr(resp, "headers", {}) or {}).get("Retry-After")
        if raw:
            return max(1.0, min(float(str(raw).strip()), 12.0))
    except Exception:
        pass
    return float(padrao)


def _ml_extrair_item_condition(item: dict) -> str:
    for attr in item.get("attributes", []) or []:
        attr_id = (attr.get("id") or "").upper()
        if attr_id in ("ITEM_CONDITION", "CONDITION"):
            return attr.get("value_name") or attr.get("value_id") or ""
    return item.get("condition") or ""


def _ml_extrair_sku(item: dict) -> str:
    campos_sku_diretos = (
        "seller_sku",
        "sellerSku",
        "SELLER_SKU",
        "sku",
        "SKU",
        "seller_custom_field",
        "sellerCustomField",
        "SELLER_CUSTOM_FIELD",
        "custom_sku",
        "item_sku",
    )

    def _pick_campo_sku(obj: dict) -> str:
        if not isinstance(obj, dict):
            return ""
        for campo in campos_sku_diretos:
            valor = obj.get(campo)
            if valor is not None and str(valor).strip():
                return str(valor).strip()
        return ""

    def _attr_valores(attr: dict) -> list[str]:
        valores = []
        if not isinstance(attr, dict):
            return valores
        for chave in ("value_name", "value_id", "value"):
            valor = attr.get(chave)
            if valor is not None and str(valor).strip():
                valores.append(str(valor).strip())
        for valor_item in attr.get("values") or []:
            if not isinstance(valor_item, dict):
                continue
            for chave in ("name", "value_name", "value_id", "value"):
                valor = valor_item.get(chave)
                if valor is not None and str(valor).strip():
                    valores.append(str(valor).strip())
        return valores

    def _pick_attr_sku(attr_list) -> str:
        prioridade = ["SELLER_SKU", "SKU", "SELLER_CUSTOM_FIELD"]
        valores = {}
        for attr in attr_list or []:
            if not isinstance(attr, dict):
                continue
            attr_id = (attr.get("id") or "").upper().strip()
            attr_nome = normalizar_texto(attr.get("name") or "").upper().strip()
            chave_match = ""
            for chave in prioridade:
                if attr_id == chave or attr_nome == chave or attr_nome.replace(" ", "_") == chave:
                    chave_match = chave
                    break
            if not chave_match and ("SKU" in attr_id or attr_nome in {"SKU", "SELLER SKU"}):
                chave_match = "SKU"
            if not chave_match:
                continue
            for valor in _attr_valores(attr):
                if valor:
                    valores[chave_match] = str(valor).strip()
                    break
        for chave in prioridade:
            if valores.get(chave):
                return valores[chave]
        return ""

    sku = _pick_attr_sku(item.get("attributes", [])) or _pick_campo_sku(item)
    variations = item.get("variations", []) or []
    skus = []

    for var in variations:
        var_sku = (
            _pick_attr_sku(var.get("attributes", []))
            or _pick_attr_sku(var.get("attribute_combinations", []))
            or _pick_campo_sku(var)
        )
        if var_sku:
            skus.append(str(var_sku).strip())

    if skus:
        sku = ", ".join(dict.fromkeys(skus))

    return str(sku or "").strip()


def _ml_favoritos_variacao_tem_sku(var: dict) -> bool:
    if not isinstance(var, dict):
        return False
    return bool(
        _ml_extrair_sku(var)
        or str(var.get("seller_sku") or "").strip()
        or str(var.get("sellerSku") or "").strip()
        or str(var.get("seller_custom_field") or "").strip()
        or str(var.get("sellerCustomField") or "").strip()
    )


def _ml_favoritos_item_precisa_variacoes_detalhadas(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    variacoes = item.get("variations") or []
    if not isinstance(variacoes, list) or not variacoes:
        return False
    for var in variacoes:
        if not isinstance(var, dict) or not _ml_favoritos_variacao_tem_sku(var):
            return True
    return False


def _ml_favoritos_completar_variacoes_item(client_id: str, loja: str, cfg: dict, item: dict) -> dict:
    """Completa dados das variaÃ§Ãµes quando o batch de itens nÃ£o traz seller_custom_field/SKU."""
    if not _ml_favoritos_item_precisa_variacoes_detalhadas(item):
        return item
    item_id = str((item or {}).get("id") or "").strip()
    if not item_id:
        return item

    try:
        resp, _cfg_local = _ml_favoritos_api_request(
            client_id,
            loja,
            dict(cfg or {}),
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}/variations",
            params={"include_attributes": "all"},
            timeout=15,
        )
        if resp.status_code != 200:
            return item
        detalhes = resp.json() or []
        if isinstance(detalhes, dict):
            detalhes = detalhes.get("variations") or detalhes.get("results") or []
        if not isinstance(detalhes, list) or not detalhes:
            return item

        detalhes_por_id = {
            str((var or {}).get("id") or "").strip(): var
            for var in detalhes
            if isinstance(var, dict) and str(var.get("id") or "").strip()
        }
        if not detalhes_por_id:
            return item

        variacoes_completas = []
        for var in item.get("variations") or []:
            if not isinstance(var, dict):
                variacoes_completas.append(var)
                continue
            var_id = str(var.get("id") or "").strip()
            detalhe = detalhes_por_id.get(var_id) or {}
            if detalhe:
                mesclada = dict(detalhe)
                mesclada.update({k: v for k, v in var.items() if v not in (None, "", [])})
                if not mesclada.get("attributes") and detalhe.get("attributes"):
                    mesclada["attributes"] = detalhe.get("attributes")
                if not mesclada.get("attribute_combinations") and detalhe.get("attribute_combinations"):
                    mesclada["attribute_combinations"] = detalhe.get("attribute_combinations")
                variacoes_completas.append(mesclada)
            else:
                variacoes_completas.append(var)
        item = dict(item)
        item["variations"] = variacoes_completas
    except Exception as exc:
        logger.debug("[Favoritos ML] Nao foi possivel completar variacoes do item %s: %s", item_id, exc)
    return item


def _normalizar_sku_compacto_favoritos(sku: str) -> str:
    """Normaliza SKU para comparacao tolerante (remove separadores nÃ£o alfanumÃ©ricos)."""
    return re.sub(r"[^A-Z0-9]", "", _normalizar_sku_match_favoritos(sku).upper())


def _ml_favoritos_variantes_busca_sku(sku: str) -> list[str]:
    """Gera variantes Ãºteis para buscar o SKU no Mercado Livre."""
    variantes: list[str] = []

    def _add(valor: str):
        txt = str(valor or "").strip()
        if not txt or txt in variantes:
            return
        variantes.append(txt)

    sku_bruto = str(sku or "").strip()
    if not sku_bruto:
        return variantes

    sku_norm = _normalizar_sku_match_favoritos(sku_bruto)
    sku_compacto = _normalizar_sku_compacto_favoritos(sku_bruto)

    _add(sku_bruto)
    _add(sku_norm)
    _add(sku_compacto)

    # Remover separadores para aumentar chance de casar com seller_sku gravado no ML.
    _add(re.sub(r"[^A-Za-z0-9]", "", sku_bruto))
    if sku_compacto:
        if sku_compacto != sku_bruto:
            _add(sku_compacto)
        sem_zero = sku_compacto.lstrip("0")
        if sem_zero:
            _add(sem_zero)
            _add(_normalizar_sku_match_favoritos(sem_zero))
    return variantes


def _ml_favoritos_extrair_skus_item(item: dict) -> set[str]:
    """Extrai todos os SKUs possÃ­veis de um item do ML, incluindo variaÃ§Ãµes."""
    if not isinstance(item, dict):
        return set()

    skus: set[str] = set()
    campos_sku_preferenciais = (
        "seller_sku",
        "sellerSku",
        "SELLER_SKU",
        "sku",
        "SKU",
        "custom_sku",
        "item_sku",
    )

    def _registrar(valor: object):
        if valor is None:
            return
        texto = str(valor).strip()
        if not texto:
            return
        for parte in re.split(r"[,;/|]", texto):
            token = str(parte or "").strip().strip(",;/|")
            if token:
                skus.add(token)
        if " " in texto and len(texto) <= 80 and " " not in "".join(re.findall(r"[A-Za-z0-9]", texto)):
            for parte in texto.split():
                token = str(parte or "").strip().strip(",;/|")
                if token:
                    skus.add(token)

    _registrar(_ml_extrair_sku(item))
    for campo in campos_sku_preferenciais:
        _registrar(item.get(campo))
    for var in item.get("variations", []) or []:
        if not isinstance(var, dict):
            continue
        _registrar(_ml_extrair_sku(var))
        for campo in campos_sku_preferenciais:
            _registrar(var.get(campo))
    variations_data = item.get("variations_data")
    if isinstance(variations_data, dict):
        for var_data in variations_data.values():
            if not isinstance(var_data, dict):
                continue
            _registrar(_ml_extrair_sku(var_data))
            for campo in campos_sku_preferenciais:
                _registrar(var_data.get(campo))

    return skus


def _ml_favoritos_item_corresponde_sku(item: dict, sku_alvo: str) -> bool:
    """Valida se o anÃºncio do ML pertence ao SKU informado."""
    sku_alvo = str(sku_alvo or "").strip()
    if not sku_alvo:
        return True

    alvo_norm = _normalizar_sku_match_favoritos(sku_alvo)
    alvo_compacto = _normalizar_sku_compacto_favoritos(alvo_norm)
    if not alvo_norm:
        return False

    for sku_raw in _ml_favoritos_extrair_skus_item(item):
        sku_norm = _normalizar_sku_match_favoritos(sku_raw)
        sku_comp = _normalizar_sku_compacto_favoritos(sku_norm)
        if not sku_norm and not sku_comp:
            continue
        if alvo_norm and alvo_norm == sku_norm:
            return True
        if alvo_compacto and sku_comp and (alvo_compacto == sku_comp):
            return True

    titulo = str(item.get("title") or "").strip()
    if alvo_compacto and len(alvo_compacto) >= 4 and titulo:
        titulo_compacto = re.sub(r"[^A-Z0-9]", "", normalizar_texto(titulo).upper())
        if alvo_compacto in titulo_compacto:
            return True

    return False


def _ml_favoritos_buscar_itens_batch(client_id: str, loja: str, cfg: dict, item_ids: list[str]):
    itens = []
    if not item_ids:
        return itens, cfg

    item_ids = [
        str(item_id or "").strip().upper()
        for item_id in item_ids
        if str(item_id or "").strip().upper().startswith("MLB")
    ]
    item_ids = list(dict.fromkeys(item_ids))
    if not item_ids:
        return itens, cfg

    batch_size = 20
    lotes = [item_ids[inicio:inicio + batch_size] for inicio in range(0, len(item_ids), batch_size)]
    try:
        max_workers = int(float(str(os.getenv("ML_FAVORITOS_BATCH_WORKERS", "8") or "8").replace(",", ".")))
    except Exception:
        max_workers = 8
    max_workers = min(max(1, max_workers), max(1, len(lotes)))

    def _body_item_valido(body: Any) -> bool:
        if not isinstance(body, dict):
            return False
        item_id = str(body.get("id") or "").strip().upper()
        if not item_id.startswith("MLB"):
            return False
        status_raw = str(body.get("status") or "").strip()
        if status_raw.isdigit():
            return False
        if body.get("error") or body.get("message") == "forbidden":
            return False
        return True

    def _buscar_lote(lote_ids: list[str]):
        itens_lote = []
        encontrados = set()
        cfg_local = dict(cfg or {})
        try:
            resp, cfg_local = _ml_favoritos_api_request(
                client_id,
                loja,
                cfg_local,
                "GET",
                "https://api.mercadolibre.com/items",
                params={"ids": ",".join(lote_ids), "include_attributes": "all"},
                timeout=20,
            )
            if resp.status_code == 200:
                for entry in resp.json() or []:
                    body = (entry or {}).get("body") or {}
                    code = int((entry or {}).get("code") or 0) if isinstance(entry, dict) else 0
                    if code == 200 and _body_item_valido(body):
                        body = _ml_favoritos_completar_variacoes_item(client_id, loja, cfg_local, body)
                        itens_lote.append(body)
                        encontrados.add(str(body.get("id") or "").strip())
        except Exception as e:
            logger.warning(f"[Favoritos ML] Falha no lote de itens {lote_ids[:2]}...: {e}")

        faltantes = [item_id for item_id in lote_ids if item_id not in encontrados]

        def _buscar_item_direto(item_id: str):
            cfg_item = dict(cfg_local or {})
            try:
                det_url = f"https://api.mercadolibre.com/items/{item_id}"
                det_resp, cfg_item = _ml_favoritos_api_request(
                    client_id,
                    loja,
                    cfg_item,
                    "GET",
                    det_url,
                    params={"include_attributes": "all"},
                    timeout=14,
                )
                if det_resp.status_code == 200:
                    body = det_resp.json() or {}
                    if _body_item_valido(body):
                        body = _ml_favoritos_completar_variacoes_item(client_id, loja, cfg_local, body)
                        return body
                else:
                    logger.warning(
                        "[Favoritos ML] Item %s nao carregou na consulta direta da loja %s: HTTP %s %s",
                        item_id,
                        loja,
                        det_resp.status_code,
                        _ml_parse_error_detail(det_resp, "Erro ao buscar item"),
                    )
            except Exception as e:
                logger.warning(f"[Favoritos ML] Falha ao buscar item {item_id}: {e}")
            return None

        if len(faltantes) <= 1:
            for item_id in faltantes:
                body = _buscar_item_direto(item_id)
                if body:
                    itens_lote.append(body)
        else:
            max_workers_direto = min(6, len(faltantes))
            with ThreadPoolExecutor(max_workers=max_workers_direto) as executor:
                futuros_diretos = [executor.submit(_buscar_item_direto, item_id) for item_id in faltantes]
                for futuro in as_completed(futuros_diretos):
                    try:
                        body = futuro.result()
                        if body:
                            itens_lote.append(body)
                    except Exception as exc:
                        logger.warning("[Favoritos ML] Falha em busca direta paralela de item: %s", exc)

        return itens_lote

    if max_workers <= 1:
        for lote in lotes:
            itens.extend(_buscar_lote(lote))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros = [executor.submit(_buscar_lote, lote) for lote in lotes]
            for futuro in as_completed(futuros):
                try:
                    itens.extend(futuro.result() or [])
                except Exception as e:
                    logger.warning(f"[Favoritos ML] Falha em execucao paralela de lote: {e}")

    itens_por_id = {str(item.get("id")): item for item in itens if isinstance(item, dict) and item.get("id")}
    itens_ordenados = [itens_por_id[item_id] for item_id in item_ids if item_id in itens_por_id]
    return itens_ordenados, cfg


def _ml_carregar_produtos_locais(client_id: str, loja: str) -> list[dict]:
    cache_key = f"{client_id}:{loja}"
    cached = _cache_get(ML_LOCAL_PRODUCTS_CACHE, cache_key, ML_LOCAL_PRODUCTS_CACHE_TTL)
    if cached is not None:
        return cached

    candidatos = [
        os.path.join(get_tenant_path(client_id), "produtos_compilado.csv"),
        os.path.join(PASTA_INFO, "default", "produtos_compilado.csv"),
    ]
    registros = []
    for caminho in candidatos:
        if not os.path.exists(caminho):
            continue
        try:
            df = pd.read_csv(caminho)
            if "loja_sync" in df.columns:
                df = df[df["loja_sync"].astype(str).str.strip().str.lower() == str(loja).strip().lower()]
            if df.empty:
                continue
            for _, row in df.iterrows():
                sku = str(row.get("sku", "") or "").strip()
                nome = str(row.get("nome_bling", "") or "").strip()
                if sku and nome:
                    registros.append({
                        "sku": sku,
                        "nome": nome,
                        "nome_norm": normalizar_texto(nome),
                    })
            if registros:
                break
        except Exception:
            continue

    _cache_set(ML_LOCAL_PRODUCTS_CACHE, cache_key, registros)
    return registros


def _ml_buscar_sku_variacao_local(client_id: str, loja: str, titulo_item: str, titulo_variacao: str) -> str:
    produtos = _ml_carregar_produtos_locais(client_id, loja)
    if not produtos:
        return ""

    item_norm = normalizar_texto(titulo_item)
    var_norm = normalizar_texto(titulo_variacao)
    tokens_item = [t for t in re.findall(r"[a-z0-9]+", item_norm) if len(t) >= 4][:6]
    tokens_var = [t for t in re.findall(r"[a-z0-9]+", var_norm) if len(t) >= 2]

    melhor_sku = ""
    melhor_score = 0
    for prod in produtos:
        nome_norm = prod.get("nome_norm", "")
        score = 0
        for tok in tokens_item:
            if tok in nome_norm:
                score += 2
        for tok in tokens_var:
            if tok in nome_norm:
                score += 4
        if score > melhor_score:
            melhor_score = score
            melhor_sku = str(prod.get("sku", "") or "").strip()

    return melhor_sku if melhor_score >= 6 else ""


def _ml_extrair_variacoes_resumo(item: dict, client_id: str = "", loja: str = "") -> list[dict]:
    """Monta um resumo amigÃƒÂ¡vel das variaÃƒÂ§ÃƒÂµes para mostrar abaixo do anuncio pai."""
    def _normalizar_sufixo_variacao(txt: str) -> str:
        base = str(txt or "").strip().upper()
        base = re.sub(r"\s+", "-", base)
        base = re.sub(r"[^A-Z0-9\-_.]+", "", base)
        return base.strip("-_.")

    item_base = dict(item or {})
    item_base["variations"] = []
    sku_pai = _ml_extrair_sku(item_base) or str(item.get("seller_custom_field") or "").strip()

    variacoes = []
    for idx, var in enumerate(item.get("variations", []) or [], start=1):
        combinacoes = []
        valores_combo = []
        for attr in (var.get("attribute_combinations") or []):
            nome = attr.get("name") or attr.get("id") or ""
            valor = attr.get("value_name") or attr.get("value_id") or ""
            texto = f"{nome}: {valor}".strip(': ')
            if texto:
                combinacoes.append(texto)
            if valor:
                valores_combo.append(_normalizar_sufixo_variacao(valor))
        titulo_variacao = " Ã¢â‚¬Â¢ ".join(combinacoes) if combinacoes else f"VariaÃƒÂ§ÃƒÂ£o {idx}"
        sku_variacao = _ml_extrair_sku(var) or str(var.get("seller_custom_field") or "").strip()
        if not sku_variacao and client_id and loja:
            sku_variacao = _ml_buscar_sku_variacao_local(client_id, loja, str(item.get("title") or ""), titulo_variacao)
        if not sku_variacao and sku_pai:
            sufixo = "-".join([v for v in valores_combo if v])
            sku_variacao = f"{sku_pai}-{sufixo}" if sufixo else sku_pai
        if not sku_variacao:
            sku_variacao = str(var.get("id") or "-")

        variacoes.append({
            "id": var.get("id"),
            "title": titulo_variacao,
            "sku": sku_variacao,
            "parent_sku": sku_pai or "-",
            "inventory_id": str(var.get("inventory_id") or "").strip(),
            "available_quantity": var.get("available_quantity", 0),
            "sold_quantity": var.get("sold_quantity", 0),
            "price": var.get("price"),
            "picture_id": var.get("picture_id"),
        })
    return variacoes


def _cache_get(cache: dict, key: str, ttl_seconds: int):
    item = cache.get(key)
    if not item:
        return None
    ts, value = item
    if (time.time() - ts) > ttl_seconds:
        cache.pop(key, None)
        return None
    return dict(value) if isinstance(value, dict) else value


def _cache_set(cache: dict, key: str, value):
    cache[key] = (time.time(), dict(value) if isinstance(value, dict) else value)


def _backend_cache_get(key: str):
    key_norm = str(key or "").strip()
    if not key_norm:
        return None
    with BACKEND_READ_CACHE_LOCK:
        item = BACKEND_READ_CACHE.get(key_norm)
        if not item:
            return None
        expires_at, value = item
        if time.time() >= float(expires_at or 0):
            BACKEND_READ_CACHE.pop(key_norm, None)
            return None
        try:
            return copy.deepcopy(value)
        except Exception:
            return value


def _backend_cache_set(key: str, value: Any, ttl_seconds: int = 60) -> None:
    key_norm = str(key or "").strip()
    if not key_norm:
        return
    ttl = max(1, int(ttl_seconds or 60))
    with BACKEND_READ_CACHE_LOCK:
        try:
            BACKEND_READ_CACHE[key_norm] = (time.time() + ttl, copy.deepcopy(value))
        except Exception:
            BACKEND_READ_CACHE[key_norm] = (time.time() + ttl, value)


def _backend_cache_invalidate_prefix(prefix: str) -> None:
    prefix_norm = str(prefix or "").strip()
    if not prefix_norm:
        return
    with BACKEND_READ_CACHE_LOCK:
        for key in list(BACKEND_READ_CACHE.keys()):
            if str(key).startswith(prefix_norm):
                BACKEND_READ_CACHE.pop(key, None)


def _backend_cache_invalidate_user_views(client_id: str = "") -> None:
    client_norm = str(client_id or "").strip()
    _backend_cache_invalidate_prefix("admin-users:")
    _backend_cache_invalidate_prefix("chat-contacts-users:")
    if client_norm:
        _backend_cache_invalidate_prefix(f"admin-users:{client_norm}:")
        _backend_cache_invalidate_prefix(f"chat-contacts-users:{client_norm}:")


def _ml_nome_tipo_anuncio(listing_type_id: str) -> str:
    key = str(listing_type_id or '').strip().lower()
    mapa = {
        'gold_pro': 'Premium',
        'gold_special': 'ClÃƒÂ¡ssico',
        'gold': 'ClÃƒÂ¡ssico',
        'silver': 'ClÃƒÂ¡ssico',
        'bronze': 'ClÃƒÂ¡ssico',
        'free': 'Gratuito',
    }
    return mapa.get(key, key.replace('_', ' ').title() if key else '-')


def _ml_buscar_itens_batch(
    client_id: str,
    loja: str,
    cfg: dict,
    item_ids: list[str],
    progress_callback: Optional[Callable[[str], None]] = None,
):
    itens = []
    if not item_ids:
        return itens, cfg

    batch_size = 20
    lotes = [item_ids[inicio:inicio + batch_size] for inicio in range(0, len(item_ids), batch_size)]
    max_workers = min(4, max(1, len(lotes)))

    def _buscar_lote(lote_ids: list[str]):
        itens_lote = []
        encontrados = set()
        cfg_local = dict(cfg or {})
        try:
            resp, cfg_local = _ml_api_request_com_retry(
                client_id,
                loja,
                cfg_local,
                "GET",
                "https://api.mercadolibre.com/items",
                params={"ids": ",".join(lote_ids)},
                timeout=35,
                max_attempts=3,
                progress_callback=progress_callback,
            )
            if resp.status_code == 200:
                for entry in resp.json() or []:
                    body = (entry or {}).get("body") or {}
                    if isinstance(body, dict) and body.get("id"):
                        itens_lote.append(body)
                        encontrados.add(str(body.get("id") or "").strip())
        except Exception as e:
            logger.warning(f"[ML API] Falha no lote de itens {lote_ids[:2]}...: {e}")

        faltantes = [item_id for item_id in lote_ids if item_id not in encontrados]
        for item_id in faltantes:
            try:
                det_url = f"https://api.mercadolibre.com/items/{item_id}"
                det_resp, cfg_local = _ml_api_request_com_retry(
                    client_id,
                    loja,
                    cfg_local,
                    "GET",
                    det_url,
                    timeout=25,
                    max_attempts=3,
                    progress_callback=progress_callback,
                )
                if det_resp.status_code == 200:
                    body = det_resp.json() or {}
                    if isinstance(body, dict) and body.get("id"):
                        itens_lote.append(body)
            except Exception as e:
                logger.warning(f"[ML API] Falha ao buscar item {item_id}: {e}")

        return itens_lote

    if max_workers <= 1:
        for lote in lotes:
            itens.extend(_buscar_lote(lote))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros = [executor.submit(_buscar_lote, lote) for lote in lotes]
            for futuro in as_completed(futuros):
                try:
                    itens.extend(futuro.result() or [])
                except Exception as e:
                    logger.warning(f"[ML API] Falha em execuÃƒÂ§ÃƒÂ£o paralela de lote: {e}")

    itens_por_id = {str(item.get('id')): item for item in itens if isinstance(item, dict) and item.get('id')}
    itens_ordenados = [itens_por_id[item_id] for item_id in item_ids if item_id in itens_por_id]
    return itens_ordenados, cfg


def _ml_montar_detalhe_anuncio_listagem(client_id: str, loja: str, cfg: dict, item: dict, sku_filtro_lower: str = ""):
    item_id = item.get("id")
    if not item_id:
        return None

    sku_item = _ml_extrair_sku(item)
    titulo = str(item.get("title") or "")

    # Calcula variaÃƒÂ§ÃƒÂµes primeiro para ter SKUs reais antes de aplicar o filtro
    variacoes_resumo = _ml_extrair_variacoes_resumo(item, client_id=client_id, loja=loja)
    sku_pai_exibicao = sku_item or "-"
    if variacoes_resumo:
        skus_variacoes = [str(v.get("sku") or "").strip() for v in variacoes_resumo if str(v.get("sku") or "").strip() and str(v.get("sku") or "").strip() != "-"]
        if skus_variacoes:
            sku_pai_exibicao = " / ".join(list(dict.fromkeys(skus_variacoes))[:4])
        else:
            sku_pai_exibicao = f"{sku_pai_exibicao} (pai)" if sku_pai_exibicao and sku_pai_exibicao != "-" else "Ver variaÃƒÂ§ÃƒÂµes"

    # Aplica filtro apÃƒÂ³s calcular todos os SKUs (item, variaÃƒÂ§ÃƒÂµes e display)
    if sku_filtro_lower:
        todos_skus = (sku_item + " " + sku_pai_exibicao).lower()
        skus_variacoes_todos = " ".join(str(v.get("sku") or "") for v in variacoes_resumo).lower()
        if (sku_filtro_lower not in todos_skus
                and sku_filtro_lower not in skus_variacoes_todos
                and sku_filtro_lower not in titulo.lower()):
            return None

    deal_ids = item.get("deal_ids", []) or []
    price_info = _ml_montar_preco_listagem(item)
    promotion_ids = [str(deal_id).strip() for deal_id in deal_ids if str(deal_id).strip()]
    shipping_data, _ = _ml_obter_frete_detalhado(client_id, loja, cfg, item_id, item.get("shipping") or {})
    fee_data, _ = _ml_obter_taxas_anuncio(client_id, loja, cfg, item)
    item_condition = _ml_extrair_item_condition(item)

    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "thumbnail": item.get("secure_thumbnail") or item.get("thumbnail") or ((item.get("pictures") or [{}])[0].get("secure_url") if (item.get("pictures") or []) else None),
        "family_name": item.get("family_name"),
        "user_product_id": item.get("user_product_id"),
        "catalog_listing": bool(item.get("catalog_listing")),
        "catalog_product_id": item.get("catalog_product_id"),
        "listing_type_id": item.get("listing_type_id"),
        "listing_type_name": fee_data.get("listing_type_name") or _ml_nome_tipo_anuncio(item.get("listing_type_id")),
        "item_condition": item_condition,
        "price": price_info.get("price"),
        "standard_price": price_info.get("standard_price"),
        "original_price": price_info.get("original_price"),
        "price_source": price_info.get("price_source"),
        "shipping_cost": shipping_data.get("shipping_cost"),
        "shipping_text": shipping_data.get("shipping_text"),
        "shipping_buyer_cost": shipping_data.get("shipping_buyer_cost"),
        "shipping_buyer_text": shipping_data.get("shipping_buyer_text"),
        "shipping_list_cost": shipping_data.get("shipping_list_cost"),
        "shipping_base_cost": shipping_data.get("shipping_base_cost"),
        "shipping_breakdown": shipping_data.get("shipping_breakdown"),
        "ad_cost": fee_data.get("ad_cost"),
        "ad_cost_text": fee_data.get("ad_cost_text"),
        "fixed_fee_amount": fee_data.get("fixed_fee_amount"),
        "fixed_fee_text": fee_data.get("fixed_fee_text"),
        "listing_fee_amount": fee_data.get("listing_fee_amount"),
        "listing_fee_text": fee_data.get("listing_fee_text"),
        "sale_fee_pct": fee_data.get("sale_fee_pct"),
        "fee_breakdown": fee_data.get("fee_breakdown"),
        "free_shipping": shipping_data.get("free_shipping"),
        "logistic_type": shipping_data.get("logistic_type"),
        "shipping_mode": shipping_data.get("shipping_mode"),
        "shipping_zip": shipping_data.get("shipping_zip"),
        "available_quantity": item.get("available_quantity"),
        "sold_quantity": item.get("sold_quantity"),
        "status": item.get("status"),
        "sku": sku_item or "N/D",
        "sku_display": sku_pai_exibicao,
        "has_promotion": bool(price_info.get("has_promotion") or deal_ids or promotion_ids),
        "discount_pct": price_info.get("discount_pct", 0),
        "deal_ids": promotion_ids or deal_ids,
        "promotion_id": price_info.get("promotion_id") or (promotion_ids[0] if promotion_ids else None),
        "promotion_type": price_info.get("promotion_type"),
        "channels": item.get("channels", []),
        "permalink": item.get("permalink") or f"https://produto.mercadolivre.com.br/{item.get('id', '')}",
        "has_variations": len(variacoes_resumo) > 0,
        "variations": variacoes_resumo,
    }


ML_FRETE_GRADE_OFICIAL = [
    ("AtÃƒÂ© 0,3 kg",   [5.65, 6.55, 7.75, 12.35, 14.35, 16.45, 18.45, 20.95]),
    ("De 0,3 a 0,5 kg", [5.95, 6.65, 7.85, 13.25, 15.45, 17.65, 19.85, 22.55]),
    ("De 0,5 a 1 kg",   [6.05, 6.75, 7.95, 13.85, 16.15, 18.45, 20.75, 23.65]),
    ("De 1 a 1,5 kg",   [6.15, 6.85, 8.05, 14.15, 16.45, 18.85, 21.15, 24.65]),
    ("De 1,5 a 2 kg",   [6.25, 6.95, 8.15, 14.45, 16.85, 19.25, 21.65, 24.65]),
    ("De 2 a 3 kg",     [6.35, 7.95, 8.55, 15.75, 18.35, 21.05, 23.65, 26.25]),
    ("De 3 a 4 kg",     [6.45, 8.15, 8.95, 17.05, 19.85, 22.65, 25.55, 28.35]),
    ("De 4 a 5 kg",     [6.55, 8.35, 9.75, 18.45, 21.55, 24.65, 27.75, 30.75]),
    ("De 5 a 6 kg",     [6.65, 8.55, 9.95, 25.45, 28.55, 32.65, 35.75, 39.75]),
    ("De 6 a 7 kg",     [6.75, 8.75, 10.15, 27.05, 31.05, 36.05, 40.05, 44.05]),
    ("De 7 a 8 kg",     [6.85, 8.95, 10.35, 28.85, 33.65, 38.45, 43.25, 48.05]),
    ("De 8 a 9 kg",     [6.95, 9.15, 10.55, 29.65, 34.55, 39.55, 44.45, 49.35]),
    ("De 9 a 11 kg",    [7.05, 9.55, 10.95, 41.25, 48.05, 54.95, 61.75, 68.65]),
    ("De 11 a 13 kg",   [7.15, 9.95, 11.35, 42.15, 49.25, 56.25, 63.25, 70.25]),
    ("De 13 a 15 kg",   [7.25, 10.15, 11.55, 45.05, 52.45, 59.95, 67.45, 74.95]),
    ("De 15 a 17 kg",   [7.35, 10.35, 11.75, 48.55, 56.05, 63.55, 70.75, 78.65]),
    ("De 17 a 20 kg",   [7.45, 10.55, 11.95, 54.75, 63.85, 72.95, 82.05, 91.15]),
    ("De 20 a 25 kg",   [7.65, 10.95, 12.15, 64.05, 75.05, 84.75, 95.35, 105.95]),
    ("De 25 a 30 kg",   [7.75, 11.15, 12.35, 65.95, 75.45, 85.55, 96.25, 106.95]),
    ("De 30 a 40 kg",   [7.85, 11.35, 12.55, 67.75, 78.95, 88.95, 99.15, 107.05]),
    ("De 40 a 50 kg",   [7.95, 11.55, 12.75, 70.25, 81.05, 92.05, 102.55, 110.75]),
    ("De 50 a 60 kg",   [8.05, 11.75, 12.95, 74.95, 86.45, 98.15, 109.35, 118.15]),
    ("De 60 a 70 kg",   [8.15, 11.95, 13.15, 80.25, 92.95, 105.05, 117.15, 126.55]),
    ("De 70 a 80 kg",   [8.25, 12.15, 13.35, 83.95, 97.05, 109.85, 122.45, 132.25]),
    ("De 80 a 90 kg",   [8.35, 12.35, 13.55, 93.25, 107.45, 122.05, 136.05, 146.95]),
    ("De 90 a 100 kg",  [8.45, 12.55, 13.75, 106.55, 123.95, 139.55, 155.55, 167.95]),
    ("De 100 a 125 kg", [8.55, 12.75, 13.95, 119.25, 138.05, 156.05, 173.95, 187.95]),
    ("De 125 a 150 kg", [8.65, 12.75, 14.15, 126.55, 146.15, 165.65, 184.65, 199.45]),
    ("Mais de 150 kg",  [8.75, 12.95, 14.35, 166.15, 192.45, 217.55, 242.55, 261.95]),
]

PEER_EXPORTS = ['_ml_retry_after_seconds', '_ml_extrair_item_condition', '_ml_extrair_sku', '_ml_favoritos_variacao_tem_sku', '_ml_favoritos_item_precisa_variacoes_detalhadas', '_ml_favoritos_completar_variacoes_item', '_normalizar_sku_compacto_favoritos', '_ml_favoritos_variantes_busca_sku', '_ml_favoritos_extrair_skus_item', '_ml_favoritos_item_corresponde_sku', '_ml_favoritos_buscar_itens_batch', '_ml_carregar_produtos_locais', '_ml_buscar_sku_variacao_local', '_ml_extrair_variacoes_resumo', '_cache_get', '_cache_set', '_backend_cache_get', '_backend_cache_set', '_backend_cache_invalidate_prefix', '_backend_cache_invalidate_user_views', '_ml_nome_tipo_anuncio', '_ml_buscar_itens_batch', '_ml_montar_detalhe_anuncio_listagem', 'ML_FRETE_GRADE_OFICIAL']
__all__ = PEER_EXPORTS + ["configure_mercadolivre_legacy_items_runtime"]

configure_mercadolivre_legacy_items_runtime()
