"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

import requests
from . import runtime as _runtime
from . import items as _items

logger = logging.getLogger(__name__)

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
    return _runtime.normalize_text(text)

def resolve_store(client_id: str, loja: Optional[str]) -> tuple[Optional[str], dict]:
    conectadas = []
    vistos = set()
    for nome in _runtime.ml_connected_stores(client_id) or []:
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

    normalized_ids = _items._ia_ml_normalizar_item_ids(*(item_ids or []))
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
    elif function_name == "get_mercado_livre_visits":
        result.update({"results": [], "coverage_complete": False, "zero_is_authoritative": False})
    elif function_name == "get_mercado_livre_promotions":
        result.update({"campaigns": [], "coverage_complete": False, "zero_is_authoritative": False})
    elif function_name == "get_mercado_livre_returns":
        result.update({"devolucoes": [], "returns": []})
    else:
        result.update({"orders": [], "by_sku": [], "totals": {}})
    return {"function": function_name, "arguments": arguments, "result": result}

def remaining_timeout(deadline: Optional[float], requested: int) -> int:
    if deadline is None:
        return max(1, int(requested or 1))
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise requests.exceptions.Timeout("A consulta completa do Mercado Livre excedeu 60 segundos.")
    return max(1, min(int(requested or 1), int(math.ceil(remaining))))

def request_get(
    client_id: str,
    loja: str,
    cfg: dict,
    url: str,
    *,
    params=None,
    headers=None,
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
            request_timeout = remaining_timeout(deadline, timeout)
            resp, cfg = _runtime.ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                url,
                params=params,
                headers=headers,
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
        detail = _runtime.ml_error_detail(resp, fallback)
    except Exception:
        detail = fallback
    return f"http_{status_code or 'error'}", str(detail or fallback)[:240], False
