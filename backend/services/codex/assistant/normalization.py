"""Internal Codex Assistant component."""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services import codex_assistant_storage, codex_turn_context
from backend.services.favoritos_margem import margem_calcular_anuncio, margem_formatar_moeda, margem_parse_float
from backend.services.whatsapp import intent as whatsapp_intent

from .runtime import _assistant_texto_norm
def _assistant_message_has_product_ref(message: str) -> bool:
    text = _assistant_texto_norm(message)
    text_without_dates = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b", " ", text)
    return bool(
        re.search(r"\bsku\s+[a-z0-9._/-]{2,}\b", text_without_dates)
        or re.search(r"\bmlb[\s_-]?\d{6,}\b", text_without_dates)
        or re.search(r"\bproduto\s+.{3,}", text_without_dates)
        or re.search(r"[a-z0-9]{2,}[-_/][a-z0-9]{1,}", text_without_dates)
    )


def _assistant_questions_addressed_to_assistant(message: Any) -> bool:
    text = _assistant_texto_norm(str(message or ""))
    return bool(re.search(
        r"\b(voce|black jhon|blackjohn|joao pretinho|assistente)\b[^.?!]{0,32}\btem\s+perguntas?\b",
        text,
    ))


def _assistant_is_broad_open_questions_query(message: Any) -> bool:
    """Identify a current unanswered-question queue request with no product scope."""

    text = _assistant_texto_norm(str(message or ""))
    if not re.search(r"\bperguntas?\b", text):
        return False
    if _assistant_questions_addressed_to_assistant(message):
        return False
    if _assistant_message_has_product_ref(str(message or "")):
        return False
    if re.search(
        r"\b(comprador|historico|anuncio|item|produto|sku|mlb|pedido|order|pack|conversa|foto|imagem|link)\b",
        text,
    ):
        return False
    answered_history = bool(re.search(r"\brespondid[ao]s?\b", text)) and not bool(
        re.search(r"\b(nao respondid[ao]s?|sem resposta)\b", text)
    )
    if answered_history or re.search(r"\b(fechad[ao]s?|encerrad[ao]s?|historico completo)\b", text):
        return False
    return bool(
        re.search(
            r"\b(em aberto|abert[ao]s?|pendentes?|sem resposta|nao respondid[ao]s?|"
            r"para responder|fila(?: atual)?|tem|ha|existe|existem|consulte|consultar|verifique|verificar)\b",
            text,
        )
    )


def _assistant_is_generic_sales_api_query(message: str) -> bool:
    text = _assistant_texto_norm(message)
    wants_sales = bool(re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|ranking|top)\b", text))
    wants_api = bool(re.search(r"\b(api|apis|via api|pelas apis|pela api)\b", text))
    names_provider = bool(re.search(r"\b(bling|mercado livre|mercadolivre|ml|mlb[\s_-]*\d+)\b", text))
    return bool(wants_sales and wants_api and not names_provider)


def _assistant_latest_ml_event_kind(message: Any) -> str:
    """Classify latest sale/return intent without treating a return order id as a sale query."""

    text = _assistant_texto_norm(str(message or ""))
    if not re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", text):
        return ""
    latest_period = bool(
        re.search(
            r"\b(ultima|ultimo)\s+(semana|mes|ano|dia|periodo|trimestre|bimestre|semestre)\b",
            text,
        )
    )
    latest_return = bool(
        re.search(
            r"\b(ultima|ultimo|mais recente)\s+(devolucao|devolucoes|reembolso|estorno)\b",
            text,
        )
        or re.search(r"\b(devolucao|devolucoes|reembolso|estorno)\s+mais recente\b", text)
    )
    latest_sale = whatsapp_intent.mercado_livre_sales_lookup(message).get("mode") == "latest"
    if not latest_return and not latest_sale:
        latest_sale = bool(
            not latest_period
            and
            re.search(r"\b(mercado livre|mercadolivre|ml)\b", text)
            and re.search(r"\b(sku\s*[a-z0-9._/-]+|mlb\s*\d{6,})\b", text)
        )
    explicit_both = bool(
        re.search(
            r"\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b",
            text,
        )
        or re.search(
            r"\b(ultima|ultimo|mais recente)\s+(devolucao|reembolso|estorno)\b[^.]{0,100}\b(e|tambem|junto|ambos)\b"
            r"[^.]{0,100}\b(ultima|ultimo|mais recente)\s+(venda|pedido)\b",
            text,
        )
    )
    if explicit_both:
        return "both"
    if latest_return:
        return "return"
    if latest_sale:
        return "sale"
    return ""
