"""Offer-scoped LIGHTNING analysis and participation metadata."""
from __future__ import annotations

import math
from datetime import datetime
from backend.services.promocoes_lightning import (
    LIGHTNING_OFFERS_KEY, merge_lightning_raw,
    lightning_offer_key, lightning_offers, lightning_offer_window, lightning_window_valid,
)

LIGHTNING_ROW_FIELDS = (
    "lightning_offer_key", "lightning_start_date", "lightning_finish_date",
    "lightning_stock_min", "lightning_stock_max", "lightning_stock",
    "lightning_offer_count", "lightning_action_blocked", "lightning_action_reason",
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
    reason = ""
    if not lightning_window_valid(raw):
        reason = "Data e horario desta oferta nao foram confirmados. Execute uma nova analise."
    elif minimum is None or maximum is None or minimum <= 0 or maximum < minimum:
        reason = "Os limites de estoque desta oferta nao foram confirmados. Execute uma nova analise."
    fields = {
        "lightning_offer_key": lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id),
        "lightning_start_date": start, "lightning_finish_date": finish,
        "lightning_stock_min": minimum, "lightning_stock_max": maximum,
        "lightning_stock": minimum if minimum is not None and minimum > 0 and maximum is not None and maximum >= minimum else None,
        "lightning_offer_count": count,
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
