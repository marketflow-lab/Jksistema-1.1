"""Incremental, read-only discovery of seller promotions missing from user lists.

Only public campaign metadata and progress live in this process-local cache.
Credentials remain in the local worker frame and never enter a job or response.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import re
import threading
import time
from uuid import uuid4
from urllib.parse import quote

from fastapi import HTTPException

from backend.services.mercadolivre_context import _ctx
from backend.services.mercadolivre_cache import _ml_cache_get, _ML_CACHE_TTL_CAMPANHA

_PAGE_SIZE = 100
_ROUND_ITEMS = 200
_ROUND_SECONDS = 55.0
_COMPLETE_TTL = 1800
_CHECKPOINT_TTL = 21600
_MAX_JOBS = 64
_MAX_OFFSET = 1000
_INACTIVE = {"finished", "ended", "closed", "paused", "cancelled", "canceled", "inactive", "expired"}
_LOCK = threading.RLock()
_SLOTS = threading.BoundedSemaphore(2)
_JOBS: dict[str, "_Job"] = {}
_CURRENT: dict[tuple[str, str, str, str], str] = {}
_MESSAGES = {
    "ROUND_LIMIT": "Busca complementar incompleta. Continue para consultar os anuncios restantes.",
    "BUSY": "A busca complementar aguarda uma vaga de processamento. Tente continuar em instantes.",
    "ITEMS_FAILED": "Alguns anuncios nao puderam ser consultados. A cobertura ainda esta incompleta.",
    "CAMPAIGNS_FAILED": "Algumas campanhas encontradas ainda nao tiveram seus detalhes confirmados.",
    "INVENTORY_FAILED": "Nao foi possivel continuar a pagina de anuncios ativos. A cobertura esta incompleta.",
    "INVENTORY_LIMIT": "A busca alternativa atingiu o limite de anuncios da API. A cobertura esta incompleta.",
    "INVENTORY_CHANGED": "A quantidade de anuncios mudou durante a coleta. Atualize a busca para verificar a cobertura.",
    "IDENTITY_CHANGED": "A identidade da loja mudou durante a busca. Atualize as promocoes.",
    "INVALID_ENTRY": "A API retornou uma oferta com identidade divergente. Essa oferta nao foi adicionada.",
    "INTERNAL_ERROR": "A busca complementar foi interrompida. Os resultados parciais foram preservados.",
}


@dataclass
class _Job:
    job_id: str
    scope: tuple[str, str, str, str]
    status: str = "queued"
    complete: bool = False
    resumable: bool = True
    revision: int = 0
    updated_at: float = field(default_factory=time.time)
    campaigns: dict = field(default_factory=dict)
    known_campaigns: set = field(default_factory=set)
    pending_items: deque = field(default_factory=deque)
    seen_items: set = field(default_factory=set)
    processed_items: set = field(default_factory=set)
    failed_items: set = field(default_factory=set)
    pending_campaigns: dict = field(default_factory=dict)
    failed_campaigns: dict = field(default_factory=dict)
    ignored_by_item: dict = field(default_factory=dict)
    warnings: dict = field(default_factory=dict)
    total: int | None = None
    pages: int = 0
    mode: str = "scan"
    scroll_id: str = ""
    offset: int = 0
    inventory_complete: bool = False
    scan_restarts: int = 0
    round_started: float = 0
    round_items: int = 0


class _Stopped(Exception):
    pass


class _Budget(Exception):
    pass


class _ReadError(Exception):
    def __init__(self, status=0, no_offers=False):
        self.status = status
        self.no_offers = no_offers


def _scope(client_id, loja):
    loja = str(loja or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja do Mercado Livre.")
    cfg = _ctx().obter_cfg_ml(client_id, loja)
    seller = str(cfg.get("user_id") or "").strip()
    site = str(cfg.get("site_id") or "").strip().upper()
    if not seller:
        raise HTTPException(status_code=400, detail="Identidade do vendedor nao encontrada para esta loja.")
    return (str(client_id), loja, seller, site), cfg


def _cfg_matches(scope, cfg):
    return (str(cfg.get("user_id") or "").strip() == scope[2]
            and str(cfg.get("site_id") or "").strip().upper() == scope[3])


def _touch(job):
    job.revision += 1
    job.updated_at = time.time()


def _warning(job, code):
    job.warnings[code] = {"code": code, "message": _MESSAGES[code]}
    _touch(job)


def _snapshot(job):
    return {
        "success": True, "job_id": job.job_id, "loja": job.scope[1],
        "seller_id": job.scope[2], "site_id": job.scope[3],
        "status": job.status, "complete": job.complete, "resumable": job.resumable,
        "campaigns": deepcopy(list(job.campaigns.values())),
        "progress": {"processed": len(job.processed_items), "total": job.total,
                     "failed": len(job.failed_items) + len(job.failed_campaigns), "pages": job.pages},
        "ignored_without_campaign_id": sum(job.ignored_by_item.values()),
        "warnings": deepcopy(list(job.warnings.values())),
        "revision": job.revision, "updated_at": job.updated_at,
    }


def _prune(*, make_room=False):
    now = time.time()
    for job_id, job in list(_JOBS.items()):
        if job.status not in {"queued", "running"} and now - job.updated_at > _CHECKPOINT_TTL:
            _JOBS.pop(job_id, None)
            if _CURRENT.get(job.scope) == job_id:
                _CURRENT.pop(job.scope, None)
    if make_room and len(_JOBS) >= _MAX_JOBS:
        expired = sorted((job for job in _JOBS.values() if job.status not in {"queued", "running"}), key=lambda j: j.updated_at)
        for job in expired[:len(_JOBS) - _MAX_JOBS + 1]:
            _JOBS.pop(job.job_id, None)
            if _CURRENT.get(job.scope) == job.job_id:
                _CURRENT.pop(job.scope, None)


def _launch_worker(job_id):
    threading.Thread(target=_run_job, args=(job_id,), daemon=True, name="ml-promotion-discovery").start()


def iniciar_descoberta_promocoes_mercado_livre(client_id, loja, force_refresh=False):
    scope, _cfg = _scope(client_id, loja)
    with _LOCK:
        _prune()
        job = _JOBS.get(_CURRENT.get(scope, ""))
        if job and not force_refresh:
            if job.status in {"queued", "running"}:
                return _snapshot(job)
            if job.complete and time.time() - job.updated_at < _COMPLETE_TTL:
                return _snapshot(job)
            if not job.resumable and not job.complete:
                return _snapshot(job)
        if not job or force_refresh or job.complete:
            _prune(make_room=True)
            if len(_JOBS) >= _MAX_JOBS:
                raise HTTPException(status_code=503, detail="Aguarde a conclusao das buscas complementares em andamento.")
            job = _Job(uuid4().hex, scope)
            _JOBS[job.job_id] = job
            _CURRENT[scope] = job.job_id
        if not _SLOTS.acquire(blocking=False):
            job.status = "partial"
            _warning(job, "BUSY")
            return _snapshot(job)
        # Retries are materialized once per round. Repeated start calls cannot
        # duplicate either a running worker or its pending queue.
        for item in sorted(job.failed_items):
            job.pending_items.append(item)
        job.failed_items.clear()
        job.pending_campaigns.update(job.failed_campaigns)
        job.failed_campaigns.clear()
        for code in ("ROUND_LIMIT", "BUSY", "ITEMS_FAILED", "CAMPAIGNS_FAILED", "INVENTORY_FAILED", "INTERNAL_ERROR"):
            job.warnings.pop(code, None)
        job.status = "queued"
        job.complete = False
        job.round_items = 0
        _touch(job)
        job_id = job.job_id
    try:
        _launch_worker(job_id)
    except Exception:
        _SLOTS.release()
        with _LOCK:
            job.status = "failed"
            _warning(job, "INTERNAL_ERROR")
    with _LOCK:
        return _snapshot(job)


def consultar_descoberta_promocoes_mercado_livre(client_id, loja, job_id):
    scope, _cfg = _scope(client_id, loja)
    with _LOCK:
        job = _JOBS.get(str(job_id))
        if (not job or job.scope != scope or _CURRENT.get(scope) != job.job_id
                or time.time() - job.updated_at > _CHECKPOINT_TTL):
            raise HTTPException(status_code=404, detail="Busca complementar nao encontrada para esta loja.")
        return _snapshot(job)


def _guard(job, *, check_budget=True):
    with _LOCK:
        if _CURRENT.get(job.scope) != job.job_id:
            raise _Stopped()
    scope, _cfg = _scope(job.scope[0], job.scope[1])
    if scope != job.scope:
        with _LOCK:
            job.resumable = False
            _warning(job, "IDENTITY_CHANGED")
        raise _Stopped()
    if check_budget and (job.round_items >= _ROUND_ITEMS or time.monotonic() - job.round_started >= _ROUND_SECONDS):
        raise _Budget()


def _read(job, cfg, path, params):
    _guard(job)
    ctx = _ctx()
    try:
        response, returned_cfg = ctx.ml_api_request_com_retry(
            job.scope[0], job.scope[1], cfg, "GET", "https://api.mercadolibre.com" + path,
            params=params, timeout=10, max_attempts=2,
        )
    except Exception:
        raise _ReadError() from None
    _guard(job, check_budget=False)
    if not _cfg_matches(job.scope, returned_cfg):
        with _LOCK:
            job.resumable = False
            _warning(job, "IDENTITY_CHANGED")
        raise _Stopped()
    if response.status_code != 200:
        no_offers = False
        if response.status_code == 404 and path.startswith("/seller-promotions/items/"):
            try:
                error = response.json()
                if isinstance(error, dict):
                    text = " ".join(str(error.get(key) or "") for key in ("message", "error", "detail"))
                    no_offers = "no offers found" in text.lower().replace("_", " ")
            except Exception:
                pass
        raise _ReadError(response.status_code, no_offers)
    try:
        payload = response.json()
    except Exception:
        raise _ReadError() from None
    return payload, returned_cfg


def _nonnegative_int(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
        return number if number >= 0 and float(value) == number else None
    except (TypeError, ValueError, OverflowError):
        return None


def _page(job, cfg):
    path = "/users/" + quote(job.scope[2], safe="") + "/items/search"
    if job.mode == "scan":
        params = {"search_type": "scan", "limit": _PAGE_SIZE, "status": "active"}
        if job.scroll_id:
            params["scroll_id"] = job.scroll_id
    else:
        params = {"offset": job.offset, "limit": min(_PAGE_SIZE, _MAX_OFFSET - job.offset), "status": "active"}
        if params["limit"] <= 0:
            with _LOCK:
                job.resumable = False
                _warning(job, "INVENTORY_LIMIT")
            return cfg, False
    try:
        data, cfg = _read(job, cfg, path, params)
    except _ReadError as exc:
        if job.mode == "scan" and exc.status in {400, 404}:
            with _LOCK:
                if job.scroll_id and job.scan_restarts < 1:
                    job.scroll_id = ""
                    job.scan_restarts += 1
                elif not job.scroll_id:
                    job.mode = "offset"
                    job.offset = 0
                else:
                    _warning(job, "INVENTORY_FAILED")
                    return cfg, False
            return cfg, True
        raise
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise _ReadError()
    batch = data["results"]
    paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
    total = _nonnegative_int(paging.get("total"))
    limit = _nonnegative_int(paging.get("limit")) or params["limit"]
    parsed = []
    for raw in batch:
        item = str(raw.get("id") or "") if isinstance(raw, dict) else str(raw)
        if not re.fullmatch(r"ML[A-Z]\d+", item):
            raise _ReadError()
        if job.scope[3] and not item.startswith(job.scope[3]):
            raise _ReadError()
        if isinstance(raw, dict) and not _owner_matches(raw, job.scope):
            raise _ReadError()
        parsed.append(item)
    with _LOCK:
        if total is not None:
            if job.total is not None and job.total != total:
                job.resumable = False
                _warning(job, "INVENTORY_CHANGED")
            job.total = total
        job.pages += 1
        added = sum(item not in job.seen_items for item in set(parsed))
        if batch and not added and job.mode == "scan" and job.scroll_id and str(data.get("scroll_id") or "").strip() == job.scroll_id:
            job.resumable = False
            raise _ReadError()
        for item in parsed:
            if item not in job.seen_items:
                job.seen_items.add(item)
                job.pending_items.append(item)
        if job.mode == "scan":
            cursor = str(data.get("scroll_id") or "").strip()
            if not batch:
                if total and len(job.seen_items) < total:
                    raise _ReadError()
                job.inventory_complete = True
            elif cursor:
                job.scroll_id = cursor
            elif total is not None and len(job.seen_items) >= total:
                job.inventory_complete = True
            elif len(batch) < limit and total is None:
                job.inventory_complete = True
            else:
                job.mode = "offset"
                job.offset = 0
        else:
            job.offset += params["limit"]
            if not batch or (total is not None and job.offset >= total) or (len(batch) < limit and total is None):
                if total is not None and len(job.seen_items) < total:
                    raise _ReadError()
                job.inventory_complete = True
            elif job.offset >= _MAX_OFFSET:
                job.resumable = False
                _warning(job, "INVENTORY_LIMIT")
        _touch(job)
    return cfg, True


def _base_campaign_keys(scope):
    # The caller loaded this authoritative seller list before requesting the
    # complement. Never trust campaign IDs supplied by a browser or another store.
    key = "promocoes:" + ":".join(scope)
    cached = _ml_cache_get(key, _ML_CACHE_TTL_CAMPANHA)
    if (not isinstance(cached, dict) or str(cached.get("seller_id") or "") != scope[2]
            or str(cached.get("site_id") or "").upper() != scope[3]):
        return set()
    return {key for campaign in cached.get("campaigns", []) if isinstance(campaign, dict)
            and (key := _campaign_key(campaign)) is not None}


def _owner_matches(raw, scope):
    if not isinstance(raw, dict):
        return False
    containers = [raw] + [raw[key] for key in ("item", "offer", "promotion", "campaign", "deal") if isinstance(raw.get(key), dict)]
    for container in containers:
        for key in ("seller_id", "user_id", "owner_id"):
            if container.get(key) is not None and str(container[key]).strip() != scope[2]:
                return False
        if scope[3] and container.get("site_id") and str(container["site_id"]).strip().upper() != scope[3]:
            return False
    return True


def _campaign_key(raw):
    ids = {str(raw[key]).strip() for key in ("promotion_id", "campaign_id", "id") if raw.get(key)}
    kinds = {str(raw[key]).strip().upper() for key in ("promotion_type", "campaign_type", "type") if raw.get(key)}
    if len(ids) != 1 or len(kinds) != 1:
        return None
    campaign, kind = next(iter(ids)), next(iter(kinds))
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", campaign)
            or re.fullmatch(r"ML[A-Z]\d+", campaign.upper())
            or campaign.upper().startswith(("OFFER-", "CANDIDATE-"))
            or not re.fullmatch(r"[A-Z][A-Z_]{0,63}", kind)):
        return None
    return campaign, kind


def _inspect_item(job, cfg, item):
    try:
        data, cfg = _read(job, cfg, "/seller-promotions/items/" + quote(item, safe=""), {"app_version": "v2"})
    except _ReadError as exc:
        if not exc.no_offers:
            raise
        data = []
    if isinstance(data, dict):
        data = data.get("results")
    if not isinstance(data, list):
        raise _ReadError()
    pending = {}
    ignored = 0
    invalid = False
    for raw in data:
        if not isinstance(raw, dict):
            raise _ReadError()
        explicit_item = str(raw.get("item_id") or raw.get("itemId") or "").strip()
        if not _owner_matches(raw, job.scope) or (explicit_item and explicit_item != item):
            invalid = True
            continue
        if not any(raw.get(key) for key in ("id", "promotion_id", "campaign_id")):
            ignored += 1
            continue
        key = _campaign_key(raw)
        if not key:
            invalid = True
            continue
        if str(raw.get("status") or raw.get("state") or "").lower() in _INACTIVE:
            continue
        pending[key] = item
    with _LOCK:
        for key, source_item in pending.items():
            if key not in job.campaigns and key not in job.failed_campaigns and key not in job.known_campaigns:
                job.pending_campaigns.setdefault(key, source_item)
        job.ignored_by_item[item] = ignored
        job.processed_items.add(item)
        job.failed_items.discard(item)
        job.round_items += 1
        if invalid:
            job.resumable = False
            _warning(job, "INVALID_ENTRY")
        _touch(job)
    return cfg


def _official_count(job, cfg, key, status):
    path = "/seller-promotions/promotions/" + quote(key[0], safe="") + "/items"
    try:
        data, cfg = _read(job, cfg, path, {"app_version": "v2", "promotion_type": key[1], "status": status, "limit": 1})
    except _ReadError:
        return None, cfg
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None, cfg
    for entry in data["results"]:
        if not isinstance(entry, dict) or not _owner_matches(entry, job.scope):
            return None, cfg
        for name in ("promotion_id", "campaign_id"):
            if entry.get(name) and str(entry[name]).strip() != key[0]:
                return None, cfg
        for name in ("promotion_type", "type"):
            if entry.get(name) and str(entry[name]).strip().upper() != key[1]:
                return None, cfg
    paging = data.get("paging")
    return _nonnegative_int(paging.get("total")) if isinstance(paging, dict) else None, cfg


def _resolve_campaign(job, cfg, key):
    data, cfg = _read(job, cfg, "/seller-promotions/promotions/" + quote(key[0], safe=""), {"app_version": "v2", "promotion_type": key[1]})
    if not isinstance(data, dict) or _campaign_key(data) != key or not _owner_matches(data, job.scope):
        raise _ReadError()
    status = str(data.get("status") or data.get("state") or "").strip()
    if status.lower() in _INACTIVE:
        return cfg, None
    active, cfg = _official_count(job, cfg, key, "started")
    eligible, cfg = _official_count(job, cfg, key, "candidate")
    group = "usuario" if key[1] in {"SELLER_CAMPAIGN", "SELLER_COUPON_CAMPAIGN"} else "mercado_livre"
    campaign = {
        "id": key[0], "name": str(data.get("name") or data.get("title") or key[0]).strip(),
        "title": str(data.get("title") or data.get("name") or key[0]).strip(),
        "status": status, "type": key[1], "promotion_type": key[1], "selection_group": group,
        "start_date": data.get("start_date") or data.get("date_start") or data.get("begin_date"),
        "finish_date": data.get("finish_date") or data.get("end_date") or data.get("date_end"),
        "active_count": active, "eligible_count": eligible, "source": "item_discovery",
    }
    return cfg, campaign


def _run_job(job_id):
    job = None
    try:
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job or _CURRENT.get(job.scope) != job_id:
                return
            job.status = "running"
            job.round_started = time.monotonic()
            _touch(job)
        _guard(job)
        _scope_now, cfg = _scope(job.scope[0], job.scope[1])
        known = _base_campaign_keys(job.scope)
        with _LOCK:
            job.known_campaigns.update(known)
            for key in known:
                job.pending_campaigns.pop(key, None)
                job.failed_campaigns.pop(key, None)
        while True:
            _guard(job)
            with _LOCK:
                candidate = next(iter(job.pending_campaigns), None)
                item = job.pending_items[0] if job.pending_items else None
                done = job.inventory_complete
            if candidate:
                try:
                    cfg, campaign = _resolve_campaign(job, cfg, candidate)
                except _ReadError:
                    with _LOCK:
                        job.failed_campaigns[candidate] = job.pending_campaigns[candidate]
                else:
                    with _LOCK:
                        if campaign is not None:
                            job.campaigns[candidate] = campaign
                        job.failed_campaigns.pop(candidate, None)
                        _touch(job)
                with _LOCK:
                    job.pending_campaigns.pop(candidate, None)
            elif item:
                try:
                    cfg = _inspect_item(job, cfg, item)
                except _ReadError:
                    with _LOCK:
                        job.failed_items.add(item)
                        job.round_items += 1
                        _touch(job)
                with _LOCK:
                    job.pending_items.popleft()
            elif done:
                break
            else:
                try:
                    cfg, keep_going = _page(job, cfg)
                    if not keep_going:
                        break
                except _ReadError:
                    with _LOCK:
                        _warning(job, "INVENTORY_FAILED")
                    break
        with _LOCK:
            if job.failed_items:
                _warning(job, "ITEMS_FAILED")
            if job.failed_campaigns:
                _warning(job, "CAMPAIGNS_FAILED")
            job.complete = bool(job.inventory_complete and not job.pending_items and not job.pending_campaigns
                                and not job.failed_items and not job.failed_campaigns and job.resumable)
            job.status = "completed" if job.complete else "partial"
            job.resumable = not job.complete and job.resumable
            _touch(job)
    except _Budget:
        with _LOCK:
            if job and _CURRENT.get(job.scope) == job_id:
                job.status = "partial"
                _warning(job, "ROUND_LIMIT")
    except _Stopped:
        with _LOCK:
            if job:
                job.status = "failed"
                job.complete = False
                _touch(job)
    except Exception:
        with _LOCK:
            if job:
                job.status = "failed"
                job.complete = False
                _warning(job, "INTERNAL_ERROR")
    finally:
        _SLOTS.release()
