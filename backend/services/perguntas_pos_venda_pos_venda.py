"""Internal slice for perguntas_pos_venda_core."""

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
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake


def configure_perguntas_pos_venda_pos_venda_runtime(runtime_module=None, peers=None):
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


configure_perguntas_pos_venda_pos_venda_runtime()


def _ml_pos_venda_anexar_perguntas_anuncio_comprador(
    client_id: str,
    loja: str,
    cfg: dict,
    seller_id: str,
    conversa: dict,
) -> tuple[dict, dict]:
    conversa = conversa if isinstance(conversa, dict) else {}
    buyer_id = str(conversa.get("buyer_id") or "").strip()
    if not buyer_id:
        return conversa, cfg
    itens = [item for item in (conversa.get("items") or []) if isinstance(item, dict)]
    item_ids = []
    vistos = set()
    for item in itens:
        item_id = str(item.get("id") or item.get("item_id") or "").strip()
        if item_id and item_id not in vistos:
            vistos.add(item_id)
            item_ids.append(item_id)
        if len(item_ids) >= 3:
            break
    if not item_ids:
        return conversa, cfg

    item_meta = {
        str(item.get("id") or item.get("item_id") or "").strip(): item
        for item in itens
        if str(item.get("id") or item.get("item_id") or "").strip()
    }
    historico_por_item: dict[str, list[dict]] = {}
    todas_perguntas: list[dict] = []
    for item_id in item_ids:
        try:
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/questions/search",
                params={
                    "seller_id": seller_id,
                    "item_id": item_id,
                    "api_version": 4,
                    "limit": 50,
                    "sort_fields": "date_created",
                    "sort_types": "DESC",
                },
                timeout=6,
            )
            if resp.status_code != 200:
                logger.warning(
                    "[ML POS VENDA] Falha ao buscar perguntas do anuncio item=%s loja=%s status=%s",
                    item_id,
                    loja,
                    resp.status_code,
                )
                continue
            data = resp.json() or {}
            lote = data.get("questions") or data.get("results") or []
            if not isinstance(lote, list):
                continue
            meta = item_meta.get(item_id) or {}
            item_fake = {
                "id": item_id,
                "title": meta.get("title") or "",
                "permalink": meta.get("permalink") or "",
                "thumbnail": meta.get("thumbnail") or "",
                "seller_sku": meta.get("sku") or "",
            }
            for pergunta_raw in lote:
                if not isinstance(pergunta_raw, dict):
                    continue
                comprador = pergunta_raw.get("from") if isinstance(pergunta_raw.get("from"), dict) else {}
                if str(comprador.get("id") or "").strip() != buyer_id:
                    continue
                pergunta_norm = _ml_perguntas_normalizar(pergunta_raw, {item_id: item_fake}, {})
                historico = _ml_perguntas_copia_historico(pergunta_norm)
                historico_por_item.setdefault(item_id, []).append(historico)
                todas_perguntas.append(historico)
        except Exception as exc:
            logger.warning(
                "[ML POS VENDA] Nao foi possivel buscar perguntas do comprador no anuncio item=%s loja=%s: %s",
                item_id,
                loja,
                exc,
            )

    if not todas_perguntas:
        conversa["buyer_listing_question_history"] = []
        conversa["buyer_listing_question_chat"] = []
        conversa["buyer_listing_question_history_count"] = 0
        return conversa, cfg

    por_id = {}
    sem_id = []
    for pergunta in todas_perguntas:
        pergunta_id = str((pergunta or {}).get("id") or "").strip()
        if pergunta_id:
            por_id[pergunta_id] = pergunta
        else:
            sem_id.append(pergunta)
    historico_final = sorted(
        list(por_id.values()) + sem_id,
        key=lambda item: str((item or {}).get("date_created") or (item or {}).get("last_updated") or ""),
    )
    conversa["buyer_listing_question_history"] = historico_final[-30:]
    conversa["buyer_listing_question_chat"] = _ml_perguntas_montar_chat_historico(historico_final)[-40:]
    conversa["buyer_listing_question_history_count"] = len(historico_final)
    conversa["buyer_listing_question_history_by_item"] = {
        item_id: sorted(
            perguntas,
            key=lambda item: str((item or {}).get("date_created") or (item or {}).get("last_updated") or ""),
        )[-15:]
        for item_id, perguntas in historico_por_item.items()
    }
    return conversa, cfg


def _ml_pos_venda_data_iso(data_obj: dt.datetime) -> str:
    return data_obj.strftime("%Y-%m-%dT%H:00:00.000-03:00")


def _ml_pos_venda_normalizar_texto_mensagem(texto) -> str:
    if isinstance(texto, dict):
        return str(texto.get("plain") or texto.get("text") or "").strip()
    return str(texto or "").strip()


def _ml_pos_venda_id_mensagem(mensagem: dict) -> str:
    return str(
        mensagem.get("id")
        or mensagem.get("message_id")
        or mensagem.get("_id")
        or ""
    ).strip()


def _ml_pos_venda_mensagem_data(mensagem: dict) -> str:
    datas = mensagem.get("message_date") if isinstance(mensagem.get("message_date"), dict) else {}
    return str(
        datas.get("received")
        or datas.get("created")
        or mensagem.get("date_received")
        or mensagem.get("date")
        or mensagem.get("date_created")
        or ""
    ).strip()


def _ml_pos_venda_from_id(mensagem: dict) -> str:
    remetente = mensagem.get("from") if isinstance(mensagem.get("from"), dict) else {}
    return str(remetente.get("user_id") or remetente.get("id") or "").strip()


def _ml_pos_venda_flag_verdadeira(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return valor > 0
    texto = str(valor or "").strip().lower()
    return texto in {"1", "true", "yes", "sim", "s", "unread", "not_read", "nao_lida", "nao-lida", "nao lida"}


def _ml_pos_venda_flag_falsa(valor: Any) -> bool:
    if isinstance(valor, bool):
        return not valor
    if isinstance(valor, (int, float)):
        return valor <= 0
    texto = str(valor or "").strip().lower()
    return texto in {"0", "false", "no", "nao", "n", "read", "lida", "lido", "seen", "viewed"}


def _ml_pos_venda_inteiro_positivo(valor: Any) -> int:
    try:
        numero = int(float(valor or 0))
        return max(0, numero)
    except Exception:
        return 0


def _ml_pos_venda_campo_numero_nao_lidas(dados: Any) -> int:
    if not isinstance(dados, dict):
        return 0
    for campo in (
        "unread_count", "unread_messages", "unread_messages_count", "messages_unread",
        "messages_unread_count", "nao_lidas", "mensagens_nao_lidas", "unanswered_messages",
    ):
        if campo in dados:
            total = _ml_pos_venda_inteiro_positivo(dados.get(campo))
            if total > 0:
                return total
    for campo in ("conversation_status", "message_status", "status_detail", "metadata", "flags"):
        total = _ml_pos_venda_campo_numero_nao_lidas(dados.get(campo))
        if total > 0:
            return total
    return 0


def _ml_pos_venda_dados_indicam_nao_lida(dados: Any) -> bool:
    if not isinstance(dados, dict):
        return False
    if _ml_pos_venda_campo_numero_nao_lidas(dados) > 0:
        return True
    for campo in (
        "unread", "has_unread", "has_unread_messages", "nao_lida", "mensagem_nao_lida",
        "mensagens_nao_lidas", "pending_unread",
    ):
        if campo in dados and _ml_pos_venda_flag_verdadeira(dados.get(campo)):
            return True
    for campo in ("read", "is_read", "message_read", "viewed", "seen"):
        if campo in dados and _ml_pos_venda_flag_falsa(dados.get(campo)):
            return True
    for campo in ("status", "substatus", "message_status"):
        valor = dados.get(campo)
        if isinstance(valor, dict):
            if _ml_pos_venda_dados_indicam_nao_lida(valor):
                return True
            continue
        texto = str(valor or "").strip().lower()
        if texto in {"unread", "not_read", "nao_lida", "nao-lida", "nao lida"}:
            return True
    for campo in ("conversation_status", "status_detail", "metadata", "flags"):
        if _ml_pos_venda_dados_indicam_nao_lida(dados.get(campo)):
            return True
    return False


def _ml_pos_venda_mensagem_indica_lida(mensagem: dict) -> bool:
    if not isinstance(mensagem, dict):
        return False
    for campo in ("read", "is_read", "message_read", "viewed", "seen"):
        if campo in mensagem and _ml_pos_venda_flag_verdadeira(mensagem.get(campo)):
            return True
    for campo in ("status", "substatus", "message_status"):
        valor = mensagem.get(campo)
        if isinstance(valor, dict):
            if _ml_pos_venda_mensagem_indica_lida(valor):
                return True
            continue
        texto = str(valor or "").strip().lower()
        if texto in {"read", "lida", "lido", "seen", "viewed"}:
            return True
    return False


def _ml_pos_venda_mensagem_eh_comprador(mensagem: dict, seller_id: str) -> bool:
    if not isinstance(mensagem, dict):
        return False
    seller = str(seller_id or "").strip()
    from_id = _ml_pos_venda_from_id(mensagem)
    if seller and from_id and from_id == seller:
        return False
    papel = str(
        mensagem.get("from_role")
        or mensagem.get("sender_role")
        or mensagem.get("role")
        or ""
    ).strip().lower()
    if papel in {"seller", "vendedor", "loja"}:
        return False
    if papel in {"buyer", "comprador", "cliente"}:
        return True
    return bool(from_id)


def _ml_pos_venda_mensagem_nao_lida(mensagem: dict, seller_id: str) -> bool:
    if not _ml_pos_venda_mensagem_eh_comprador(mensagem, seller_id):
        return False
    return _ml_pos_venda_dados_indicam_nao_lida(mensagem)


def _ml_pos_venda_conversa_nao_lida(mensagens_data: dict, seller_id: str) -> bool:
    if not isinstance(mensagens_data, dict):
        return False
    if _ml_pos_venda_dados_indicam_nao_lida(mensagens_data):
        return True
    conversation_status = mensagens_data.get("conversation_status")
    if _ml_pos_venda_dados_indicam_nao_lida(conversation_status):
        return True
    mensagens = mensagens_data.get("messages") if isinstance(mensagens_data.get("messages"), list) else []
    mensagens_validas = [m for m in mensagens if isinstance(m, dict)]
    if any(_ml_pos_venda_mensagem_nao_lida(mensagem, seller_id) for mensagem in mensagens_validas):
        return True
    mensagens_validas.sort(key=_ml_pos_venda_mensagem_data)
    ultima = mensagens_validas[-1] if mensagens_validas else {}
    return bool(
        ultima
        and _ml_pos_venda_mensagem_eh_comprador(ultima, seller_id)
        and not _ml_pos_venda_mensagem_indica_lida(ultima)
    )


def _ml_pos_venda_contar_nao_lidas(mensagens_data: dict, seller_id: str) -> int:
    if not isinstance(mensagens_data, dict):
        return 0
    total_direto = _ml_pos_venda_campo_numero_nao_lidas(mensagens_data)
    if total_direto > 0:
        return total_direto
    mensagens = mensagens_data.get("messages") if isinstance(mensagens_data.get("messages"), list) else []
    total = sum(1 for mensagem in mensagens if _ml_pos_venda_mensagem_nao_lida(mensagem, seller_id))
    if total <= 0 and _ml_pos_venda_conversa_nao_lida(mensagens_data, seller_id):
        return 1
    return total


def _ml_pos_venda_anexo_url(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    candidatos = (
        "url", "secure_url", "download_url", "file_url", "original_url",
        "thumbnail", "thumbnail_url", "preview_url", "image_url", "src",
    )
    for chave in candidatos:
        valor = str(item.get(chave) or "").strip()
        if valor.startswith(("http://", "https://")):
            return valor
    for chave in ("file", "image", "picture", "source", "content", "data"):
        filho = item.get(chave)
        if isinstance(filho, dict):
            url = _ml_pos_venda_anexo_url(filho)
            if url:
                return url
    return ""


def _ml_pos_venda_mensagem_anexos(mensagem: dict, loja: str = "") -> list[dict]:
    anexos_raw = []

    def adicionar(valor):
        if isinstance(valor, list):
            anexos_raw.extend([v for v in valor if v])
        elif isinstance(valor, dict):
            achou_lista = False
            for chave_lista in ("attachments", "files", "images", "pictures"):
                sub = valor.get(chave_lista)
                if isinstance(sub, list):
                    achou_lista = True
                    anexos_raw.extend([v for v in sub if v])
            if not achou_lista:
                anexos_raw.append(valor)
        elif isinstance(valor, str) and valor.strip():
            anexos_raw.append(valor)

    for chave in (
        "attachments", "message_attachments", "files", "images", "pictures",
    ):
        adicionar(mensagem.get(chave))

    anexos = []
    vistos = set()
    for raw in anexos_raw:
        if isinstance(raw, str):
            url = raw.strip()
            attachment_id = ""
            nome = url.split("?")[0].rstrip("/").split("/")[-1] or "anexo"
            mime = ""
        elif isinstance(raw, dict):
            parece_anexo = any(raw.get(chave) for chave in (
                "url", "secure_url", "download_url", "file_url", "original_url",
                "thumbnail", "thumbnail_url", "preview_url", "image_url", "src",
                "filename", "file_name", "original_filename", "attachment_id", "file_id",
                "mime_type", "content_type", "media_type", "type",
            ))
            if not parece_anexo and str(raw.get("name") or "").strip().lower() in {"packs", "orders", "items", "order"}:
                continue
            attachment_id = str(
                raw.get("id")
                or raw.get("attachment_id")
                or raw.get("file_id")
                or raw.get("resource_id")
                or ""
            ).strip()
            url = _ml_pos_venda_anexo_url(raw)
            nome = str(
                raw.get("filename")
                or raw.get("file_name")
                or raw.get("original_filename")
                or raw.get("name")
                or raw.get("title")
                or attachment_id
                or "anexo"
            ).strip()
            mime = str(
                raw.get("mime_type")
                or raw.get("content_type")
                or raw.get("type")
                or raw.get("media_type")
                or ""
            ).strip()
        else:
            continue

        if not url and attachment_id:
            url = f"/api/mercadolivre/pos-venda/anexos/{quote_plus(attachment_id)}?loja={quote_plus(str(loja or '').strip())}"
        chave_visto = url or attachment_id or nome
        if not chave_visto or chave_visto in vistos:
            continue
        vistos.add(chave_visto)
        eh_imagem = bool(re.search(r"image|foto|picture|jpg|jpeg|png|webp|gif", f"{mime} {nome} {url}", re.I))
        anexos.append({
            "id": attachment_id,
            "name": nome[:160],
            "mime_type": mime[:120],
            "url": url,
            "is_image": eh_imagem,
        })
    return anexos


def _ml_pos_venda_normalizar_mensagens(mensagens: list[dict], seller_id: str, loja: str = "") -> list[dict]:
    normalizadas = []
    seller = str(seller_id or "").strip()
    for msg in mensagens or []:
        if not isinstance(msg, dict):
            continue
        from_id = _ml_pos_venda_from_id(msg)
        texto = _ml_pos_venda_normalizar_texto_mensagem(msg.get("text"))
        anexos = _ml_pos_venda_mensagem_anexos(msg, loja)
        if not texto and not anexos:
            continue
        moderation = msg.get("message_moderation") if isinstance(msg.get("message_moderation"), dict) else {}
        normalizadas.append({
            "id": _ml_pos_venda_id_mensagem(msg),
            "date": _ml_pos_venda_mensagem_data(msg),
            "from_id": from_id,
            "from_role": "seller" if seller and from_id == seller else "buyer",
            "text": texto,
            "attachments": anexos,
            "status": msg.get("status") or "",
            "moderation_status": moderation.get("status") or "",
            "moderation_reason": moderation.get("reason") or "",
        })
    normalizadas.sort(key=lambda item: item.get("date") or "")
    return normalizadas


def _ml_pos_venda_normalizar_pedido(order: dict, mensagens_data: dict, seller_id: str, item_por_id: dict[str, dict] | None = None) -> dict:
    order = order or {}
    order_id = str(order.get("id") or "").strip()
    pack_id = str(order.get("pack_id") or order_id).strip()
    buyer = order.get("buyer") if isinstance(order.get("buyer"), dict) else {}
    buyer_id = str(buyer.get("id") or "").strip()
    buyer_name = _ml_perguntas_nome_comprador(buyer)
    conversation_status = mensagens_data.get("conversation_status") if isinstance(mensagens_data.get("conversation_status"), dict) else {}
    mensagens = mensagens_data.get("messages") if isinstance(mensagens_data.get("messages"), list) else []
    nao_lida = _ml_pos_venda_conversa_nao_lida(mensagens_data, seller_id)
    mensagens_nao_lidas = _ml_pos_venda_contar_nao_lidas(mensagens_data, seller_id)
    mensagens_ordenadas = sorted(
        [m for m in mensagens if isinstance(m, dict)],
        key=_ml_pos_venda_mensagem_data,
        reverse=True,
    )
    ultima = mensagens_ordenadas[0] if mensagens_ordenadas else {}
    itens = []
    for entry in order.get("order_items") or []:
        item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
        item_id = str(item.get("id") or item.get("item_id") or entry.get("item_id") or "").strip() if isinstance(entry, dict) else ""
        item_detalhado = (item_por_id or {}).get(item_id) or {}
        titulo = str(item_detalhado.get("title") or item.get("title") or entry.get("title") or "").strip() if isinstance(entry, dict) else ""
        sku = (
            _ml_extrair_sku(item_detalhado)
            or str(item.get("seller_sku") or item.get("seller_custom_field") or "").strip()
        )
        quantidade = entry.get("quantity") if isinstance(entry, dict) else None
        thumbnail = _ml_perguntas_foto_item(item_detalhado) or _ml_perguntas_foto_item(item)
        permalink = str(item_detalhado.get("permalink") or item.get("permalink") or "").strip()
        if titulo or sku or item_id:
            itens.append({
                "id": item_id,
                "title": titulo,
                "sku": sku,
                "quantity": quantidade,
                "thumbnail": thumbnail,
                "permalink": permalink,
            })
    last_message_from = ((ultima.get("from") or {}).get("user_id") or (ultima.get("from") or {}).get("id")) if isinstance(ultima.get("from"), dict) else None
    last_message_role = ""
    if last_message_from:
        last_message_role = "loja" if str(last_message_from) == str(seller_id or "") else "comprador"
    return {
        "order_id": order_id,
        "pack_id": pack_id,
        "seller_id": seller_id,
        "date_created": order.get("date_created") or order.get("date_closed") or "",
        "date_closed": order.get("date_closed") or "",
        "status": order.get("status") or "",
        "total_amount": order.get("total_amount") or order.get("paid_amount") or 0,
        "buyer_id": buyer_id,
        "buyer_name": buyer_name,
        "buyer_nickname": str(buyer.get("nickname") or buyer_name or "").strip(),
        "items": itens,
        "item_title": " / ".join([item.get("title") for item in itens if item.get("title")][:3]),
        "messages_count": len(mensagens_ordenadas),
        "unread": bool(nao_lida),
        "is_unread": bool(nao_lida),
        "nao_lida": bool(nao_lida),
        "unread_count": int(mensagens_nao_lidas or 0),
        "mensagens_nao_lidas": int(mensagens_nao_lidas or 0),
        "seller_max_message_length": int(mensagens_data.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS),
        "last_message_date": _ml_pos_venda_mensagem_data(ultima),
        "last_message_id": _ml_pos_venda_id_mensagem(ultima),
        "last_message_text": _ml_pos_venda_normalizar_texto_mensagem(ultima.get("text")),
        "last_message_from": last_message_from,
        "last_message_role": last_message_role,
        "last_message_sender_label": "Loja" if last_message_role == "loja" else ("Comprador" if last_message_role == "comprador" else ""),
        "conversation_status": {
            "status": conversation_status.get("status") or "",
            "substatus": conversation_status.get("substatus") or "",
            "path": conversation_status.get("path") or "",
        },
    }


def _ml_pos_venda_normalizar_termo_busca(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", texto)


def _ml_pos_venda_conversa_corresponde_busca(conversa: dict, busca: str) -> bool:
    termo = _ml_pos_venda_normalizar_termo_busca(busca)
    if not termo:
        return True
    campos = [
        conversa.get("order_id"),
        conversa.get("pack_id"),
        conversa.get("buyer_id"),
        conversa.get("buyer_name"),
        conversa.get("buyer_nickname"),
        conversa.get("item_title"),
        conversa.get("claim_id"),
        conversa.get("claim_reason_id"),
        conversa.get("claim_reason_name"),
        conversa.get("claim_reason_detail"),
    ]
    for item in conversa.get("items") or []:
        if not isinstance(item, dict):
            continue
        campos.extend([
            item.get("id"),
            item.get("sku"),
            item.get("title"),
        ])
    texto = _ml_pos_venda_normalizar_termo_busca(" ".join(str(campo or "") for campo in campos))
    return termo in texto


def _ml_pos_venda_pedido_corresponde_busca(venda: dict, busca: str) -> bool:
    return _ml_pos_venda_conversa_corresponde_busca(venda, busca)


def _ml_mediacao_player_id(claim: dict, role: str) -> str:
    role_norm = str(role or "").strip().lower()
    for player in claim.get("players") or []:
        if not isinstance(player, dict):
            continue
        if str(player.get("role") or "").strip().lower() == role_norm:
            return str(player.get("user_id") or player.get("id") or "").strip()
    return ""


def _ml_mediacao_order_id(claim: dict) -> str:
    order_id = str((claim or {}).get("order_id") or "").strip()
    if order_id:
        return order_id
    resource = str((claim or {}).get("resource") or "").strip().lower()
    if resource in {"", "order"}:
        return str((claim or {}).get("resource_id") or "").strip()
    return ""


_ML_MEDIACAO_REASON_CACHE: dict[str, tuple[float, dict]] = {}


_ML_MEDIACAO_REASON_CACHE_TTL = 6 * 60 * 60


def _ml_mediacao_texto_motivo(reason: dict) -> str:
    if not isinstance(reason, dict):
        return ""
    for campo in ("detail", "description", "message", "name"):
        valor = str(reason.get(campo) or "").strip()
        if valor:
            return valor
    return ""


def _ml_mediacao_motivo_generico_por_codigo(reason_id: str) -> str:
    codigo = str(reason_id or "").strip().upper()
    if codigo.startswith("PDD"):
        return "Produto diferente ou defeituoso"
    if codigo.startswith("PNR"):
        return "Produto n\u00e3o recebido"
    if codigo.startswith("CS"):
        return "Compra cancelada"
    return ""


def _ml_mediacao_buscar_motivos_claims(
    client_id: str,
    loja: str,
    cfg: dict,
    reason_ids: list[str],
) -> tuple[dict[str, dict], dict]:
    motivos = {}
    agora = time.time()
    vistos = set()
    for reason_id in reason_ids:
        codigo = str(reason_id or "").strip()
        if not codigo or codigo in vistos:
            continue
        vistos.add(codigo)
        cache = _ML_MEDIACAO_REASON_CACHE.get(codigo)
        if cache and agora - cache[0] < _ML_MEDIACAO_REASON_CACHE_TTL:
            motivos[codigo] = cache[1]
            continue
        try:
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/post-purchase/v1/claims/reasons/{codigo}",
                timeout=12,
            )
            if resp.status_code != 200:
                logger.warning("[ML MEDIACAO] Falha ao buscar motivo %s loja=%s: HTTP %s", codigo, loja, resp.status_code)
                continue
            dados = resp.json() or {}
            if isinstance(dados, dict):
                normalizado = {
                    "id": str(dados.get("id") or codigo).strip(),
                    "name": str(dados.get("name") or "").strip(),
                    "detail": str(dados.get("detail") or dados.get("description") or "").strip(),
                    "flow": str(dados.get("flow") or "").strip(),
                }
                _ML_MEDIACAO_REASON_CACHE[codigo] = (agora, normalizado)
                motivos[codigo] = normalizado
        except Exception as exc:
            logger.warning("[ML MEDIACAO] Erro ao buscar motivo %s loja=%s: %s", codigo, loja, exc)
    return motivos, cfg


def _ml_mediacao_normalizar_claim(claim: dict, order: dict, seller_id: str, item_por_id: dict[str, dict] | None = None) -> dict:
    order_id = _ml_mediacao_order_id(claim)
    venda = _ml_pos_venda_normalizar_pedido(order or {"id": order_id, "pack_id": claim.get("pack_id") or order_id}, {}, seller_id, item_por_id)
    resolution = claim.get("resolution") if isinstance(claim.get("resolution"), dict) else {}
    reason = claim.get("reason") if isinstance(claim.get("reason"), dict) else {}
    reason_id = str(claim.get("reason_id") or "").strip()
    reason_detail = _ml_mediacao_texto_motivo(reason) or _ml_mediacao_motivo_generico_por_codigo(reason_id)
    venda.update({
        "claim_id": str(claim.get("id") or "").strip(),
        "claim_type": str(claim.get("type") or "").strip(),
        "claim_kind": str(claim.get("_jk_claim_tipo") or "").strip(),
        "claim_kind_label": str(claim.get("_jk_claim_tipo_label") or "").strip(),
        "claim_stage": str(claim.get("stage") or "").strip(),
        "claim_status": str(claim.get("status") or "").strip(),
        "claim_reason_id": reason_id,
        "claim_reason_name": str(reason.get("name") or claim.get("reason_name") or "").strip(),
        "claim_reason_detail": reason_detail,
        "claim_date_created": claim.get("date_created") or "",
        "claim_last_updated": claim.get("last_updated") or "",
        "claim_complainant_id": _ml_mediacao_player_id(claim, "complainant"),
        "claim_respondent_id": _ml_mediacao_player_id(claim, "respondent"),
        "claim_resolution_reason": str(resolution.get("reason") or "").strip(),
        "claim_closed_by": str(resolution.get("closed_by") or "").strip(),
        "last_message_date": claim.get("last_updated") or venda.get("date_created") or "",
        "last_message_text": "Venda em mediaÃ§Ã£o com o Mercado Livre.",
    })
    if not venda.get("pack_id") and claim.get("pack_id"):
        venda["pack_id"] = str(claim.get("pack_id") or "").strip()
    if venda.get("claim_complainant_id") and not venda.get("buyer_id"):
        venda["buyer_id"] = venda["claim_complainant_id"]
    return venda


def _ml_pos_venda_buscar_mensagens_pack(
    client_id: str,
    loja: str,
    cfg: dict,
    pack_id: str,
    seller_id: str,
    limit: int = 50,
    max_messages: int = 300,
) -> tuple[dict, dict]:
    pack = str(pack_id or "").strip()
    if not pack:
        raise HTTPException(status_code=400, detail="Informe o pack da conversa.")
    todas = []
    offset = 0
    paging_total = None
    ultimo_payload = {}
    while len(todas) < max_messages:
        resp_msg = None
        detalhe_rate_limit = ""
        for tentativa in range(4):
            resp_msg, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/messages/packs/{pack}/sellers/{seller_id}",
                params={"tag": "post_sale", "mark_as_read": "false", "limit": limit, "offset": offset},
                timeout=20,
            )
            if resp_msg.status_code == 200:
                break
            detalhe_rate_limit = _ml_parse_error_detail(resp_msg, "Erro ao buscar mensagens do pÃ³s venda")
            if not _ml_response_eh_rate_limit(resp_msg, detalhe_rate_limit) or tentativa >= 3:
                break
            espera = _ml_retry_after_seconds(resp_msg, padrao=1.5 + tentativa * 1.5)
            logger.info(
                "[ML POS VENDA] Rate limit ao buscar mensagens pack=%s loja=%s offset=%s. Tentativa %s/4; aguardando %.1fs.",
                pack,
                loja,
                offset,
                tentativa + 1,
                espera,
            )
            time.sleep(espera)
        if resp_msg is None or resp_msg.status_code != 200:
            detalhe = detalhe_rate_limit or _ml_parse_error_detail(resp_msg, "Erro ao buscar mensagens do pÃ³s venda")
            if _ml_response_eh_rate_limit(resp_msg, detalhe):
                detalhe = "Mercado Livre limitou temporariamente a consulta da conversa. Aguarde alguns segundos e tente novamente."
            raise HTTPException(status_code=getattr(resp_msg, "status_code", 429) or 429, detail=detalhe)
        data = resp_msg.json() or {}
        ultimo_payload = data if isinstance(data, dict) else {}
        mensagens = ultimo_payload.get("messages") if isinstance(ultimo_payload.get("messages"), list) else []
        todas.extend([m for m in mensagens if isinstance(m, dict)])
        paging = ultimo_payload.get("paging") if isinstance(ultimo_payload.get("paging"), dict) else {}
        paging_total = int(paging.get("total") or paging_total or len(todas) or 0)
        offset += len(mensagens)
        if not mensagens or offset >= paging_total:
            break
    ultimo_payload["messages"] = todas[:max_messages]
    ultimo_payload["paging"] = {
        **(ultimo_payload.get("paging") if isinstance(ultimo_payload.get("paging"), dict) else {}),
        "total_loaded": len(ultimo_payload["messages"]),
    }
    return ultimo_payload, cfg


def _ml_pos_venda_descobrir_buyer_id(mensagens: list[dict], seller_id: str, order: dict | None = None) -> str:
    seller = str(seller_id or "").strip()
    for msg in mensagens or []:
        from_id = _ml_pos_venda_from_id(msg)
        if from_id and from_id != seller:
            return from_id
        destino = msg.get("to") if isinstance(msg.get("to"), dict) else {}
        to_id = str(destino.get("user_id") or destino.get("id") or "").strip()
        if to_id and to_id != seller:
            return to_id
    buyer = (order or {}).get("buyer") if isinstance((order or {}).get("buyer"), dict) else {}
    return str(buyer.get("id") or "").strip()


ML_POS_VENDA_AGENT_USER_IDS = {
    "MLC": "3020819166",
    "MCO": "3037204123",
    "MLM": "3037204279",
    "MLA": "3037674934",
    "MLB": "3037675074",
    "MLU": "3037204685",
}


def _ml_pos_venda_resolver_site_id(client_id: str, loja: str, cfg: dict, seller_id: str) -> tuple[str, dict]:
    site_id = str(
        (cfg or {}).get("site_id")
        or (cfg or {}).get("site")
        or (cfg or {}).get("country_id")
        or ""
    ).strip().upper()
    if re.match(r"^ML[A-Z]$", site_id):
        return site_id, cfg

    try:
        resp_user, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/users/{seller_id}",
            timeout=12,
        )
        if resp_user.status_code == 200:
            data_user = resp_user.json() or {}
            site_id = str(data_user.get("site_id") or "").strip().upper()
            if re.match(r"^ML[A-Z]$", site_id):
                try:
                    cfg["site_id"] = site_id
                    atualizar_api_loja(client_id, loja, "mercadolivre", cfg)
                except Exception:
                    pass
                return site_id, cfg
    except Exception as exc:
        logger.warning("[ML POS VENDA] Nao foi possivel resolver site_id da loja %s: %s", loja, exc)

    return "MLB", cfg


def _ml_pos_venda_destinatarios_mensagem(client_id: str, loja: str, cfg: dict, seller_id: str, buyer_id: str) -> tuple[list[tuple[str, str]], dict, str]:
    site_id, cfg = _ml_pos_venda_resolver_site_id(client_id, loja, cfg, seller_id)
    agent_id = ML_POS_VENDA_AGENT_USER_IDS.get(site_id) or ML_POS_VENDA_AGENT_USER_IDS["MLU"]
    buyer = str(buyer_id or "").strip()
    candidatos = []
    vistos = set()
    for user_id, origem in ((agent_id, "agent"), (buyer, "buyer")):
        user_id = str(user_id or "").strip()
        if not user_id or user_id == seller_id or user_id in vistos:
            continue
        vistos.add(user_id)
        candidatos.append((user_id, origem))
    if not candidatos:
        raise HTTPException(status_code=400, detail="ID do destinatÃ¡rio do pÃ³s venda nÃ£o encontrado.")
    return candidatos, cfg, site_id


def _ml_pos_venda_enviar_resposta_ml(
    client_id: str,
    loja: str,
    cfg: dict,
    pack_id: str,
    buyer_id: str,
    texto: str,
    max_chars: int | None = None,
) -> tuple[dict, dict]:
    pack = str(pack_id or "").strip()
    buyer = str(buyer_id or "").strip()
    seller_id = str((cfg or {}).get("user_id") or "").strip()
    if not pack:
        raise HTTPException(status_code=400, detail="Pack da conversa nÃ£o informado.")
    if not seller_id:
        raise HTTPException(status_code=400, detail="ID do vendedor nÃ£o encontrado.")
    resposta = _pos_venda_ia_limpar_resposta(texto, max_chars)
    if not resposta:
        raise HTTPException(status_code=400, detail="Resposta vazia.")
    candidatos, cfg, site_id = _ml_pos_venda_destinatarios_mensagem(client_id, loja, cfg, seller_id, buyer)
    ultimo_status = 400
    ultimo_detalhe = "Erro ao enviar mensagem no pÃ³s venda"
    for destinatario_id, origem_destinatario in candidatos:
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "POST",
            f"https://api.mercadolibre.com/messages/packs/{pack}/sellers/{seller_id}",
            params={"tag": "post_sale", "site_id": site_id},
            json={
                "from": {"user_id": seller_id},
                "to": {"user_id": destinatario_id},
                "text": resposta,
            },
            timeout=20,
        )
        if resp.status_code in {200, 201}:
            try:
                data = resp.json() or {}
            except Exception:
                data = {"raw": resp.text}
            if isinstance(data, dict):
                data.setdefault("site_id", site_id)
                data.setdefault("to_user_id_usado", destinatario_id)
                data.setdefault("to_user_id_origem", origem_destinatario)
            return data, cfg
        ultimo_status = resp.status_code
        ultimo_detalhe = _ml_parse_error_detail(resp, "Erro ao enviar mensagem no pÃ³s venda")
        logger.warning(
            "[ML POS VENDA] Falha ao enviar resposta pack=%s loja=%s destinatario=%s origem=%s status=%s detalhe=%s",
            pack,
            loja,
            destinatario_id,
            origem_destinatario,
            resp.status_code,
            ultimo_detalhe,
        )
    raise HTTPException(status_code=ultimo_status, detail=ultimo_detalhe)


def _ml_pos_venda_gerar_resposta_ia(
    client_id: str,
    loja: str,
    conversa: dict,
    max_chars: int | None = None,
    contexto_pipeline: Optional[dict] = None,
) -> tuple[str, str]:
    limite = int(max_chars or conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS)
    limite = max(1, min(limite, ML_POS_VENDA_DEFAULT_MAX_CHARS))
    mensagens = conversa.get("messages") if isinstance(conversa.get("messages"), list) else []
    historico = "\n".join([
        f"{'Vendedor' if msg.get('from_role') == 'seller' else 'Comprador'}: {msg.get('text') or ''}"
        for msg in mensagens[-12:]
        if isinstance(msg, dict)
    ])
    perguntas_anuncio_chat = _ml_pos_venda_perguntas_anuncio_chat(conversa)
    historico_perguntas_anuncio = "\n".join([
        f"{evento.get('label') or ('Loja' if evento.get('role') == 'seller' else 'Comprador')}: {evento.get('text') or ''}"
        for evento in perguntas_anuncio_chat[-20:]
        if isinstance(evento, dict)
    ])
    itens = conversa.get("items") if isinstance(conversa.get("items"), list) else []
    produtos = "\n".join([
        f"- SKU {item.get('sku') or '-'} | {item.get('title') or '-'} | ID {item.get('id') or '-'}"
        for item in itens[:8]
        if isinstance(item, dict)
    ])
    ultima = str(conversa.get("last_message_text") or (mensagens[-1].get("text") if mensagens else "") or "").strip()
    memoria_sku = _ml_pos_venda_memoria_bloco_prompt(client_id, conversa)
    assinatura_loja = _perguntas_ia_assinatura_loja(loja)
    contexto_estruturado = _ml_pos_venda_contexto_prompt(contexto_pipeline)
    resposta_atual = str(conversa.get("_resposta_atual") or "").strip()[:1200]
    bloco_resposta_atual = (
        "RESPOSTA ATUAL QUE O OPERADOR ESTA EDITANDO:\n"
        f"{resposta_atual}\n\n"
        "Preserve literalmente todo trecho que a orientacao do operador nao mandar alterar. "
        "Se ele pedir para repetir a resposta removendo ou trocando apenas uma parte, faca somente essa alteracao.\n\n"
        if resposta_atual
        else ""
    )
    orientacao_usuario = str(conversa.get("_orientacao_usuario") or "").strip()[:1200]
    bloco_orientacao_usuario = (
        "COMANDO EDITORIAL DO OPERADOR PARA ESTA NOVA RESPOSTA:\n"
        f"{orientacao_usuario}\n\n"
        "Execute literalmente, sem explicar a edicao. Se o operador fornecer a frase final, copie a redacao dele. "
        "Se pedir para remover, incluir, trocar ou manter um trecho, altere somente esse trecho. Nao mencione esta orientacao "
        "ao comprador. So deixe de cumpri-la se contrariar o historico confirmado ou as regras de seguranca do Mercado Livre.\n\n"
        if orientacao_usuario
        else ""
    )
    mensagem = (
        "Fluxo: IA de POS-VENDA do Mercado Livre. "
        "Responda somente como equipe da loja, sem se apresentar como assistente, IA, Gemini, Vertex ou JK Sistema. "
        "Use as orientacoes salvas no treinamento de pos-venda e o contexto estruturado como fonte de verdade. "
        "Nao reaproveite o tom de perguntas publicas do anuncio e nao chame o comprador para comprar novamente. "
        "Nao use Markdown, asteriscos, tabelas, emojis ou caracteres especiais desnecessarios. "
        "Nao invente prazos, garantia, estoque, compatibilidade, devolucao, troca ou procedimentos. "
        "Se o contexto indicar que precisa consultar regras oficiais ou humano, nao prometa solucao final; responda que a equipe vai verificar o caso e retornar pelo Mercado Livre. "
        "Considere as perguntas anteriores feitas pelo comprador no anuncio apenas como contexto do atendimento. "
        "Use esse historico para entender o que ja foi perguntado e respondido, sem repetir tudo ao comprador. "
        "Se faltar informacao para resolver o atendimento, peca o dado necessario de forma educada. "
        f"A resposta final completa deve ter no maximo {min(limite, ML_POS_VENDA_LIMITE_SEGURO)} caracteres. "
        f"Finalize exatamente com: {assinatura_loja}\n\n"
        f"Contexto estruturado do pipeline:\n{contexto_estruturado or '-'}\n\n"
        f"{bloco_orientacao_usuario}"
        f"{bloco_resposta_atual}"
        f"Loja: {loja}\n"
        f"Pack: {conversa.get('pack_id') or '-'}\n"
        f"Pedido: {conversa.get('order_id') or '-'}\n"
        f"Comprador: {conversa.get('buyer_nickname') or conversa.get('buyer_id') or '-'}\n"
        f"Produtos:\n{produtos or '-'}\n\n"
        f"Memoria tecnica local dos SKUs da venda:\n{memoria_sku or '-'}\n\n"
        f"Perguntas anteriores do comprador no anuncio:\n{historico_perguntas_anuncio or '-'}\n\n"
        f"HistÃ³rico da conversa:\n{historico or '-'}\n\n"
        f"Ãšltima mensagem do comprador:\n{ultima or '-'}"
    )
    payload = IAChatRequest(
        message=mensagem,
        page="Perguntas e pos venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": ML_POS_VENDA_IA_V2_MODO,
            "tipo_treinamento": "pos_venda",
            "ia_finalidade": "pos_venda",
            "origem_ia": "mercado_livre_pos_venda_ia_v2",
            "desativar_recursos_chat": True,
            "desativar_busca_web_chat": True,
            "loja": loja,
            "conversa": conversa,
        },
        model=None,
    )
    model_req = _normalizar_ia_modelo_padrao(_ia_modelo_pos_venda_configurado())
    payload.model = model_req
    if _modelo_eh_codex(model_req):
        resposta = _chamar_codex_chat(payload, client_id)
        model_usado = f"codex:{_codex_modelo_nome_curto(model_req)}"
    elif _modelo_eh_vertex_ai(model_req):
        resposta = _chamar_vertex_ai_chat(payload, client_id)
        model_usado = f"vertex:{_vertex_modelo_nome_curto(model_req) or _vertex_ai_modelo_padrao()}"
    elif _modelo_eh_gemini_api(model_req):
        resposta = _chamar_gemini_chat(payload, client_id)
        model_usado = f"gemini:{_gemini_nome_curto(model_req) or 'gemini-2.5-flash'}"
    elif model_req.startswith("deepseek-"):
        resposta = _chamar_deepseek_chat(payload, client_id)
        model_usado = model_req
    else:
        resposta = _chamar_openai_responses(payload, client_id)
        model_usado = model_req or (os.getenv("OPENAI_MODEL") or "gpt-5.4-nano").strip()
    resposta_limpa = _pos_venda_ia_resposta_final_loja(resposta, loja, limite)
    if not resposta_limpa:
        raise PerguntasIARespostaIndisponivel("IA de pos-venda nao gerou resposta.")
    try:
        _ml_pos_venda_memoria_registrar_geracao(client_id, loja, conversa, resposta_limpa, model_usado)
    except Exception as exc:
        logger.warning("[ML POS VENDA IA] Falha ao registrar memoria de geracao do SKU: %s", exc)
    return resposta_limpa, model_usado


def _ml_pos_venda_buscar_pedido(client_id: str, loja: str, cfg: dict, order_id: str) -> tuple[dict, dict]:
    pedido_id = str(order_id or "").strip()
    if not pedido_id:
        return {}, cfg
    resp, cfg = _ml_api_request(
        client_id,
        loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/orders/{pedido_id}",
        timeout=20,
    )
    if resp.status_code == 404:
        return {}, cfg
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao buscar pedido do pos venda"))
    data = resp.json() or {}
    return data if isinstance(data, dict) else {}, cfg


def _ml_pos_venda_item_ids_pedido(order: dict) -> list[str]:
    ids = []
    vistos = set()
    for entry in (order or {}).get("order_items") or []:
        item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
        item_id = str(item.get("id") or item.get("item_id") or entry.get("item_id") or "").strip() if isinstance(entry, dict) else ""
        if item_id and item_id not in vistos:
            vistos.add(item_id)
            ids.append(item_id)
    return ids


def _ml_pos_venda_montar_conversa_normalizada(
    client_id: str,
    loja: str,
    cfg: dict,
    pack_id: str,
    order_id: str = "",
    mensagens_data: Optional[dict] = None,
) -> tuple[dict, dict]:
    seller_id = str((cfg or {}).get("user_id") or "").strip()
    if not seller_id:
        raise HTTPException(status_code=400, detail="ID do vendedor do Mercado Livre nao encontrado.")
    mensagens_data = mensagens_data if isinstance(mensagens_data, dict) else None
    if mensagens_data is None:
        mensagens_data, cfg = _ml_pos_venda_buscar_mensagens_pack(client_id, loja, cfg, pack_id, seller_id)
    order, cfg = _ml_pos_venda_buscar_pedido(client_id, loja, cfg, order_id)
    if not order:
        order = {"id": order_id or "", "pack_id": pack_id}
    item_ids = _ml_pos_venda_item_ids_pedido(order)
    itens, cfg = _ml_buscar_itens_batch(client_id, loja, cfg, item_ids)
    item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
    conversa = _ml_pos_venda_normalizar_pedido(order, mensagens_data, seller_id, item_por_id)
    mensagens_raw = mensagens_data.get("messages") if isinstance(mensagens_data.get("messages"), list) else []
    conversa["messages"] = _ml_pos_venda_normalizar_mensagens(mensagens_raw, seller_id, loja)
    if not conversa.get("buyer_id"):
        conversa["buyer_id"] = _ml_pos_venda_descobrir_buyer_id(mensagens_raw, seller_id, order)
    return conversa, cfg


def _ml_pos_venda_preparar_conversa_ia(
    client_id: str,
    loja: str,
    cfg: dict,
    conversa: dict,
) -> tuple[dict, dict]:
    conversa = conversa if isinstance(conversa, dict) else {}
    if isinstance(conversa.get("buyer_listing_question_chat"), list):
        return conversa, cfg
    seller_id = str((cfg or {}).get("user_id") or conversa.get("seller_id") or "").strip()
    if not seller_id:
        return conversa, cfg
    return _ml_pos_venda_anexar_perguntas_anuncio_comprador(
        client_id,
        loja,
        cfg,
        seller_id,
        conversa,
    )

PEER_EXPORTS = ['_ml_pos_venda_anexar_perguntas_anuncio_comprador', '_ml_pos_venda_data_iso', '_ml_pos_venda_normalizar_texto_mensagem', '_ml_pos_venda_id_mensagem', '_ml_pos_venda_mensagem_data', '_ml_pos_venda_from_id', '_ml_pos_venda_flag_verdadeira', '_ml_pos_venda_flag_falsa', '_ml_pos_venda_inteiro_positivo', '_ml_pos_venda_campo_numero_nao_lidas', '_ml_pos_venda_dados_indicam_nao_lida', '_ml_pos_venda_mensagem_indica_lida', '_ml_pos_venda_mensagem_eh_comprador', '_ml_pos_venda_mensagem_nao_lida', '_ml_pos_venda_conversa_nao_lida', '_ml_pos_venda_contar_nao_lidas', '_ml_pos_venda_anexo_url', '_ml_pos_venda_mensagem_anexos', '_ml_pos_venda_normalizar_mensagens', '_ml_pos_venda_normalizar_pedido', '_ml_pos_venda_normalizar_termo_busca', '_ml_pos_venda_conversa_corresponde_busca', '_ml_pos_venda_pedido_corresponde_busca', '_ml_mediacao_player_id', '_ml_mediacao_order_id', '_ML_MEDIACAO_REASON_CACHE', '_ML_MEDIACAO_REASON_CACHE_TTL', '_ml_mediacao_texto_motivo', '_ml_mediacao_motivo_generico_por_codigo', '_ml_mediacao_buscar_motivos_claims', '_ml_mediacao_normalizar_claim', '_ml_pos_venda_buscar_mensagens_pack', '_ml_pos_venda_descobrir_buyer_id', 'ML_POS_VENDA_AGENT_USER_IDS', '_ml_pos_venda_resolver_site_id', '_ml_pos_venda_destinatarios_mensagem', '_ml_pos_venda_enviar_resposta_ml', '_ml_pos_venda_gerar_resposta_ia', '_ml_pos_venda_buscar_pedido', '_ml_pos_venda_item_ids_pedido', '_ml_pos_venda_montar_conversa_normalizada', '_ml_pos_venda_preparar_conversa_ia']
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_pos_venda_runtime"]

configure_perguntas_pos_venda_pos_venda_runtime()
