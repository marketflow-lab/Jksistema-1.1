"""GET-only confirmation of a server-bound LIGHTNING submission."""
from __future__ import annotations

import copy
import math
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .promocoes_lightning import lightning_offer_window, lightning_window_valid
from .promocoes_lightning_acao import (
    lightning_has_schedule_data, lightning_reference, lightning_same_window,
)

CONTEXT_VERSION = 1
CONTEXT_KEY = "lightning_confirmation_contexts"
DEFAULT_TTL_SECONDS = 24 * 60 * 60
_MAX_READS = 4
_MAX_POLL_SECONDS = 12.0
_PARTICIPATING = frozenset(("pending", "programmed", "programada", "scheduled", "started", "active", "approved"))


class ConfirmationContextError(ValueError):
    """The server context cannot authorize a confirmation query."""


def _text(value):
    return str(value or "").strip()


def _cents(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            return None
        cents = int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return cents if cents > 0 else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def _timestamp(value):
    value = time.time() if value is None else value
    if isinstance(value, bool):
        raise ConfirmationContextError("Horario do contexto de confirmacao invalido.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfirmationContextError("Horario do contexto de confirmacao invalido.") from exc
    if not math.isfinite(number):
        raise ConfirmationContextError("Horario do contexto de confirmacao invalido.")
    return number


def _scope(client_id, loja, seller_id):
    values = tuple(_text(value) for value in (client_id, loja, seller_id))
    if not all(values):
        raise ConfirmationContextError("Loja ou conta da confirmacao nao confirmada.")
    return values


def _minimal_raw(raw):
    """Keep only what the existing window/preexisting predicates consume."""
    if not isinstance(raw, dict):
        raise ConfirmationContextError("Oferta da confirmacao nao confirmada.")
    result = {"ref_id": lightning_reference(raw)}
    start, finish = lightning_offer_window(raw)
    if start or finish:
        result.update(start_date=start, finish_date=finish)
    elif lightning_has_schedule_data(raw):
        # Preserve invalid schedule evidence; never turn it into an undated offer.
        result["start_date"] = "invalid"
    value = raw.get("price") if "price" in raw else raw.get("deal_price")
    if _cents(value) is not None:
        result["price"] = str(value)
    return result


def select_confirmed_lightning(service, records, cfg, campaign, item_id, raw, price):
    """Apply the existing strict reconciliation rules to one fresh snapshot."""
    expected = _cents(price)
    if expected is None or not isinstance(raw, dict):
        return None
    if lightning_has_schedule_data(raw) and not lightning_window_valid(raw):
        return None
    entries = service._promo_lightning_scoped_offers(records, campaign, item_id, cfg)
    conflicts = service._promo_lightning_conflicting_keys(records, campaign, item_id, cfg)
    previous = raw.get("_jk_lightning_preexisting_participations", [])
    matches = []
    for key, entry in entries.items():
        if key in conflicts or service._lightning_status(entry) not in service._LIGHTNING_PARTICIPATING_STATES:
            continue
        value = entry.get("price") if "price" in entry else entry.get("deal_price")
        if _cents(value) != expected or service._promo_lightning_preexisting(entry, previous):
            continue
        if lightning_window_valid(raw):
            if not lightning_same_window(entry, raw):
                continue
        elif lightning_has_schedule_data(entry) and not lightning_window_valid(entry):
            continue
        elif not lightning_window_valid(entry) and not re.fullmatch(
            rf"OFFER-{re.escape(item_id)}-.+", lightning_reference(entry)
        ):
            continue
        matches.append(entry)
    return matches[0] if len(matches) == 1 else None


def reconcile_lightning_participation(
    service, client_id, loja, cfg, *, campaign, item_id, raw, price,
    max_reads=3, timeout_seconds=6.0, delays=(0.0, 0.5, 1.5),
    clock=None, sleep=None,
):
    """Poll only the item GET, within a deadline, without OAuth refresh or sends."""
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    if isinstance(max_reads, bool) or not isinstance(max_reads, int) or not 1 <= max_reads <= _MAX_READS:
        raise ValueError("Numero de consultas de confirmacao invalido.")
    if isinstance(timeout_seconds, bool) or not math.isfinite(float(timeout_seconds)) or not 0 < float(timeout_seconds) <= _MAX_POLL_SECONDS:
        raise ValueError("Orcamento de confirmacao invalido.")
    delays = tuple(float(value) for value in delays)
    if not delays or any(not math.isfinite(value) or value < 0 for value in delays):
        raise ValueError("Intervalos de confirmacao invalidos.")
    start = clock()
    deadline = start + float(timeout_seconds)
    count, matched, exhausted = 0, None, False
    url = f"https://api.mercadolibre.com/seller-promotions/items/{item_id}"

    def get_only(client, store, current_cfg, method, request_url, **kwargs):
        remaining = deadline - clock()
        if (
            client != client_id or store != loja or method != "GET" or request_url != url
            or kwargs.get("params") != {"app_version": "v2"}
            or kwargs.get("json") is not None or kwargs.get("data") is not None
        ):
            raise ValueError("Consulta de confirmacao fora do escopo.")
        if remaining <= 0:
            raise TimeoutError("Orcamento de confirmacao esgotado.")
        return service._ml_api_request(
            client, store, current_cfg, "GET", request_url,
            params={"app_version": "v2"},
            timeout=min(float(kwargs.get("timeout") or remaining), remaining),
            retry_unauthorized=False,
        )

    for index in range(max_reads):
        remaining = deadline - clock()
        if remaining <= 0:
            exhausted = True
            break
        delay = delays[min(index, len(delays) - 1)]
        if delay >= remaining:
            exhausted = True
            break
        if delay:
            sleep(delay)
        if clock() >= deadline:
            exhausted = True
            break
        count += 1
        try:
            records, cfg = service._ml_obter_promocoes_item(
                client_id, loja, cfg, item_id, request_fn=get_only,
                strict=True, force_refresh=True,
            )
            if clock() >= deadline:
                exhausted = True
                break
            matched = select_confirmed_lightning(service, records, cfg, campaign, item_id, raw, price)
        except Exception:
            matched = None
        if matched is not None:
            break
    return matched, cfg, {
        "read_count": count,
        "elapsed_seconds": max(0.0, clock() - start),
        "budget_exhausted": exhausted or clock() >= deadline,
    }


def issue_confirmation_context(
    job_dict, client_id, loja, seller_id, analysis_job_id, client_ref,
    campaign, item_id, raw, price, *, now=None, ttl_seconds=DEFAULT_TTL_SECONDS,
):
    """Internal only: materialize trusted server data before a submission.

    The opaque confirmation job ID is the public handle. The caller owns the
    job lock and persistence; HTTP input must never supply raw or job_dict.
    """
    client, store, seller = _scope(client_id, loja, seller_id)
    now = _timestamp(now)
    if not isinstance(job_dict, dict):
        raise ConfirmationContextError("Job de confirmacao invalido.")
    for key, expected in (("client_id", client), ("loja", store), ("seller_id", seller)):
        if key in job_dict and _text(job_dict[key]) != expected:
            raise ConfirmationContextError("Job de confirmacao pertence a outra loja ou conta.")
    ref, campaign, item_id = _text(client_ref), _text(campaign), _text(item_id).upper()
    if (
        not re.fullmatch(r"[a-fA-F0-9]{32}", _text(analysis_job_id))
        or not ref or len(ref) > 200 or not campaign or not re.fullmatch(r"MLB\d+", item_id)
        or _cents(price) is None
    ):
        raise ConfirmationContextError("Identidade ou preco da confirmacao invalido.")
    ttl = _timestamp(ttl_seconds)
    if not 0 < ttl <= DEFAULT_TTL_SECONDS:
        raise ConfirmationContextError("Validade da confirmacao invalida.")
    if lightning_has_schedule_data(raw) and not lightning_window_valid(raw):
        raise ConfirmationContextError("Calendario da confirmacao invalido.")
    minimal = _minimal_raw(raw)
    minimal["_jk_lightning_preexisting_participations"] = [
        _minimal_raw(entry) for entry in raw.get("_jk_lightning_preexisting_participations", [])
        if isinstance(entry, dict)
    ]
    contexts = job_dict.setdefault(CONTEXT_KEY, {})
    if not isinstance(contexts, dict) or ref in contexts:
        raise ConfirmationContextError("Contexto da confirmacao ja registrado ou invalido.")
    record = {
        "version": CONTEXT_VERSION, "client_id": client, "loja": store, "seller_id": seller,
        "analysis_job_id": _text(analysis_job_id), "client_ref": ref,
        "campaign": campaign, "item_id": item_id, "price_cents": _cents(price),
        "raw": minimal, "issued_at": now, "expires_at": now + ttl,
        "submission_attempted": False, "outcome": "prepared", "confirmed": None,
    }
    job_dict.update(client_id=client, loja=store, seller_id=seller)
    contexts[ref] = record
    return copy.deepcopy(record)


def resolve_confirmation_context(job_dict, client_id, loja, seller_id, client_ref, *, now=None):
    client, store, seller = _scope(client_id, loja, seller_id)
    now = _timestamp(now)
    if not isinstance(job_dict, dict) or any(
        _text(job_dict.get(key)) != expected
        for key, expected in (("client_id", client), ("loja", store), ("seller_id", seller))
    ):
        raise ConfirmationContextError("Confirmacao nao encontrada para esta loja ou conta.")
    ref = _text(client_ref)
    contexts = job_dict.get(CONTEXT_KEY)
    record = contexts.get(ref) if isinstance(contexts, dict) else None
    if (
        not isinstance(record, dict) or record.get("version") != CONTEXT_VERSION
        or any(record.get(key) != expected for key, expected in (
            ("client_id", client), ("loja", store), ("seller_id", seller), ("client_ref", ref),
        ))
        or not isinstance(record.get("price_cents"), int) or isinstance(record["price_cents"], bool)
        or record["price_cents"] <= 0 or not isinstance(record.get("raw"), dict)
        or not re.fullmatch(r"[a-fA-F0-9]{32}", _text(record.get("analysis_job_id")))
        or not record.get("campaign") or not re.fullmatch(r"MLB\d+", _text(record.get("item_id")))
        or record.get("issued_at") is None or record.get("expires_at") is None
        or not 0 < _timestamp(record["expires_at"]) - _timestamp(record["issued_at"]) <= DEFAULT_TTL_SECONDS
        or now < _timestamp(record["issued_at"]) or now >= _timestamp(record["expires_at"])
    ):
        raise ConfirmationContextError("Contexto de confirmacao invalido ou expirado.")
    result = copy.deepcopy(record)
    result["pollable"] = (
        record.get("submission_attempted") is True
        and record.get("outcome") == "unknown" and not record.get("confirmed")
    )
    return result


def update_confirmation_context(
    job_dict, client_id, loja, seller_id, client_ref, *,
    submission_attempted=None, outcome=None, matched=None, now=None,
):
    record = resolve_confirmation_context(job_dict, client_id, loja, seller_id, client_ref, now=now)
    if record.get("confirmed"):
        return record
    if submission_attempted is not None:
        if not isinstance(submission_attempted, bool) or (record["submission_attempted"] and not submission_attempted):
            raise ConfirmationContextError("Estado de envio da confirmacao invalido.")
        record["submission_attempted"] = submission_attempted
    if outcome is not None:
        if outcome not in {"prepared", "unknown", "applied", "rejected", "blocked"}:
            raise ConfirmationContextError("Resultado da confirmacao invalido.")
        record["outcome"] = outcome
    if matched is not None:
        if not isinstance(matched, dict):
            raise ConfirmationContextError("Participacao da confirmacao nao validada.")
        status = (matched.get("status") or matched.get("status_item")
                  or matched.get("statusItem") or matched.get("_jk_status_item_consultado"))
        if isinstance(status, dict):
            status = status.get("id") or status.get("name") or status.get("status")
        status = _text(status).lower()
        value = matched.get("price") if "price" in matched else matched.get("deal_price")
        if (
            record["submission_attempted"] is not True or record["outcome"] not in {"unknown", "applied"}
            or status not in _PARTICIPATING or _cents(value) != record["price_cents"]
        ):
            raise ConfirmationContextError("Participacao da confirmacao nao validada.")
        start, finish = lightning_offer_window(matched)
        record.update(outcome="applied", confirmed={
            "status": status, "lightning_start_date": start, "lightning_finish_date": finish,
            "lightning_schedule_pending": not lightning_window_valid(matched),
        })
    record.pop("pollable", None)
    job_dict[CONTEXT_KEY][_text(client_ref)] = copy.deepcopy(record)
    return resolve_confirmation_context(job_dict, client_id, loja, seller_id, client_ref, now=now)


def recheck_confirmation_context(service, job_dict, client_id, loja, cfg, client_ref, *, now=None, **poll_options):
    """Resolve a server context; a confirmed replay performs zero network calls."""
    seller = _text((cfg or {}).get("user_id") or (cfg or {}).get("seller_id"))
    record = resolve_confirmation_context(job_dict, client_id, loja, seller, client_ref, now=now)
    if not record["pollable"]:
        return record, cfg, {"read_count": 0, "elapsed_seconds": 0.0, "budget_exhausted": False}
    matched, cfg, metadata = reconcile_lightning_participation(
        service, client_id, loja, cfg, campaign=record["campaign"], item_id=record["item_id"],
        raw=record["raw"], price=record["price_cents"] / 100, **poll_options,
    )
    record = update_confirmation_context(
        job_dict, client_id, loja, seller, client_ref, matched=matched, now=now,
    )
    return record, cfg, metadata
