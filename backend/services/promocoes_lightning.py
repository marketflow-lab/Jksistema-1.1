"""Identidade e preservacao das janelas de ofertas relampago."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Callable

LIGHTNING_OFFERS_KEY = "_jk_lightning_offers"
LIGHTNING_OBSERVATIONS_KEY = "_jk_lightning_observations"
_REFERENCE_FIELDS = ("offer_id", "offerId", "ref_id", "refId", "candidate_offer_id", "candidateOfferId")
_START_FIELDS = ("start_date", "startDate", "date_start", "begin_date")
_FINISH_FIELDS = ("finish_date", "finishDate", "end_date", "endDate", "date_end")


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
        _text(raw, _START_FIELDS),
        _text(raw, _FINISH_FIELDS),
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


def _reference_identity(raw: dict, campaign_id: str, item_id: str) -> tuple[str, str, str]:
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
    return campaign, item, _text(raw, _REFERENCE_FIELDS).upper()


def lightning_offer_key(raw: dict, *, campaign_id: str = "", item_id: str = "") -> str:
    raw = raw if isinstance(raw, dict) else {}
    campaign, item, offer = _reference_identity(raw, campaign_id, item_id)
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
            individual.pop(LIGHTNING_OBSERVATIONS_KEY, None)
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
        for name in _REFERENCE_FIELDS:
            container.pop(name, None)
    return lightning_offer_key(without_reference, campaign_id=campaign_id, item_id=item_id)


def _observations(raw):
    values = raw.get(LIGHTNING_OBSERVATIONS_KEY) if isinstance(raw, dict) else None
    if isinstance(values, list):
        return lightning_offers({LIGHTNING_OFFERS_KEY: values})
    return lightning_offers(raw)


def _compatible_incomplete_window(raw, confirmed):
    """Missing boundaries may be filled; conflicting or invalid values may not."""
    containers = [raw] + [raw[name] for name in ("offer", "promotion", "campaign")
                          if isinstance(raw.get(name), dict)]
    for fields, boundary in zip((_START_FIELDS, _FINISH_FIELDS), lightning_offer_window(confirmed)):
        for container in containers:
            for name in fields:
                value = container.get(name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                if not isinstance(value, str) or _datetime(value.strip()) is None:
                    return False
                if _normalized_datetime(value.strip()) != _normalized_datetime(boundary):
                    return False
    return True


def _status_priority(raw):
    status = raw.get("status") or raw.get("state") or raw.get("status_item") or raw.get("statusItem") or raw.get("_jk_status_item_consultado")
    if isinstance(status, dict):
        status = status.get("id") or status.get("name") or status.get("status")
    return {
        "eligible": 10, "candidate": 20,
        "pending": 30, "programmed": 30, "programada": 30, "scheduled": 30,
        "approved": 40, "started": 50, "active": 50, "paused": 60,
        "finished": 70, "ended": 70, "closed": 70, "expired": 70,
        "cancelled": 70, "canceled": 70, "inactive": 70,
    }.get(str(status or "").strip().lower(), 0)


def _merge_same_record(previous, fresh, callback, *, prefer_fresh=False):
    merged = callback(previous, fresh) if callback else dict(previous)
    merged = dict(merged or previous)
    if prefer_fresh:
        merged.update(fresh)
    # Keep complementary API fields without restoring rejected provenance.
    for raw in (previous, fresh):
        for name, value in raw.items():
            if name.startswith("_jk_"):
                continue
            existing = merged.get(name)
            if existing is None or (isinstance(existing, str) and not existing.strip()):
                merged[name] = copy.deepcopy(value)
            elif name == "stock" and isinstance(existing, dict) and isinstance(value, dict):
                merged[name] = {**copy.deepcopy(value), **existing}
    # A candidate or an older pending response must not reactivate this offer.
    preferred = max((previous, fresh), key=_status_priority)
    if _status_priority(previous) != _status_priority(fresh):
        for name in ("status", "state", "status_item", "statusItem", "_jk_status_item_consultado", "_jk_status_param_consultado"):
            merged.pop(name, None)
        for name in ("status", "state", "status_item", "statusItem", "_jk_status_item_consultado", "_jk_status_param_consultado"):
            if name in preferred:
                merged[name] = copy.deepcopy(preferred[name])
    if not lightning_window_valid(merged):
        confirmed = next((raw for raw in (previous, fresh) if lightning_window_valid(raw)), None)
        if confirmed is not None:
            merged["start_date"], merged["finish_date"] = lightning_offer_window(confirmed)
    return merged


def merge_lightning_raw(
    current: dict,
    incoming: dict,
    *,
    campaign_id: str = "",
    item_id: str = "",
    merge_same_offer: Callable[[dict, dict], dict] | None = None,
) -> dict:
    observations = {}
    for raw in _observations(current) + _observations(incoming):
        key = lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id)
        observations[key] = (_merge_same_record(observations[key], raw, merge_same_offer)
                             if key in observations else raw)
    records = list(observations.values())
    references_by_window = {}
    windows_by_reference = {}
    for raw in records:
        reference = _text(raw, _REFERENCE_FIELDS)
        window = _window_identity(raw, campaign_id, item_id)
        if reference and window:
            key = lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id)
            references_by_window.setdefault(window, {})[key] = reference
            scope = _reference_identity(raw, campaign_id, item_id)
            windows_by_reference.setdefault(scope, {})[key] = raw
    by_identity = {}
    explicit_by_identity = {}
    for raw in records:
        key = lightning_offer_key(raw, campaign_id=campaign_id, item_id=item_id)
        reference = _text(raw, _REFERENCE_FIELDS)
        known = references_by_window.get(_window_identity(raw, campaign_id, item_id), {})
        explicit = bool(reference)
        if reference and not lightning_window_valid(raw):
            matches = [(candidate_key, confirmed) for candidate_key, confirmed in
                       windows_by_reference.get(_reference_identity(raw, campaign_id, item_id), {}).items()
                       if _compatible_incomplete_window(raw, confirmed)]
            if len(matches) == 1:
                key, _confirmed = matches[0]
        # An omitted reference may alias one confirmed offer in the same window.
        # Different explicit references remain independent even at the same time.
        if not reference and len(known) == 1:
            key, reference = next(iter(known.items()))
            raw = dict(raw, offer_id=reference)
        if key in by_identity:
            by_identity[key] = _merge_same_record(
                by_identity[key], raw, merge_same_offer,
                prefer_fresh=explicit and not explicit_by_identity[key],
            )
            explicit_by_identity[key] = explicit_by_identity[key] or explicit
        else:
            by_identity[key] = raw
            explicit_by_identity[key] = explicit
    if not by_identity:
        return {}
    representative_current = copy.deepcopy(current) if isinstance(current, dict) else {}
    representative_incoming = copy.deepcopy(incoming) if isinstance(incoming, dict) else {}
    for representative in (representative_current, representative_incoming):
        representative.pop(LIGHTNING_OFFERS_KEY, None)
        representative.pop(LIGHTNING_OBSERVATIONS_KEY, None)
    if merge_same_offer is not None:
        representative = merge_same_offer(representative_current, representative_incoming)
    else:
        representative = representative_current or representative_incoming
    if len(by_identity) == 1:
        representative = next(iter(by_identity.values()))
    result = dict(representative or next(iter(by_identity.values())))
    result[LIGHTNING_OFFERS_KEY] = list(by_identity.values())
    # Keep pre-alias observations so later conflicting windows can be reconciled
    # conservatively, regardless of endpoint/page order. They never become rows.
    result[LIGHTNING_OBSERVATIONS_KEY] = records
    return result
