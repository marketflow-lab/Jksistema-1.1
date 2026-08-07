"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import quote_plus

from . import client as _client
from . import order_models as _order_models

logger = logging.getLogger(__name__)

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
        response, cfg = _client.request_get(
            client_id,
            store,
            cfg,
            f"{_client.ML_IA_API_BASE}/messages/packs/{quote_plus(str(pack_id))}/sellers/{quote_plus(str(seller_id))}",
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
            code, message, reconnect = _client._ia_ml_http_failure(response, "Nao foi possivel consultar as mensagens pos-venda.")
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
    shipment_id = _order_models._ia_ml_order_shipment_id(raw_order)
    if not shipment_id:
        return {
            "available": False,
            "complete": False,
            "shipment_id": "",
            "delivery_state": "unavailable",
            "delivery_state_label": "Indisponivel",
            "reason": "Pedido sem shipment_id.",
        }, cfg
    response, cfg = _client.request_get(
        client_id,
        store,
        cfg,
        f"{_client.ML_IA_API_BASE}/shipments/{shipment_id}",
        timeout=20,
        deadline=deadline,
    )
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code not in {200, 206}:
        code, message, reconnect = _client._ia_ml_http_failure(response, "Nao foi possivel consultar o envio.")
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
