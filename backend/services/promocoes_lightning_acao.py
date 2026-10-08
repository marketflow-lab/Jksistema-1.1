"""Offer-scoped LIGHTNING analysis and participation metadata."""
from __future__ import annotations

import math
import re
from datetime import datetime
from backend.services.promocoes_lightning import (
    LIGHTNING_OFFERS_KEY, merge_lightning_raw,
    lightning_offer_key, lightning_offers, lightning_offer_window, lightning_window_valid,
)

LIGHTNING_ROW_FIELDS = (
    "lightning_offer_key", "lightning_start_date", "lightning_finish_date",
    "lightning_stock_min", "lightning_stock_max", "lightning_stock",
    "lightning_offer_count", "lightning_action_blocked", "lightning_action_reason",
    "lightning_schedule_pending",
)


def lightning_integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() else None


def lightning_stock_bounds(raw):
    raw = raw if isinstance(raw, dict) else {}
    nested = raw.get("stock") if isinstance(raw.get("stock"), dict) else {}
    def confirmed(keys, nested_key):
        values = [raw[key] for key in keys if key in raw]
        if nested_key in nested:
            values.append(nested[nested_key])
        numbers = [lightning_integer(value) for value in values]
        if not numbers or any(value is None for value in numbers) or len(set(numbers)) != 1:
            return None
        return numbers[0]
    return confirmed(("min_stock", "minimum_stock", "min_stock_quantity"), "min"), confirmed((
        "max_stock", "maximum_stock", "max_stock_quantity",
    ), "max")


def lightning_has_schedule_data(raw):
    """Nontext and partial date values are evidence of an invalid schedule."""
    if not isinstance(raw, dict):
        return False
    date_fields = ("start_date", "startDate", "date_start", "begin_date",
                   "finish_date", "finishDate", "end_date", "endDate", "date_end")
    containers = [raw] + [raw[key] for key in ("offer", "promotion", "campaign", "deal")
                          if isinstance(raw.get(key), dict)]
    return any(value is not None and not (isinstance(value, str) and not value.strip())
               for container in containers for key in date_fields
               if key in container for value in (container[key],))


def lightning_reference(raw):
    containers = [raw] + [raw[key] for key in ("offer", "promotion", "campaign", "deal")
                          if isinstance(raw.get(key), dict)] if isinstance(raw, dict) else []
    references = {str(container[key] or "").strip().upper()
                  for container in containers for key in (
                      "offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId")
                  if container.get(key)}
    return next(iter(references)) if len(references) == 1 else ""


def lightning_schedule_pending(raw, campaign_id, item_id):
    """Only a proven, priced candidate may await scheduling after its inclusion."""
    if not isinstance(raw, dict) or lightning_has_schedule_data(raw):
        return False
    expected = {"_jk_campaign_id_consultado": campaign_id,
                "_jk_item_id_consultado": item_id,
                "_jk_promotion_type_consultado": "LIGHTNING"}
    if raw.get("_jk_contexto_promocao_confirmado") is not True or any(
        str(raw.get(key) or "").strip().upper() != str(value or "").strip().upper()
        for key, value in expected.items()
    ):
        return False
    containers = [raw] + [raw[key] for key in ("offer", "promotion", "campaign", "deal")
                          if isinstance(raw.get(key), dict)]
    for container in containers:
        for keys, wanted in (
            (("promotion_id", "promotionId", "campaign_id", "campaignId", "deal_id", "dealId"), campaign_id),
            (("item_id", "itemId", "item_id_to"), item_id),
            (("type", "promotion_type", "promotionType", "campaign_type", "campaignType"), "LIGHTNING"),
        ):
            if any(str(container[key]).strip().upper() != str(wanted).strip().upper()
                   for key in keys if container.get(key)):
                return False
    raw_id = str(raw.get("id") or "").strip().upper()
    if (raw_id.startswith("LGH-") and raw_id != str(campaign_id).upper()) or (
        raw_id.startswith("MLB") and raw_id != str(item_id).upper()
    ):
        return False
    status = raw.get("status") or raw.get("state") or raw.get("status_item") or raw.get("_jk_status_item_consultado")
    if isinstance(status, dict):
        status = status.get("id") or status.get("name") or status.get("status")
    if str(status or "").strip().lower() not in {"candidate", "eligible"}:
        return False
    reference = lightning_reference(raw)
    matched = re.fullmatch(r"CANDIDATE-(MLB\d+)-.+", reference)
    if not matched or matched.group(1) != str(item_id).strip().upper():
        return False
    raw_price = raw.get("price") if "price" in raw else raw.get("deal_price")
    if isinstance(raw_price, bool):
        return False
    try:
        price = float(raw_price)
    except (TypeError, ValueError, OverflowError):
        return False
    minimum, maximum = lightning_stock_bounds(raw)
    return (math.isfinite(price) and price > 0 and minimum is not None and maximum is not None
            and minimum > 0 and maximum >= minimum)


def lightning_same_window(left, right):
    if not lightning_window_valid(left) or not lightning_window_valid(right):
        return False
    try:
        return all(datetime.fromisoformat(a.replace("Z", "+00:00")) ==
                   datetime.fromisoformat(b.replace("Z", "+00:00"))
                   for a, b in zip(lightning_offer_window(left), lightning_offer_window(right)))
    except (TypeError, ValueError):
        return False


def lightning_stock_error(raw, stock):
    value = lightning_integer(stock)
    minimum, maximum = lightning_stock_bounds(raw)
    if value is None or value <= 0:
        return "Informe uma quantidade inteira positiva de estoque reservado."
    if minimum is None or maximum is None or minimum <= 0 or maximum < minimum:
        return "Os limites de estoque desta oferta nao foram confirmados. Execute uma nova analise."
    if value < minimum or value > maximum:
        return f"Estoque reservado deve estar entre {minimum} e {maximum} unidades."
    return ""


def expand_lightning_offers(item_ids, raw_by_item, promotion_type, campaign_id):
    """Reuse item details by MLB; preserve one analysis work item per offer."""
    result = []
    for item_id in dict.fromkeys(item_ids):
        raw = raw_by_item.get(item_id, {}) if isinstance(raw_by_item, dict) else {}
        if str(promotion_type or "").upper() != "LIGHTNING":
            result.append((item_id, item_id, raw))
            continue
        offers = lightning_offers(raw) or [{}]
        unique = {}
        for offer in offers:
            key = lightning_offer_key(offer, campaign_id=campaign_id, item_id=item_id)
            unique.setdefault(key, dict(offer))
        for key, offer in unique.items():
            offer["_jk_lightning_selected_offer"] = True
            offer["_jk_lightning_offer_count"] = len(unique)
            result.append((item_id, key, offer))
    return result


def lightning_row_fields(raw, promotion_type, campaign_id, item_id):
    if str(promotion_type or "").upper() != "LIGHTNING":
        return {}
    raw = raw if isinstance(raw, dict) else {}
    offers = lightning_offers(raw)
    count = int(raw.get("_jk_lightning_offer_count") or len(offers) or 1)
    start, finish = lightning_offer_window(raw)
    minimum, maximum = lightning_stock_bounds(raw)
    schedule_pending = lightning_schedule_pending(raw, campaign_id, item_id)
    reason = ""
    if not lightning_window_valid(raw) and not schedule_pending:
        reason = "Data e horario desta oferta nao foram confirmados. Execute uma nova analise."
    elif minimum is None or maximum is None or minimum <= 0 or maximum < minimum:
        reason = "Os limites de estoque desta oferta nao foram confirmados. Execute uma nova analise."
    fields = {
        "lightning_offer_key": lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id),
        "lightning_start_date": start, "lightning_finish_date": finish,
        "lightning_stock_min": minimum, "lightning_stock_max": maximum,
        "lightning_stock": minimum if minimum is not None and minimum > 0 and maximum is not None and maximum >= minimum else None,
        "lightning_offer_count": count,
        "lightning_schedule_pending": schedule_pending,
        "lightning_action_blocked": bool(reason), "lightning_action_reason": reason,
    }
    if reason:
        fields.update(action_tecnico_apto=False, action_impedimento_tecnico=reason)
    return fields


def lightning_scoped_item_offers(records, campaign_id, item_id, contextualize):
    """Bind GET /items/{MLB} provenance without inferring a missing campaign."""
    offers = {}
    for entry in records if isinstance(records, list) else []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or entry.get("promotion_type") or "").upper()
        raw_id = str(entry.get("id") or "").upper()
        if kind != "LIGHTNING" and raw_id != campaign_id.upper():
            continue
        campaign_ids = {str(entry.get(key) or "").upper() for key in (
            "promotion_id", "promotionId", "campaign_id", "campaignId", "deal_id", "dealId",
        ) if entry.get(key)}
        if raw_id.startswith("LGH-"):
            campaign_ids.add(raw_id)
        for field in ("promotion", "campaign", "deal"):
            nested = entry.get(field)
            if isinstance(nested, dict) and nested.get("id"):
                campaign_ids.add(str(nested["id"]).upper())
        if campaign_id.upper() not in campaign_ids or any(value != campaign_id.upper() for value in campaign_ids):
            continue
        entry = dict(entry)
        entry.setdefault("item_id", item_id)
        scoped = contextualize(
            entry, campaign_id=campaign_id, promotion_type_consultado="LIGHTNING", item_id=item_id,
        )
        if not scoped:
            continue
        for offer in lightning_offers(scoped):
            offers.setdefault(lightning_offer_key(offer, campaign_id=campaign_id, item_id=item_id), offer)
    combined = merge_lightning_raw({}, {LIGHTNING_OFFERS_KEY: list(offers.values())},
        campaign_id=campaign_id, item_id=item_id)
    return lightning_offers(combined)
