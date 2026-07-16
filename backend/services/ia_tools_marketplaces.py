"""Internal slice for ia_tools."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import base64
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from jose import JWTError
from backend.schemas import (
    IAAgentQueryRequest,
    IAChatRequest,
    IASalvarConversaRequest,
    IARagIndexRequest,
    IARagReindexRequest,
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
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.ia_common import *
from backend.services.ia_context import get_tenant_id, get_tenant_path
from backend.services.ia_state import *


logger = logging.getLogger(__name__)


def configure_ia_tools_marketplaces_runtime(runtime_module=None, peers=None):
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


configure_ia_tools_marketplaces_runtime()


def _ia_lojas_bling_conectadas(client_id: str) -> list[str]:
    lojas = []
    for loja in carregar_lojas(client_id) or []:
        if not isinstance(loja, dict):
            continue
        nome = str(loja.get("nome") or "").strip()
        integracoes = loja.get("integracoes") or {}
        cfg = integracoes.get("bling") if isinstance(integracoes, dict) else {}
        if nome and isinstance(cfg, dict) and str(cfg.get("access_token") or "").strip():
            lojas.append(nome)
    return lojas


def _ia_lojas_com_integracao(client_id: str, provedor: str, loja: Optional[str] = None) -> list[str]:
    provedor_norm = str(provedor or "").strip().lower()
    conectadas = _ia_lojas_ml_conectadas(client_id) if provedor_norm in {"ml", "mercadolivre", "mercado_livre"} else _ia_lojas_bling_conectadas(client_id)
    loja_txt = str(loja or "").strip()
    if not loja_txt or loja_txt in {"__todas", "Todas as lojas"}:
        return conectadas[:5]

    alvo_norm = _normalizar_texto(loja_txt)
    for nome in conectadas:
        if _normalizar_texto(nome) == alvo_norm:
            return [nome]
    for nome in conectadas:
        nome_norm = _normalizar_texto(nome)
        if alvo_norm and (alvo_norm in nome_norm or nome_norm in alvo_norm):
            return [nome]
    return conectadas[:5]


def _ia_obter_cfg_bling(client_id: str, nome_loja: str) -> dict:
    loja = buscar_loja(client_id, nome_loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja nao encontrada")

    integracoes = loja.get("integracoes") or {}
    cfg = dict(integracoes.get("bling") or {})
    if not cfg:
        raise HTTPException(status_code=400, detail="Integracao Bling nao configurada para esta loja")

    cfg["id"] = cfg.get("id") or cfg.get("client_id")
    cfg["secret"] = cfg.get("secret") or cfg.get("client_secret")
    if not cfg.get("access_token"):
        raise HTTPException(status_code=401, detail="Token Bling ausente. Refaca a autenticacao OAuth.")
    return cfg


def _ia_tool_get_integrations_status(client_id: str, loja: Optional[str] = None) -> Optional[dict]:
    try:
        loja_filtro = str(loja or "").strip()
        registros = []
        for loja_cfg in carregar_lojas(client_id) or []:
            if not isinstance(loja_cfg, dict):
                continue
            nome = str(loja_cfg.get("nome") or "").strip()
            if not nome:
                continue
            if loja_filtro and loja_filtro not in {"__todas", "Todas as lojas"}:
                if _normalizar_texto(loja_filtro) not in _normalizar_texto(nome):
                    continue
            integracoes = loja_cfg.get("integracoes") or {}
            cfg_ml = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
            cfg_bling = integracoes.get("bling") if isinstance(integracoes, dict) else {}
            registros.append({
                "loja": nome,
                "mercado_livre_conectado": bool(isinstance(cfg_ml, dict) and str(cfg_ml.get("access_token") or "").strip()),
                "mercado_livre_user_id": str((cfg_ml or {}).get("user_id") or "").strip() if isinstance(cfg_ml, dict) else "",
                "bling_conectado": bool(isinstance(cfg_bling, dict) and str(cfg_bling.get("access_token") or "").strip()),
                "bling_cliente_configurado": bool(isinstance(cfg_bling, dict) and str((cfg_bling or {}).get("id") or (cfg_bling or {}).get("client_id") or "").strip()),
            })

        return {
            "function": "get_integrations_status",
            "arguments": {"loja": loja_filtro or ""},
            "result": {
                "lojas": registros,
                "total_lojas": len(registros),
                "ml_conectadas": sum(1 for item in registros if item.get("mercado_livre_conectado")),
                "bling_conectadas": sum(1 for item in registros if item.get("bling_conectado")),
            },
        }
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar status das integracoes: %s", exc)
        return None


def _ia_ml_precisa_descricao(mensagem: str) -> bool:
    texto = _normalizar_texto(mensagem or "")
    return any(chave in texto for chave in ("DESCRICAO", "DESCRICAO DO ANUNCIO", "TEXTO DO ANUNCIO", "ANUNCIO COMPLETO"))


def _ia_ml_item_resumo(
    item: dict,
    loja: str,
    descricao: str = "",
    detalhes: Optional[dict] = None,
    requested_sku: str = "",
) -> dict:
    variacoes = [
        _ia_ml_variation_summary(var)
        for var in (item.get("variations") or [])[:8]
        if isinstance(var, dict)
    ]
    sku_match = item.get("_ia_sku_match") if isinstance(item.get("_ia_sku_match"), dict) else {}
    if requested_sku and not sku_match:
        sku_match = _ia_ml_find_exact_sku_match(item, requested_sku)
    matched_variation = sku_match.get("variation") if isinstance(sku_match.get("variation"), dict) else None
    parent_sku = _ia_ml_parent_sku_item(item)
    effective_sku = str(sku_match.get("matched_sku") or _ia_ml_sku_item(item) or "").strip()
    effective_price = matched_variation.get("price") if matched_variation else item.get("price")
    effective_available = matched_variation.get("available_quantity") if matched_variation else item.get("available_quantity")
    effective_sold = matched_variation.get("sold_quantity") if matched_variation else item.get("sold_quantity")
    result = {
        "loja": loja,
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "status": str(item.get("status") or "").strip(),
        "sub_status": item.get("sub_status") or [],
        "seller_sku": effective_sku,
        "parent_sku": parent_sku,
        "requested_sku": str(requested_sku or "").strip(),
        "matched_sku": str(sku_match.get("matched_sku") or "").strip(),
        "match": {
            "exact": bool(sku_match.get("exact")),
            "matched_by": str(sku_match.get("matched_by") or "").strip(),
            "variation_id": str((matched_variation or {}).get("id") or "").strip(),
        },
        "selected_variation": matched_variation,
        "currency_id": str(item.get("currency_id") or "BRL").strip(),
        "price": effective_price,
        "base_price": item.get("base_price"),
        "original_price": item.get("original_price"),
        "available_quantity": effective_available,
        "sold_quantity": effective_sold,
        "sold_quantity_scope": "acumulado_da_variacao" if matched_variation else "acumulado_do_anuncio",
        "item_price": item.get("price"),
        "item_available_quantity": item.get("available_quantity"),
        "item_sold_quantity": item.get("sold_quantity"),
        "listing_type_id": str(item.get("listing_type_id") or "").strip(),
        "category_id": str(item.get("category_id") or "").strip(),
        "permalink": str(item.get("permalink") or "").strip(),
        "thumbnail": str(item.get("thumbnail") or "").strip(),
        "health": item.get("health"),
        "catalog_listing": bool(item.get("catalog_listing")),
        "variations": variacoes,
        "description": descricao[:1500] if descricao else "",
    }
    if isinstance(detalhes, dict):
        result["details"] = detalhes
    return result


def _ia_ml_obter_descricao_item(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    *,
    timeout: int = 15,
) -> tuple[str, dict]:
    item_id_txt = str(item_id or "").strip()
    if not item_id_txt:
        return "", cfg
    try:
        resp, cfg = _ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id_txt}/description",
            timeout=max(1, min(int(timeout or 15), 15)),
        )
        if resp.status_code != 200:
            return "", cfg
        data = resp.json() or {}
        return str(data.get("plain_text") or data.get("text") or "").strip(), cfg
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar descricao ML %s/%s: %s", loja, item_id_txt, exc)
        return "", cfg


ML_IA_API_BASE = "https://api.mercadolibre.com"
ML_IA_QUERY_TIMEOUT_SECONDS = 60
ML_IA_LATEST_QUERY_TIMEOUT_SECONDS = 25
ML_IA_TARGET_MAX_ITEM_IDS = 100
ML_IA_TARGET_MAX_CLAIMS = 100
ML_IA_TARGET_MAX_ORDERS = 1000
ML_IA_ORDER_STATUSES = {
    "confirmed",
    "payment_required",
    "payment_in_process",
    "partially_paid",
    "paid",
    "partially_refunded",
    "pending_cancel",
    "cancelled",
    "invalid",
}
ML_IA_LISTING_STATUSES = {"active", "paused", "closed", "under_review", "inactive", "pending"}


def _ia_ml_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _ia_ml_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _ia_ml_money(value: Any) -> float:
    return round(_ia_ml_float(value), 2)


def _ia_ml_store_match_key(value: Any) -> str:
    """Normalize legacy mojibake store names without changing the stored label."""

    text = str(value or "").strip()
    markers = ("Ã", "Â", "â€", "ï¿½")
    for _ in range(3):
        if not any(marker in text for marker in markers):
            break
        candidates = []
        for encoding in ("cp1252", "latin1"):
            try:
                candidate = text.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if candidate and candidate != text:
                candidates.append(candidate)
        if not candidates:
            break
        text = min(candidates, key=lambda item: sum(item.count(marker) for marker in markers))
    return _normalizar_texto(text)


def _ia_ml_resolver_loja_exata(client_id: str, loja: Optional[str]) -> tuple[Optional[str], dict]:
    conectadas = []
    vistos = set()
    for nome in _ia_lojas_ml_conectadas(client_id) or []:
        nome_txt = str(nome or "").strip()
        chave = _ia_ml_store_match_key(nome_txt)
        if nome_txt and chave not in vistos:
            vistos.add(chave)
            conectadas.append(nome_txt)

    loja_txt = str(loja or "").strip()
    if not loja_txt or loja_txt in {"__todas", "Todas as lojas"}:
        return None, {
            "code": "store_required",
            "message": "Informe exatamente uma loja com Mercado Livre conectado.",
            "available_stores": conectadas,
        }

    alvo = _ia_ml_store_match_key(loja_txt)
    exatas = [nome for nome in conectadas if _ia_ml_store_match_key(nome) == alvo]
    if len(exatas) == 1:
        return exatas[0], {}
    if len(exatas) > 1:
        return None, {
            "code": "ambiguous_store",
            "message": "O nome informado corresponde a mais de uma loja autorizada.",
            "available_stores": exatas,
        }

    parciais = [
        nome
        for nome in conectadas
        if alvo and (alvo in _ia_ml_store_match_key(nome) or _ia_ml_store_match_key(nome) in alvo)
    ]
    return None, {
        "code": "ambiguous_store" if len(parciais) > 1 else "store_not_found",
        "message": "Loja nao encontrada por nome exato entre as contas autorizadas.",
        "available_stores": parciais or conectadas,
    }


def _ia_ml_base_result(*, warnings: Optional[list[str]] = None, reconnect_required: bool = False) -> dict:
    result = {
        "read_only": True,
        "sources": [],
        "warnings": list(warnings or []),
        "reconnect_required": bool(reconnect_required),
    }
    return result


def _ia_ml_mark_exact_failure(
    result: dict,
    *,
    sku: str,
    item_ids: list[str],
    stop_reason: str,
) -> None:
    """Keep an interrupted latest-event lookup identifiable as inconclusive."""

    normalized_ids = _ia_ml_normalizar_item_ids(*(item_ids or []))
    result.setdefault(
        "target",
        {
            "sku": str(sku or ""),
            "item_ids": normalized_ids,
            "resolution_complete": False,
            "filters_used": ["explicit_item_id"] if normalized_ids else [],
        },
    )
    result.setdefault(
        "match",
        {"exact": False, "matched_by": "", "sku": str(sku or ""), "item_id": ""},
    )
    coverage = result.get("exact_coverage") if isinstance(result.get("exact_coverage"), dict) else {}
    coverage.update({
        "complete": False,
        "stop_reason": str(stop_reason or "provider_unavailable")[:80],
        "pages_fetched": int(coverage.get("pages_fetched") or 0),
        "orders_inspected": int(coverage.get("orders_inspected") or 0),
    })
    result["coverage"] = coverage
    result["exact_coverage"] = coverage
    result["coverage_complete"] = False
    result["truncated"] = True


def _ia_ml_store_failure(function_name: str, arguments: dict, failure: dict) -> dict:
    result = _ia_ml_base_result()
    result.update({
        "found": False,
        "error": str(failure.get("code") or "store_not_found"),
        "message": str(failure.get("message") or "Loja do Mercado Livre indisponivel."),
        "available_stores": list(failure.get("available_stores") or []),
        "paging": {"offset": 0, "limit": 0, "returned": 0, "total": 0, "next_offset": None, "has_more": False},
        "truncated": False,
    })
    if function_name == "get_mercado_livre_listing":
        result["matches"] = []
    elif function_name == "get_mercado_livre_returns":
        result.update({"devolucoes": [], "returns": []})
    else:
        result.update({"orders": [], "by_sku": [], "totals": {}})
    return {"function": function_name, "arguments": arguments, "result": result}


def _ia_ml_remaining_timeout(deadline: Optional[float], requested: int) -> int:
    if deadline is None:
        return max(1, int(requested or 1))
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise requests.exceptions.Timeout("A consulta completa do Mercado Livre excedeu 60 segundos.")
    return max(1, min(int(requested or 1), int(math.ceil(remaining))))


def _ia_ml_request_get(
    client_id: str,
    loja: str,
    cfg: dict,
    url: str,
    *,
    params=None,
    timeout: int = 20,
    deadline: Optional[float] = None,
):
    """Executa somente GET e repete uma vez exclusivamente em timeout/5xx.

    O refresh OAuth continua centralizado em ``_ml_api_request``. Portanto, um
    401 devolvido por este helper ja e o resultado posterior a essa tentativa.
    """
    last_exc = None
    for attempt in range(2):
        try:
            request_timeout = _ia_ml_remaining_timeout(deadline, timeout)
            resp, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                url,
                params=params,
                timeout=request_timeout,
            )
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            if attempt == 0:
                continue
            raise
        response_status = int(getattr(resp, "status_code", 0) or 0)
        if 500 <= response_status <= 599 and attempt == 0:
            continue
        return resp, cfg
    raise last_exc or requests.exceptions.Timeout("Mercado Livre nao respondeu.")


def _ia_ml_http_failure(resp, fallback: str) -> tuple[str, str, bool]:
    status_code = int(getattr(resp, "status_code", 0) or 0)
    reconnect_required = status_code == 401
    if reconnect_required:
        return "reconnect_required", "A autenticacao do Mercado Livre precisa ser refeita para esta loja.", True
    if status_code == 429:
        return "rate_limited", "O Mercado Livre limitou temporariamente as consultas. Tente novamente mais tarde.", False
    try:
        detail = _ml_parse_error_detail(resp, fallback)
    except Exception:
        detail = fallback
    return f"http_{status_code or 'error'}", str(detail or fallback)[:240], False


def _ia_ml_normalizar_item_ids(*values: Any) -> list[str]:
    ids = []
    seen = set()
    for value in values:
        for candidate in re.findall(r"\bMLB[\s_-]*\d+\b", str(value or "").upper()):
            normalized = re.sub(r"[^A-Z0-9]", "", str(candidate or "").upper())
            if normalized.startswith("MLB") and normalized not in seen:
                seen.add(normalized)
                ids.append(normalized)
    return ids


def _ia_ml_sku_parece_data(value: Any) -> bool:
    texto = str(value or "").strip()
    return bool(
        re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", texto)
        or re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", texto)
    )


def _ia_ml_sku_item(item: dict) -> str:
    extractor = globals().get("_ml_extrair_sku")
    if callable(extractor):
        try:
            sku = str(extractor(item) or "").strip()
            if sku:
                return sku
        except Exception:
            pass
    for key in ("seller_sku", "seller_custom_field", "sku"):
        sku = str((item or {}).get(key) or "").strip()
        if sku:
            return sku
    for attribute in (item or {}).get("attributes") or []:
        if not isinstance(attribute, dict):
            continue
        if str(attribute.get("id") or "").upper() in {"SELLER_SKU", "SKU"}:
            sku = str(attribute.get("value_name") or attribute.get("value_id") or "").strip()
            if sku:
                return sku
    return ""


def _ia_ml_sku_comparison_key(value: Any) -> str:
    """Normaliza SKU sem confundir variantes numericamente diferentes."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9]", "", text)


def _ia_ml_parent_sku_item(item: dict) -> str:
    """Extrai apenas o SKU do anuncio pai, sem herdar SKUs das variacoes."""
    parent = dict(item or {})
    parent.pop("variations", None)
    parent.pop("variations_data", None)
    return _ia_ml_sku_item(parent)


def _ia_ml_variation_options(variation: dict) -> tuple[list[dict[str, str]], str]:
    options: list[dict[str, str]] = []
    for attribute in (variation or {}).get("attribute_combinations") or []:
        if not isinstance(attribute, dict):
            continue
        name = str(attribute.get("name") or attribute.get("id") or "Variação").strip()
        value = str(attribute.get("value_name") or attribute.get("value_id") or "").strip()
        if not value:
            values = attribute.get("values") if isinstance(attribute.get("values"), list) else []
            for entry in values:
                if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                    value = str(entry.get("name") or "").strip()
                    break
        if value:
            options.append({"id": str(attribute.get("id") or "").strip(), "name": name, "value": value})
    label = " | ".join(
        f"{option['name']}: {option['value']}" if option.get("name") else option["value"]
        for option in options
    )
    return options, label


def _ia_ml_variation_summary(variation: dict) -> dict:
    options, option_label = _ia_ml_variation_options(variation)
    return {
        "id": str((variation or {}).get("id") or "").strip(),
        "sku": _ia_ml_sku_item(variation or {}),
        "option_label": option_label,
        "options": options,
        "price": (variation or {}).get("price"),
        "available_quantity": (variation or {}).get("available_quantity"),
        "sold_quantity": (variation or {}).get("sold_quantity"),
    }


def _ia_ml_find_exact_sku_match(item: dict, requested_sku: str) -> dict:
    requested = str(requested_sku or "").strip()
    requested_key = _ia_ml_sku_comparison_key(requested)
    if not requested_key:
        return {}
    parent_sku = _ia_ml_parent_sku_item(item)
    if parent_sku and _ia_ml_sku_comparison_key(parent_sku) == requested_key:
        return {
            "exact": True,
            "requested_sku": requested,
            "matched_sku": parent_sku,
            "matched_by": "parent_seller_sku",
            "parent_sku": parent_sku,
            "variation": None,
        }
    for variation in (item or {}).get("variations") or []:
        if not isinstance(variation, dict):
            continue
        variation_sku = _ia_ml_sku_item(variation)
        if variation_sku and _ia_ml_sku_comparison_key(variation_sku) == requested_key:
            return {
                "exact": True,
                "requested_sku": requested,
                "matched_sku": variation_sku,
                "matched_by": "variation_seller_sku",
                "parent_sku": parent_sku,
                "variation": _ia_ml_variation_summary(variation),
            }
    return {}


def _ia_ml_parse_date(value: Any, tz: ZoneInfo, *, end_of_day: bool = False) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value or "").strip()
        if not text:
            return None
        parsed = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                parsed = datetime.strptime(text[:10], fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    if end_of_day:
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _ia_ml_periodo_orders(
    mensagem: str,
    data_inicio: Any,
    data_fim: Any,
) -> tuple[Optional[datetime], Optional[datetime], list[str], dict, bool]:
    tz = ZoneInfo("America/Sao_Paulo")
    warnings = []
    dates_in_message = re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", str(mensagem or ""))
    inicio_raw = data_inicio or (dates_in_message[0] if dates_in_message else None)
    fim_raw = data_fim or (dates_in_message[1] if len(dates_in_message) > 1 else None)
    now = datetime.now(tz)
    fim = _ia_ml_parse_date(fim_raw, tz, end_of_day=True) or now.replace(microsecond=0)
    inicio = _ia_ml_parse_date(inicio_raw, tz) or (fim - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    requested = {
        "from": inicio.isoformat(timespec="milliseconds"),
        "to": fim.isoformat(timespec="milliseconds"),
    }
    if inicio > fim:
        return None, None, warnings, requested, False
    coverage_cutoff = now - timedelta(days=365)
    historical_only = bool(inicio < coverage_cutoff or (fim - inicio) > timedelta(days=365))
    if historical_only:
        warnings.append(
            "O periodo solicitado ultrapassa a cobertura de 12 meses da API de pedidos do Mercado Livre; use o historico local como fonte separada."
        )
    return inicio, fim, warnings, requested, historical_only


def _ia_ml_order_refund(order: dict) -> Optional[float]:
    for key in ("refunded_amount", "refund_amount", "amount_refunded"):
        if (order or {}).get(key) is not None:
            return _ia_ml_money((order or {}).get(key))
    found = False
    total = 0.0
    for payment in (order or {}).get("payments") or []:
        if not isinstance(payment, dict):
            continue
        for key in ("transaction_amount_refunded", "amount_refunded", "refund_amount"):
            if payment.get(key) is not None:
                found = True
                total += _ia_ml_float(payment.get(key))
                break
    if found:
        return round(total, 2)
    if str((order or {}).get("status") or "").strip().lower() == "partially_refunded":
        return None
    return 0.0


def _ia_ml_order_paid(order: dict, gross: float) -> float:
    if (order or {}).get("paid_amount") is not None:
        return _ia_ml_money((order or {}).get("paid_amount"))
    total = 0.0
    found = False
    for payment in (order or {}).get("payments") or []:
        if not isinstance(payment, dict) or str(payment.get("status") or "").lower() not in {"approved", "refunded", "partially_refunded"}:
            continue
        value = payment.get("total_paid_amount")
        if value is None:
            value = payment.get("transaction_amount")
        if value is not None:
            found = True
            total += _ia_ml_float(value)
    return round(total if found else gross, 2)


def _ia_ml_buyer_name(order: dict) -> str:
    """Retorna somente o nome/apelido publico permitido do comprador."""
    buyer = (order or {}).get("buyer") if isinstance((order or {}).get("buyer"), dict) else {}
    full_name = " ".join(
        str(buyer.get(key) or "").strip()
        for key in ("first_name", "last_name")
        if str(buyer.get(key) or "").strip()
    ).strip()
    return (full_name or str(buyer.get("nickname") or "").strip())[:120]


def _ia_ml_city_name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("city_name")
    return str(value or "").strip()[:120]


def _ia_ml_order_embedded_city(order: dict) -> str:
    shipping = (order or {}).get("shipping") if isinstance((order or {}).get("shipping"), dict) else {}
    candidates = [
        shipping.get("receiver_address"),
        shipping.get("shipping_address"),
        (order or {}).get("receiver_address"),
    ]
    destination = shipping.get("destination") if isinstance(shipping.get("destination"), dict) else {}
    candidates.append(destination.get("shipping_address"))
    for address in candidates:
        if not isinstance(address, dict):
            continue
        city = _ia_ml_city_name(address.get("city") or address.get("city_name"))
        if city:
            return city
    return ""


def _ia_ml_order_shipment_id(order: dict) -> str:
    shipping = (order or {}).get("shipping") if isinstance((order or {}).get("shipping"), dict) else {}
    shipment_id = str(shipping.get("id") or (order or {}).get("shipping_id") or "").strip()
    return re.sub(r"[^0-9]", "", shipment_id)


def _ia_ml_sanitize_order(order: dict, buyer_city: str = "", include_buyer_summary: bool = False) -> dict:
    items = []
    items_gross = 0.0
    for entry in (order or {}).get("order_items") or []:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        quantity = _ia_ml_float(entry.get("quantity"))
        unit_price = _ia_ml_float(entry.get("unit_price"))
        gross = round(quantity * unit_price, 2)
        items_gross += gross
        items.append({
            "item_id": str(item.get("id") or "").strip(),
            "sku": _ia_ml_sku_item(item),
            "title": str(item.get("title") or "").strip()[:160],
            "variation_id": str(item.get("variation_id") or "").strip(),
            "variation_attributes": [
                {
                    "name": str(attribute.get("name") or attribute.get("id") or "").strip()[:100],
                    "value": str(attribute.get("value_name") or attribute.get("value_id") or "").strip()[:160],
                }
                for attribute in (item.get("variation_attributes") or [])[:20]
                if isinstance(attribute, dict)
            ],
            "quantity": quantity,
            "unit_price": round(unit_price, 2),
            "gross_amount": gross,
            "currency_id": str(entry.get("currency_id") or (order or {}).get("currency_id") or "BRL").strip(),
        })
    gross = _ia_ml_money((order or {}).get("total_amount") if (order or {}).get("total_amount") is not None else items_gross)
    paid = _ia_ml_order_paid(order, gross)
    refund = _ia_ml_order_refund(order)
    net = round(paid - refund, 2) if refund is not None else None
    result = {
        "order_id": str((order or {}).get("id") or "").strip(),
        "pack_id": str((order or {}).get("pack_id") or (order or {}).get("id") or "").strip(),
        "status": str((order or {}).get("status") or "").strip(),
        "status_detail": str((order or {}).get("status_detail") or "").strip()[:240],
        "date_created": str((order or {}).get("date_created") or "").strip(),
        "date_closed": str((order or {}).get("date_closed") or "").strip(),
        "date_last_updated": str((order or {}).get("date_last_updated") or (order or {}).get("last_updated") or "").strip(),
        "currency_id": str((order or {}).get("currency_id") or "BRL").strip(),
        "gross_amount": gross,
        "paid_amount": paid,
        "refund_amount": refund,
        "net_amount": net,
        "items": items,
    }
    if include_buyer_summary:
        buyer = (order or {}).get("buyer") if isinstance((order or {}).get("buyer"), dict) else {}
        result["buyer_name"] = _ia_ml_buyer_name(order)
        result["buyer_nickname"] = str(buyer.get("nickname") or "").strip()[:120]
        result["buyer_city"] = str(buyer_city or _ia_ml_order_embedded_city(order)).strip()[:120]
    return result


def _ia_ml_order_details_requested(mensagem: str, incluir_detalhes: bool, order_id: str) -> bool:
    if incluir_detalhes or order_id:
        return True
    normalized = _normalizar_texto(str(mensagem or "")).lower()
    return bool(re.search(r"\b(ultima|ultimo|detalhe|detalhes|comprador|cliente|cidade|destinatario)\b", normalized))


def _ia_ml_claims_period(
    mensagem: str,
    data_inicio: Any,
    data_fim: Any,
) -> tuple[Optional[datetime], Optional[datetime], dict[str, str]]:
    tz = ZoneInfo("America/Sao_Paulo")
    text = str(mensagem or "")
    normalized = _normalizar_texto(text).lower()
    dates_in_message = re.findall(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", text)
    latest_requested = bool(
        re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", normalized)
    )
    now = datetime.now(tz).replace(microsecond=0)
    if latest_requested and not dates_in_message:
        inicio = (now - timedelta(days=364)).replace(hour=0, minute=0, second=0, microsecond=0)
        fim = now
    else:
        inicio_raw = data_inicio or (dates_in_message[0] if dates_in_message else None)
        fim_raw = data_fim or (dates_in_message[1] if len(dates_in_message) > 1 else None)
        fim = _ia_ml_parse_date(fim_raw, tz, end_of_day=True) or now
        inicio = _ia_ml_parse_date(inicio_raw, tz) or (fim - timedelta(days=29)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if inicio > fim:
        return None, None, {"from": inicio.isoformat(timespec="milliseconds"), "to": fim.isoformat(timespec="milliseconds")}
    return inicio, fim, {"from": inicio.isoformat(timespec="milliseconds"), "to": fim.isoformat(timespec="milliseconds")}


def _ia_ml_sanitize_return_detail(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    shipments = []
    for shipment in payload.get("shipments") or []:
        if not isinstance(shipment, dict):
            continue
        shipments.append({
            "shipment_id": str(shipment.get("shipment_id") or shipment.get("id") or "").strip(),
            "status": str(shipment.get("status") or "").strip(),
            "date_created": str(shipment.get("date_created") or "").strip(),
            "last_updated": str(shipment.get("last_updated") or "").strip(),
        })
    return {
        "return_id": str(payload.get("id") or "").strip(),
        "status": str(payload.get("status") or "").strip(),
        "status_money": str(payload.get("status_money") or "").strip(),
        "type": str(payload.get("type") or "").strip(),
        "subtype": str(payload.get("subtype") or "").strip(),
        "refund_at": str(payload.get("refund_at") or "").strip(),
        "date_created": str(payload.get("date_created") or "").strip(),
        "date_closed": str(payload.get("date_closed") or "").strip(),
        "last_updated": str(payload.get("last_updated") or "").strip(),
        "shipments": shipments,
    }


def _ia_ml_sanitize_return_claim(claim: dict) -> dict[str, Any]:
    resolution = claim.get("resolution") if isinstance(claim.get("resolution"), dict) else {}
    return {
        "claim_id": str(claim.get("id") or "").strip(),
        "order_id": str(claim.get("resource_id") or claim.get("order_id") or "").strip(),
        "resource": str(claim.get("resource") or "").strip(),
        "type": str(claim.get("type") or "").strip(),
        "stage": str(claim.get("stage") or "").strip(),
        "status": str(claim.get("status") or "").strip(),
        "reason_id": str(claim.get("reason_id") or "").strip(),
        "quantity_type": str(claim.get("quantity_type") or "").strip(),
        "claimed_quantity": _ia_ml_float(claim.get("claimed_quantity")),
        "date_created": str(claim.get("date_created") or "").strip(),
        "last_updated": str(claim.get("last_updated") or "").strip(),
        "resolution": {
            "reason": str(resolution.get("reason") or "").strip(),
            "date_created": str(resolution.get("date_created") or "").strip(),
        },
    }


def _ia_ml_claim_has_return(claim: Any) -> bool:
    if not isinstance(claim, dict):
        return False
    if str(claim.get("type") or "").strip().lower() in {"return", "returns"}:
        return True
    for entity in claim.get("related_entities") or []:
        if isinstance(entity, str) and entity.strip().lower() == "return":
            return True
        if isinstance(entity, dict):
            entity_type = str(
                entity.get("type") or entity.get("name") or entity.get("resource") or ""
            ).strip().lower()
            if entity_type == "return":
                return True
    return False


def _ia_ml_latest_requested(message: Any) -> bool:
    normalized = _normalizar_texto(str(message or "")).lower()
    return bool(
        re.search(
            r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b",
            normalized,
        )
    )


def _ia_ml_latest_deadline(query_deadline: Optional[float] = None) -> float:
    """Return the hard deadline for one exact latest-event lookup."""

    deadline = time.monotonic() + ML_IA_LATEST_QUERY_TIMEOUT_SECONDS
    if query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    return deadline


def _ia_ml_resolve_sku_item_ids(
    client_id: str,
    loja: str,
    cfg: dict,
    sku: str,
    *,
    item_ids: Optional[list[str]] = None,
    deadline: Optional[float] = None,
) -> tuple[dict[str, Any], dict]:
    """Resolve an exact seller SKU to listing ids without restricting status."""

    explicit_ids = _ia_ml_normalizar_item_ids(*(item_ids or []))
    sku_value = str(sku or "").strip()
    target = {
        "sku": sku_value,
        "item_ids": explicit_ids[:ML_IA_TARGET_MAX_ITEM_IDS],
        "resolution_complete": True,
        "filters_used": ["explicit_item_id"] if explicit_ids else [],
        "sources": [],
        "warnings": [],
        "error": "",
        "message": "",
        "reconnect_required": False,
    }
    if explicit_ids or not sku_value:
        return target, cfg

    seller_id = str((cfg or {}).get("user_id") or "").strip()
    if not seller_id:
        target.update({
            "resolution_complete": False,
            "error": "seller_id_missing",
            "message": "A conta Mercado Livre nao informou o seller id.",
        })
        return target, cfg

    url = f"{ML_IA_API_BASE}/users/{seller_id}/items/search"
    seen: set[str] = set()
    successful_filters = 0
    for field in ("seller_sku", "sku"):
        try:
            response, cfg = _ia_ml_request_get(
                client_id,
                loja,
                cfg,
                url,
                params={field: sku_value, "offset": 0, "limit": ML_IA_TARGET_MAX_ITEM_IDS},
                timeout=10,
                deadline=deadline,
            )
        except requests.exceptions.Timeout:
            target["resolution_complete"] = False
            target["warnings"].append(f"A resolucao do SKU pelo campo {field} excedeu o prazo.")
            break
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in {200, 206}:
            code, message, reconnect = _ia_ml_http_failure(
                response,
                "Erro ao localizar os anuncios do SKU no Mercado Livre",
            )
            target["resolution_complete"] = False
            target["warnings"].append(f"Filtro {field}: {message}")
            if not target["error"]:
                target.update({
                    "error": code,
                    "message": message,
                    "reconnect_required": reconnect,
                })
            if reconnect:
                break
            continue
        successful_filters += 1
        target["filters_used"].append(field)
        target["sources"].append("users/{seller_id}/items/search")
        payload = response.json() or {}
        raw_results = payload.get("results") or []
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _ia_ml_int(paging.get("total"), len(raw_results), 0, 100_000_000)
        if status_code == 206 or total > len(raw_results):
            target["resolution_complete"] = False
        for entry in raw_results:
            item_id = str((entry.get("id") if isinstance(entry, dict) else entry) or "").strip().upper()
            if item_id.startswith("MLB") and item_id not in seen:
                seen.add(item_id)
                target["item_ids"].append(item_id)
                if len(target["item_ids"]) >= ML_IA_TARGET_MAX_ITEM_IDS:
                    break
        if len(target["item_ids"]) >= ML_IA_TARGET_MAX_ITEM_IDS:
            target["resolution_complete"] = target["resolution_complete"] and total <= ML_IA_TARGET_MAX_ITEM_IDS
            break

    target["item_ids"] = list(dict.fromkeys(target["item_ids"]))[:ML_IA_TARGET_MAX_ITEM_IDS]
    if successful_filters and not target["item_ids"]:
        target["message"] = "Nenhum anuncio foi localizado para o SKU informado."
    if successful_filters and target["error"] and target["item_ids"]:
        target["error"] = ""
        target["message"] = ""
        target["reconnect_required"] = False
    return target, cfg


def _ia_ml_order_matches_target(order: dict, sku: str, item_ids: list[str]) -> dict[str, Any]:
    """Match an order item exactly by SKU, or by an item id resolved from it."""

    sanitized = order if isinstance(order.get("items"), list) else _ia_ml_sanitize_order(order)
    sku_norm = _normalizar_texto(str(sku or ""))
    ids = {str(item or "").strip().upper() for item in item_ids if str(item or "").strip()}
    item_id_fallback = None
    for entry in sanitized.get("items") or []:
        if not isinstance(entry, dict):
            continue
        entry_sku = str(entry.get("sku") or "").strip()
        entry_id = str(entry.get("item_id") or "").strip().upper()
        if sku_norm and _normalizar_texto(entry_sku) == sku_norm:
            return {
                "exact": True,
                "matched_by": "sku",
                "sku": entry_sku or str(sku or ""),
                "item_id": entry_id,
            }
        if entry_id and entry_id in ids and (not sku_norm or not entry_sku):
            item_id_fallback = {
                "exact": True,
                "matched_by": "item_id_resolved_from_sku" if sku_norm else "item_id",
                "sku": str(sku or entry_sku),
                "item_id": entry_id,
            }
    return item_id_fallback or {
        "exact": False,
        "matched_by": "",
        "sku": str(sku or ""),
        "item_id": "",
    }


def _ia_ml_order_sort_value(order: dict) -> float:
    for value in (
        order.get("date_closed"),
        order.get("date_created"),
        order.get("date_last_updated"),
    ):
        text = str(value or "").strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return 0.0


def _ia_ml_find_latest_order_by_target(
    client_id: str,
    loja: str,
    cfg: dict,
    *,
    seller_id: str,
    inicio: datetime,
    fim: datetime,
    statuses: list[str],
    sku: str,
    item_ids: list[str],
    include_buyer_summary: bool,
    deadline: float,
) -> tuple[dict[str, Any], dict]:
    """Find the newest exact order for every listing mapped to the target SKU."""

    target, cfg = _ia_ml_resolve_sku_item_ids(
        client_id,
        loja,
        cfg,
        sku,
        item_ids=item_ids,
        deadline=deadline,
    )
    ids = list(target.get("item_ids") or [])
    outcome: dict[str, Any] = {
        "target": target,
        "orders": [],
        "match": {"exact": False, "matched_by": "", "sku": str(sku or ""), "item_id": ""},
        "coverage": {
            "complete": bool(target.get("resolution_complete")),
            "stop_reason": "target_not_resolved" if not ids else "exhausted",
            "pages_fetched": 0,
            "orders_inspected": 0,
            "item_ids_total": len(ids),
            "item_ids_checked": 0,
        },
        "evidence": [],
        "warnings": list(target.get("warnings") or []),
        "partial_response": False,
        "error": str(target.get("error") or ""),
        "message": str(target.get("message") or ""),
        "reconnect_required": bool(target.get("reconnect_required")),
    }
    if not ids:
        outcome["coverage"]["complete"] = bool(target.get("resolution_complete") and not target.get("error"))
        return outcome, cfg

    candidates: list[tuple[float, dict, dict]] = []
    inspected = 0
    pages = 0
    checked_ids = 0
    complete = bool(target.get("resolution_complete"))
    local_status_filter = "partially_refunded" in statuses
    for item_id in ids:
        if time.monotonic() >= deadline or inspected >= ML_IA_TARGET_MAX_ORDERS:
            complete = False
            outcome["coverage"]["stop_reason"] = "deadline" if time.monotonic() >= deadline else "order_cap"
            break
        api_offset = 0
        item_complete = False
        item_pages = 0
        while item_pages < 20 and inspected < ML_IA_TARGET_MAX_ORDERS:
            params = {
                "seller": seller_id,
                "q": item_id,
                "order.date_created.from": inicio.isoformat(timespec="milliseconds"),
                "order.date_created.to": fim.isoformat(timespec="milliseconds"),
                "sort": "date_desc",
                "offset": api_offset,
                "limit": 50,
            }
            if not local_status_filter:
                params["order.status"] = ",".join(statuses)
            response, cfg = _ia_ml_request_get(
                client_id,
                loja,
                cfg,
                f"{ML_IA_API_BASE}/orders/search",
                params=params,
                timeout=10,
                deadline=deadline,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code not in {200, 206}:
                code, message, reconnect = _ia_ml_http_failure(response, "Erro ao consultar pedidos do SKU")
                outcome.update({"error": code, "message": message, "reconnect_required": reconnect})
                complete = False
                outcome["coverage"]["stop_reason"] = code
                break
            if status_code == 206:
                outcome["partial_response"] = True
                complete = False
            payload = response.json() or {}
            rows = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            total = _ia_ml_int(paging.get("total"), len(rows), 0, 100_000_000)
            pages += 1
            item_pages += 1
            if not rows:
                item_complete = True
                break
            found_for_item = False
            for raw in rows:
                inspected += 1
                sanitized = _ia_ml_sanitize_order(raw, include_buyer_summary=include_buyer_summary)
                if str(sanitized.get("status") or "").strip().lower() not in statuses:
                    continue
                match = _ia_ml_order_matches_target(sanitized, sku, [item_id])
                if not match.get("exact"):
                    continue
                candidates.append((_ia_ml_order_sort_value(sanitized), sanitized, match))
                found_for_item = True
                item_complete = True
                break
            api_offset += len(rows)
            if found_for_item or api_offset >= total or len(rows) < 50:
                item_complete = True
                break
        if outcome.get("error"):
            break
        checked_ids += 1
        if not item_complete:
            complete = False
            outcome["coverage"]["stop_reason"] = "page_cap"

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    if candidates:
        _sort, selected, match = candidates[0]
        outcome["orders"] = [selected]
        outcome["match"] = match
        if complete and checked_ids == len(ids):
            outcome["coverage"]["stop_reason"] = "exact_match"
    elif complete and checked_ids == len(ids):
        outcome["coverage"]["stop_reason"] = "exhausted"
    outcome["coverage"].update({
        "complete": bool(complete and checked_ids == len(ids) and not outcome.get("partial_response") and not outcome.get("error")),
        "pages_fetched": pages,
        "orders_inspected": inspected,
        "item_ids_checked": checked_ids,
    })
    outcome["evidence"] = [
        {"resource": "users/{seller_id}/items/search", "identifiers": ids[:20]},
        {"resource": "orders/search", "identifiers": [str(row[1].get("order_id") or "") for row in candidates[:20]]},
    ]
    return outcome, cfg


def _ia_ml_find_latest_return_by_target(
    client_id: str,
    loja: str,
    cfg: dict,
    *,
    seller_id: str,
    inicio: datetime,
    fim: datetime,
    sku: str,
    item_ids: list[str],
    deadline: float,
) -> tuple[dict[str, Any], dict]:
    """Find the newest return whose order contains the exact target SKU."""

    target, cfg = _ia_ml_resolve_sku_item_ids(
        client_id,
        loja,
        cfg,
        sku,
        item_ids=item_ids,
        deadline=deadline,
    )
    resolved_ids = list(target.get("item_ids") or [])
    outcome: dict[str, Any] = {
        "target": target,
        "rows": [],
        "match": {"exact": False, "matched_by": "", "sku": str(sku or ""), "item_id": ""},
        "coverage": {
            "complete": False,
            "stop_reason": "exhausted",
            "pages_fetched": 0,
            "claims_total": 0,
            "claims_scanned": 0,
            "orders_inspected": 0,
        },
        "evidence": [],
        "warnings": list(target.get("warnings") or []),
        "partial_response": False,
        "error": str(target.get("error") or ""),
        "message": str(target.get("message") or ""),
        "reconnect_required": bool(target.get("reconnect_required")),
    }
    if not sku and not resolved_ids:
        outcome.update({
            "error": str(target.get("error") or "target_required"),
            "message": str(target.get("message") or "Informe um SKU ou MLB para localizar a devolucao exata."),
            "reconnect_required": bool(target.get("reconnect_required")),
        })
        outcome["coverage"]["stop_reason"] = "target_not_resolved"
        return outcome, cfg

    claim_offset = 0
    claims_total = 0
    claims_scanned = 0
    orders_inspected = 0
    pages = 0
    coverage_degraded = bool(
        not target.get("resolution_complete")
        or target.get("error")
    )
    found = False
    while claims_scanned < ML_IA_TARGET_MAX_CLAIMS and pages < 5:
        if time.monotonic() >= deadline:
            outcome["coverage"]["stop_reason"] = "deadline"
            coverage_degraded = True
            break
        page_limit = min(20, ML_IA_TARGET_MAX_CLAIMS - claims_scanned)
        response, cfg = _ia_ml_request_get(
            client_id,
            loja,
            cfg,
            f"{ML_IA_API_BASE}/post-purchase/v1/claims/search",
            params={
                "player_user_id": seller_id,
                "player_role": "respondent",
                "resource": "order",
                "range": f"last_updated:after:{inicio.isoformat(timespec='milliseconds')},before:{fim.isoformat(timespec='milliseconds')}",
                "sort": "last_updated:desc",
                "offset": claim_offset,
                "limit": page_limit,
            },
            timeout=10,
            deadline=deadline,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in {200, 206}:
            code, message, reconnect = _ia_ml_http_failure(response, "Erro ao consultar devolucoes do Mercado Livre")
            outcome.update({"error": code, "message": message, "reconnect_required": reconnect})
            outcome["coverage"]["stop_reason"] = code
            coverage_degraded = True
            break
        if status_code == 206:
            outcome["partial_response"] = True
            coverage_degraded = True
        payload = response.json() or {}
        claims = [item for item in (payload.get("data") or []) if isinstance(item, dict)]
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        claims_total = _ia_ml_int(paging.get("total"), claims_total, 0, 100_000_000)
        pages += 1
        if not claims:
            break

        for claim in claims:
            claims_scanned += 1
            order_id = str(claim.get("resource_id") or claim.get("order_id") or "").strip()
            if not order_id:
                coverage_degraded = True
                continue
            order_response, cfg = _ia_ml_request_get(
                client_id,
                loja,
                cfg,
                f"{ML_IA_API_BASE}/orders/{order_id}",
                timeout=10,
                deadline=deadline,
            )
            order_status = int(getattr(order_response, "status_code", 0) or 0)
            if order_status not in {200, 206}:
                code, message, reconnect = _ia_ml_http_failure(order_response, f"Pedido {order_id} indisponivel")
                coverage_degraded = True
                outcome["warnings"].append(message)
                if reconnect:
                    outcome.update({"error": code, "message": message, "reconnect_required": True})
                    outcome["coverage"]["stop_reason"] = code
                    break
                continue
            if order_status == 206:
                coverage_degraded = True
                outcome["partial_response"] = True
            orders_inspected += 1
            sanitized_order = _ia_ml_sanitize_order(order_response.json() or {})
            match = _ia_ml_order_matches_target(sanitized_order, sku, resolved_ids)
            if not match.get("exact"):
                continue

            effective_claim = claim
            claim_id = str(claim.get("id") or "").strip()
            if not _ia_ml_claim_has_return(effective_claim):
                detail_response, cfg = _ia_ml_request_get(
                    client_id,
                    loja,
                    cfg,
                    f"{ML_IA_API_BASE}/post-purchase/v1/claims/{claim_id}",
                    timeout=10,
                    deadline=deadline,
                )
                if int(getattr(detail_response, "status_code", 0) or 0) not in {200, 206}:
                    coverage_degraded = True
                    outcome["warnings"].append(f"Reclamacao {claim_id}: detalhes indisponiveis.")
                    continue
                detail_payload = detail_response.json() or {}
                if isinstance(detail_payload, dict):
                    effective_claim = detail_payload
            if not _ia_ml_claim_has_return(effective_claim):
                continue

            return_response, cfg = _ia_ml_request_get(
                client_id,
                loja,
                cfg,
                f"{ML_IA_API_BASE}/post-purchase/v2/claims/{claim_id}/returns",
                timeout=10,
                deadline=deadline,
            )
            if int(getattr(return_response, "status_code", 0) or 0) not in {200, 206}:
                coverage_degraded = True
                outcome["warnings"].append(f"Reclamacao {claim_id}: retorno indisponivel.")
                continue
            return_payload: Any = return_response.json() or {}
            if isinstance(return_payload, list):
                return_payload = next((item for item in return_payload if isinstance(item, dict)), {})
            if not isinstance(return_payload, dict) or not return_payload.get("id"):
                coverage_degraded = True
                continue
            return_detail = _ia_ml_sanitize_return_detail(return_payload)
            row = _ia_ml_sanitize_return_claim(effective_claim)
            row["order"] = sanitized_order
            row["return_detail"] = return_detail
            row["event_at"] = str(
                return_detail.get("last_updated")
                or return_detail.get("refund_at")
                or return_detail.get("date_created")
                or row.get("last_updated")
                or ""
            )
            row["match"] = match
            outcome["rows"] = [row]
            outcome["match"] = match
            outcome["coverage"]["stop_reason"] = "exact_match"
            found = True
            break
        if outcome.get("reconnect_required") or found:
            break
        claim_offset += len(claims)
        if claim_offset >= claims_total or len(claims) < page_limit:
            break

    claims_remaining = bool(claim_offset < claims_total and not found)
    if not found and claims_remaining and claims_scanned >= ML_IA_TARGET_MAX_CLAIMS:
        outcome["coverage"]["stop_reason"] = "claim_cap"
        coverage_degraded = True
    if not found and not claims_remaining and not outcome.get("error"):
        outcome["coverage"]["stop_reason"] = "exhausted"
    outcome["coverage"].update({
        "complete": bool(
            (found or not claims_remaining)
            and not coverage_degraded
            and not outcome.get("partial_response")
            and not outcome.get("error")
        ),
        "pages_fetched": pages,
        "claims_total": claims_total,
        "claims_scanned": claims_scanned,
        "orders_inspected": orders_inspected,
    })
    outcome["evidence"] = [
        {"resource": "users/{seller_id}/items/search", "identifiers": resolved_ids[:20]},
        {"resource": "post-purchase/v1/claims/search", "identifiers": []},
        {
            "resource": "orders/{order_id}",
            "identifiers": [str(row.get("order_id") or "") for row in outcome.get("rows") or []],
        },
        {
            "resource": "post-purchase/v2/claims/{claim_id}/returns",
            "identifiers": [str(row.get("claim_id") or "") for row in outcome.get("rows") or []],
        },
    ]
    return outcome, cfg


ML_IA_EXACT_MAX_MESSAGES = 300
ML_IA_EXACT_MAX_CLAIMS = 100


def _ia_ml_exact_source_add(sources: list[dict], seen: set[tuple[str, str]], resource: str, store: str) -> None:
    key = (str(resource or "").strip(), str(store or "").strip())
    if not key[0] or key in seen:
        return
    seen.add(key)
    sources.append({
        "provider": "mercado_livre",
        "resource": key[0],
        "method": "GET",
        "store": key[1],
    })


def _ia_ml_exact_partial_fields(resp: Any) -> list[str]:
    if int(getattr(resp, "status_code", 0) or 0) != 206:
        return []
    headers = getattr(resp, "headers", {}) or {}
    raw = str(headers.get("X-Content-Missing") or headers.get("x-content-missing") or "").strip()
    return [part.strip()[:120] for part in raw.split(",") if part.strip()]


def _ia_ml_exact_message_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("plain", "text", "message", "body", "value"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return str(value or "").strip()


def _ia_ml_exact_attachments(message: dict) -> list[dict]:
    candidates: list[Any] = []
    for key in ("attachments", "message_attachments", "files", "images", "pictures"):
        value = (message or {}).get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            nested = False
            for nested_key in ("attachments", "files", "images", "pictures"):
                nested_value = value.get(nested_key)
                if isinstance(nested_value, list):
                    nested = True
                    candidates.extend(nested_value)
            if not nested:
                candidates.append(value)
        elif isinstance(value, str) and value.strip():
            candidates.append(value)
    result = []
    seen = set()
    for raw in candidates:
        if isinstance(raw, str):
            attachment_id = ""
            name = raw.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "anexo"
            mime_type = ""
            size = None
        elif isinstance(raw, dict):
            attachment_id = str(raw.get("id") or raw.get("attachment_id") or raw.get("file_id") or "").strip()
            name = str(
                raw.get("original_filename")
                or raw.get("filename")
                or raw.get("file_name")
                or raw.get("name")
                or attachment_id
                or "anexo"
            ).strip()
            mime_type = str(raw.get("mime_type") or raw.get("content_type") or raw.get("type") or "").strip()
            size = raw.get("size")
        else:
            continue
        key = attachment_id or name
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({
            "id": attachment_id[:160],
            "name": name[:200],
            "mime_type": mime_type[:120],
            "size": size if isinstance(size, (int, float)) else None,
        })
    return result[:20]


def _ia_ml_exact_normalize_post_sale_messages(messages: list[dict], seller_id: str) -> list[dict]:
    seller = str(seller_id or "").strip()
    result = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        sender_id = str(sender.get("user_id") or sender.get("id") or message.get("from_id") or "").strip()
        text = _ia_ml_exact_message_text(message.get("text") or message.get("message"))
        attachments = _ia_ml_exact_attachments(message)
        if not text and not attachments:
            continue
        moderation = message.get("message_moderation") if isinstance(message.get("message_moderation"), dict) else {}
        result.append({
            "message_id": str(message.get("id") or message.get("message_id") or "").strip()[:160],
            "date": str(
                message.get("message_date")
                or message.get("date_created")
                or message.get("date")
                or message.get("last_updated")
                or ""
            ).strip()[:100],
            "role": "seller" if seller and sender_id == seller else "buyer",
            "label": "Loja" if seller and sender_id == seller else "Comprador",
            "text": text,
            "attachments": attachments,
            "status": str(message.get("status") or "").strip()[:80],
            "moderation_status": str(moderation.get("status") or message.get("moderation_status") or "").strip()[:80],
            "moderation_reason": str(moderation.get("reason") or "").strip()[:160],
        })
    result.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("message_id") or "")))
    return result


def _ia_ml_exact_normalize_claim_messages(messages: list[dict], claim: dict, seller_id: str) -> list[dict]:
    seller = str(seller_id or "").strip()
    seller_role = ""
    for player in (claim or {}).get("players") or []:
        if not isinstance(player, dict):
            continue
        if str(player.get("user_id") or "").strip() == seller or str(player.get("type") or "").strip().lower() == "seller":
            seller_role = str(player.get("role") or "").strip().lower()
            if seller_role:
                break
    result = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        sender_role = str(message.get("sender_role") or "").strip().lower()
        if seller_role and sender_role == seller_role:
            role, label = "seller", "Loja"
        elif sender_role == "mediator":
            role, label = "marketplace", "Mercado Livre"
        else:
            role, label = "buyer", "Comprador"
        text = _ia_ml_exact_message_text(message.get("message") or message.get("translated_message") or message.get("text"))
        attachments = _ia_ml_exact_attachments(message)
        if not text and not attachments:
            continue
        moderation = message.get("message_moderation") if isinstance(message.get("message_moderation"), dict) else {}
        result.append({
            "date": str(
                message.get("message_date")
                or message.get("date_created")
                or message.get("last_updated")
                or ""
            ).strip()[:100],
            "role": role,
            "label": label,
            "text": text,
            "attachments": attachments,
            "status": str(message.get("status") or "").strip()[:80],
            "moderation_status": str(moderation.get("status") or "").strip()[:80],
            "moderation_reason": str(moderation.get("reason") or "").strip()[:160],
        })
    result.sort(key=lambda item: str(item.get("date") or ""))
    return result[:ML_IA_EXACT_MAX_MESSAGES]


def _ia_ml_exact_fetch_post_sale_messages(
    client_id: str,
    store: str,
    cfg: dict,
    pack_id: str,
    seller_id: str,
    deadline: Optional[float],
) -> tuple[dict, dict]:
    if not pack_id or not seller_id:
        return {
            "available": False,
            "complete": False,
            "has_messages": None,
            "total_messages": None,
            "messages": [],
            "reason": "Pedido sem pack_id ou seller_id para consultar mensagens.",
        }, cfg
    loaded: list[dict] = []
    offset = 0
    provider_total: Optional[int] = None
    while len(loaded) < ML_IA_EXACT_MAX_MESSAGES:
        response, cfg = _ia_ml_request_get(
            client_id,
            store,
            cfg,
            f"{ML_IA_API_BASE}/messages/packs/{quote_plus(str(pack_id))}/sellers/{quote_plus(str(seller_id))}",
            params={
                "tag": "post_sale",
                "mark_as_read": "false",
                "limit": min(50, ML_IA_EXACT_MAX_MESSAGES - len(loaded)),
                "offset": offset,
            },
            timeout=20,
            deadline=deadline,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 404 and not loaded:
            return {
                "available": True,
                "complete": True,
                "has_messages": False,
                "total_messages": 0,
                "messages": [],
                "reason": "O Mercado Livre nao possui conversa pos-venda para este pack.",
            }, cfg
        if status_code not in {200, 206}:
            code, message, reconnect = _ia_ml_http_failure(response, "Nao foi possivel consultar as mensagens pos-venda.")
            return {
                "available": bool(loaded),
                "complete": False,
                "has_messages": bool(loaded) if loaded else None,
                "total_messages": provider_total,
                "messages": _ia_ml_exact_normalize_post_sale_messages(loaded, seller_id),
                "error": code,
                "message": message,
                "reconnect_required": reconnect,
            }, cfg
        payload = response.json() or {}
        page = [item for item in (payload.get("messages") or []) if isinstance(item, dict)]
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        try:
            provider_total = int(paging.get("total")) if paging.get("total") is not None else provider_total
        except (TypeError, ValueError):
            pass
        loaded.extend(page[: ML_IA_EXACT_MAX_MESSAGES - len(loaded)])
        offset += len(page)
        if not page or (provider_total is not None and offset >= provider_total):
            break
    normalized = _ia_ml_exact_normalize_post_sale_messages(loaded, seller_id)
    total = provider_total if provider_total is not None else len(normalized)
    return {
        "available": True,
        "complete": total <= len(normalized),
        "has_messages": bool(normalized),
        "total_messages": total,
        "loaded_messages": len(normalized),
        "messages": normalized,
        "truncated": total > len(normalized),
        "mark_as_read": False,
    }, cfg


def _ia_ml_exact_shipment_state(status: str, substatus: str, logistic_type: str) -> tuple[str, str]:
    status_norm = str(status or "").strip().lower()
    substatus_norm = str(substatus or "").strip().lower()
    logistic_norm = str(logistic_type or "").strip().lower()
    if status_norm == "delivered":
        return "delivered", "Entregue"
    if status_norm == "not_delivered":
        return "not_delivered", "Nao entregue"
    if status_norm == "cancelled":
        return "cancelled", "Cancelado"
    if status_norm == "shipped":
        return "in_transit", "Em transito"
    if status_norm == "ready_to_ship" and substatus_norm in {"picked_up", "authorized_by_carrier", "in_hub"}:
        return "in_transit", "Em transito"
    if status_norm in {"pending", "handling", "ready_to_ship"}:
        if logistic_norm == "fulfillment" and substatus_norm == "in_warehouse":
            return "preparing", "Em preparacao no armazem Full"
        return "preparing", "Em preparacao"
    return "unavailable", "Indisponivel"


def _ia_ml_exact_fetch_shipment(
    client_id: str,
    store: str,
    cfg: dict,
    raw_order: dict,
    deadline: Optional[float],
) -> tuple[dict, dict]:
    shipment_id = _ia_ml_order_shipment_id(raw_order)
    if not shipment_id:
        return {
            "available": False,
            "complete": False,
            "shipment_id": "",
            "delivery_state": "unavailable",
            "delivery_state_label": "Indisponivel",
            "reason": "Pedido sem shipment_id.",
        }, cfg
    response, cfg = _ia_ml_request_get(
        client_id,
        store,
        cfg,
        f"{ML_IA_API_BASE}/shipments/{shipment_id}",
        timeout=20,
        deadline=deadline,
    )
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code not in {200, 206}:
        code, message, reconnect = _ia_ml_http_failure(response, "Nao foi possivel consultar o envio.")
        return {
            "available": False,
            "complete": False,
            "shipment_id": shipment_id,
            "delivery_state": "unavailable",
            "delivery_state_label": "Indisponivel",
            "error": code,
            "message": message,
            "reconnect_required": reconnect,
        }, cfg
    payload = response.json() or {}
    status = str(payload.get("status") or "").strip()
    substatus = str(payload.get("substatus") or "").strip()
    logistic_type = str(payload.get("logistic_type") or "").strip()
    state, state_label = _ia_ml_exact_shipment_state(status, substatus, logistic_type)
    estimated = payload.get("estimated_delivery_time") if isinstance(payload.get("estimated_delivery_time"), dict) else {}
    missing = _ia_ml_exact_partial_fields(response)
    return {
        "available": True,
        "complete": not bool(missing),
        "shipment_id": shipment_id,
        "status": status,
        "substatus": substatus,
        "logistic_type": logistic_type,
        "mode": str(payload.get("mode") or "").strip(),
        "delivery_state": state,
        "delivery_state_label": state_label,
        "date_delivered": str(payload.get("date_delivered") or "").strip(),
        "estimated_delivery": str(
            estimated.get("date")
            or estimated.get("estimated_delivery_time")
            or payload.get("estimated_delivery")
            or ""
        ).strip(),
        "partial_fields": missing,
    }, cfg


def _ia_ml_exact_claim_summary(claim: dict) -> dict:
    resolution = claim.get("resolution") if isinstance(claim.get("resolution"), dict) else {}
    return {
        "claim_id": str(claim.get("id") or "").strip(),
        "type": str(claim.get("type") or "").strip(),
        "stage": str(claim.get("stage") or "").strip(),
        "status": str(claim.get("status") or "").strip(),
        "reason_id": str(claim.get("reason_id") or "").strip(),
        "quantity_type": str(claim.get("quantity_type") or "").strip(),
        "claimed_quantity": claim.get("claimed_quantity"),
        "date_created": str(claim.get("date_created") or "").strip(),
        "last_updated": str(claim.get("last_updated") or "").strip(),
        "resolution": {
            "reason": str(resolution.get("reason") or "").strip(),
            "date_created": str(resolution.get("date_created") or "").strip(),
            "closed_by": str(resolution.get("closed_by") or "").strip(),
        },
    }


def _ia_ml_exact_fetch_claims(
    client_id: str,
    store: str,
    cfg: dict,
    order_id: str,
    seller_id: str,
    deadline: Optional[float],
) -> tuple[dict, dict]:
    raw_claims: list[dict] = []
    claims_offset = 0
    total_claims: Optional[int] = None
    complete = True
    while len(raw_claims) < ML_IA_EXACT_MAX_CLAIMS:
        page_limit = min(30, ML_IA_EXACT_MAX_CLAIMS - len(raw_claims))
        response, cfg = _ia_ml_request_get(
            client_id,
            store,
            cfg,
            f"{ML_IA_API_BASE}/post-purchase/v1/claims/search",
            params={
                "order_id": order_id,
                "limit": page_limit,
                "offset": claims_offset,
                "sort": "last_updated:desc",
            },
            timeout=20,
            deadline=deadline,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code not in {200, 206}:
            if raw_claims:
                complete = False
                break
            code, message, reconnect = _ia_ml_http_failure(response, "Nao foi possivel consultar as reclamacoes.")
            return {
                "available": False,
                "complete": False,
                "has_claims": None,
                "claims": [],
                "returns": [],
                "error": code,
                "message": message,
                "reconnect_required": reconnect,
            }, cfg
        payload = response.json() or {}
        page = [item for item in (payload.get("data") or payload.get("results") or []) if isinstance(item, dict)]
        raw_claims.extend(page[: ML_IA_EXACT_MAX_CLAIMS - len(raw_claims)])
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        try:
            total_claims = int(paging.get("total")) if paging.get("total") is not None else total_claims
        except (TypeError, ValueError):
            pass
        complete = complete and status_code == 200 and not bool(_ia_ml_exact_partial_fields(response))
        claims_offset += len(page)
        if not page or (total_claims is not None and claims_offset >= total_claims):
            break
    claims = []
    returns = []
    warnings = []
    for raw_claim in raw_claims:
        claim = _ia_ml_exact_claim_summary(raw_claim)
        claim_id = str(claim.get("claim_id") or "")
        detail_response, cfg = _ia_ml_request_get(
            client_id,
            store,
            cfg,
            f"{ML_IA_API_BASE}/post-purchase/v1/claims/{quote_plus(claim_id)}/detail",
            timeout=15,
            deadline=deadline,
        )
        if int(getattr(detail_response, "status_code", 0) or 0) in {200, 206}:
            detail = detail_response.json() or {}
            claim["detail"] = {
                "title": str(detail.get("title") or "").strip()[:300],
                "description": str(detail.get("description") or "").strip()[:1200],
                "problem": str(detail.get("problem") or "").strip()[:1200],
                "due_date": str(detail.get("due_date") or "").strip(),
                "action_responsible": str(detail.get("action_responsible") or "").strip(),
            }
        elif int(getattr(detail_response, "status_code", 0) or 0) not in {403, 404}:
            complete = False
            warnings.append(f"Detalhes da reclamacao {claim_id} indisponiveis.")

        messages_response, cfg = _ia_ml_request_get(
            client_id,
            store,
            cfg,
            f"{ML_IA_API_BASE}/post-purchase/v1/claims/{quote_plus(claim_id)}/messages",
            timeout=15,
            deadline=deadline,
        )
        messages_status = int(getattr(messages_response, "status_code", 0) or 0)
        if messages_status in {200, 206}:
            messages_payload = messages_response.json() or []
            if isinstance(messages_payload, dict):
                messages_payload = messages_payload.get("data") or messages_payload.get("messages") or []
            normalized_messages = _ia_ml_exact_normalize_claim_messages(
                [item for item in messages_payload if isinstance(item, dict)] if isinstance(messages_payload, list) else [],
                raw_claim,
                seller_id,
            )
            claim["conversation"] = {
                "available": True,
                "complete": messages_status == 200,
                "has_messages": bool(normalized_messages),
                "total_messages": len(normalized_messages),
                "messages": normalized_messages,
            }
        elif messages_status == 404:
            claim["conversation"] = {
                "available": True,
                "complete": True,
                "has_messages": False,
                "total_messages": 0,
                "messages": [],
            }
        else:
            complete = False
            claim["conversation"] = {
                "available": False,
                "complete": False,
                "has_messages": None,
                "total_messages": None,
                "messages": [],
            }

        if _ia_ml_claim_has_return(raw_claim):
            return_response, cfg = _ia_ml_request_get(
                client_id,
                store,
                cfg,
                f"{ML_IA_API_BASE}/post-purchase/v2/claims/{quote_plus(claim_id)}/returns",
                timeout=15,
                deadline=deadline,
            )
            return_status = int(getattr(return_response, "status_code", 0) or 0)
            if return_status in {200, 206}:
                return_detail = _ia_ml_sanitize_return_detail(return_response.json() or {})
                if return_detail.get("return_id") or return_detail.get("status"):
                    return_detail["claim_id"] = claim_id
                    returns.append(return_detail)
                    claim["return_detail"] = return_detail
            elif return_status not in {403, 404}:
                complete = False
                warnings.append(f"Detalhes da devolucao da reclamacao {claim_id} indisponiveis.")
        claims.append(claim)
    if total_claims is None:
        total_claims = len(claims)
    if total_claims > len(claims):
        complete = False
        warnings.append(f"A order possui {total_claims} reclamacoes; foram carregadas {len(claims)}.")
    return {
        "available": True,
        "complete": complete,
        "has_claims": bool(claims),
        "total_claims": total_claims,
        "claims": claims,
        "returns": returns,
        "warnings": warnings,
    }, cfg


def _ia_ml_exact_return_state(claims_payload: dict) -> tuple[Optional[bool], str, str]:
    if not claims_payload.get("available"):
        return None, "unavailable", "Indisponivel"
    returns = [item for item in (claims_payload.get("returns") or []) if isinstance(item, dict)]
    if not returns:
        has_return_claim = any(
            str(claim.get("type") or "").strip().lower() in {"return", "returns"}
            for claim in (claims_payload.get("claims") or [])
            if isinstance(claim, dict)
        )
        if has_return_claim:
            return True, "return_opened", "Devolucao registrada"
        return False, "not_requested", "Sem devolucao registrada"
    shipment_statuses = {
        str(shipment.get("status") or "").strip().lower()
        for item in returns
        for shipment in (item.get("shipments") or [])
        if isinstance(shipment, dict)
    }
    if "delivered" in shipment_statuses:
        return True, "returned", "Produto devolvido ao destino"
    if shipment_statuses & {"shipped", "ready_to_ship"}:
        return True, "return_in_transit", "Devolucao em transito"
    if any(str(item.get("status_money") or "").strip().lower() == "refunded" for item in returns):
        return True, "refunded", "Valor devolvido ao comprador"
    return True, "return_opened", "Devolucao registrada"


def _ia_ml_exact_enrich_order(
    client_id: str,
    store: str,
    cfg: dict,
    raw_order: dict,
    deadline: Optional[float],
    sources: list[dict],
    source_seen: set[tuple[str, str]],
) -> tuple[dict, dict, list[str], bool]:
    warnings: list[str] = []
    partial = False
    order = _ia_ml_sanitize_order(raw_order, include_buyer_summary=True)
    order["store"] = store
    order["buyer_city"] = ""
    seller_id = str((cfg or {}).get("user_id") or "").strip()

    shipment, cfg = _ia_ml_exact_fetch_shipment(client_id, store, cfg, raw_order, deadline)
    _ia_ml_exact_source_add(sources, source_seen, "shipments/{id}", store)
    order["shipment"] = shipment
    logistic_type = str(shipment.get("logistic_type") or "").strip().lower()
    order["fulfillment"] = {
        "available": bool(logistic_type),
        "is_full": True if logistic_type == "fulfillment" else (False if logistic_type else None),
        "logistic_type": str(shipment.get("logistic_type") or ""),
        "evidence": "shipments/{id}.logistic_type" if logistic_type else "",
    }
    if not shipment.get("complete"):
        partial = True
        if shipment.get("message") or shipment.get("reason"):
            warnings.append(str(shipment.get("message") or shipment.get("reason"))[:300])

    claims_payload, cfg = _ia_ml_exact_fetch_claims(
        client_id,
        store,
        cfg,
        str(order.get("order_id") or ""),
        seller_id,
        deadline,
    )
    _ia_ml_exact_source_add(sources, source_seen, "post-purchase/v1/claims/search", store)
    if claims_payload.get("claims"):
        _ia_ml_exact_source_add(sources, source_seen, "post-purchase/v1/claims/{id}/detail", store)
        _ia_ml_exact_source_add(sources, source_seen, "post-purchase/v1/claims/{id}/messages", store)
    if claims_payload.get("returns"):
        _ia_ml_exact_source_add(sources, source_seen, "post-purchase/v2/claims/{id}/returns", store)
    order["claims"] = claims_payload.get("claims") or []
    order["returns"] = claims_payload.get("returns") or []
    order["claims_status"] = {
        key: claims_payload.get(key)
        for key in ("available", "complete", "has_claims", "total_claims", "error", "message")
        if key in claims_payload
    }
    returned, return_state, return_label = _ia_ml_exact_return_state(claims_payload)
    order["return_status"] = {
        "available": claims_payload.get("available") is True,
        "has_return": returned,
        "state": return_state,
        "label": return_label,
    }
    warnings.extend(str(item)[:300] for item in (claims_payload.get("warnings") or []))
    if not claims_payload.get("complete"):
        partial = True
        if claims_payload.get("message"):
            warnings.append(str(claims_payload.get("message"))[:300])

    pack_id = str(order.get("pack_id") or order.get("order_id") or "").strip()
    post_sale, cfg = _ia_ml_exact_fetch_post_sale_messages(
        client_id,
        store,
        cfg,
        pack_id,
        seller_id,
        deadline,
    )
    _ia_ml_exact_source_add(sources, source_seen, "messages/packs/{pack_id}/sellers/{seller_id}", store)
    claim_conversations = [
        {
            "claim_id": str(claim.get("claim_id") or ""),
            **(claim.get("conversation") if isinstance(claim.get("conversation"), dict) else {}),
        }
        for claim in order.get("claims") or []
        if isinstance(claim, dict)
    ]
    order["conversations"] = {
        "post_sale": post_sale,
        "claims": claim_conversations,
        "total_messages": int(post_sale.get("loaded_messages") or len(post_sale.get("messages") or []))
        + sum(int(item.get("total_messages") or 0) for item in claim_conversations),
        "complete": bool(post_sale.get("complete")) and all(item.get("complete") for item in claim_conversations),
    }
    if not post_sale.get("complete"):
        partial = True
        if post_sale.get("message") or post_sale.get("reason"):
            warnings.append(str(post_sale.get("message") or post_sale.get("reason"))[:300])

    order["data_quality"] = {
        "order": "confirmed",
        "shipment": "confirmed" if shipment.get("available") and shipment.get("complete") else "partial_or_unavailable",
        "claims": "confirmed" if claims_payload.get("available") and claims_payload.get("complete") else "partial_or_unavailable",
        "messages": "confirmed" if post_sale.get("available") and post_sale.get("complete") else "partial_or_unavailable",
    }
    return order, cfg, list(dict.fromkeys(warnings)), partial


def _ia_ml_resolve_exact_order(
    client_id: str,
    requested_id: str,
    selected_store: str,
    *,
    query_deadline: Optional[float] = None,
) -> dict:
    requested = re.sub(r"\D+", "", str(requested_id or ""))
    function_name = "get_mercado_livre_orders"
    arguments = {
        "loja": selected_store,
        "order_id": requested,
        "exact_lookup": True,
        "force_refresh": True,
    }
    result = _ia_ml_base_result()
    result.update({
        "exact_lookup": True,
        "requested_id": requested,
        "identifier_type": "",
        "found": False,
        "store": selected_store,
        "matched_stores": [],
        "resolved_order_ids": [],
        "searched_stores": [],
        "orders": [],
        "shipment": [],
        "fulfillment": [],
        "claims": [],
        "returns": [],
        "conversations": [],
        "by_sku": [],
        "by_day": [],
        "totals": {},
        "chart_data": _ia_ml_sales_chart_data([], {}, coverage_complete=False),
        "coverage": "mercado_livre_exact_order_and_post_sale",
        "coverage_complete": False,
        "partial_response": False,
        "truncated": False,
        "paging": {
            "offset": 0,
            "limit": 0,
            "returned": 0,
            "total": 0,
            "next_offset": None,
            "has_more": False,
            "pages_fetched": 0,
            "report_mode": False,
        },
    })
    if not requested:
        result.update({"error": "invalid_order_id", "message": "Informe um numero de venda, order ou pack valido."})
        return {"function": function_name, "arguments": arguments, "result": result}

    connected = list(_ia_lojas_ml_conectadas(client_id) or [])
    stores = [selected_store] + [store for store in connected if _normalizar_texto(store) != _normalizar_texto(selected_store)]
    sources: list[dict] = []
    source_seen: set[tuple[str, str]] = set()
    matched_store = ""
    identifier_type = ""
    raw_orders: list[dict] = []
    cfg: dict = {}
    cfg_by_store: dict[str, dict] = {}
    had_forbidden = False
    had_reconnect = False

    for store in stores:
        diagnostic = {"store": store, "order_http": None, "pack_http": None, "result": "not_found"}
        try:
            store_cfg = _obter_cfg_ml(client_id, store)
        except HTTPException as exc:
            status_code = int(getattr(exc, "status_code", 0) or 0)
            diagnostic.update({"result": "integration_error", "http": status_code})
            had_reconnect = had_reconnect or status_code == 401
            result["searched_stores"].append(diagnostic)
            continue
        try:
            order_response, store_cfg = _ia_ml_request_get(
                client_id,
                store,
                store_cfg,
                f"{ML_IA_API_BASE}/orders/{requested}",
                timeout=20,
                deadline=query_deadline,
            )
            _ia_ml_exact_source_add(sources, source_seen, "orders/{id}", store)
            order_status = int(getattr(order_response, "status_code", 0) or 0)
            diagnostic["order_http"] = order_status
            if order_status in {200, 206}:
                payload = order_response.json() or {}
                if isinstance(payload, dict) and payload.get("id"):
                    raw_orders = [payload]
                    cfg = store_cfg
                    cfg_by_store[store] = store_cfg
                    matched_store = store
                    identifier_type = "order"
                    diagnostic["result"] = "matched_order"
                    if order_status == 206:
                        result["partial_response"] = True
                        missing = _ia_ml_exact_partial_fields(order_response)
                        result["warnings"].append(
                            "A order foi retornada parcialmente pelo Mercado Livre."
                            + (f" Campos ausentes: {', '.join(missing)}." if missing else "")
                        )
                    result["searched_stores"].append(diagnostic)
                    break
            had_forbidden = had_forbidden or order_status == 403
            had_reconnect = had_reconnect or order_status == 401

            pack_response, store_cfg = _ia_ml_request_get(
                client_id,
                store,
                store_cfg,
                f"{ML_IA_API_BASE}/packs/{requested}",
                timeout=20,
                deadline=query_deadline,
            )
            _ia_ml_exact_source_add(sources, source_seen, "packs/{id}", store)
            pack_status = int(getattr(pack_response, "status_code", 0) or 0)
            diagnostic["pack_http"] = pack_status
            if pack_status in {200, 206}:
                pack = pack_response.json() or {}
                order_ids = [
                    re.sub(r"\D+", "", str(item.get("id") or ""))
                    for item in (pack.get("orders") or [])
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ]
                pack_orders = []
                for order_id in list(dict.fromkeys(order_ids)):
                    linked_found = False
                    linked_stores = [store] + [
                        candidate
                        for candidate in stores
                        if _normalizar_texto(candidate) != _normalizar_texto(store)
                    ]
                    for linked_store in linked_stores:
                        try:
                            linked_cfg = (
                                store_cfg
                                if _normalizar_texto(linked_store) == _normalizar_texto(store)
                                else _obter_cfg_ml(client_id, linked_store)
                            )
                            linked_response, linked_cfg = _ia_ml_request_get(
                                client_id,
                                linked_store,
                                linked_cfg,
                                f"{ML_IA_API_BASE}/orders/{order_id}",
                                timeout=20,
                                deadline=query_deadline,
                            )
                        except Exception:
                            continue
                        _ia_ml_exact_source_add(sources, source_seen, "orders/{id}", linked_store)
                        linked_status = int(getattr(linked_response, "status_code", 0) or 0)
                        if linked_status not in {200, 206}:
                            continue
                        linked = linked_response.json() or {}
                        if not isinstance(linked, dict) or not linked.get("id"):
                            continue
                        linked_copy = copy.deepcopy(linked)
                        linked_copy["__jk_exact_store"] = linked_store
                        pack_orders.append(linked_copy)
                        cfg_by_store[linked_store] = linked_cfg
                        linked_found = True
                        if linked_status == 206:
                            result["partial_response"] = True
                        break
                    if not linked_found:
                        result["partial_response"] = True
                        result["warnings"].append(f"A order {order_id} do pack nao pode ser carregada nas contas permitidas.")
                if pack_orders:
                    raw_orders = pack_orders
                    cfg = cfg_by_store.get(store) or store_cfg
                    matched_store = store
                    identifier_type = "pack"
                    result["pack"] = {
                        "pack_id": str(pack.get("id") or requested),
                        "status": str(pack.get("status") or "").strip(),
                        "status_detail": str(pack.get("status_detail") or "").strip()[:240],
                        "date_created": str(pack.get("date_created") or "").strip(),
                        "last_updated": str(pack.get("last_updated") or "").strip(),
                        "order_ids": order_ids,
                    }
                    diagnostic["result"] = "matched_pack"
                    if pack_status == 206:
                        result["partial_response"] = True
                    result["searched_stores"].append(diagnostic)
                    break
            had_forbidden = had_forbidden or pack_status == 403
            had_reconnect = had_reconnect or pack_status == 401
            if order_status == 403 or pack_status == 403:
                diagnostic["result"] = "access_denied"
            elif order_status == 401 or pack_status == 401:
                diagnostic["result"] = "reconnect_required"
        except requests.exceptions.Timeout:
            diagnostic["result"] = "timeout"
            result["partial_response"] = True
        except Exception as exc:
            diagnostic["result"] = "provider_unavailable"
            diagnostic["error_type"] = type(exc).__name__
            result["partial_response"] = True
        result["searched_stores"].append(diagnostic)

    result["sources"] = sources
    if not raw_orders or not matched_store:
        if had_reconnect:
            result.update({
                "error": "reconnect_required",
                "message": "A autenticacao de uma ou mais contas Mercado Livre precisa ser refeita.",
                "reconnect_required": True,
            })
        elif had_forbidden:
            result.update({
                "error": "access_denied_or_not_found",
                "message": "O numero nao foi encontrado nas contas acessiveis; outras contas recusaram acesso ao recurso.",
            })
        else:
            result.update({
                "error": "not_found",
                "message": "O numero nao foi localizado como order nem como pack nas contas Mercado Livre permitidas.",
            })
        result["warnings"].append("Nenhum historico local foi usado para substituir a consulta exata da API.")
        return {"function": function_name, "arguments": arguments, "result": result}

    result["identifier_type"] = identifier_type
    result["store"] = matched_store
    enriched = []
    for raw_order in raw_orders:
        order_store = str(raw_order.pop("__jk_exact_store", "") or matched_store).strip()
        order_cfg = cfg_by_store.get(order_store) or cfg
        _ia_ml_exact_source_add(sources, source_seen, "orders/{id}", order_store)
        order, order_cfg, order_warnings, order_partial = _ia_ml_exact_enrich_order(
            client_id,
            order_store,
            order_cfg,
            raw_order,
            query_deadline,
            sources,
            source_seen,
        )
        cfg_by_store[order_store] = order_cfg
        enriched.append(order)
        result["warnings"].extend(order_warnings)
        result["partial_response"] = bool(result["partial_response"] or order_partial)

    totals, by_sku, aggregate_warnings = _ia_ml_aggregate_orders(enriched)
    by_day, daily_warnings = _ia_ml_aggregate_orders_by_day(enriched)
    result["warnings"].extend(aggregate_warnings)
    result["warnings"].extend(daily_warnings)
    result["warnings"] = list(dict.fromkeys(str(item) for item in result["warnings"] if str(item).strip()))
    result["matched_stores"] = list(dict.fromkeys(str(order.get("store") or "") for order in enriched if order.get("store")))
    result["resolved_order_ids"] = [str(order.get("order_id") or "") for order in enriched if order.get("order_id")]
    result["orders"] = enriched
    result["shipment"] = [
        {"order_id": order.get("order_id"), **(order.get("shipment") or {})}
        for order in enriched
    ]
    result["fulfillment"] = [
        {"order_id": order.get("order_id"), **(order.get("fulfillment") or {})}
        for order in enriched
    ]
    result["claims"] = [
        {"order_id": order.get("order_id"), "items": copy.deepcopy(order.get("claims") or [])}
        for order in enriched
    ]
    result["returns"] = [
        {"order_id": order.get("order_id"), "items": copy.deepcopy(order.get("returns") or [])}
        for order in enriched
    ]
    result["conversations"] = [
        {"order_id": order.get("order_id"), **copy.deepcopy(order.get("conversations") or {})}
        for order in enriched
    ]
    result["by_sku"] = by_sku
    result["by_day"] = by_day
    result["totals"] = totals
    result["found"] = bool(enriched)
    result["coverage_complete"] = not bool(result["partial_response"])
    result["truncated"] = any(
        bool(((order.get("conversations") or {}).get("post_sale") or {}).get("truncated"))
        for order in enriched
        if isinstance(order, dict)
    )
    result["paging"].update({
        "limit": len(enriched),
        "returned": len(enriched),
        "total": len(enriched),
    })
    result["chart_data"] = _ia_ml_sales_chart_data(
        by_day,
        totals,
        coverage_complete=result["coverage_complete"],
        paging=result["paging"],
    )
    result["data_quality"] = {
        "exact_identifier_resolved": True,
        "identifier_type": identifier_type,
        "store_confirmed": matched_store,
        "coverage_complete": result["coverage_complete"],
        "missing_values_are_null": True,
        "sensitive_buyer_fields_omitted": True,
    }
    return {"function": function_name, "arguments": arguments, "result": result}


def _ia_ml_fetch_order_city(
    client_id: str,
    store: str,
    cfg: dict,
    raw_order: dict,
    deadline: float,
) -> tuple[str, dict, Optional[str]]:
    embedded = _ia_ml_order_embedded_city(raw_order)
    if embedded:
        return embedded, cfg, None
    shipment_id = _ia_ml_order_shipment_id(raw_order)
    if not shipment_id:
        return "", cfg, "Cidade do comprador indisponivel: o pedido nao informou o envio associado."
    response, cfg = _ia_ml_request_get(
        client_id,
        store,
        cfg,
        f"{ML_IA_API_BASE}/shipments/{shipment_id}",
        timeout=20,
        deadline=deadline,
    )
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code not in {200, 206}:
        return "", cfg, "Cidade do comprador indisponivel no recurso de envio do Mercado Livre."
    payload = response.json() or {}
    # O payload completo do envio contem endereco e contato. Extraia apenas a cidade
    # e descarte o restante sem inclui-lo no resultado, cache ou auditoria.
    address = payload.get("receiver_address") if isinstance(payload.get("receiver_address"), dict) else {}
    city = _ia_ml_city_name(address.get("city") or address.get("city_name"))
    if not city:
        destination = payload.get("destination") if isinstance(payload.get("destination"), dict) else {}
        shipping_address = destination.get("shipping_address") if isinstance(destination.get("shipping_address"), dict) else {}
        city = _ia_ml_city_name(shipping_address.get("city") or shipping_address.get("city_name"))
    return city, cfg, None if city else "Cidade do comprador nao informada pelo Mercado Livre."


def _ia_ml_aggregate_orders(orders: list[dict]) -> tuple[dict, list[dict], list[str]]:
    totals = {
        "orders": len(orders),
        "items_quantity": 0.0,
        "gross_amount": 0.0,
        "paid_amount": 0.0,
        "refund_amount": 0.0,
        "net_amount": 0.0,
        "currency_id": "BRL",
    }
    by_sku: dict[str, dict] = {}
    unknown_refund = False
    for order in orders:
        totals["currency_id"] = str(order.get("currency_id") or totals["currency_id"])
        gross = _ia_ml_float(order.get("gross_amount"))
        paid = _ia_ml_float(order.get("paid_amount"))
        refund = order.get("refund_amount")
        totals["gross_amount"] += gross
        totals["paid_amount"] += paid
        if refund is None:
            unknown_refund = True
        else:
            totals["refund_amount"] += _ia_ml_float(refund)
        order_items = order.get("items") or []
        item_gross_total = sum(_ia_ml_float(item.get("gross_amount")) for item in order_items if isinstance(item, dict))
        for item in order_items:
            if not isinstance(item, dict):
                continue
            sku = str(item.get("sku") or item.get("item_id") or "SEM_SKU").strip()
            row = by_sku.setdefault(sku, {
                "sku": sku,
                "title": str(item.get("title") or "").strip(),
                "orders": set(),
                "quantity": 0.0,
                "gross_amount": 0.0,
                "paid_amount": 0.0,
                "refund_amount": 0.0,
                "net_amount": 0.0,
                "refund_known": True,
            })
            row["orders"].add(str(order.get("order_id") or ""))
            row["quantity"] += _ia_ml_float(item.get("quantity"))
            totals["items_quantity"] += _ia_ml_float(item.get("quantity"))
            item_gross = _ia_ml_float(item.get("gross_amount"))
            share = (item_gross / item_gross_total) if item_gross_total > 0 else (1.0 / max(1, len(order_items)))
            # O total do pedido pode diferir da soma das linhas por descontos
            # ou arredondamentos. Ratear o bruto confirmado pelo mesmo peso
            # garante que a abertura por SKU feche com os indicadores gerais.
            row["gross_amount"] += gross * share
            row["paid_amount"] += paid * share
            if refund is None:
                row["refund_known"] = False
            else:
                row["refund_amount"] += _ia_ml_float(refund) * share

    warnings = []
    if unknown_refund:
        totals["refund_amount"] = None
        totals["net_amount"] = None
        warnings.append("Uma ou mais vendas parcialmente reembolsadas nao informaram o valor do reembolso; liquido indisponivel.")
    else:
        totals["net_amount"] = totals["paid_amount"] - totals["refund_amount"]
    for key in ("items_quantity", "gross_amount", "paid_amount", "refund_amount", "net_amount"):
        if totals.get(key) is not None:
            totals[key] = round(float(totals[key]), 2)

    rows = []
    for row in by_sku.values():
        row["orders"] = len([value for value in row["orders"] if value])
        if row.pop("refund_known", True):
            row["net_amount"] = row["paid_amount"] - row["refund_amount"]
        else:
            row["refund_amount"] = None
            row["net_amount"] = None
        rows.append(row)

    # Fecha residuos de centavos no SKU de maior participacao. Sem isso, duas
    # parcelas de 4,995 viram 5,00 + 5,00 para um pedido de 9,99.
    metric_totals = {
        "quantity": totals.get("items_quantity"),
        "gross_amount": totals.get("gross_amount"),
        "paid_amount": totals.get("paid_amount"),
        "refund_amount": totals.get("refund_amount"),
        "net_amount": totals.get("net_amount"),
    }
    for key, expected in metric_totals.items():
        if expected is None or not rows or any(row.get(key) is None for row in rows):
            continue
        for row in rows:
            row[key] = round(float(row.get(key) or 0), 2)
        actual = round(sum(float(row.get(key) or 0) for row in rows), 2)
        residual = round(float(expected) - actual, 2)
        if residual:
            target = max(rows, key=lambda row: (abs(float(row.get(key) or 0)), str(row.get("sku") or "")))
            target[key] = round(float(target.get(key) or 0) + residual, 2)
    for row in rows:
        for key in ("quantity", "gross_amount", "paid_amount", "refund_amount", "net_amount"):
            if row.get(key) is not None:
                row[key] = round(float(row[key]), 2)
    rows.sort(key=lambda row: (-_ia_ml_float(row.get("gross_amount")), str(row.get("sku") or "")))
    return totals, rows, warnings


def _ia_ml_order_date_sao_paulo(value: Any) -> Optional[str]:
    """Converte o instante do pedido para o dia civil de America/Sao_Paulo."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    tz = ZoneInfo("America/Sao_Paulo")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    else:
        parsed = parsed.astimezone(tz)
    return parsed.date().isoformat()


def _ia_ml_aggregate_orders_by_day(orders: list[dict]) -> tuple[list[dict], list[str]]:
    """Serie diaria numerica e sem PII, derivada somente dos pedidos sanitizados."""
    buckets: dict[Optional[str], dict[str, Any]] = {}
    missing_dates = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        local_date = _ia_ml_order_date_sao_paulo(order.get("date_created"))
        if local_date is None:
            missing_dates += 1
        bucket = buckets.setdefault(local_date, {
            "date": local_date,
            "label": local_date or "Data indisponível",
            "timezone": "America/Sao_Paulo",
            "orders": 0,
            "items_quantity": 0.0,
            "gross_amount": 0.0,
            "paid_amount": 0.0,
            "refund_amount": 0.0,
            "net_amount": 0.0,
            "currency_id": str(order.get("currency_id") or "BRL"),
            "refund_known": True,
        })
        bucket["orders"] += 1
        bucket["currency_id"] = str(order.get("currency_id") or bucket["currency_id"] or "BRL")
        bucket["items_quantity"] += sum(
            _ia_ml_float(item.get("quantity"))
            for item in (order.get("items") or [])
            if isinstance(item, dict)
        )
        bucket["gross_amount"] += _ia_ml_float(order.get("gross_amount"))
        bucket["paid_amount"] += _ia_ml_float(order.get("paid_amount"))
        if order.get("refund_amount") is None:
            bucket["refund_known"] = False
        else:
            bucket["refund_amount"] += _ia_ml_float(order.get("refund_amount"))

    points: list[dict] = []
    for _date, bucket in sorted(buckets.items(), key=lambda item: (item[0] is None, item[0] or "")):
        refund_known = bool(bucket.pop("refund_known", True))
        if refund_known:
            bucket["net_amount"] = bucket["paid_amount"] - bucket["refund_amount"]
        else:
            bucket["refund_amount"] = None
            bucket["net_amount"] = None
        for key in ("items_quantity", "gross_amount", "paid_amount", "refund_amount", "net_amount"):
            if bucket.get(key) is not None:
                bucket[key] = round(float(bucket[key]), 2)
        points.append(bucket)

    warnings = []
    if missing_dates:
        warnings.append(
            f"{missing_dates} pedido(s) nao informaram uma data valida; foram mantidos no agregado diario com date=null."
        )
    return points, warnings


def _ia_ml_sales_chart_data(
    by_day: list[dict],
    totals: dict,
    *,
    coverage_complete: bool,
    paging: Optional[dict] = None,
) -> dict:
    """Contrato estavel para consumidores de graficos read-only."""
    paging = dict(paging or {}) if isinstance(paging, dict) else {}
    metric_keys = (
        "orders",
        "items_quantity",
        "gross_amount",
        "paid_amount",
        "refund_amount",
        "net_amount",
    )
    reconciliation: dict[str, Optional[bool]] = {}
    for key in metric_keys:
        expected = totals.get(key) if isinstance(totals, dict) else None
        values = [point.get(key) for point in by_day if isinstance(point, dict)]
        if expected is None or any(value is None for value in values):
            reconciliation[key] = None
            continue
        actual = round(sum(_ia_ml_float(value) for value in values), 2)
        reconciliation[key] = abs(actual - _ia_ml_float(expected)) < 0.01
    temporal_coverage_complete = all(point.get("date") for point in by_day if isinstance(point, dict))
    chart_coverage_complete = bool(coverage_complete and temporal_coverage_complete)
    return {
        "schema": "jk.marketplace.sales_by_day.v1",
        "kind": "sales_by_day",
        "timezone": "America/Sao_Paulo",
        "x_key": "date",
        "currency_id": str((totals or {}).get("currency_id") or "BRL"),
        "metrics": [
            {"key": "orders", "type": "integer"},
            {"key": "items_quantity", "type": "number"},
            {"key": "gross_amount", "type": "currency"},
            {"key": "paid_amount", "type": "currency"},
            {"key": "refund_amount", "type": "currency", "nullable": True},
            {"key": "net_amount", "type": "currency", "nullable": True},
        ],
        "points": copy.deepcopy(by_day),
        "totals": {key: (totals or {}).get(key) for key in (*metric_keys, "currency_id")},
        "reconciliation": reconciliation,
        "coverage_complete": chart_coverage_complete,
        "partial": not chart_coverage_complete,
        "coverage": {
            "pages_fetched": _ia_ml_int(paging.get("pages_fetched"), 0, 0, 100_000),
            "scanned_orders": _ia_ml_int(paging.get("scanned"), (totals or {}).get("orders") or 0, 0, 100_000_000),
            "included_orders": _ia_ml_int((totals or {}).get("orders"), 0, 0, 100_000_000),
            "provider_total": _ia_ml_int(paging.get("total"), 0, 0, 100_000_000),
            "has_more": bool(paging.get("has_more")),
            "dates_complete": temporal_coverage_complete,
        },
        "pii_included": False,
        "read_only": True,
    }


def _ia_ml_listing_chart_data(matches: list[dict], *, coverage_complete: bool, paging: Optional[dict] = None) -> dict:
    """Snapshot estruturado de anuncios; usa apenas campos publicos/read-only."""
    paging = dict(paging or {}) if isinstance(paging, dict) else {}
    points = []
    status_counts: dict[str, int] = {}
    for item in matches:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown").strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        points.append({
            "item_id": str(item.get("id") or "").strip(),
            "sku": str(item.get("seller_sku") or item.get("id") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "status": status,
            "currency_id": str(item.get("currency_id") or "BRL").strip(),
            "price": item.get("price"),
            "available_quantity": item.get("available_quantity"),
            "sold_quantity": item.get("sold_quantity"),
            "sold_quantity_scope": "acumulado_do_anuncio",
        })
    return {
        "schema": "jk.marketplace.listing_snapshot.v1",
        "kind": "listing_snapshot",
        "currency_id": str((points[0] if points else {}).get("currency_id") or "BRL"),
        "metrics": [
            {"key": "price", "type": "currency"},
            {"key": "available_quantity", "type": "number"},
            {"key": "sold_quantity", "type": "number", "scope": "acumulado_do_anuncio"},
        ],
        "points": points,
        "summary": {
            "returned": len(points),
            "status_counts": status_counts,
            "available_quantity": round(sum(_ia_ml_float(item.get("available_quantity")) for item in points), 2),
            "sold_quantity_accumulated": round(sum(_ia_ml_float(item.get("sold_quantity")) for item in points), 2),
        },
        "coverage_complete": bool(coverage_complete),
        "partial": not bool(coverage_complete),
        "coverage": {
            "pages_fetched": _ia_ml_int(paging.get("pages_fetched"), 0, 0, 100_000),
            "provider_total": _ia_ml_int(paging.get("total"), 0, 0, 100_000_000),
            "has_more": bool(paging.get("has_more")),
        },
        "pii_included": False,
        "read_only": True,
    }


def _ia_tool_get_mercado_livre_orders(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    status: Any = None,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    offset: int = 0,
    limite: Optional[int] = None,
    incluir_detalhes: bool = False,
    force_refresh: bool = False,
    statuses: Any = None,
    id_pedido: Optional[str] = None,
    query_deadline: Optional[float] = None,
    modo_relatorio: bool = False,
    max_paginas: Optional[int] = None,
) -> dict:
    del force_refresh  # O bypass de cache e tratado pelo chamador; este provedor nao mantem cache proprio.
    function_name = "get_mercado_livre_orders"
    requested_statuses = statuses if statuses is not None else status
    if isinstance(requested_statuses, str):
        status_values = [part.strip().lower() for part in requested_statuses.split(",") if part.strip()]
    elif isinstance(requested_statuses, (list, tuple, set)):
        status_values = [str(part or "").strip().lower() for part in requested_statuses if str(part or "").strip()]
    else:
        status_values = []
    status_values = list(dict.fromkeys(value for value in status_values if value in ML_IA_ORDER_STATUSES))
    if not status_values:
        status_values = ["paid", "partially_refunded"]
    local_status_filter = "partially_refunded" in status_values
    report_mode = bool(modo_relatorio)
    offset_value = _ia_ml_int(offset, 0, 0, 1_000_000)
    limit_value = _ia_ml_int(limite, 20_000 if report_mode else 50, 1, 20_000 if report_mode else 100)
    max_pages_value = _ia_ml_int(max_paginas, 400 if report_mode else 2, 1, 400 if report_mode else 2)
    sku_value = str(sku or "").strip()
    if _ia_ml_sku_parece_data(sku_value):
        sku_value = ""
    item_ids = _ia_ml_normalizar_item_ids(item_id, mensagem)
    latest_targeted = bool(
        not report_mode
        and _ia_ml_latest_requested(mensagem)
        and (sku_value or item_ids)
    )
    if latest_targeted:
        offset_value = 0
        limit_value = 1
    order_id_value = re.sub(r"\D+", "", str(id_pedido or "").strip())
    details_requested = _ia_ml_order_details_requested(mensagem, incluir_detalhes, order_id_value)
    arguments = {
        "loja": str(loja or "").strip(),
        "data_inicio": data_inicio or "",
        "data_fim": data_fim or "",
        "statuses": status_values,
        "sku": sku_value,
        "item_id": item_ids[0] if item_ids else "",
        "order_id": order_id_value,
        "offset": offset_value,
        "limit": limit_value,
        "report_mode": report_mode,
        "max_pages": max_pages_value,
        "include_buyer_summary": details_requested,
    }
    nome_loja, failure = _ia_ml_resolver_loja_exata(client_id, loja)
    if not nome_loja:
        return _ia_ml_store_failure(function_name, arguments, failure)

    # Um identificador exato pode ser tanto order_id quanto pack_id. Essa
    # consulta e sempre direta na API e nao depende de periodo, status,
    # paginacao ou historico local.
    if order_id_value:
        exact_response = _ia_ml_resolve_exact_order(
            client_id,
            order_id_value,
            nome_loja,
            query_deadline=query_deadline,
        )
        exact_arguments = exact_response.setdefault("arguments", {})
        exact_arguments.update(arguments)
        exact_arguments["exact_lookup"] = True
        exact_arguments["force_refresh"] = True
        return exact_response

    inicio, fim, warnings, requested_period, historical_only = _ia_ml_periodo_orders(mensagem, data_inicio, data_fim)
    if inicio is None or fim is None:
        failure = {"code": "invalid_period", "message": "A data inicial deve ser anterior ou igual a data final.", "available_stores": [nome_loja]}
        return _ia_ml_store_failure(function_name, arguments, failure)
    arguments["period"] = {
        "from": inicio.isoformat(timespec="milliseconds"),
        "to": fim.isoformat(timespec="milliseconds"),
    }

    result = _ia_ml_base_result(warnings=warnings)
    result.update({
        "found": False,
        "store": nome_loja,
        "coverage": "mercado_livre_api_last_12_months",
        "period": {"requested": requested_period, "effective": arguments["period"], "timezone": "America/Sao_Paulo"},
        "orders": [],
        "by_sku": [],
        "by_day": [],
        "totals": {},
        "chart_data": _ia_ml_sales_chart_data([], {}, coverage_complete=False),
        "paging": {
            "offset": offset_value,
            "limit": limit_value,
            "returned": 0,
            "total": 0,
            "next_offset": None,
            "has_more": False,
            "pages_fetched": 0,
            "max_pages": max_pages_value,
            "report_mode": report_mode,
        },
        "truncated": False,
    })
    if historical_only:
        result.update({
            "status": "historical_period_local_only",
            "coverage": "historical_period_local_only",
            "historical_period_local_only": True,
            "api_consulted": False,
            "message": "Periodo fora da cobertura direta do Mercado Livre; consulte o historico local sem misturar as fontes.",
        })
        return {"function": function_name, "arguments": arguments, "result": result}
    if local_status_filter:
        result["warnings"].append(
            "O filtro partially_refunded nao e aceito pelo search desta conta; consultei o periodo e apliquei paid/partially_refunded localmente."
        )
    deadline = (
        _ia_ml_latest_deadline(query_deadline)
        if latest_targeted
        else time.monotonic() + ML_IA_QUERY_TIMEOUT_SECONDS
    )
    if not latest_targeted and query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    try:
        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str((cfg or {}).get("user_id") or "").strip()
        if not seller_id:
            result.update({"error": "seller_id_missing", "message": "A conta Mercado Livre nao informou o seller id."})
            return {"function": function_name, "arguments": arguments, "result": result}

        if latest_targeted:
            exact, cfg = _ia_ml_find_latest_order_by_target(
                client_id,
                nome_loja,
                cfg,
                seller_id=seller_id,
                inicio=inicio,
                fim=fim,
                statuses=status_values,
                sku=sku_value,
                item_ids=item_ids,
                include_buyer_summary=details_requested,
                deadline=deadline,
            )
            orders = list(exact.get("orders") or [])
            totals, by_sku, aggregate_warnings = _ia_ml_aggregate_orders(orders)
            by_day, daily_warnings = _ia_ml_aggregate_orders_by_day(orders)
            result["warnings"].extend(exact.get("warnings") or [])
            result["warnings"].extend(aggregate_warnings)
            result["warnings"].extend(daily_warnings)
            coverage = dict(exact.get("coverage") or {})
            coverage_complete = bool(coverage.get("complete"))
            target = dict(exact.get("target") or {})
            resolved_ids = list(target.get("item_ids") or [])
            result["sources"].append({
                "provider": "mercado_livre",
                "resource": "users/{seller_id}/items/search",
                "method": "GET",
                "store": nome_loja,
                "filters": list(target.get("filters_used") or []),
            })
            if resolved_ids:
                result["sources"].append({
                    "provider": "mercado_livre",
                    "resource": "orders/search",
                    "method": "GET",
                    "store": nome_loja,
                    "filters": ["seller", "q=item_id", "date_created", "sort=date_desc"],
                })
            result.update({
                "found": bool(orders),
                "orders": orders,
                "by_sku": by_sku,
                "by_day": by_day,
                "totals": totals,
                "target": {
                    "sku": str(target.get("sku") or sku_value),
                    "item_ids": resolved_ids,
                    "resolution_complete": bool(target.get("resolution_complete")),
                    "filters_used": list(target.get("filters_used") or []),
                },
                "match": dict(exact.get("match") or {}),
                "coverage": coverage,
                "exact_coverage": coverage,
                "coverage_complete": coverage_complete,
                "evidence": list(exact.get("evidence") or []),
                "partial_response": bool(exact.get("partial_response")),
                "truncated": not coverage_complete,
                "paging": {
                    "offset": 0,
                    "limit": 1,
                    "returned": len(orders),
                    "total": len(orders),
                    "next_offset": None,
                    "has_more": not coverage_complete,
                    "pages_fetched": int(coverage.get("pages_fetched") or 0),
                    "scanned": int(coverage.get("orders_inspected") or 0),
                    "report_mode": False,
                },
                "chart_data": _ia_ml_sales_chart_data(
                    by_day,
                    totals,
                    coverage_complete=coverage_complete,
                    paging={
                        "returned": len(orders),
                        "pages_fetched": int(coverage.get("pages_fetched") or 0),
                        "has_more": not coverage_complete,
                    },
                ),
                "buyer_summary": {
                    "requested": details_requested,
                    "allowed_fields": ["buyer_name", "buyer_city"],
                    "cities_returned": 0,
                    "city_lookup_limit": 0,
                    "sensitive_fields_omitted": True,
                },
            })
            if exact.get("error"):
                result.update({
                    "error": str(exact.get("error") or ""),
                    "message": str(exact.get("message") or "")[:240],
                    "reconnect_required": bool(exact.get("reconnect_required")),
                })
            if not orders and not result.get("error"):
                if coverage_complete:
                    result["warnings"].append("A API do Mercado Livre nao encontrou venda deste SKU no periodo consultado.")
                else:
                    result["warnings"].append("A busca terminou sem cobertura suficiente para confirmar a ultima venda deste SKU.")
            return {"function": function_name, "arguments": arguments, "result": result}

        url = f"{ML_IA_API_BASE}/orders/search"
        result["sources"].append({"provider": "mercado_livre", "resource": "orders/search", "method": "GET", "store": nome_loja})
        orders_raw = []
        api_offset = offset_value
        total = 0
        pages = 0
        partial_response = False
        partial_content = []
        has_local_filter = bool(sku_value or item_ids or order_id_value or local_status_filter)
        collection_limit = max(100, limit_value) if has_local_filter else limit_value
        collection_limit = min(collection_limit, 20_000 if report_mode else 100)
        page_budget = min(max_pages_value, max(1, (collection_limit + 49) // 50))
        while len(orders_raw) < collection_limit and pages < page_budget:
            page_limit = min(50, collection_limit - len(orders_raw))
            params = {
                "seller": seller_id,
                "order.date_created.from": inicio.isoformat(timespec="milliseconds"),
                "order.date_created.to": fim.isoformat(timespec="milliseconds"),
                "sort": "date_desc",
                "offset": api_offset,
                "limit": page_limit,
            }
            if not local_status_filter:
                params["order.status"] = ",".join(status_values)
            if order_id_value:
                params["q"] = order_id_value
            elif item_ids:
                params["q"] = item_ids[0]
            resp, cfg = _ia_ml_request_get(
                client_id,
                nome_loja,
                cfg,
                url,
                params=params,
                timeout=20,
                deadline=deadline,
            )
            response_status = int(getattr(resp, "status_code", 0) or 0)
            if response_status not in {200, 206}:
                code, message, reconnect = _ia_ml_http_failure(resp, "Erro ao consultar pedidos do Mercado Livre")
                result.update({"error": code, "message": message, "reconnect_required": reconnect})
                return {"function": function_name, "arguments": arguments, "result": result}
            if response_status == 206:
                partial_response = True
                headers = getattr(resp, "headers", {}) or {}
                missing = str(headers.get("X-Content-Missing") or headers.get("x-content-missing") or "").strip()
                if missing:
                    partial_content.extend(part.strip() for part in missing.split(",") if part.strip())
            payload = resp.json() or {}
            page_rows = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            total = _ia_ml_int(paging.get("total"), total, 0, 100_000_000)
            pages += 1
            if not page_rows:
                break
            orders_raw.extend(page_rows[: collection_limit - len(orders_raw)])
            api_offset += len(page_rows)
            if api_offset >= total or len(page_rows) < page_limit:
                break

        filtered_with_index = []
        item_filter = item_ids[0] if item_ids else ""
        sku_filter_norm = _normalizar_texto(sku_value)
        for raw_index, raw in enumerate(orders_raw):
            order = _ia_ml_sanitize_order(raw)
            if str(order.get("status") or "").strip().lower() not in status_values:
                continue
            if order_id_value and str(order.get("order_id") or "") != order_id_value:
                continue
            if item_filter and not any(str(entry.get("item_id") or "").upper() == item_filter for entry in order.get("items") or []):
                continue
            if sku_filter_norm and not any(_normalizar_texto(str(entry.get("sku") or "")) == sku_filter_norm for entry in order.get("items") or []):
                continue
            filtered_with_index.append((raw_index, order, raw))
        selected = filtered_with_index[:limit_value]
        city_enriched = 0
        shipment_lookups = 0
        detail_warnings = []
        if details_requested:
            for _raw_index, order, raw in selected[:10]:
                order["buyer_name"] = _ia_ml_buyer_name(raw)
                order["buyer_city"] = _ia_ml_order_embedded_city(raw)
                if not order["buyer_city"] and _ia_ml_order_shipment_id(raw):
                    shipment_lookups += 1
                city, cfg, city_warning = _ia_ml_fetch_order_city(
                    client_id,
                    nome_loja,
                    cfg,
                    raw,
                    deadline,
                )
                order["buyer_city"] = city
                if city:
                    city_enriched += 1
                if city_warning:
                    detail_warnings.append(city_warning)
            if len(selected) > 10:
                detail_warnings.append("A cidade foi consultada somente para os 10 primeiros pedidos deste resultado.")
            if shipment_lookups:
                result["sources"].append({
                    "provider": "mercado_livre",
                    "resource": "shipments/{id}",
                    "method": "GET",
                    "store": nome_loja,
                    "fields_used": ["receiver_address.city.name"],
                })
        sanitized = [order for _raw_index, order, _raw in selected]
        totals, by_sku, aggregate_warnings = _ia_ml_aggregate_orders(sanitized)
        by_day, daily_warnings = _ia_ml_aggregate_orders_by_day(sanitized)
        result["warnings"].extend(aggregate_warnings)
        result["warnings"].extend(daily_warnings)
        result["warnings"].extend(dict.fromkeys(detail_warnings))
        if partial_response:
            suffix = f" Campos omitidos: {', '.join(dict.fromkeys(partial_content))}." if partial_content else ""
            result["warnings"].append(f"O Mercado Livre retornou resposta parcial (HTTP 206).{suffix}")
        has_unreturned_matches = len(filtered_with_index) > len(selected)
        provider_has_more = bool(total > api_offset)
        has_more = bool(has_unreturned_matches or provider_has_more)
        if has_more and selected:
            next_offset = offset_value + selected[-1][0] + 1
        elif has_more:
            next_offset = api_offset
        else:
            next_offset = None
        coverage_complete = not bool(has_more or partial_response)
        paging_result = {
            "offset": offset_value,
            "limit": limit_value,
            "returned": len(sanitized),
            "total": total,
            "next_offset": next_offset,
            "has_more": has_more,
            "pages_fetched": pages,
            "max_pages": max_pages_value,
            "page_size": 50,
            "report_mode": report_mode,
            "scanned": len(orders_raw),
        }
        result.update({
            "found": bool(sanitized),
            "orders": sanitized,
            "by_sku": by_sku,
            "by_day": by_day,
            "totals": totals,
            "paging": paging_result,
            "chart_data": _ia_ml_sales_chart_data(
                by_day,
                totals,
                coverage_complete=coverage_complete,
                paging=paging_result,
            ),
            "truncated": bool(has_more or partial_response),
            "partial_response": partial_response,
            "coverage_complete": coverage_complete,
            "buyer_summary": {
                "requested": details_requested,
                "allowed_fields": ["buyer_name", "buyer_city"],
                "cities_returned": city_enriched,
                "city_lookup_limit": 10,
                "sensitive_fields_omitted": True,
            },
        })
        if report_mode and has_more:
            result["warnings"].append(
                f"O relatorio consultou {pages} pagina(s) e {len(orders_raw)} pedido(s) do Mercado Livre, "
                "mas o periodo possui mais registros; a cobertura foi marcada como incompleta."
            )
        if not sanitized:
            result["warnings"].append("A API do Mercado Livre retornou zero pedidos para os filtros informados.")
        return {"function": function_name, "arguments": arguments, "result": result}
    except requests.exceptions.Timeout:
        timeout_seconds = ML_IA_LATEST_QUERY_TIMEOUT_SECONDS if latest_targeted else ML_IA_QUERY_TIMEOUT_SECONDS
        result.update({
            "error": "timeout",
            "message": f"A consulta do Mercado Livre excedeu {timeout_seconds} segundos.",
            "coverage_complete": False,
        })
        if latest_targeted:
            _ia_ml_mark_exact_failure(result, sku=sku_value, item_ids=item_ids, stop_reason="timeout")
        return {"function": function_name, "arguments": arguments, "result": result}
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        result.update({
            "error": "reconnect_required" if reconnect else "integration_error",
            "message": str(getattr(exc, "detail", None) or exc)[:240],
            "reconnect_required": reconnect,
        })
        if latest_targeted:
            _ia_ml_mark_exact_failure(
                result,
                sku=sku_value,
                item_ids=item_ids,
                stop_reason="reconnect_required" if reconnect else "integration_error",
            )
        return {"function": function_name, "arguments": arguments, "result": result}
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar pedidos Mercado Livre (%s): %s", nome_loja, exc)
        result.update({"error": "provider_unavailable", "message": "Nao foi possivel consultar os pedidos do Mercado Livre agora."})
        if latest_targeted:
            _ia_ml_mark_exact_failure(result, sku=sku_value, item_ids=item_ids, stop_reason="provider_unavailable")
        return {"function": function_name, "arguments": arguments, "result": result}


def _ia_tool_get_mercado_livre_returns(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
    limite: int = 1,
    offset: int = 0,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> dict:
    """Consulta devolucoes diretamente na API de claims/returns do Mercado Livre."""

    del force_refresh  # O chamador controla o cache curto da ferramenta.
    function_name = "get_mercado_livre_returns"
    sku_value = str(sku or "").strip()
    if _ia_ml_sku_parece_data(sku_value):
        sku_value = ""
    item_ids = _ia_ml_normalizar_item_ids(item_id, mensagem)
    latest_targeted = bool(_ia_ml_latest_requested(mensagem) and (sku_value or item_ids))
    limit_value = _ia_ml_int(limite, 1, 1, 100)
    offset_value = _ia_ml_int(offset, 0, 0, 10_000)
    if latest_targeted:
        limit_value = 1
        offset_value = 0
    arguments = {
        "loja": str(loja or "").strip(),
        "data_inicio": data_inicio or "",
        "data_fim": data_fim or "",
        "sku": sku_value,
        "item_id": item_ids[0] if item_ids else "",
        "limit": limit_value,
        "offset": offset_value,
        "sort": "last_updated:desc",
        "association": "related_entities:return",
    }
    nome_loja, failure = _ia_ml_resolver_loja_exata(client_id, loja)
    if not nome_loja:
        return _ia_ml_store_failure(function_name, arguments, failure)

    inicio, fim, requested_period = _ia_ml_claims_period(mensagem, data_inicio, data_fim)
    arguments["period"] = requested_period
    if inicio is None or fim is None:
        return _ia_ml_store_failure(
            function_name,
            arguments,
            {"code": "invalid_period", "message": "A data inicial deve ser anterior ou igual a data final.", "available_stores": [nome_loja]},
        )

    result = _ia_ml_base_result()
    result.update({
        "found": False,
        "store": nome_loja,
        "coverage": "mercado_livre_claims_returns_api",
        "period": {"requested": requested_period, "effective": requested_period, "timezone": "America/Sao_Paulo", "field": "last_updated"},
        "devolucoes": [],
        "returns": [],
        "paging": {"offset": offset_value, "limit": limit_value, "returned": 0, "total": 0, "next_offset": None, "has_more": False},
        "latest_first": True,
    })
    deadline = (
        _ia_ml_latest_deadline(query_deadline)
        if latest_targeted
        else time.monotonic() + ML_IA_QUERY_TIMEOUT_SECONDS
    )
    if not latest_targeted and query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass

    try:
        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str((cfg or {}).get("user_id") or "").strip()
        if not seller_id:
            result.update({"error": "seller_id_missing", "message": "A conta Mercado Livre nao informou o seller id."})
            return {"function": function_name, "arguments": arguments, "result": result}

        if latest_targeted:
            exact, cfg = _ia_ml_find_latest_return_by_target(
                client_id,
                nome_loja,
                cfg,
                seller_id=seller_id,
                inicio=inicio,
                fim=fim,
                sku=sku_value,
                item_ids=item_ids,
                deadline=deadline,
            )
            rows = list(exact.get("rows") or [])
            coverage = dict(exact.get("coverage") or {})
            coverage_complete = bool(coverage.get("complete"))
            target = dict(exact.get("target") or {})
            resolved_ids = list(target.get("item_ids") or [])
            result["warnings"].extend(exact.get("warnings") or [])
            result["sources"].extend([
                {
                    "provider": "mercado_livre",
                    "resource": "users/{seller_id}/items/search",
                    "method": "GET",
                    "store": nome_loja,
                    "filters": list(target.get("filters_used") or []),
                },
                {
                    "provider": "mercado_livre",
                    "resource": "post-purchase/v1/claims/search",
                    "method": "GET",
                    "store": nome_loja,
                    "filters": ["player_user_id", "resource=order", "last_updated", "sort=desc"],
                },
            ])
            if rows:
                result["sources"].extend([
                    {
                        "provider": "mercado_livre",
                        "resource": "orders/{order_id}",
                        "method": "GET",
                        "store": nome_loja,
                    },
                    {
                        "provider": "mercado_livre",
                        "resource": "post-purchase/v2/claims/{claim_id}/returns",
                        "method": "GET",
                        "store": nome_loja,
                    },
                ])
            result.update({
                "found": bool(rows),
                "devolucoes": rows,
                "returns": rows,
                "target": {
                    "sku": str(target.get("sku") or sku_value),
                    "item_ids": resolved_ids,
                    "resolution_complete": bool(target.get("resolution_complete")),
                    "filters_used": list(target.get("filters_used") or []),
                },
                "match": dict(exact.get("match") or {}),
                "coverage": coverage,
                "exact_coverage": coverage,
                "coverage_complete": coverage_complete,
                "evidence": list(exact.get("evidence") or []),
                "partial_response": bool(exact.get("partial_response")),
                "paging": {
                    "offset": 0,
                    "limit": 1,
                    "returned": len(rows),
                    "total": len(rows),
                    "total_complete": coverage_complete,
                    "claims_total": int(coverage.get("claims_total") or 0),
                    "claims_scanned": int(coverage.get("claims_scanned") or 0),
                    "orders_inspected": int(coverage.get("orders_inspected") or 0),
                    "pages_fetched": int(coverage.get("pages_fetched") or 0),
                    "next_offset": None,
                    "has_more": not coverage_complete,
                },
            })
            if exact.get("error"):
                result.update({
                    "error": str(exact.get("error") or ""),
                    "message": str(exact.get("message") or "")[:240],
                    "reconnect_required": bool(exact.get("reconnect_required")),
                })
            if not rows and not result.get("error"):
                if coverage_complete:
                    result["warnings"].append("A API do Mercado Livre nao encontrou devolucao deste SKU no periodo consultado.")
                else:
                    result["warnings"].append("A busca terminou sem cobertura suficiente para confirmar a ultima devolucao deste SKU.")
            return {"function": function_name, "arguments": arguments, "result": result}

        url = f"{ML_IA_API_BASE}/post-purchase/v1/claims/search"
        target_matches = offset_value + limit_value
        scan_cap = min(100, max(20, target_matches * 10))
        claim_offset = 0
        claims_scanned = 0
        claims_total = 0
        pages = 0
        response_partial = False
        matches: list[dict[str, Any]] = []
        detail_source_used = False
        return_source_used = False
        result["sources"].append({
            "provider": "mercado_livre",
            "resource": "post-purchase/v1/claims/search",
            "method": "GET",
            "store": nome_loja,
            "filters": ["player_user_id", "player_role=respondent", "resource=order", "last_updated"],
        })

        while len(matches) < target_matches and claims_scanned < scan_cap and pages < 5:
            page_limit = min(20, scan_cap - claims_scanned)
            params = {
                "player_user_id": seller_id,
                "player_role": "respondent",
                "resource": "order",
                "range": f"last_updated:after:{inicio.isoformat(timespec='milliseconds')},before:{fim.isoformat(timespec='milliseconds')}",
                "sort": "last_updated:desc",
                "offset": claim_offset,
                "limit": page_limit,
            }
            response, cfg = _ia_ml_request_get(
                client_id,
                nome_loja,
                cfg,
                url,
                params=params,
                timeout=20,
                deadline=deadline,
            )
            response_status = int(getattr(response, "status_code", 0) or 0)
            if response_status not in {200, 206}:
                code, message, reconnect = _ia_ml_http_failure(
                    response, "Erro ao consultar devolucoes do Mercado Livre"
                )
                if pages == 0:
                    result.update({"error": code, "message": message, "reconnect_required": reconnect})
                    return {"function": function_name, "arguments": arguments, "result": result}
                result["warnings"].append(
                    "A busca de reclamacoes foi interrompida antes de concluir a varredura de devolucoes."
                )
                break
            response_partial = response_partial or response_status == 206
            payload = response.json() or {}
            claims = [item for item in (payload.get("data") or []) if isinstance(item, dict)]
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            claims_total = _ia_ml_int(paging.get("total"), claims_total, 0, 100_000_000)
            pages += 1
            if not claims:
                break

            for claim in claims:
                claims_scanned += 1
                effective_claim = claim
                if not _ia_ml_claim_has_return(effective_claim):
                    claim_id = str(claim.get("id") or "").strip()
                    if not claim_id:
                        continue
                    detail_response, cfg = _ia_ml_request_get(
                        client_id,
                        nome_loja,
                        cfg,
                        f"{ML_IA_API_BASE}/post-purchase/v1/claims/{claim_id}",
                        timeout=20,
                        deadline=deadline,
                    )
                    if int(getattr(detail_response, "status_code", 0) or 0) not in {200, 206}:
                        continue
                    detail_source_used = True
                    detail_payload = detail_response.json() or {}
                    if isinstance(detail_payload, dict):
                        effective_claim = detail_payload
                if not _ia_ml_claim_has_return(effective_claim):
                    continue

                claim_id = str(effective_claim.get("id") or claim.get("id") or "").strip()
                if not claim_id:
                    continue
                return_response, cfg = _ia_ml_request_get(
                    client_id,
                    nome_loja,
                    cfg,
                    f"{ML_IA_API_BASE}/post-purchase/v2/claims/{claim_id}/returns",
                    timeout=20,
                    deadline=deadline,
                )
                if int(getattr(return_response, "status_code", 0) or 0) not in {200, 206}:
                    continue
                return_payload: Any = return_response.json() or {}
                if isinstance(return_payload, list):
                    return_payload = next((item for item in return_payload if isinstance(item, dict)), {})
                if not isinstance(return_payload, dict) or not return_payload.get("id"):
                    continue
                return_source_used = True
                row = _ia_ml_sanitize_return_claim(effective_claim)
                row["return_detail"] = _ia_ml_sanitize_return_detail(return_payload)
                matches.append(row)
                if len(matches) >= target_matches:
                    break

            claim_offset += len(claims)
            if claim_offset >= claims_total or len(claims) < page_limit:
                break

        if detail_source_used:
            result["sources"].append({
                "provider": "mercado_livre",
                "resource": "post-purchase/v1/claims/{claim_id}",
                "method": "GET",
                "store": nome_loja,
                "fields_used": ["related_entities"],
            })
        if return_source_used:
            result["sources"].append({
                "provider": "mercado_livre",
                "resource": "post-purchase/v2/claims/{claim_id}/returns",
                "method": "GET",
                "store": nome_loja,
            })

        rows = matches[offset_value : offset_value + limit_value]
        if rows:
            result["sources"].append({
                "provider": "mercado_livre",
                "resource": "orders/{order_id}",
                "method": "GET",
                "store": nome_loja,
            })
        for row in rows[:10]:
            order_id = str(row.get("order_id") or "").strip()
            if order_id:
                order_response, cfg = _ia_ml_request_get(
                    client_id,
                    nome_loja,
                    cfg,
                    f"{ML_IA_API_BASE}/orders/{order_id}",
                    timeout=20,
                    deadline=deadline,
                )
                if int(getattr(order_response, "status_code", 0) or 0) in {200, 206}:
                    row["order"] = _ia_ml_sanitize_order(order_response.json() or {})
                else:
                    result["warnings"].append(f"Pedido {order_id}: detalhes indisponiveis na API do Mercado Livre.")

        claims_remaining = bool(claim_offset < claims_total)
        has_more = bool(len(matches) > offset_value + len(rows) or claims_remaining)
        next_offset = offset_value + len(rows) if has_more and rows else None
        result.update({
            "found": bool(rows),
            "devolucoes": rows,
            "returns": rows,
            "paging": {
                "offset": offset_value,
                "limit": limit_value,
                "returned": len(rows),
                "total": offset_value + len(rows),
                "total_complete": not has_more,
                "claims_total": claims_total,
                "claims_scanned": claims_scanned,
                "pages_fetched": pages,
                "next_offset": next_offset,
                "has_more": has_more,
            },
            # Para "ultima devolucao", o primeiro item e conclusivo porque a
            # propria API ordenou por last_updated desc, mesmo havendo historico.
            "coverage_complete": bool(rows) or not has_more,
            "partial_response": response_partial,
        })
        if response_partial:
            result["warnings"].append("O Mercado Livre retornou resposta parcial (HTTP 206) para devolucoes.")
        if not rows and has_more:
            result["warnings"].append(
                "A varredura atingiu o limite de 100 reclamacoes sem confirmar uma devolucao; informe um periodo menor para ampliar a precisao."
            )
        if not rows:
            result["warnings"].append("A API do Mercado Livre nao retornou devolucoes para o periodo informado.")
        return {"function": function_name, "arguments": arguments, "result": result}
    except requests.exceptions.Timeout:
        timeout_seconds = ML_IA_LATEST_QUERY_TIMEOUT_SECONDS if latest_targeted else ML_IA_QUERY_TIMEOUT_SECONDS
        result.update({
            "error": "timeout",
            "message": f"A consulta de devolucoes do Mercado Livre excedeu {timeout_seconds} segundos.",
            "coverage_complete": False,
        })
        if latest_targeted:
            _ia_ml_mark_exact_failure(result, sku=sku_value, item_ids=item_ids, stop_reason="timeout")
        return {"function": function_name, "arguments": arguments, "result": result}
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        result.update({
            "error": "reconnect_required" if reconnect else "integration_error",
            "message": str(getattr(exc, "detail", None) or exc)[:240],
            "reconnect_required": reconnect,
        })
        if latest_targeted:
            _ia_ml_mark_exact_failure(
                result,
                sku=sku_value,
                item_ids=item_ids,
                stop_reason="reconnect_required" if reconnect else "integration_error",
            )
        return {"function": function_name, "arguments": arguments, "result": result}
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha ao consultar devolucoes Mercado Livre (%s): %s", nome_loja, exc)
        result.update({"error": "provider_unavailable", "message": "Nao foi possivel consultar as devolucoes do Mercado Livre agora."})
        if latest_targeted:
            _ia_ml_mark_exact_failure(result, sku=sku_value, item_ids=item_ids, stop_reason="provider_unavailable")
        return {"function": function_name, "arguments": arguments, "result": result}


def _ia_ml_search_listing_ids(
    client_id: str,
    loja: str,
    cfg: dict,
    *,
    status_item: str,
    offset: int,
    limite: int,
    sku: str = "",
    deadline: Optional[float] = None,
) -> tuple[list[str], dict, dict, Optional[dict]]:
    user_id = str((cfg or {}).get("user_id") or "").strip()
    if not user_id:
        return [], cfg, {"offset": offset, "limit": limite, "total": 0, "next_offset": None, "has_more": False, "pages_fetched": 0}, {
            "code": "seller_id_missing",
            "message": "A conta Mercado Livre nao informou o seller id.",
            "reconnect_required": False,
        }
    url = f"{ML_IA_API_BASE}/users/{user_id}/items/search"
    ids = []
    seen = set()
    api_offset = offset
    total = 0
    pages = 0
    partial_response = False
    partial_content = []
    while len(ids) < limite and pages < 5:
        page_limit = min(20, limite - len(ids))
        params = {"offset": api_offset, "limit": page_limit, "status": status_item}
        if sku:
            params["seller_sku"] = sku
        resp, cfg = _ia_ml_request_get(
            client_id,
            loja,
            cfg,
            url,
            params=params,
            timeout=20,
            deadline=deadline,
        )
        if int(getattr(resp, "status_code", 0) or 0) not in {200, 206}:
            code, message, reconnect = _ia_ml_http_failure(resp, "Erro ao listar anuncios do Mercado Livre")
            return ids, cfg, {"offset": offset, "limit": limite, "total": total, "next_offset": None, "has_more": False, "pages_fetched": pages}, {
                "code": code,
                "message": message,
                "reconnect_required": reconnect,
            }
        if int(getattr(resp, "status_code", 0) or 0) == 206:
            partial_response = True
            headers = getattr(resp, "headers", {}) or {}
            missing = str(headers.get("X-Content-Missing") or headers.get("x-content-missing") or "").strip()
            if missing:
                partial_content.extend(part.strip() for part in missing.split(",") if part.strip())
        payload = resp.json() or {}
        raw_results = payload.get("results") or []
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        total = _ia_ml_int(paging.get("total"), total, 0, 100_000_000)
        pages += 1
        if not raw_results:
            break
        for entry in raw_results:
            item_id = str((entry.get("id") if isinstance(entry, dict) else entry) or "").strip().upper()
            if item_id and item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
                if len(ids) >= limite:
                    break
        api_offset += len(raw_results)
        if api_offset >= total or len(raw_results) < page_limit:
            break
    has_more = bool(total > api_offset)
    return ids, cfg, {
        "offset": offset,
        "limit": limite,
        "returned": len(ids),
        "total": total,
        "next_offset": api_offset if has_more else None,
        "has_more": has_more,
        "pages_fetched": pages,
        "partial_response": partial_response,
        "partial_content": list(dict.fromkeys(partial_content)),
    }, None


def _ia_ml_fetch_listing_items(
    client_id: str,
    loja: str,
    cfg: dict,
    item_ids: list[str],
    *,
    deadline: Optional[float] = None,
) -> tuple[list[dict], dict, Optional[dict]]:
    items_by_id = {}
    for start in range(0, len(item_ids), 20):
        chunk = item_ids[start : start + 20]
        resp, cfg = _ia_ml_request_get(
            client_id,
            loja,
            cfg,
            f"{ML_IA_API_BASE}/items",
            params={"ids": ",".join(chunk), "include_attributes": "all"},
            timeout=20,
            deadline=deadline,
        )
        if int(getattr(resp, "status_code", 0) or 0) != 200:
            code, message, reconnect = _ia_ml_http_failure(resp, "Erro ao detalhar anuncios do Mercado Livre")
            return [items_by_id[item_id] for item_id in item_ids if item_id in items_by_id], cfg, {
                "code": code,
                "message": message,
                "reconnect_required": reconnect,
            }
        payload = resp.json() or []
        for entry in payload if isinstance(payload, list) else []:
            body = entry.get("body") if isinstance(entry, dict) and isinstance(entry.get("body"), dict) else entry
            if not isinstance(body, dict):
                continue
            item_id = str(body.get("id") or "").strip().upper()
            if item_id:
                items_by_id[item_id] = body
    return [items_by_id[item_id] for item_id in item_ids if item_id in items_by_id], cfg, None


def _ia_ml_complete_listing_variations(
    client_id: str,
    loja: str,
    cfg: dict,
    item: dict,
    *,
    deadline: Optional[float] = None,
) -> tuple[dict, dict, Optional[dict]]:
    item_id = str((item or {}).get("id") or "").strip().upper()
    variations = (item or {}).get("variations") if isinstance((item or {}).get("variations"), list) else []
    if not item_id or not variations:
        return item, cfg, None
    resp, cfg = _ia_ml_request_get(
        client_id,
        loja,
        cfg,
        f"{ML_IA_API_BASE}/items/{item_id}/variations",
        params={"include_attributes": "all"},
        timeout=15,
        deadline=deadline,
    )
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        code, message, reconnect = _ia_ml_http_failure(resp, "Erro ao detalhar variacoes do anuncio")
        return item, cfg, {"code": code, "message": message, "reconnect_required": reconnect}
    payload = resp.json() or []
    detailed = payload.get("variations") or payload.get("results") or [] if isinstance(payload, dict) else payload
    details_by_id = {
        str(variation.get("id") or "").strip(): variation
        for variation in detailed or []
        if isinstance(variation, dict) and str(variation.get("id") or "").strip()
    }
    if not details_by_id:
        return item, cfg, None
    merged_variations = []
    for variation in variations:
        if not isinstance(variation, dict):
            merged_variations.append(variation)
            continue
        detail = details_by_id.get(str(variation.get("id") or "").strip()) or {}
        merged = dict(detail)
        merged.update({key: value for key, value in variation.items() if value not in (None, "", [])})
        if detail.get("attributes") and not merged.get("attributes"):
            merged["attributes"] = detail.get("attributes")
        if detail.get("attribute_combinations") and not merged.get("attribute_combinations"):
            merged["attribute_combinations"] = detail.get("attribute_combinations")
        merged_variations.append(merged)
    enriched = dict(item)
    enriched["variations"] = merged_variations
    return enriched, cfg, None


def _ia_ml_validate_listing_sku_matches(
    client_id: str,
    loja: str,
    cfg: dict,
    items: list[dict],
    requested_sku: str,
    *,
    deadline: Optional[float] = None,
) -> tuple[list[dict], dict, dict]:
    verified: list[dict] = []
    rejected_ids: list[str] = []
    unverified_ids: list[str] = []
    errors: list[dict] = []
    variation_requests = 0
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = raw_item
        match = _ia_ml_find_exact_sku_match(item, requested_sku)
        if not match and isinstance(item.get("variations"), list) and item.get("variations"):
            variation_requests += 1
            try:
                item, cfg, failure = _ia_ml_complete_listing_variations(
                    client_id,
                    loja,
                    cfg,
                    item,
                    deadline=deadline,
                )
            except requests.exceptions.Timeout:
                failure = {
                    "code": "timeout",
                    "message": "A validacao das variacoes excedeu o tempo disponivel.",
                    "reconnect_required": False,
                }
            if failure:
                item_id = str(item.get("id") or "").strip()
                unverified_ids.append(item_id)
                errors.append({"item_id": item_id, **failure})
                continue
            match = _ia_ml_find_exact_sku_match(item, requested_sku)
        if not match:
            rejected_ids.append(str(item.get("id") or "").strip())
            continue
        matched_item = dict(item)
        matched_item["_ia_sku_match"] = match
        verified.append(matched_item)
    return verified, cfg, {
        "requested_sku": str(requested_sku or "").strip(),
        "search_results": len(items),
        "verified_matches": len(verified),
        "rejected_ids": rejected_ids,
        "unverified_ids": unverified_ids,
        "variation_requests": variation_requests,
        "complete": not bool(unverified_ids),
        "errors": errors,
    }


def _ia_ml_listing_details_from_item(item: dict) -> dict:
    """Extrai somente detalhes read-only presentes no recurso de item, sem PII."""
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    price = item.get("price")
    original_price = item.get("original_price")
    promotion_detected = bool(
        price is not None
        and original_price is not None
        and _ia_ml_float(original_price) > _ia_ml_float(price)
    )
    fee_fields = {
        "sale_fee_amount": item.get("sale_fee_amount"),
        "listing_fee_amount": item.get("listing_fee_amount"),
    }
    return {
        "shipping": {
            "mode": str(shipping.get("mode") or "").strip(),
            "logistic_type": str(shipping.get("logistic_type") or "").strip(),
            "free_shipping": bool(shipping.get("free_shipping")),
            "store_pick_up": bool(shipping.get("store_pick_up")),
        },
        "fees": fee_fields,
        "promotion": {
            "detected_from_price": promotion_detected,
            "price": price,
            "original_price": original_price,
            "campaign_details_available": False,
        },
    }


def _ia_ml_listar_anuncios(
    client_id: str,
    loja: str,
    cfg: dict,
    status_item: str,
    limite: int = 10,
    offset: int = 0,
    sku: str = "",
) -> tuple[list[dict], dict]:
    """Wrapper legado; agora pagina em blocos de 20 e continua somente read-only."""
    ids, cfg, _paging, failure = _ia_ml_search_listing_ids(
        client_id,
        loja,
        cfg,
        status_item=status_item,
        offset=_ia_ml_int(offset, 0, 0, 1_000_000),
        limite=_ia_ml_int(limite, 10, 1, 100),
        sku=str(sku or "").strip(),
    )
    if failure or not ids:
        return [], cfg
    items, cfg, _failure = _ia_ml_fetch_listing_items(client_id, loja, cfg, ids)
    return items, cfg


def _ia_tool_get_mercado_livre_listing(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    produto_tool: Optional[dict] = None,
    limite: int = 8,
    incluir_descricao: bool = False,
    status: Optional[str] = None,
    sku: Optional[str] = None,
    item_id: Optional[str] = None,
    offset: int = 0,
    incluir_detalhes: bool = False,
    force_refresh: bool = False,
    mlb: Optional[str] = None,
    query_deadline: Optional[float] = None,
) -> Optional[dict]:
    del force_refresh  # Reservado para a camada de cache do dispatcher.
    function_name = "get_mercado_livre_listing"
    try:
        explicit_ids = _ia_ml_normalizar_item_ids(item_id, mlb)
        item_ids = explicit_ids or _ia_ml_normalizar_item_ids(mensagem)
        sku_value = str(sku or "").strip()
        if not sku_value and not item_ids:
            sku_value = str(_ia_tool_resolver_sku(client_id, mensagem, produto_tool) or "").strip()
            ref = _ia_extrair_referencia_produto_mensagem(mensagem)
            if not sku_value and isinstance(ref, dict) and ref.get("sku"):
                sku_value = str(ref.get("sku") or "").strip()
        if _ia_ml_sku_parece_data(sku_value):
            sku_value = ""

        texto_norm = _normalizar_texto(mensagem)
        listar_sem_ref = bool(
            not item_ids
            and not sku_value
            and any(chave in texto_norm for chave in ("ANUNCIOS", "ANUNCIO", "ITENS ATIVOS", "ITENS PAUSADOS", "LISTE", "LISTAR"))
        )
        if not item_ids and not sku_value and not listar_sem_ref:
            return None

        status_value = str(status or "").strip().lower()
        if not status_value:
            status_value = "paused" if "PAUSAD" in texto_norm else "active"
        if status_value not in ML_IA_LISTING_STATUSES:
            status_value = "active"
        offset_value = _ia_ml_int(offset, 0, 0, 1_000_000)
        limit_value = _ia_ml_int(limite, 8, 1, 100)
        details_requested = bool(incluir_descricao or incluir_detalhes or _ia_ml_precisa_descricao(mensagem))
        arguments = {
            "sku": sku_value,
            "item_ids": item_ids,
            "loja": str(loja or "").strip(),
            "status": status_value,
            "offset": offset_value,
            "limit": limit_value,
            "include_details": details_requested,
            "modo": "lista" if listar_sem_ref else ("item_id" if item_ids else "sku"),
        }
        nome_loja, failure = _ia_ml_resolver_loja_exata(client_id, loja)
        if not nome_loja:
            return _ia_ml_store_failure(function_name, arguments, failure)

        result = _ia_ml_base_result()
        result.update({
            "found": False,
            "store": nome_loja,
            "coverage": "mercado_livre_api_items",
            "matches": [],
            "chart_data": _ia_ml_listing_chart_data([], coverage_complete=False),
            "paging": {"offset": offset_value, "limit": limit_value, "returned": 0, "total": 0, "next_offset": None, "has_more": False},
            "truncated": False,
        })
        deadline = time.monotonic() + ML_IA_QUERY_TIMEOUT_SECONDS
        if query_deadline is not None:
            try:
                deadline = min(deadline, float(query_deadline))
            except (TypeError, ValueError):
                pass
        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)
            result["sources"] = [
                {"provider": "mercado_livre", "resource": "users/{seller_id}/items/search", "method": "GET", "store": nome_loja},
                {"provider": "mercado_livre", "resource": "items multiget", "method": "GET", "store": nome_loja},
            ]
            if item_ids:
                ids = item_ids[offset_value : offset_value + limit_value]
                total = len(item_ids)
                next_offset = offset_value + len(ids)
                paging = {
                    "offset": offset_value,
                    "limit": limit_value,
                    "returned": len(ids),
                    "total": total,
                    "next_offset": next_offset if next_offset < total else None,
                    "has_more": next_offset < total,
                    "pages_fetched": 0,
                }
                search_failure = None
            else:
                ids, cfg, paging, search_failure = _ia_ml_search_listing_ids(
                    client_id,
                    nome_loja,
                    cfg,
                    status_item=status_value,
                    offset=offset_value,
                    limite=limit_value,
                    sku=sku_value,
                    deadline=deadline,
                )
            if search_failure:
                result.update({
                    "error": search_failure["code"],
                    "message": search_failure["message"],
                    "reconnect_required": bool(search_failure.get("reconnect_required")),
                    "paging": paging,
                })
                return {"function": function_name, "arguments": arguments, "result": result}

            items, cfg, detail_failure = _ia_ml_fetch_listing_items(
                client_id,
                nome_loja,
                cfg,
                ids,
                deadline=deadline,
            )
            if detail_failure:
                result.update({
                    "error": detail_failure["code"],
                    "message": detail_failure["message"],
                    "reconnect_required": bool(detail_failure.get("reconnect_required")),
                })
            sku_validation = {}
            if sku_value and items:
                items, cfg, sku_validation = _ia_ml_validate_listing_sku_matches(
                    client_id,
                    nome_loja,
                    cfg,
                    items,
                    sku_value,
                    deadline=deadline,
                )
                result["sku_validation"] = sku_validation
                if sku_validation.get("variation_requests"):
                    result["sources"].append({
                        "provider": "mercado_livre",
                        "resource": "items/{item_id}/variations?include_attributes=all",
                        "method": "GET",
                        "store": nome_loja,
                    })
                rejected_count = len(sku_validation.get("rejected_ids") or [])
                if rejected_count:
                    result["warnings"].append(
                        f"{rejected_count} anuncio(s) devolvido(s) pela busca foram descartados porque os detalhes nao confirmaram o SKU {sku_value}."
                    )
                if sku_validation.get("unverified_ids"):
                    result["warnings"].append(
                        "Alguns anuncios foram omitidos porque a API nao permitiu validar o SKU de suas variacoes."
                    )
            matches = []
            descriptions_used = 0
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                description = ""
                details = None
                if details_requested and item_index < 10:
                    description, cfg = _ia_ml_obter_descricao_item(
                        client_id,
                        nome_loja,
                        cfg,
                        str(item.get("id") or ""),
                        timeout=_ia_ml_remaining_timeout(deadline, 15),
                    )
                    descriptions_used += 1
                    details = _ia_ml_listing_details_from_item(item)
                matches.append(
                    _ia_ml_item_resumo(
                        item,
                        nome_loja,
                        descricao=description,
                        detalhes=details,
                        requested_sku=sku_value,
                    )
                )
            if details_requested and len(items) > 10:
                result["warnings"].append("Detalhes e descricoes foram limitados aos primeiros 10 anuncios.")
            if details_requested:
                result["details_coverage"] = {
                    "limit": 10,
                    "description": "items/{id}/description",
                    "shipping": "item_resource",
                    "fees": "item_resource_when_available",
                    "promotions": "price_fields_only",
                }
                result["warnings"].append(
                    "Tarifas completas e campanhas promocionais nao foram consultadas em endpoints adicionais; campos ausentes permanecem indisponiveis."
                )
            listing_partial = bool(
                paging.get("partial_response")
                or detail_failure
                or (sku_validation and not sku_validation.get("complete"))
            )
            if listing_partial:
                missing = ", ".join(paging.get("partial_content") or [])
                suffix = f" Campos omitidos: {missing}." if missing else ""
                result["warnings"].append(f"O Mercado Livre retornou resposta parcial de anuncios (HTTP 206).{suffix}")
            result["warnings"].append("sold_quantity representa o total acumulado do anuncio, nao as vendas do periodo.")
            coverage_complete = not bool(paging.get("has_more") or listing_partial)
            result.update({
                "found": bool(matches),
                "matches": matches,
                "paging": {**paging, "returned": len(matches)},
                "chart_data": _ia_ml_listing_chart_data(
                    matches,
                    coverage_complete=coverage_complete,
                    paging={**paging, "returned": len(matches)},
                ),
                "truncated": bool(paging.get("has_more") or listing_partial),
                "partial_response": listing_partial,
                "coverage_complete": coverage_complete,
            })
            if not matches and not result.get("error"):
                if sku_validation and sku_validation.get("search_results"):
                    result["warnings"].append(
                        f"Nenhum anuncio retornado pela busca confirmou exatamente o SKU {sku_value} nos detalhes do item ou de suas variacoes."
                    )
                else:
                    result["warnings"].append("A API do Mercado Livre retornou zero anuncios para os filtros informados.")
            return {"function": function_name, "arguments": arguments, "result": result}
        except requests.exceptions.Timeout:
            result.update({"error": "timeout", "message": "A consulta completa do Mercado Livre excedeu 60 segundos."})
            return {"function": function_name, "arguments": arguments, "result": result}
        except HTTPException as exc:
            reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
            result.update({
                "error": "reconnect_required" if reconnect else "integration_error",
                "message": str(getattr(exc, "detail", None) or exc)[:240],
                "reconnect_required": reconnect,
            })
            return {"function": function_name, "arguments": arguments, "result": result}
        except Exception as exc:
            logger.warning("[IA TOOLS] Falha ao consultar Mercado Livre para IA (%s): %s", nome_loja, exc)
            result.update({"error": "provider_unavailable", "message": "Nao foi possivel consultar os anuncios do Mercado Livre agora."})
            return {"function": function_name, "arguments": arguments, "result": result}
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha geral ao consultar Mercado Livre: %s", exc)
        return None


def _ia_bling_valor_tributario(produto: dict, campo: str) -> str:
    trib = produto.get("tributacao") if isinstance(produto, dict) else {}
    candidatos = []
    if isinstance(trib, dict):
        candidatos.append(trib.get(campo))
    candidatos.append((produto or {}).get(campo))
    for valor in candidatos:
        if isinstance(valor, dict):
            valor = valor.get("codigo") or valor.get("id") or valor.get("valor")
        valor_txt = str(valor or "").strip()
        if valor_txt:
            return valor_txt
    return ""


def _ia_buscar_imagem_ml_sku(client_id: str, sku: str, produto: dict | None = None, cadastro: dict | None = None) -> dict:
    sku_ref = str(sku or "").strip()
    item_ids = _ia_extrair_item_ids_ml(
        (produto or {}).get("mlb_principal"),
        (produto or {}).get("mlb_ids"),
        (cadastro or {}).get("mlb_principal"),
        (cadastro or {}).get("mlb_ids"),
    )

    for nome_loja in _ia_lojas_ml_conectadas(client_id):
        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)

            if item_ids:
                itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids[:10])
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    imagem = _ml_perguntas_foto_item(item)
                    if imagem:
                        return {
                            "imagem_url": imagem,
                            "item_id": str(item.get("id") or "").strip(),
                            "titulo": str(item.get("title") or "").strip(),
                            "loja": nome_loja,
                        }

            if sku_ref:
                itens, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg, sku_ref)
                for item in itens:
                    if not isinstance(item, dict):
                        continue
                    imagem = _ml_perguntas_foto_item(item)
                    if imagem:
                        return {
                            "imagem_url": imagem,
                            "item_id": str(item.get("id") or "").strip(),
                            "titulo": str(item.get("title") or "").strip(),
                            "loja": nome_loja,
                        }
        except Exception as exc:
            logger.warning("[IA TOOLS] Falha ao buscar imagem do SKU %s no Mercado Livre (%s): %s", sku_ref, nome_loja, exc)
            continue

    return {}


def _ia_montar_prompt_geracao_imagem_sku(client_id: str, mensagem: str) -> tuple[str, dict]:
    produto_tool = _ia_tool_get_product_data(client_id, mensagem, limite=1)
    result = (produto_tool or {}).get("result") or {}
    matches = result.get("matches") or []
    produto = matches[0] if matches else {}
    sku = str(produto.get("sku") or result.get("canonical_sku") or "").strip()
    nome = str(produto.get("nome") or "").strip()
    categoria = str(produto.get("categoria") or "").strip()
    marca = str(produto.get("marca") or "").strip()

    cadastro = _ia_tool_get_product_registry_info(client_id, mensagem, produto_tool=produto_tool, limite=1) if sku else None
    cadastro_matches = ((cadastro or {}).get("result") or {}).get("matches") or []
    if cadastro_matches:
        cad = cadastro_matches[0] or {}
        nome = nome or str(cad.get("nome") or "").strip()
        categoria = categoria or str(cad.get("categoria") or "").strip()
        marca = marca or str(cad.get("marca") or "").strip()
        descricao = str(cad.get("descricao") or "").strip()
    else:
        descricao = ""

    detalhes = []
    if sku:
        detalhes.append(f"SKU: {sku}")
    if nome:
        detalhes.append(f"Produto: {nome}")
    if categoria:
        detalhes.append(f"Categoria: {categoria}")
    if marca:
        detalhes.append(f"Marca: {marca}")
    if descricao:
        detalhes.append(f"Descricao do cadastro: {descricao[:700]}")

    prompt = (
        "Crie uma imagem comercial realista e profissional para e-commerce/marketplace.\n"
        "Use a solicitacao do usuario como direcao criativa principal.\n"
        "Nao inclua textos, logos, marcas d'agua, codigos, SKU escrito ou legendas na imagem.\n"
        "Mostre o produto de forma clara, com iluminacao limpa e contexto coerente.\n\n"
        f"Solicitacao do usuario: {mensagem}\n"
        + ("\nDados do produto no cadastro:\n" + "\n".join(detalhes) if detalhes else "")
    ).strip()

    return prompt, {
        "sku": sku,
        "produto": nome,
        "categoria": categoria,
        "marca": marca,
    }


def _ia_salvar_imagem_gerada(client_id: str, image_bytes: bytes, sku: str = "") -> tuple[str, str]:
    tenant_path = get_tenant_path(client_id)
    pasta = os.path.join(tenant_path, "ia_imagens")
    os.makedirs(pasta, exist_ok=True)
    sku_norm = re.sub(r"[^A-Za-z0-9_-]+", "_", str(sku or "sku").strip()).strip("_") or "sku"
    nome = f"ia_{sku_norm}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
    caminho = os.path.join(pasta, nome)
    with open(caminho, "wb") as f:
        f.write(image_bytes)
    return caminho, f"/api/ia/imagens/{quote_plus(nome)}"


def _ia_gerar_imagem_sku_resposta(payload: IAChatRequest, client_id: str) -> Optional[str]:
    mensagem = str(payload.message or "").strip()
    if not _ia_chat_pede_geracao_imagem(mensagem):
        return None

    if not _ia_provedor_ativo("openai"):
        return "A geracao de imagem esta desativada pelo administrador nas configuracoes de IA."

    api_key = _obter_openai_api_key()
    if not api_key:
        return "Nao consegui gerar a imagem porque a chave da OpenAI nao esta configurada."

    prompt, meta = _ia_montar_prompt_geracao_imagem_sku(client_id, mensagem)
    if len(prompt) > 4000:
        prompt = prompt[:4000]

    model = (os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1").strip()
    try:
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "prompt": prompt,
                "size": "1024x1024",
                "quality": "medium",
                "n": 1,
            },
            timeout=180,
        )
    except requests.RequestException as exc:
        logger.warning(f"[IA IMG] Falha de conexao ao gerar imagem: {exc}")
        return "Nao consegui gerar a imagem agora por uma falha de conexao com a OpenAI."

    if not resp.ok:
        detail = "falha desconhecida"
        try:
            detail = str((resp.json().get("error") or {}).get("message") or detail)
        except Exception:
            pass
        logger.warning(f"[IA IMG] OpenAI imagens HTTP {resp.status_code}: {detail}")
        return f"Nao consegui gerar a imagem agora. Retorno da OpenAI: {detail}"

    b64_img = _extrair_b64_openai_image_response(resp.json())
    if not b64_img:
        logger.warning("[IA IMG] OpenAI retornou sem imagem em base64.")
        return "A OpenAI respondeu, mas nao retornou uma imagem utilizavel."

    try:
        image_bytes = base64.b64decode(b64_img)
        _, url = _ia_salvar_imagem_gerada(client_id, image_bytes, meta.get("sku") or "")
    except Exception as exc:
        logger.warning(f"[IA IMG] Falha ao salvar imagem gerada: {exc}")
        return "A imagem foi gerada, mas nao consegui salvar o arquivo no sistema."

    sku_txt = f" do SKU {meta.get('sku')}" if meta.get("sku") else ""
    produto_txt = f"\nProduto usado como referencia: {meta.get('produto')}" if meta.get("produto") else ""
    return (
        f"Pronto, gerei uma nova imagem{sku_txt} com base no seu pedido.\n\n"
        f"![Imagem gerada{sku_txt}]({url})"
        f"{produto_txt}"
    )

PEER_EXPORTS = ['_ia_lojas_bling_conectadas', '_ia_lojas_com_integracao', '_ia_obter_cfg_bling', '_ia_tool_get_integrations_status', '_ia_ml_precisa_descricao', '_ia_ml_item_resumo', '_ia_ml_obter_descricao_item', '_ia_ml_listar_anuncios', '_ia_ml_resolve_exact_order', '_ia_tool_get_mercado_livre_listing', '_ia_tool_get_mercado_livre_orders', '_ia_tool_get_mercado_livre_returns', '_ia_bling_valor_tributario', '_ia_buscar_imagem_ml_sku', '_ia_montar_prompt_geracao_imagem_sku', '_ia_salvar_imagem_gerada', '_ia_gerar_imagem_sku_resposta']
__all__ = PEER_EXPORTS + ["configure_ia_tools_marketplaces_runtime"]

configure_ia_tools_marketplaces_runtime()
