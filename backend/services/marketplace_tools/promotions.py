"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import client as _client

logger = logging.getLogger(__name__)

def query(
    client_id: str,
    mensagem: str,
    loja: Optional[str] = None,
    status: Optional[str] = None,
    promotion_id: Optional[str] = None,
    incluir_contagens: bool = False,
    limite: int = 20,
    force_refresh: bool = False,
    query_deadline: Optional[float] = None,
) -> dict:
    del mensagem, force_refresh
    function_name = "get_mercado_livre_promotions"
    limit_value = _client._ia_ml_int(limite, 20, 1, 50)
    status_value = str(status or "").strip().lower()
    promotion_value = str(promotion_id or "").strip()
    arguments = {"loja": str(loja or "").strip(), "status": status_value, "promotion_id": promotion_value, "include_counts": bool(incluir_contagens), "limit": limit_value}
    nome_loja, failure = _client.resolve_store(client_id, loja)
    if not nome_loja:
        return _client._ia_ml_store_failure(function_name, arguments, failure)
    result = _client._ia_ml_base_result()
    result.update({"store": nome_loja, "campaigns": [], "coverage_complete": False, "zero_is_authoritative": False})
    deadline = time.monotonic() + 45
    if query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    try:
        cfg = _runtime.ml_config(client_id, nome_loja)
        seller_id = str((cfg or {}).get("user_id") or "").strip()
        if not seller_id:
            result.update({"error": "seller_id_missing", "message": "A loja nao possui seller id confirmado."})
            return {"function": function_name, "arguments": arguments, "result": result}
        resp, cfg = _client.request_get(
            client_id, nome_loja, cfg,
            f"{_client.ML_IA_API_BASE}/seller-promotions/users/{seller_id}",
            params={"app_version": "v2"}, timeout=18, deadline=deadline,
        )
        result["sources"].append({"provider": "mercado_livre", "resource": "seller-promotions/users/{seller_id}", "method": "GET", "store": nome_loja})
        if int(getattr(resp, "status_code", 0) or 0) != 200:
            code, message, reconnect = _client._ia_ml_http_failure(resp, "Erro ao consultar promocoes")
            result.update({"error": code, "message": message, "reconnect_required": reconnect})
            return {"function": function_name, "arguments": arguments, "result": result}
        payload = resp.json() or {}
        raw_campaigns = payload if isinstance(payload, list) else (payload.get("results") or payload.get("campaigns") or payload.get("promotions") or [])
        filtered = []
        for campaign in raw_campaigns:
            if not isinstance(campaign, dict):
                continue
            campaign_id = str(campaign.get("id") or campaign.get("promotion_id") or "").strip()
            campaign_status = str(campaign.get("status") or campaign.get("state") or "").strip()
            if not campaign_id or (promotion_value and campaign_id != promotion_value) or (status_value and campaign_status.lower() != status_value):
                continue
            benefits = campaign.get("benefits") if isinstance(campaign.get("benefits"), dict) else {}
            filtered.append({
                "id": campaign_id,
                "name": str(campaign.get("name") or campaign.get("title") or campaign_id).strip()[:200],
                "type": str(campaign.get("type") or campaign.get("promotion_type") or "").strip(),
                "status": campaign_status,
                "start_date": campaign.get("start_date") or campaign.get("date_start"),
                "finish_date": campaign.get("finish_date") or campaign.get("end_date"),
                "benefits": {key: benefits.get(key) for key in ("type", "meli_percent", "seller_percent") if benefits.get(key) is not None},
                "counts": {},
            })
        total = len(filtered)
        campaigns = filtered[:limit_value]
        counts_complete = True
        if incluir_contagens:
            for campaign in campaigns[:5]:
                for label, api_status in (("active", "started"), ("eligible", "candidate")):
                    count_resp, cfg = _client.request_get(
                        client_id, nome_loja, cfg,
                        f"{_client.ML_IA_API_BASE}/seller-promotions/promotions/{campaign['id']}/items",
                        params={"app_version": "v2", "promotion_type": campaign["type"], "status": api_status, "limit": 1, "offset": 0},
                        timeout=15, deadline=deadline,
                    )
                    result["sources"].append({"provider": "mercado_livre", "resource": "seller-promotions/promotions/{promotion_id}/items", "method": "GET", "store": nome_loja})
                    if int(getattr(count_resp, "status_code", 0) or 0) != 200:
                        counts_complete = False
                        campaign["counts"][label] = None
                        continue
                    count_payload = count_resp.json() or {}
                    paging = count_payload.get("paging") if isinstance(count_payload.get("paging"), dict) else {}
                    campaign["counts"][label] = _client._ia_ml_int(paging.get("total"), len(count_payload.get("results") or []), 0, 100_000_000)
            if len(campaigns) > 5:
                counts_complete = False
                result["warnings"].append("Contagens detalhadas foram limitadas as primeiras 5 campanhas.")
        coverage_complete = total <= limit_value and counts_complete
        result.update({
            "found": bool(campaigns), "campaigns": campaigns, "total": total,
            "coverage_complete": coverage_complete, "truncated": total > limit_value,
            "zero_is_authoritative": coverage_complete and total == 0,
            "paging": {"offset": 0, "limit": limit_value, "returned": len(campaigns), "total": total, "has_more": total > limit_value},
        })
    except requests.exceptions.Timeout:
        result.update({"error": "timeout", "message": "A consulta de promocoes excedeu o limite seguro."})
    except HTTPException as exc:
        reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
        result.update({"error": "reconnect_required" if reconnect else "integration_error", "message": str(getattr(exc, "detail", exc))[:240], "reconnect_required": reconnect})
    return {"function": function_name, "arguments": arguments, "result": result}
