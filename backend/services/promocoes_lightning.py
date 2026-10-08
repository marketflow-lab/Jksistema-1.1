"""Identidade e preservacao das janelas de ofertas relampago."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

LIGHTNING_OFFERS_KEY = "_jk_lightning_offers"


def _text(raw: dict, names: tuple[str, ...]) -> str:
    containers = [raw]
    for name in ("offer", "promotion", "campaign"):
        value = raw.get(name)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for name in names:
            value = container.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def lightning_offer_window(raw: dict) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return "", ""
    return (
        _text(raw, ("start_date", "startDate", "date_start", "begin_date")),
        _text(raw, ("finish_date", "finishDate", "end_date", "endDate", "date_end")),
    )


def _datetime(value: str) -> datetime | None:
    if not value or len(value) < 16 or value[10] not in {"T", " "}:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except (ValueError, TypeError):
        return None


def lightning_window_valid(raw: dict) -> bool:
    start, finish = (_datetime(value) for value in lightning_offer_window(raw))
    if start is None or finish is None:
        return False
    try:
        return start < finish
    except TypeError:
        # Nao inferir um fuso para reconciliar datas conscientes e locais.
        return False


def _normalized_datetime(value: str) -> str:
    parsed = _datetime(value)
    if parsed is None:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat()


def lightning_offer_key(raw: dict, *, campaign_id: str = "", item_id: str = "") -> str:
    raw = raw if isinstance(raw, dict) else {}
    campaign = str(campaign_id or _text(raw, (
        "promotion_id", "promotionId", "campaign_id", "campaignId", "deal_id", "dealId",
        "_jk_campaign_id_consultado",
    ))).strip().upper()
    item = str(item_id or _text(raw, (
        "item_id", "itemId", "item_id_to", "_jk_item_id_consultado",
    ))).strip().upper()
    raw_id = str(raw.get("id") or "").strip().upper()
    if not item and raw_id.startswith("MLB"):
        item = raw_id
    if not campaign and raw_id.startswith("LGH-"):
        campaign = raw_id
    offer = _text(raw, (
        "offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId",
    )).upper()
    start, finish = lightning_offer_window(raw)
    identity = [campaign, item, offer, _normalized_datetime(start), _normalized_datetime(finish)]
    if not offer and not start and not finish:
        # Sem identidade da janela, conservar registros distintos sem inventar datas.
        identity.append({k: v for k, v in raw.items() if not str(k).startswith("_jk_")})
    payload = json.dumps(identity, sort_keys=True, ensure_ascii=True, default=str)
    return "lightning:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lightning_offers(raw: dict) -> list[dict]:
    if not isinstance(raw, dict) or not raw:
        return []
    values = raw.get(LIGHTNING_OFFERS_KEY)
    if not isinstance(values, list):
        values = [raw]
    result = []
    for value in values:
        if isinstance(value, dict) and value:
            individual = copy.deepcopy(value)
            individual.pop(LIGHTNING_OFFERS_KEY, None)
            result.append(individual)
    return result


def _window_identity(raw, campaign_id, item_id):
    if not lightning_window_valid(raw):
        return ""
    without_reference = copy.deepcopy(raw)
    for container in [without_reference] + [
        without_reference[name] for name in ("offer", "promotion", "campaign")
        if isinstance(without_reference.get(name), dict)
    ]:
        for name in ("offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId"):
            container.pop(name, None)
    return lightning_offer_key(without_reference, campaign_id=campaign_id, item_id=item_id)


def merge_lightning_raw(
    current: dict,
    incoming: dict,
    *,
    campaign_id: str = "",
    item_id: str = "",
    merge_same_offer: Callable[[dict, dict], dict] | None = None,
) -> dict:
    records = lightning_offers(current) + lightning_offers(incoming)
    references_by_window = {}
    for raw in records:
        reference = _text(raw, ("offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId"))
        window = _window_identity(raw, campaign_id, item_id)
        if reference and window:
            key = lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id)
            references_by_window.setdefault(window, {})[key] = reference
    by_identity = {}
    explicit_by_identity = {}
    for raw in records:
        key = lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id)
        reference = _text(raw, ("offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId"))
        known = references_by_window.get(_window_identity(raw, campaign_id, item_id), {})
        # An omitted reference may alias one confirmed offer in the same window.
        # Different explicit references remain independent even at the same time.
        explicit = bool(reference)
        if not reference and len(known) == 1:
            key, reference = next(iter(known.items()))
            raw = dict(raw, offer_id=reference)
        if key in by_identity:
            previous = by_identity[key]
            if merge_same_offer is not None:
                by_identity[key] = merge_same_offer(previous, raw)
            if explicit and not explicit_by_identity[key]:
                by_identity[key] = {**by_identity[key], **raw}
            explicit_by_identity[key] = explicit_by_identity[key] or explicit
        else:
            by_identity[key] = raw
            explicit_by_identity[key] = explicit
    if not by_identity:
        return {}
    representative_current = copy.deepcopy(current) if isinstance(current, dict) else {}
    representative_incoming = copy.deepcopy(incoming) if isinstance(incoming, dict) else {}
    representative_current.pop(LIGHTNING_OFFERS_KEY, None)
    representative_incoming.pop(LIGHTNING_OFFERS_KEY, None)
    if merge_same_offer is not None:
        representative = merge_same_offer(representative_current, representative_incoming)
    else:
        representative = representative_current or representative_incoming
    result = dict(representative or next(iter(by_identity.values())))
    result[LIGHTNING_OFFERS_KEY] = list(by_identity.values())
    return result
