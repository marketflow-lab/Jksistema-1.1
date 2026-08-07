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
def _assistant_result_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    try:
        total_registros = int(value.get("total_registros") or 0)
        if total_registros:
            return total_registros
    except Exception:
        pass
    # Comparacoes validas sao agregados com dois objetos, nao listas. Sem
    # este reconhecimento o executor marcava o resultado como vazio e
    # disparava um fallback desnecessario para sales_ranking.
    if isinstance(value.get("periodo_a"), dict) and isinstance(value.get("periodo_b"), dict):
        return 2
    for key in (
        "rows",
        "results",
        "campaigns",
        "by_sku",
        "por_sku",
        "vendas",
        "devolucoes",
        "por_loja",
        "items",
        "itens",
        "matches",
        "lojas",
        "top_skus",
        "produtos",
        "produtos_fiscais",
        "saldos",
        "depositos",
        "pedidos",
        "orders",
        "notas",
        "naturezas",
        "lotes",
        "lancamentos",
        "financeiro",
        "resources",
        "sources",
        "capabilities",
        "candidates",
        "routes",
        "services",
        "actions",
        "pages",
        "modules",
        "functions",
        "missing_params",
        "anomalias",
        "alertas",
        "pontos",
    ):
        if isinstance(value.get(key), list):
            return len(value.get(key) or [])
    total = (
        value.get("record_count")
        or value.get("records")
        or value.get("count")
        or value.get("total_registros")
        or value.get("total")
        or value.get("total_lojas")
        or value.get("total_skus_analisados")
        or value.get("total_skus_avaliados")
    )
    try:
        total_int = int(total or 0)
        if total_int:
            return total_int
    except Exception:
        pass
    scalar_keys = (
        "quantidade_total",
        "quantidade_vendida_total",
        "quantidade_devolvida_total",
        "valor_total",
        "valor_vendido_total",
        "valor_devolvido_total",
        "pedidos_total",
        "lucro_estimado",
        "ticket_medio",
        "taxa_devolucao_quantidade_percentual",
        "taxa_devolucao_valor_percentual",
    )
    if any(value.get(key) not in (None, "", 0, 0.0) for key in scalar_keys):
        return 1
    return 0


def _assistant_function_name(item: dict[str, Any]) -> str:
    name = str((item or {}).get("function") or "consulta").strip()
    if name.startswith("_ia_tool_"):
        name = name[len("_ia_tool_"):]
    return name or "consulta"


def _assistant_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _assistant_money(value: Any) -> str:
    amount = _assistant_float(value)
    raw = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {raw}"


def _assistant_percent(value: Any) -> str:
    return f"{_assistant_float(value):.1f}%".replace(".", ",")


def _assistant_find_result(results: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for item in results:
        if not isinstance(item, dict):
            continue
        if _assistant_function_name(item) == name:
            result = item.get("result")
            return result if isinstance(result, dict) else {}
    return {}


def _assistant_first_list(result: dict[str, Any], *keys: str) -> list[Any]:
    if not isinstance(result, dict):
        return []
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def _assistant_sku_label(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return "-"
    sku = str(item.get("sku") or item.get("seller_sku") or item.get("id") or "-").strip() or "-"
    nome = str(item.get("produto") or item.get("nome") or item.get("title") or "").strip()
    return f"{sku} - {nome}" if nome else sku


def _assistant_qty(value: Any, decimals: int = 1) -> str:
    amount = _assistant_float(value)
    if abs(amount - round(amount)) < 0.0001:
        return str(int(round(amount)))
    return f"{amount:.{decimals}f}".replace(".", ",")


def _assistant_risk_label(value: Any) -> str:
    risk = _assistant_texto_norm(value)
    labels = {
        "critico": "Critico",
        "alto": "Alto",
        "medio": "Medio",
        "baixo": "Baixo",
        "sem_consumo": "Sem consumo recente",
    }
    return labels.get(risk, str(value or "-").strip() or "-")
