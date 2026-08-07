"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import client as _client
from . import items as _items
from . import listing_search as _listing_search

logger = logging.getLogger(__name__)

def query_visits(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    item_id: Optional[str] = None,
    dias: int = 30,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> dict:
    del force_refresh
    function_name = "get_mercado_livre_visits"
    ids = _items._ia_ml_normalizar_item_ids(item_id, mensagem)
    days = _client._ia_ml_int(dias, 30, 1, 150)
    arguments = {"loja": str(loja or "").strip(), "item_id": ids[0] if ids else "", "days": days}
    if not ids:
        result = _client._ia_ml_base_result()
        result.update({"error": "item_id_required", "message": "Informe um MLB exato para consultar visitas.", "results": [], "coverage_complete": False, "zero_is_authoritative": False})
        return {"function": function_name, "arguments": arguments, "result": result}
    nome_loja, failure = _client.resolve_store(client_id, loja)
    if not nome_loja:
        return _client._ia_ml_store_failure(function_name, arguments, failure)
    result = _client._ia_ml_base_result()
    result.update({"store": nome_loja, "item_id": ids[0], "results": [], "coverage_complete": False, "zero_is_authoritative": False})
    deadline = time.monotonic() + 25
    if query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    try:
        cfg = _runtime.ml_config(client_id, nome_loja)
        _item, cfg, ownership_failure = _listing_search.resolve_owned_item(client_id, nome_loja, cfg, ids[0], deadline=deadline)
        result["sources"].append({"provider": "mercado_livre", "resource": "items multiget ownership", "method": "GET", "store": nome_loja})
        if ownership_failure:
            result.update({"error": ownership_failure.get("code"), "message": ownership_failure.get("message")})
            return {"function": function_name, "arguments": arguments, "result": result}
        ending = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
        resp, cfg = _client.request_get(
            client_id,
            nome_loja,
            cfg,
            f"{_client.ML_IA_API_BASE}/items/{ids[0]}/visits/time_window",
            params={"last": days, "unit": "day", "ending": ending},
            timeout=15,
            deadline=deadline,
        )
        result["sources"].append({"provider": "mercado_livre", "resource": "items/{item_id}/visits/time_window", "method": "GET", "store": nome_loja})
        if int(getattr(resp, "status_code", 0) or 0) != 200:
            code, message, reconnect = _client._ia_ml_http_failure(resp, "Erro ao consultar visitas do anuncio")
            result.update({"error": code, "message": message, "reconnect_required": reconnect})
            return {"function": function_name, "arguments": arguments, "result": result}
        payload = resp.json() or {}
        points = []
        for entry in (payload.get("results") or [])[:160]:
            if not isinstance(entry, dict):
                continue
            try:
                total = max(0, int(float(entry.get("total") or 0)))
            except (TypeError, ValueError):
                total = 0
            points.append({"date": str(entry.get("date") or "")[:32], "total": total})
        try:
            total_visits = max(0, int(float(payload.get("total_visits"))))
        except (TypeError, ValueError):
            total_visits = sum(point["total"] for point in points)
        result.update({
            "found": True,
            "date_from": str(payload.get("date_from") or ""),
            "date_to": str(payload.get("date_to") or ""),
            "unit": str(payload.get("unit") or "day"),
            "total_visits": total_visits,
            "average": round(total_visits / len(points), 2) if points else 0,
            "record": max(points, key=lambda point: point["total"]) if points else None,
            "results": points,
            "coverage_complete": True,
            "zero_is_authoritative": total_visits == 0,
            "paging": {"offset": 0, "limit": len(points), "returned": len(points), "total": len(points), "has_more": False},
        })
    except requests.exceptions.Timeout:
        result.update({"error": "timeout", "message": "A consulta de visitas excedeu o limite seguro."})
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        result.update({"error": "reconnect_required" if reconnect else "integration_error", "message": str(getattr(exc, "detail", exc))[:240], "reconnect_required": reconnect})
    return {"function": function_name, "arguments": arguments, "result": result}
