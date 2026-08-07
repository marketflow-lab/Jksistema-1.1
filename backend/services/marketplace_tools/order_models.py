"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import runtime as _runtime
from . import client as _client
from . import items as _items

logger = logging.getLogger(__name__)

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
            return _client._ia_ml_money((order or {}).get(key))
    found = False
    total = 0.0
    for payment in (order or {}).get("payments") or []:
        if not isinstance(payment, dict):
            continue
        for key in ("transaction_amount_refunded", "amount_refunded", "refund_amount"):
            if payment.get(key) is not None:
                found = True
                total += _client._ia_ml_float(payment.get(key))
                break
    if found:
        return round(total, 2)
    if str((order or {}).get("status") or "").strip().lower() == "partially_refunded":
        return None
    return 0.0

def _ia_ml_order_paid(order: dict, gross: float) -> float:
    if (order or {}).get("paid_amount") is not None:
        return _client._ia_ml_money((order or {}).get("paid_amount"))
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
            total += _client._ia_ml_float(value)
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

def _ia_ml_sanitize_order(
    order: dict,
    buyer_city: str = "",
    include_buyer_summary: bool = False,
    include_shipment_id: bool = False,
) -> dict:
    items = []
    items_gross = 0.0
    for entry in (order or {}).get("order_items") or []:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
        quantity = _client._ia_ml_float(entry.get("quantity"))
        unit_price = _client._ia_ml_float(entry.get("unit_price"))
        gross = round(quantity * unit_price, 2)
        items_gross += gross
        items.append({
            "item_id": str(item.get("id") or "").strip(),
            "sku": _items._ia_ml_sku_item(item),
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
    gross = _client._ia_ml_money((order or {}).get("total_amount") if (order or {}).get("total_amount") is not None else items_gross)
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
    if include_shipment_id:
        result["shipment_id"] = _ia_ml_order_shipment_id(order)
    if include_buyer_summary:
        buyer = (order or {}).get("buyer") if isinstance((order or {}).get("buyer"), dict) else {}
        result["buyer_name"] = _ia_ml_buyer_name(order)
        result["buyer_nickname"] = str(buyer.get("nickname") or "").strip()[:120]
        result["buyer_city"] = str(buyer_city or _ia_ml_order_embedded_city(order)).strip()[:120]
    return result

def _ia_ml_order_details_requested(mensagem: str, incluir_detalhes: bool, order_id: str) -> bool:
    if incluir_detalhes or order_id:
        return True
    normalized = _runtime.normalize_text(str(mensagem or "")).lower()
    return bool(re.search(r"\b(ultima|ultimo|detalhe|detalhes|comprador|cliente|cidade|destinatario)\b", normalized))

def _ia_ml_claims_period(
    mensagem: str,
    data_inicio: Any,
    data_fim: Any,
) -> tuple[Optional[datetime], Optional[datetime], dict[str, str]]:
    tz = ZoneInfo("America/Sao_Paulo")
    text = str(mensagem or "")
    normalized = _runtime.normalize_text(text).lower()
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
        "claimed_quantity": _client._ia_ml_float(claim.get("claimed_quantity")),
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
    normalized = _runtime.normalize_text(str(message or "")).lower()
    return bool(
        re.search(
            r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b",
            normalized,
        )
    )

def latest_deadline(query_deadline: Optional[float] = None) -> float:
    """Return the hard deadline for one exact latest-event lookup."""

    deadline = time.monotonic() + _client.ML_IA_LATEST_QUERY_TIMEOUT_SECONDS
    if query_deadline is not None:
        try:
            deadline = min(deadline, float(query_deadline))
        except (TypeError, ValueError):
            pass
    return deadline
