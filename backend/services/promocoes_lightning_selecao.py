"""Campaign selections scoped to the exact invitation window returned by ML."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from backend.services.promocoes_lightning import lightning_offer_window


def _normalize_boundary(value):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value.strip()) < 16:
        raise ValueError("Data e horário da oferta relâmpago inválidos.")
    value = value.strip()
    if value[10] not in {"T", " "}:
        raise ValueError("Data e horário da oferta relâmpago inválidos.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Data e horário da oferta relâmpago inválidos.") from exc
    return parsed.isoformat()


def normalize_lightning_window(value):
    """None preserves legacy whole-campaign requests; explicit windows never widen."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"start_date", "finish_date", "undated"}:
        raise ValueError("Seleção de data da oferta relâmpago inválida.")
    if "undated" in value:
        if value != {"undated": True} or value["undated"] is not True:
            raise ValueError("Seleção sem data da oferta relâmpago inválida.")
        return {"undated": True}
    start, finish = (_normalize_boundary(value.get(key)) for key in ("start_date", "finish_date"))
    if not start and not finish:
        raise ValueError("Informe a janela selecionada ou a opção sem data.")
    if start and finish:
        try:
            valid = datetime.fromisoformat(start) < datetime.fromisoformat(finish)
        except TypeError:
            valid = False
        if not valid:
            raise ValueError("O término da oferta relâmpago deve ser posterior ao início, no mesmo fuso.")
    return {"start_date": start, "finish_date": finish}


def normalize_campaign_selection(campaign_id, promotion_type, meta):
    meta = meta if isinstance(meta, dict) else {}
    window = normalize_lightning_window(meta.get("lightning_window"))
    supplied = str(meta.get("selection_id") or "").strip()
    campaign = str(campaign_id or "").strip()
    if window is None:
        if supplied and supplied != campaign:
            raise ValueError("A seleção da campanha precisa informar a janela correspondente.")
        return {}
    if str(promotion_type or "").strip().upper() != "LIGHTNING":
        raise ValueError("A seleção por data é exclusiva de ofertas relâmpago.")
    identity = json.dumps([campaign, window], sort_keys=True, separators=(",", ":"))
    selection_id = campaign + "::window:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if supplied and supplied != selection_id:
        raise ValueError("A seleção não corresponde à campanha e à data informadas.")
    return {"selection_id": selection_id, "lightning_window": window}


def selection_identity(campaign_id, promotion_type, meta):
    campaign = str(campaign_id or "").strip()
    kind = str(promotion_type or "").strip().upper()
    selection = normalize_campaign_selection(campaign, kind, meta)
    return campaign, kind, selection.get("selection_id", campaign)


def lightning_selection_window(raw):
    start, finish = lightning_offer_window(raw)
    if not start and not finish:
        return {"undated": True}
    try:
        return normalize_lightning_window({"start_date": start, "finish_date": finish})
    except ValueError:
        # Keep malformed schedules visible in the unspecified bucket. Row-level
        # eligibility validation still blocks them; never invent a usable date.
        return {"undated": True}


def filter_lightning_selection(offer_pairs, window):
    selected = normalize_lightning_window(window)
    if selected is None:
        return offer_pairs
    return [pair for pair in offer_pairs if lightning_selection_window(pair[2]) == selected]


def lightning_selection_options(campaign_id, offer_pairs):
    groups = {}
    for item_id, offer_key, raw in offer_pairs:
        selection = normalize_campaign_selection(campaign_id, "LIGHTNING", {
            "lightning_window": lightning_selection_window(raw),
        })
        group = groups.setdefault(selection["selection_id"], {
            **selection, "active_count": 0, "eligible_count": 0, "total_count": 0,
        })
        group["total_count"] += 1
        status = raw.get("status") or raw.get("state") or raw.get("status_item") or raw.get("_jk_status_item_consultado")
        if isinstance(status, dict):
            status = status.get("id") or status.get("name") or status.get("status")
        status = str(status or "").strip().lower()
        if status in {"started", "pending", "paused", "active"}:
            group["active_count"] += 1
        elif status in {"candidate", "eligible"}:
            group["eligible_count"] += 1
    return sorted(groups.values(), key=lambda group: (
        group["lightning_window"].get("undated", False),
        group["lightning_window"].get("start_date", ""),
        group["lightning_window"].get("finish_date", ""),
    ))
