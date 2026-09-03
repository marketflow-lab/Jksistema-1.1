"""Read-only Mercado Livre catalogue adapter for store-scoped Cadastro imports.

The adapter collects and normalizes review candidates only.  It never writes a
Cadastro row and it never manufactures a SKU.  The caller remains responsible
for presenting a preview and applying an explicitly approved merge.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import secrets
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable

import requests
from fastapi import HTTPException

from backend.services import cadastro_mercadolivre as cadastro_ml
from backend.services import integracoes
from backend.services import mercadolivre_legacy_core as mercadolivre
from backend.services.cadastro_catalogo_common import configuracao_catalogo_fingerprint
from backend.services.cadastro_common import _normalizar_sku_mes


ML_API_BASE = "https://api.mercadolibre.com"
ML_PAGE_SIZE = 100
ML_MULTIGET_BATCH_SIZE = 20
ML_CATALOG_MAX_ROWS = 25_000
# Compatibility alias for the pagination guards used by older local tests.
ML_MAX_ITEMS = ML_CATALOG_MAX_ROWS
ML_MAX_PAGES = (ML_MAX_ITEMS // ML_PAGE_SIZE) + 2
ML_DESCRIPTION_LIMIT = 50_000
ML_CATALOG_TIMEOUT_SECONDS = 8 * 60 * 60
ML_CATALOG_MIN_INTERVAL_SECONDS = 0.10
ML_USER_PRODUCT_STOCK_MIN_INTERVAL_SECONDS = 0.60
ML_CATALOG_MAX_ATTEMPTS = 4
ML_CATALOG_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ML_CATALOG_RETRY_AFTER_MAX_SECONDS = 30.0
ML_CATALOG_BACKOFF_BASE_SECONDS = 0.75

_ML_CATALOG_RATE_LOCK = threading.Lock()
_ML_CATALOG_NEXT_CALL: dict[str, float] = {}

_STATUS_RANK = {
    "active": 0,
    "paused": 1,
    "under_review": 2,
    "pending": 3,
    "inactive": 4,
    "closed": 5,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _detail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    result.update(extra)
    return result


def _catalog_limit_warning(subject: str) -> str:
    limit = f"{ML_CATALOG_MAX_ROWS:,}".replace(",", ".")
    return (
        f"O catalogo Mercado Livre excede o limite seguro de {limit} {subject}; "
        "a coleta foi interrompida."
    )


def _cancelled(cancel_event: Any) -> bool:
    checker = getattr(cancel_event, "is_set", None)
    if not callable(checker) and callable(cancel_event):
        checker = cancel_event
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


class _CatalogoMLCancelado(RuntimeError):
    pass


def _catalog_deadline_error() -> HTTPException:
    return HTTPException(
        status_code=504,
        detail=_detail(
            "catalog_collection_deadline_exceeded",
            "A coleta do Mercado Livre excedeu o prazo seguro. Gere uma nova previa.",
        ),
    )


def _check_catalog_limits(cancel_event: Any, deadline: float | None) -> None:
    if _cancelled(cancel_event):
        raise _CatalogoMLCancelado("Coleta do Mercado Livre cancelada.")
    if deadline is not None and time.monotonic() >= float(deadline):
        raise _catalog_deadline_error()


def _cancelable_sleep(seconds: float, cancel_event: Any, deadline: float | None) -> None:
    remaining = max(0.0, float(seconds or 0.0))
    _check_catalog_limits(cancel_event, deadline)
    while remaining > 0:
        if deadline is not None:
            available = float(deadline) - time.monotonic()
            if available <= 0:
                raise _catalog_deadline_error()
        else:
            available = remaining
        chunk = min(0.25, remaining, available)
        if chunk <= 0:
            raise _catalog_deadline_error()
        time.sleep(chunk)
        remaining -= chunk
        _check_catalog_limits(cancel_event, deadline)


def _catalog_rate_key(cfg: dict[str, Any]) -> str:
    app_id = str(
        (cfg or {}).get("app_id")
        or (cfg or {}).get("id")
        or (cfg or {}).get("client_id")
        or ""
    ).strip()
    if not app_id:
        return "ml-catalog-process-wide"
    return "ml-app:" + hashlib.sha256(app_id.encode("utf-8")).hexdigest()


def _wait_catalog_rate_turn(
    cfg: dict[str, Any],
    cancel_event: Any,
    deadline: float | None,
    *,
    bucket: str = "catalog",
) -> None:
    _check_catalog_limits(cancel_event, deadline)
    normalized_bucket = str(bucket or "catalog").strip() or "catalog"
    key = f"{normalized_bucket}:{_catalog_rate_key(cfg)}"
    now = time.monotonic()
    interval = max(
        0.0,
        float(
            ML_USER_PRODUCT_STOCK_MIN_INTERVAL_SECONDS
            if normalized_bucket == "user-product-stock"
            else ML_CATALOG_MIN_INTERVAL_SECONDS
        ),
    )
    with _ML_CATALOG_RATE_LOCK:
        target = max(now, _ML_CATALOG_NEXT_CALL.get(key, now))
        _ML_CATALOG_NEXT_CALL[key] = target + interval
        if len(_ML_CATALOG_NEXT_CALL) > 256:
            stale_before = now - 5 * 60
            for stale_key, next_call in list(_ML_CATALOG_NEXT_CALL.items()):
                if next_call < stale_before:
                    _ML_CATALOG_NEXT_CALL.pop(stale_key, None)
    _cancelable_sleep(target - now, cancel_event, deadline)


def _defer_catalog_rate_bucket(
    cfg: dict[str, Any],
    bucket: str,
    seconds: float,
) -> None:
    delay = max(0.0, float(seconds or 0.0))
    if delay <= 0:
        return
    normalized_bucket = str(bucket or "catalog").strip() or "catalog"
    key = f"{normalized_bucket}:{_catalog_rate_key(cfg)}"
    target = time.monotonic() + delay
    with _ML_CATALOG_RATE_LOCK:
        _ML_CATALOG_NEXT_CALL[key] = max(
            target,
            _ML_CATALOG_NEXT_CALL.get(key, target),
        )


def _request_rate_buckets(url: str) -> tuple[str, ...]:
    normalized_url = str(url or "").split("?", 1)[0].rstrip("/")
    if (
        normalized_url.startswith(f"{ML_API_BASE}/user-products/")
        and normalized_url.endswith("/stock")
    ):
        return ("user-product-stock", "catalog")
    return ("catalog",)


def _retry_after_seconds(response: Any) -> float:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    raw = getter("Retry-After") if callable(getter) else None
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text:
        return 0.0
    try:
        seconds = float(text)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(text)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0.0
    return max(0.0, min(float(seconds), ML_CATALOG_RETRY_AFTER_MAX_SECONDS))


def _retry_delay(response: Any, attempt: int) -> float:
    explicit = _retry_after_seconds(response)
    if explicit > 0:
        return explicit
    ceiling = min(
        ML_CATALOG_RETRY_AFTER_MAX_SECONDS,
        ML_CATALOG_BACKOFF_BASE_SECONDS * (2 ** max(0, int(attempt) - 1)),
    )
    return random.uniform(0.0, max(0.0, ceiling))


def _emit_progress(
    callback: Callable[[dict[str, Any]], Any] | None,
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if not callable(callback):
        return
    payload = {
        "source": "mercadolivre",
        "stage": str(stage or ""),
        "current": max(0, int(current or 0)),
        "total": max(0, int(total or 0)),
        "message": str(message or ""),
    }
    try:
        callback(payload)
    except Exception:
        # Progress must never decide whether catalogue evidence is complete.
        return


def _base_result(store: dict[str, str], seller_id: str) -> dict[str, Any]:
    return {
        "source": "mercadolivre",
        "store_id": store["store_id"],
        "store_name": store["store_name"],
        "seller_id": str(seller_id or ""),
        "coverage_complete": True,
        # General enrichment (description, category name and stock) may be
        # partial without making the already-confirmed SKU identities unsafe.
        # Keep that decision separate so additive Cadastro imports can proceed
        # for proven SKUs while incomplete optional data remains a warning.
        "sku_coverage_complete": True,
        "cancelled": False,
        "items": [],
        "skipped": [],
        "stats": {
            "pages_fetched": 0,
            "listed_ids": 0,
            "detailed_listings": 0,
            "catalog_items": 0,
            "skipped": 0,
            "missing_sku": 0,
            "ambiguous_sku": 0,
            "duplicate_sku": 0,
            "owner_mismatch": 0,
            "missing_details": 0,
            "incomplete_details": 0,
            "descriptions_absent": 0,
            "description_failures": 0,
            "variation_detail_failures": 0,
            "categories_requested": 0,
            "categories_complete": 0,
            "categories_failed": 0,
            "user_products_requested": 0,
            "user_products_complete": 0,
            "user_product_stocks_complete": 0,
            "user_product_stocks_absent": 0,
            "user_product_failures": 0,
            "user_product_sku_conflicts": 0,
            "user_product_identity_conflicts": 0,
            "warehouse_management": False,
        },
        "warnings": [],
    }


def _resolve_store(
    client_id: str,
    store_id: str,
) -> tuple[dict[str, str], dict[str, Any], str, dict[str, Any]]:
    requested = str(store_id or "").strip()
    if not requested:
        raise HTTPException(status_code=400, detail=_detail("store_id_required", "store_id e obrigatorio."))

    matches = [
        dict(store)
        for store in integracoes.carregar_lojas(client_id) or []
        if isinstance(store, dict) and str(store.get("store_id") or "").strip() == requested
    ]
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=_detail("store_not_found", "Loja nao encontrada para este cliente."),
        )
    if len(matches) != 1:
        raise HTTPException(
            status_code=409,
            detail=_detail("store_config_ambiguous", "A identidade da loja esta duplicada na configuracao."),
        )

    raw_store = matches[0]
    name = str(raw_store.get("nome") or requested).strip() or requested
    integrations = raw_store.get("integracoes") if isinstance(raw_store.get("integracoes"), dict) else {}
    persisted_cfg = dict(integrations.get("mercadolivre") or {})
    if not persisted_cfg:
        raise HTTPException(
            status_code=400,
            detail=_detail("ml_not_configured", "Mercado Livre nao configurado para a loja escolhida."),
        )
    cfg = dict(persisted_cfg)
    cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
    cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
    if not str(cfg.get("access_token") or "").strip():
        raise HTTPException(
            status_code=401,
            detail=_detail("ml_reconnect_required", "Token do Mercado Livre ausente. Reconecte esta loja."),
        )
    configured_seller = str(cfg.get("user_id") or "").strip()
    if not configured_seller:
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "seller_id_missing",
                "A conta Mercado Livre da loja nao possui seller configurado. Reconecte a integracao.",
            ),
        )

    # The context is runtime-only.  Refreshes, if required by the shared
    # transport, are persisted back to this exact opaque store identity.
    cfg = mercadolivre._ml_cfg_com_store_id_context(cfg, requested)
    return (
        {"store_id": requested, "store_name": name},
        cfg,
        configured_seller,
        persisted_cfg,
    )


def _fingerprint_value(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower()
    if "token" in normalized_key or "secret" in normalized_key:
        return {
            "sha256": hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()
        }
    if isinstance(value, dict):
        return {
            str(child_key): _fingerprint_value(child_value, key=str(child_key))
            for child_key, child_value in sorted(value.items(), key=lambda entry: str(entry[0]))
            if str(child_key) != "_store_id_context"
        }
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _configuration_fingerprint(
    store: dict[str, str],
    cfg: dict[str, Any],
    seller_id: str,
) -> str:
    safe_payload = {
        "store_id": str(store.get("store_id") or "").strip(),
        "store_name": str(store.get("store_name") or "").strip(),
        "seller_id": str(seller_id or "").strip(),
        "config": _fingerprint_value(dict(cfg or {})),
    }
    return hashlib.sha256(_canonical_json(safe_payload).encode("utf-8")).hexdigest()


def _account_fingerprint(
    store: dict[str, str],
    cfg: dict[str, Any],
    seller_id: str,
) -> str:
    payload = {
        "store_id": str(store.get("store_id") or "").strip(),
        "store_name": str(store.get("store_name") or "").strip(),
        "seller_id": str(seller_id or "").strip(),
        "site_id": str(cfg.get("site_id") or "").strip(),
        "app_id": str(cfg.get("app_id") or cfg.get("id") or cfg.get("client_id") or "").strip(),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _store_config_changed() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=_detail(
            "store_config_changed",
            "A configuracao Mercado Livre da loja mudou durante a coleta. Gere uma nova previa.",
        ),
    )


def _revalidate_configuration(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    seller_id: str,
    initial_configuration_fingerprint: str,
    initial_account_fingerprint: str,
) -> str:
    used_account_fingerprint = _account_fingerprint(store, cfg, seller_id)
    if not secrets.compare_digest(used_account_fingerprint, initial_account_fingerprint):
        raise _store_config_changed()
    used_configuration_fingerprint = _configuration_fingerprint(store, cfg, seller_id)
    transport_refreshed_configuration = not secrets.compare_digest(
        used_configuration_fingerprint, initial_configuration_fingerprint
    )

    try:
        with integracoes._LOJAS_CONFIG_LOCK:
            current_store, current_cfg, current_seller, current_persisted_cfg = _resolve_store(
                client_id, store["store_id"]
            )
            current_fingerprint = _configuration_fingerprint(
                current_store, current_cfg, current_seller
            )
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "store_config_changed":
            raise
        raise _store_config_changed() from exc

    expected_fingerprint = (
        used_configuration_fingerprint
        if transport_refreshed_configuration
        else initial_configuration_fingerprint
    )
    if not secrets.compare_digest(current_fingerprint, expected_fingerprint):
        raise _store_config_changed()
    return configuracao_catalogo_fingerprint(
        "mercadolivre",
        current_store["store_id"],
        current_store["store_name"],
        current_persisted_cfg,
    )


def _request_json(
    client_id: str,
    store_name: str,
    cfg: dict[str, Any],
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 25,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[Any, dict[str, Any], int, str]:
    last_response: Any = None
    last_error = "provider_error"
    attempts = max(1, min(int(ML_CATALOG_MAX_ATTEMPTS), 4))
    rate_buckets = _request_rate_buckets(url)
    for attempt in range(1, attempts + 1):
        _check_catalog_limits(cancel_event, deadline)
        for rate_bucket in rate_buckets:
            _wait_catalog_rate_turn(
                cfg,
                cancel_event,
                deadline,
                bucket=rate_bucket,
            )
        request_timeout = max(1, min(int(timeout or 25), 35))
        if deadline is not None:
            remaining = float(deadline) - time.monotonic()
            if remaining <= 0:
                raise _catalog_deadline_error()
            request_timeout = max(1, min(request_timeout, int(math.ceil(remaining))))
        try:
            response, cfg = mercadolivre._ml_api_request(
                client_id,
                store_name,
                cfg,
                "GET",
                url,
                params=params,
                timeout=request_timeout,
            )
            last_response = response
        except HTTPException as exc:
            return None, cfg, int(exc.status_code or 0), "integration_error"
        except requests.exceptions.Timeout:
            last_error = "timeout"
            response = None
        except requests.exceptions.ConnectionError:
            last_error = "connection_error"
            response = None
        except requests.RequestException:
            return None, cfg, 0, "connection_error"
        except Exception:
            return None, cfg, 0, "provider_error"

        status = int(getattr(response, "status_code", 0) or 0) if response is not None else 0
        retryable = response is None or status in ML_CATALOG_RETRYABLE_STATUS
        retry_delay = _retry_delay(response, attempt) if retryable else 0.0
        if status == 429 and retry_delay > 0:
            for rate_bucket in rate_buckets:
                _defer_catalog_rate_bucket(cfg, rate_bucket, retry_delay)
        if retryable and attempt < attempts:
            _cancelable_sleep(retry_delay, cancel_event, deadline)
            continue
        if response is None:
            return None, cfg, 0, last_error
        try:
            payload = response.json()
        except Exception:
            return None, cfg, status, "invalid_json"
        if status == 429:
            return payload, cfg, status, "rate_limited"
        if status in ML_CATALOG_RETRYABLE_STATUS:
            return payload, cfg, status, "provider_transient_error"
        return payload, cfg, status, ""

    if last_response is not None:
        return None, cfg, int(getattr(last_response, "status_code", 0) or 0), last_error
    return None, cfg, 0, last_error


def _fetch_category_names(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    category_ids: Iterable[Any],
    progress_callback: Callable[[dict[str, Any]], Any] | None,
    cancel_event: Any,
    deadline: float | None = None,
) -> tuple[dict[str, str], dict[str, Any], bool, list[str]]:
    requested = _distinct(str(value or "").strip() for value in category_ids)
    names: dict[str, str] = {}
    warnings: list[str] = []
    complete = True
    for index, category_id in enumerate(requested, start=1):
        if _cancelled(cancel_event):
            return names, cfg, False, [*warnings, "Coleta de categorias Mercado Livre cancelada."]
        _emit_progress(
            progress_callback,
            "categories",
            index,
            len(requested),
            "Lendo nomes das categorias Mercado Livre.",
        )
        payload, cfg, status, _error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            f"{ML_API_BASE}/categories/{category_id}",
            timeout=20,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        observed_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
        name = str(payload.get("name") or payload.get("nome") or "").strip() if isinstance(payload, dict) else ""
        if status != 200 or observed_id != category_id or not name:
            complete = False
            warnings.append(f"A categoria {category_id} nao foi detalhada com seguranca.")
            continue
        names[category_id] = name
    return names, cfg, complete, warnings


def _preflight_seller(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    configured_seller: str,
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[dict[str, Any], set[str], bool]:
    payload, cfg, status, error = _request_json(
        client_id,
        store["store_name"],
        cfg,
        f"{ML_API_BASE}/users/me",
        timeout=15,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    if status in {401, 403}:
        raise HTTPException(
            status_code=401,
            detail=_detail("ml_reconnect_required", "A autenticacao do Mercado Livre precisa ser refeita."),
        )
    if status != 200 or not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail=_detail(
                "ml_seller_preflight_failed",
                "Nao foi possivel confirmar a conta Mercado Livre da loja.",
                provider_status=status or None,
                reason=error or None,
            ),
        )
    observed = str(payload.get("id") or payload.get("user_id") or "").strip()
    if not observed or observed != str(configured_seller):
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "ml_seller_mismatch",
                "O token Mercado Livre nao pertence ao seller configurado para esta loja.",
            ),
        )
    raw_tags = payload.get("tags")
    tags = {
        str(value or "").strip()
        for value in raw_tags
        if str(value or "").strip()
    } if isinstance(raw_tags, list) else set()
    return cfg, tags, isinstance(raw_tags, list)


def _item_id(value: Any) -> str:
    return cadastro_ml._normalizar_item_id(value)


def _parse_search_page(payload: Any) -> tuple[list[str], int | None, str, int | None]:
    if not isinstance(payload, dict):
        return [], None, "", None
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return [], None, "", None
    ids = [_item_id(entry.get("id") if isinstance(entry, dict) else entry) for entry in raw_results]
    if any(not item for item in ids):
        return [], None, "", None
    paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
    try:
        total = int(paging["total"]) if "total" in paging else None
    except (TypeError, ValueError):
        total = None
    try:
        offset = int(paging["offset"]) if "offset" in paging else None
    except (TypeError, ValueError):
        offset = None
    return ids, max(0, total) if total is not None else None, str(payload.get("scroll_id") or "").strip(), offset


def _list_ids_offset(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    seller_id: str,
    progress_callback: Callable[[dict[str, Any]], Any] | None,
    cancel_event: Any,
    deadline: float | None = None,
) -> tuple[list[str], dict[str, Any], bool, list[str], int]:
    ids: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    expected_total: int | None = None
    offset = 0
    pages = 0
    url = f"{ML_API_BASE}/users/{seller_id}/items/search"

    while pages < ML_MAX_PAGES:
        if _cancelled(cancel_event):
            warnings.append("Coleta do Mercado Livre cancelada.")
            return ids, cfg, False, warnings, pages
        payload, cfg, status, error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            url,
            params={"offset": offset, "limit": ML_PAGE_SIZE},
            timeout=35,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        pages += 1
        if status != 200:
            warnings.append(f"A paginacao por offset falhou (HTTP {status or 'indisponivel'}; {error or 'provider_error'}).")
            return ids, cfg, False, warnings, pages
        batch, total, _scroll_id, reported_offset = _parse_search_page(payload)
        if total is None or (reported_offset is not None and reported_offset != offset):
            warnings.append("O Mercado Livre nao devolveu uma paginacao por offset comprovavel.")
            return ids, cfg, False, warnings, pages
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            warnings.append("O total de anuncios mudou durante a paginacao.")
            return ids, cfg, False, warnings, pages
        if expected_total > ML_CATALOG_MAX_ROWS:
            warnings.append(_catalog_limit_warning("anuncios"))
            return ids, cfg, False, warnings, pages

        before = len(ids)
        for item in batch:
            if item not in seen:
                seen.add(item)
                ids.append(item)
        if len(ids) > ML_CATALOG_MAX_ROWS:
            warnings.append(_catalog_limit_warning("anuncios"))
            return [], cfg, False, warnings, pages
        _emit_progress(progress_callback, "listing", len(ids), expected_total, "Listando anuncios do Mercado Livre.")
        if len(ids) >= expected_total:
            return ids, cfg, len(ids) == expected_total, warnings, pages
        if not batch or len(ids) == before:
            warnings.append("A paginacao por offset nao avancou ate o total informado.")
            return ids, cfg, False, warnings, pages
        offset += len(batch)

    warnings.append("A paginacao excedeu o limite seguro de paginas.")
    return ids, cfg, False, warnings, pages


def _list_all_ids(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    seller_id: str,
    progress_callback: Callable[[dict[str, Any]], Any] | None,
    cancel_event: Any,
    deadline: float | None = None,
) -> tuple[list[str], dict[str, Any], bool, list[str], int]:
    url = f"{ML_API_BASE}/users/{seller_id}/items/search"
    ids: list[str] = []
    seen: set[str] = set()
    expected_total: int | None = None
    scroll_id = ""
    warnings: list[str] = []
    pages = 0

    for page in range(ML_MAX_PAGES):
        if _cancelled(cancel_event):
            warnings.append("Coleta do Mercado Livre cancelada.")
            return ids, cfg, False, warnings, pages
        params: dict[str, Any] = {"search_type": "scan", "limit": ML_PAGE_SIZE}
        if page:
            params["scroll_id"] = scroll_id
        payload, cfg, status, error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            url,
            params=params,
            timeout=35,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        pages += 1
        if status != 200:
            if page == 0:
                fallback_ids, cfg, complete, fallback_warnings, fallback_pages = _list_ids_offset(
                    client_id, store, cfg, seller_id, progress_callback, cancel_event, deadline
                )
                return fallback_ids, cfg, complete, fallback_warnings, pages + fallback_pages
            warnings.append(f"A paginacao por scroll falhou (HTTP {status or 'indisponivel'}; {error or 'provider_error'}).")
            return ids, cfg, False, warnings, pages

        batch, total, next_scroll, _offset = _parse_search_page(payload)
        if total is None:
            if page == 0:
                fallback_ids, cfg, complete, fallback_warnings, fallback_pages = _list_ids_offset(
                    client_id, store, cfg, seller_id, progress_callback, cancel_event, deadline
                )
                return fallback_ids, cfg, complete, fallback_warnings, pages + fallback_pages
            warnings.append("O Mercado Livre deixou de informar o total durante o scroll.")
            return ids, cfg, False, warnings, pages
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            warnings.append("O total de anuncios mudou durante o scroll.")
            return ids, cfg, False, warnings, pages
        if expected_total > ML_CATALOG_MAX_ROWS:
            warnings.append(_catalog_limit_warning("anuncios"))
            return ids, cfg, False, warnings, pages

        before = len(ids)
        duplicates = 0
        for item in batch:
            if item in seen:
                duplicates += 1
                continue
            seen.add(item)
            ids.append(item)
        if len(ids) > ML_CATALOG_MAX_ROWS:
            warnings.append(_catalog_limit_warning("anuncios"))
            return [], cfg, False, warnings, pages
        if duplicates:
            warnings.append(f"O scroll repetiu {duplicates} ID(s) de anuncio; a cobertura foi conferida pelo total unico.")
        _emit_progress(progress_callback, "listing", len(ids), expected_total, "Listando anuncios do Mercado Livre.")
        if len(ids) >= expected_total:
            return ids, cfg, len(ids) == expected_total, warnings, pages
        if not batch or len(ids) == before:
            warnings.append("O scroll nao avancou ate o total informado.")
            return ids, cfg, False, warnings, pages
        if not next_scroll:
            fallback_ids, cfg, complete, fallback_warnings, fallback_pages = _list_ids_offset(
                client_id, store, cfg, seller_id, progress_callback, cancel_event, deadline
            )
            return fallback_ids, cfg, complete, [*warnings, *fallback_warnings], pages + fallback_pages
        # Mercado Livre requires the same initial scroll_id on every
        # subsequent call. Progress is proven by unique listing IDs and total.
        if not scroll_id:
            scroll_id = next_scroll

    warnings.append("O scroll excedeu o limite seguro de paginas.")
    return ids, cfg, False, warnings, pages


def _fetch_details(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    item_ids: list[str],
    progress_callback: Callable[[dict[str, Any]], Any] | None,
    cancel_event: Any,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool, list[dict[str, Any]], list[str]]:
    details_by_id: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = True

    def parse_batch(payload: Any, batch: list[str], *, bulk: bool) -> tuple[dict[str, dict[str, Any]], bool]:
        if not isinstance(payload, list):
            return {}, False
        parsed: dict[str, dict[str, Any]] = {}
        shape_complete = True
        for entry in payload:
            if not isinstance(entry, dict):
                shape_complete = False
                continue
            if bulk:
                # The replacement endpoint exposes both fields at the root.
                # ``body`` is accepted when supplied, but root ``id`` and
                # ``status_code`` remain mandatory so an old response cannot
                # accidentally be interpreted as a successful bulk response.
                item_id = _item_id(entry.get("id"))
                code_raw = entry.get("status_code")
                body = entry.get("body") if isinstance(entry.get("body"), dict) else entry
            else:
                body = entry.get("body") if isinstance(entry.get("body"), dict) else None
                item_id = _item_id(body.get("id") if isinstance(body, dict) else "")
                code_raw = entry.get("code")
            try:
                code = int(code_raw)
            except (TypeError, ValueError):
                code = 0
            if not item_id or item_id not in batch or code != 200 or not isinstance(body, dict):
                shape_complete = False
                continue
            body_id = _item_id(body.get("id"))
            if body_id and body_id != item_id:
                shape_complete = False
                continue
            if item_id in parsed:
                shape_complete = False
                continue
            detail = dict(body)
            detail.pop("status_code", None)
            detail["id"] = item_id
            parsed[item_id] = detail
        return parsed, shape_complete

    for start in range(0, len(item_ids), ML_MULTIGET_BATCH_SIZE):
        if _cancelled(cancel_event):
            warnings.append("Coleta do Mercado Livre cancelada durante o detalhamento.")
            return list(details_by_id.values()), cfg, False, skipped, warnings
        batch = item_ids[start : start + ML_MULTIGET_BATCH_SIZE]
        payload, cfg, status, error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            f"{ML_API_BASE}/items/bulk",
            params={"ids": ",".join(batch), "include_attributes": "all"},
            timeout=35,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        endpoint = "bulk"
        if status in {400, 404, 405, 410, 501}:
            warnings.append("O endpoint bulk nao esta disponivel; foi usado o multiget legado temporario.")
            payload, cfg, status, error = _request_json(
                client_id,
                store["store_name"],
                cfg,
                f"{ML_API_BASE}/items",
                params={"ids": ",".join(batch), "include_attributes": "all"},
                timeout=35,
                cancel_event=cancel_event,
                deadline=deadline,
            )
            endpoint = "legacy"
        if status != 200 or not isinstance(payload, list):
            complete = False
            warnings.append(f"O multiget de anuncios falhou (HTTP {status or 'indisponivel'}; {error or 'invalid_response'}).")
            skipped.extend({"reason": "listing_details_missing", "mlb": item_id} for item_id in batch)
            continue

        parsed, response_shape_complete = parse_batch(payload, batch, bulk=endpoint == "bulk")
        details_by_id.update(parsed)
        missing = [item_id for item_id in batch if item_id not in parsed]
        if missing or not response_shape_complete:
            complete = False
            if missing:
                warnings.append(f"O multiget nao devolveu detalhes de {len(missing)} anuncio(s).")
            else:
                warnings.append("O multiget devolveu entradas inesperadas; a cobertura nao pode ser comprovada.")
            skipped.extend({"reason": "listing_details_missing", "mlb": item_id} for item_id in missing)
        _emit_progress(
            progress_callback,
            "details",
            min(start + len(batch), len(item_ids)),
            len(item_ids),
            "Detalhando anuncios do Mercado Livre.",
        )

    return [details_by_id[item_id] for item_id in item_ids if item_id in details_by_id], cfg, complete, skipped, warnings


def _merge_variation_details(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    item: dict[str, Any],
    cancel_event: Any = None,
    deadline: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    variations = item.get("variations") if isinstance(item.get("variations"), list) else []
    if not variations:
        return item, cfg, True
    item_id = _item_id(item.get("id"))
    payload, cfg, status, _error = _request_json(
        client_id,
        store["store_name"],
        cfg,
        f"{ML_API_BASE}/items/{item_id}/variations",
        params={"include_attributes": "all"},
        timeout=25,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    if status != 200:
        return item, cfg, False
    detailed = (
        payload.get("variations") or payload.get("results") or []
        if isinstance(payload, dict)
        else payload
    )
    if not isinstance(detailed, list):
        return item, cfg, False
    by_id = {
        str(variation.get("id") or "").strip(): variation
        for variation in detailed
        if isinstance(variation, dict) and str(variation.get("id") or "").strip()
    }
    expected_ids = [str(variation.get("id") or "").strip() for variation in variations if isinstance(variation, dict)]
    if not expected_ids or any(not variation_id or variation_id not in by_id for variation_id in expected_ids):
        return item, cfg, False
    merged: list[Any] = []
    for variation in variations:
        if not isinstance(variation, dict):
            return item, cfg, False
        authoritative = by_id[str(variation.get("id") or "").strip()]
        detail = dict(variation)
        for key, value in authoritative.items():
            if value in (None, "", []):
                continue
            if key in {"attributes", "attribute_combinations"}:
                detail[key] = _merge_attribute_records(
                    variation.get(key),
                    value,
                )
            else:
                detail[key] = value
        merged.append(detail)
    enriched = dict(item)
    enriched["variations"] = merged
    return enriched, cfg, len(by_id) == len(expected_ids)


def _merge_attribute_records(detailed: Any, summary: Any) -> list[Any]:
    result: list[Any] = []
    positions: dict[str, int] = {}
    anonymous_seen: set[str] = set()
    for records in (detailed, summary):
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            attribute_id = str(record.get("id") or "").strip().upper()
            if attribute_id:
                if attribute_id in positions:
                    index = positions[attribute_id]
                    merged = dict(result[index])
                    merged.update(
                        {
                            key: value
                            for key, value in record.items()
                            if value not in (None, "", [])
                        }
                    )
                    result[index] = merged
                else:
                    positions[attribute_id] = len(result)
                    result.append(dict(record))
                continue
            fingerprint = _canonical_json(record)
            if fingerprint not in anonymous_seen:
                anonymous_seen.add(fingerprint)
                result.append(dict(record))
    return result


def _user_product_ids(items: Iterable[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        values.append(str(item.get("user_product_id") or "").strip())
        for variation in item.get("variations") or []:
            if isinstance(variation, dict):
                values.append(str(variation.get("user_product_id") or "").strip())
    return [str(value) for value in _distinct(values)]


def _normalize_user_product_detail(payload: Any, expected_id: str, seller_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    observed_id = str(payload.get("id") or "").strip()
    observed_seller = str(payload.get("user_id") or "").strip()
    if observed_id != expected_id or observed_seller != str(seller_id or "").strip():
        return None
    attributes = [
        record
        for attribute in payload.get("attributes") or []
        if (record := _attribute_record(attribute, "user_product"))
    ] if isinstance(payload.get("attributes"), list) else []
    tags = [
        str(value).strip()
        for value in payload.get("tags") or []
        if str(value or "").strip()
    ] if isinstance(payload.get("tags"), list) else []
    pictures = _normalize_picture_records(payload.get("pictures"))
    thumbnail_records = _normalize_picture_records([payload.get("thumbnail")])
    detail = {
        "id": expected_id,
        "name": str(payload.get("name") or "").strip(),
        "site_id": str(payload.get("site_id") or "").strip().upper(),
        "family_id": str(payload.get("family_id") or "").strip(),
        "domain_id": str(payload.get("domain_id") or "").strip(),
        "catalog_product_id": str(payload.get("catalog_product_id") or "").strip(),
        "date_created": str(payload.get("date_created") or "").strip(),
        "last_updated": str(payload.get("last_updated") or "").strip(),
        "attributes": attributes,
        "pictures": pictures,
        "thumbnail": thumbnail_records[0] if thumbnail_records else {},
        "tags": tags,
        "bundle": payload.get("bundle") if isinstance(payload.get("bundle"), dict) else {},
    }
    return {key: value for key, value in detail.items() if _value_present(value)}


def _normalize_picture_records(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for picture in value:
        if isinstance(picture, str):
            picture_id = ""
            secure_url = picture.strip()
        elif isinstance(picture, dict):
            picture_id = str(picture.get("id") or "").strip()
            secure_url = str(
                picture.get("secure_url") or picture.get("url") or ""
            ).strip()
        else:
            continue
        if secure_url and not cadastro_ml._photo_url_allowed(secure_url):
            secure_url = ""
        record = {
            key: field_value
            for key, field_value in {"id": picture_id, "secure_url": secure_url}.items()
            if field_value
        }
        if not record:
            continue
        fingerprint = _canonical_json(record)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(record)
    return result


def _normalize_user_product_stock(payload: Any, expected_id: str, seller_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    observed_id = str(payload.get("id") or "").strip()
    observed_seller = str(payload.get("user_id") or "").strip()
    locations = payload.get("locations")
    if observed_id != expected_id or observed_seller != str(seller_id or "").strip() or not isinstance(locations, list):
        return None
    normalized_by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    for location in locations:
        if not isinstance(location, dict):
            return None
        location_type = str(location.get("type") or "").strip()
        quantity = _quantity_as_nonnegative_int(location.get("quantity"))
        if not location_type or quantity is None:
            return None
        store_id = str(location.get("store_id") or "").strip()
        network_node_id = str(location.get("network_node_id") or "").strip()
        if location_type == "seller_warehouse" and not store_id:
            return None
        row: dict[str, Any] = {"type": location_type, "quantity": quantity}
        if store_id:
            row["store_id"] = store_id
        if network_node_id:
            row["network_node_id"] = network_node_id
        identity = (location_type, store_id, network_node_id)
        previous = normalized_by_identity.get(identity)
        if previous is not None:
            if previous["quantity"] != quantity:
                return None
            continue
        normalized_by_identity[identity] = row
    normalized = list(normalized_by_identity.values())
    normalized.sort(
        key=lambda row: (
            str(row.get("type") or ""),
            str(row.get("store_id") or ""),
            str(row.get("network_node_id") or ""),
        )
    )
    return {
        "id": expected_id,
        "locations": normalized,
        "total": sum(int(row["quantity"]) for row in normalized),
    }


def _fetch_user_products(
    client_id: str,
    store: dict[str, str],
    cfg: dict[str, Any],
    user_product_ids: list[str],
    seller_id: str,
    warehouse_management: bool,
    stock_absence_allowed: bool,
    progress_callback: Callable[[dict[str, Any]], Any] | None,
    cancel_event: Any,
    deadline: float | None,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
    bool,
    list[str],
    dict[str, int],
]:
    details: dict[str, dict[str, Any]] = {}
    stocks: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    complete = True
    stats = {"details": 0, "stocks": 0, "stocks_absent": 0, "failures": 0}
    for index, user_product_id in enumerate(user_product_ids, start=1):
        _check_catalog_limits(cancel_event, deadline)
        _emit_progress(
            progress_callback,
            "user_products",
            index,
            len(user_product_ids),
            "Detalhando User Products e estoques do Mercado Livre.",
        )
        detail_payload, cfg, detail_status, _detail_error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            f"{ML_API_BASE}/user-products/{user_product_id}",
            timeout=25,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        detail = (
            _normalize_user_product_detail(detail_payload, user_product_id, seller_id)
            if detail_status == 200
            else None
        )
        if detail is None:
            complete = False
            stats["failures"] += 1
            warnings.append(f"O User Product {user_product_id} nao foi detalhado com seguranca.")
            continue
        details[user_product_id] = detail
        stats["details"] += 1

        stock_payload, cfg, stock_status, _stock_error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            f"{ML_API_BASE}/user-products/{user_product_id}/stock",
            timeout=25,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        if stock_status == 404 and stock_absence_allowed and not warehouse_management:
            stats["stocks_absent"] += 1
            continue
        stock = (
            _normalize_user_product_stock(stock_payload, user_product_id, seller_id)
            if stock_status == 200
            else None
        )
        if stock is None:
            complete = False
            stats["failures"] += 1
            warnings.append(f"O estoque do User Product {user_product_id} nao foi comprovado.")
            continue
        stocks[user_product_id] = stock
        stats["stocks"] += 1
    return details, stocks, cfg, complete, warnings, stats


def _listing_detail_missing_fields(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return ["item"]
    missing = [
        key
        for key in ("id", "seller_id", "title", "status", "category_id")
        if not str(item.get(key) or "").strip()
    ]
    for key in ("attributes", "variations"):
        if key not in item or not isinstance(item.get(key), list):
            missing.append(key)
    return missing


def _attribute_values(attribute: dict[str, Any]) -> list[str]:
    return cadastro_ml._valores_atributo(attribute)


def _explicit_sku_candidates(entity: Any) -> list[dict[str, Any]]:
    if not isinstance(entity, dict):
        return []
    found: dict[str, dict[str, Any]] = {}

    def add(value: Any, source: str) -> None:
        raw = str(value or "").strip()
        if not raw:
            return
        display = _normalizar_sku_mes(raw)
        normalized = display.upper()
        comparison = unicodedata.normalize("NFKC", normalized).casefold()
        if not comparison:
            return
        entry = found.setdefault(
            comparison,
            {"sku": display, "sku_normalizado": normalized, "comparison_key": comparison, "sources": []},
        )
        if source not in entry["sources"]:
            entry["sources"].append(source)

    for attribute in cadastro_ml._atributos(entity):
        attribute_id = str(attribute.get("id") or "").strip().upper()
        if attribute_id not in {"SELLER_SKU", "SKU"}:
            continue
        for value in _attribute_values(attribute):
            add(value, f"attribute:{attribute_id}")
    add(entity.get("seller_sku"), "seller_sku")
    add(entity.get("sku"), "sku")
    add(entity.get("seller_custom_field"), "seller_custom_field")
    return sorted(found.values(), key=lambda entry: entry["comparison_key"])


def _reconcile_sku_candidates(
    entity: Any,
    user_product: Any,
) -> tuple[list[dict[str, Any]], bool]:
    listing_candidates = _explicit_sku_candidates(entity)
    user_product_candidates = _explicit_sku_candidates(user_product)
    for candidate in user_product_candidates:
        candidate["sources"] = [
            f"user_product:{source}"
            for source in candidate.get("sources") or []
        ]
    if not user_product_candidates:
        return listing_candidates, False
    if not listing_candidates:
        return user_product_candidates, False

    listing_by_key = {
        candidate["comparison_key"]: candidate
        for candidate in listing_candidates
    }
    user_product_by_key = {
        candidate["comparison_key"]: candidate
        for candidate in user_product_candidates
    }
    conflict = set(listing_by_key) != set(user_product_by_key)
    combined: dict[str, dict[str, Any]] = {}
    for candidate in listing_candidates + user_product_candidates:
        key = candidate["comparison_key"]
        existing = combined.setdefault(key, dict(candidate))
        sources = existing.setdefault("sources", [])
        for source in candidate.get("sources") or []:
            if source not in sources:
                sources.append(source)
    return sorted(combined.values(), key=lambda entry: entry["comparison_key"]), conflict


def _attribute_record(attribute: Any, source: str) -> dict[str, Any] | None:
    if not isinstance(attribute, dict):
        return None
    values = _attribute_values(attribute)
    normalized_values: list[dict[str, Any]] = []
    if isinstance(attribute.get("values"), list):
        for raw_value in attribute["values"]:
            if not isinstance(raw_value, dict):
                continue
            normalized_value: dict[str, Any] = {}
            for key in ("id", "name"):
                value = str(raw_value.get(key) or "").strip()
                if value:
                    normalized_value[key] = value
            raw_struct = raw_value.get("struct")
            if isinstance(raw_struct, dict):
                struct: dict[str, Any] = {}
                number = raw_struct.get("number")
                if (
                    isinstance(number, (int, float))
                    and not isinstance(number, bool)
                    and math.isfinite(number)
                ):
                    struct["number"] = number
                unit = str(raw_struct.get("unit") or "").strip()
                if unit:
                    struct["unit"] = unit
                if struct:
                    normalized_value["struct"] = struct
            if normalized_value:
                normalized_values.append(normalized_value)
    value_id = str(attribute.get("value_id") or "").strip()
    if not value_id and normalized_values:
        value_id = str(normalized_values[0].get("id") or "").strip()
    record: dict[str, Any] = {
        "id": str(attribute.get("id") or "").strip(),
        "name": str(attribute.get("name") or "").strip(),
        "value_id": value_id,
        "value_name": values[0] if values else "",
        "source": source,
    }
    if normalized_values:
        record["values"] = normalized_values
    elif len(values) > 1:
        record["values"] = [{"name": value} for value in values]
    return {key: value for key, value in record.items() if value}


def _effective_attributes(item: dict[str, Any], variation: dict[str, Any] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for attribute in cadastro_ml._atributos(item):
        record = _attribute_record(attribute, "item")
        if record:
            records.append(record)
    if isinstance(variation, dict):
        for attribute in cadastro_ml._atributos(variation):
            record = _attribute_record(attribute, "variation")
            if record:
                records.append(record)
    records.sort(key=lambda entry: (entry.get("id", ""), entry.get("source", ""), entry.get("value_name", "")))
    return records


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _canonical_json_if_present(value: Any) -> str:
    return _canonical_json(value) if _value_present(value) else ""


def _first_nonempty(item: dict[str, Any], variation: dict[str, Any] | None, key: str) -> Any:
    if isinstance(variation, dict) and _value_present(variation.get(key)):
        return variation.get(key)
    return item.get(key)


def _occurrence(
    item: dict[str, Any],
    sku_entry: dict[str, Any],
    variation: dict[str, Any] | None,
    descriptions: dict[str, str],
    user_products: dict[str, dict[str, Any]] | None = None,
    user_product_stocks: dict[str, dict[str, Any]] | None = None,
    warehouse_management: bool = False,
) -> dict[str, Any]:
    item_id = _item_id(item.get("id"))
    gtin_values = cadastro_ml._attribute_values(variation, cadastro_ml.GTIN_ATTRIBUTE_IDS) if variation else []
    if not (variation and cadastro_ml._declares_attribute(variation, cadastro_ml.GTIN_ATTRIBUTE_IDS)):
        gtin_values = cadastro_ml._attribute_values(item, cadastro_ml.GTIN_ATTRIBUTE_IDS)
    photo_url = cadastro_ml._listing_picture(item, variation)
    if not cadastro_ml._photo_url_allowed(photo_url):
        photo_url = ""
    user_product_id = str(
        _first_nonempty(item, variation, "user_product_id") or ""
    ).strip()
    user_product = (user_products or {}).get(user_product_id) or {}
    user_product_stock = (user_product_stocks or {}).get(user_product_id) or {}
    catalog_product_id = str(
        _first_nonempty(item, variation, "catalog_product_id") or ""
    ).strip()
    user_product_catalog_product_id = str(
        user_product.get("catalog_product_id") or ""
    ).strip()
    item_site_id = str(item.get("site_id") or "").strip().upper()
    user_product_site_id = str(user_product.get("site_id") or "").strip().upper()
    identity_conflicts: dict[str, list[str]] = {}
    if (
        catalog_product_id
        and user_product_catalog_product_id
        and catalog_product_id != user_product_catalog_product_id
    ):
        identity_conflicts["catalog_product_id"] = [
            catalog_product_id,
            user_product_catalog_product_id,
        ]
    if item_site_id and user_product_site_id and item_site_id != user_product_site_id:
        identity_conflicts["site_id"] = [item_site_id, user_product_site_id]
    return {
        "sku": sku_entry["sku"],
        "sku_normalizado": sku_entry["sku_normalizado"],
        "comparison_key": sku_entry["comparison_key"],
        "sku_sources": list(sku_entry.get("sources") or []),
        "mlb": item_id,
        "title": str(item.get("title") or "").strip(),
        "status": str(item.get("status") or "").strip(),
        "category_id": str(item.get("category_id") or "").strip(),
        "brand": cadastro_ml._first_attribute(item, variation, "BRAND"),
        "model": cadastro_ml._first_attribute(item, variation, "MODEL"),
        "gtins": [str(value).strip() for value in gtin_values if str(value).strip()],
        "description": str(descriptions.get(item_id) or "").strip(),
        "price": _first_nonempty(item, variation, "price"),
        "currency_id": str(_first_nonempty(item, variation, "currency_id") or "").strip(),
        "base_price": _first_nonempty(item, variation, "base_price"),
        "original_price": _first_nonempty(item, variation, "original_price"),
        "available_quantity": _first_nonempty(item, variation, "available_quantity"),
        "sold_quantity": _first_nonempty(item, variation, "sold_quantity"),
        "sale_terms": item.get("sale_terms") if isinstance(item.get("sale_terms"), list) else [],
        "shipping": item.get("shipping") if isinstance(item.get("shipping"), dict) else {},
        "condition": str(item.get("condition") or "").strip(),
        "condition_name": cadastro_ml._first_attribute(item, variation, "ITEM_CONDITION"),
        "warranty": str(item.get("warranty") or "").strip(),
        "date_created": str(item.get("date_created") or "").strip(),
        "last_updated": str(item.get("last_updated") or "").strip(),
        "channels": item.get("channels") if isinstance(item.get("channels"), list) else [],
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "family_name": str(item.get("family_name") or "").strip(),
        "family_id": str(user_product.get("family_id") or item.get("family_id") or "").strip(),
        "domain_id": str(user_product.get("domain_id") or item.get("domain_id") or "").strip(),
        "site_id": item_site_id,
        "catalog_listing": item.get("catalog_listing"),
        "buying_mode": str(item.get("buying_mode") or "").strip(),
        "variation_id": str((variation or {}).get("id") or "").strip(),
        "listing_type": str(item.get("listing_type_id") or "").strip(),
        "catalog_product_id": catalog_product_id,
        "user_product_id": user_product_id,
        "user_product_name": str(user_product.get("name") or "").strip(),
        "user_product_site_id": user_product_site_id,
        "user_product_catalog_product_id": user_product_catalog_product_id,
        "user_product_date_created": str(user_product.get("date_created") or "").strip(),
        "user_product_last_updated": str(user_product.get("last_updated") or "").strip(),
        "user_product_attributes": user_product.get("attributes") or [],
        "user_product_pictures": user_product.get("pictures") or [],
        "user_product_thumbnail": user_product.get("thumbnail") or {},
        "user_product_tags": user_product.get("tags") or [],
        "user_product_bundle": user_product.get("bundle") or {},
        "user_product_stock_total": user_product_stock.get("total"),
        "user_product_stock_locations": user_product_stock.get("locations") or [],
        "user_product_stock_authoritative": _value_present(
            user_product_stock.get("total")
        ),
        "warehouse_management": bool(warehouse_management),
        "inventory_id": str(
            _first_nonempty(item, variation, "inventory_id") or ""
        ).strip(),
        "permalink": str(item.get("permalink") or "").strip(),
        "photo_url": photo_url,
        "pictures": _normalize_picture_records(item.get("pictures")),
        "attributes": _effective_attributes(item, variation),
        "user_product_identity_conflicts": identity_conflicts,
    }


def _collect_occurrences(
    items: Iterable[dict[str, Any]],
    descriptions: dict[str, str],
    *,
    max_rows: int,
    user_products: dict[str, dict[str, Any]] | None = None,
    user_product_stocks: dict[str, dict[str, Any]] | None = None,
    warehouse_management: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter, bool]:
    occurrences: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    counts: Counter = Counter()
    candidate_rows = 0

    for item in items:
        item_id = _item_id(item.get("id"))
        variations = item.get("variations") if isinstance(item.get("variations"), list) else []
        entities: list[tuple[str, dict[str, Any] | None]] = [("parent", None)]
        entities.extend(("variation", variation) for variation in variations if isinstance(variation, dict))
        local_occurrences: list[dict[str, Any]] = []
        skipped_before_item = len(skipped)
        counts_before_item = counts.copy()

        for kind, variation in entities:
            entity = item if variation is None else variation
            user_product_id = str(
                _first_nonempty(item, variation, "user_product_id") or ""
            ).strip()
            user_product = (user_products or {}).get(user_product_id) or {}
            candidates, user_product_sku_conflict = _reconcile_sku_candidates(
                entity,
                user_product,
            )
            represents_candidate_row = bool(candidates) or kind == "variation" or not variations
            if represents_candidate_row:
                if candidate_rows >= max(0, int(max_rows)):
                    # Do not expose a partially inspected listing: a later
                    # variation could invalidate an earlier SKU identity.
                    del skipped[skipped_before_item:]
                    return occurrences, skipped, counts_before_item, True
                candidate_rows += 1
            variation_id = str((variation or {}).get("id") or "").strip()
            if user_product_sku_conflict:
                counts["user_product_sku_conflicts"] += 1
                skipped.append({
                    "reason": "user_product_sku_conflict",
                    "mlb": item_id,
                    "variation_id": variation_id,
                    "entity": kind,
                    "candidates": [candidate["sku"] for candidate in candidates],
                })
                continue
            if not candidates:
                # A variation always represents a candidate identity.  A parent
                # without SKU is only missing when there are no variations.
                if kind == "variation" or not variations:
                    counts["missing_sku"] += 1
                    skipped.append({
                        "reason": "sku_missing",
                        "mlb": item_id,
                        "variation_id": variation_id,
                        "entity": kind,
                    })
                continue
            if len(candidates) != 1:
                counts["ambiguous_sku"] += 1
                skipped.append({
                    "reason": "sku_ambiguous",
                    "mlb": item_id,
                    "variation_id": variation_id,
                    "entity": kind,
                    "candidates": [candidate["sku"] for candidate in candidates],
                })
                continue
            local_occurrences.append(
                _occurrence(
                    item,
                    candidates[0],
                    variation,
                    descriptions,
                    user_products,
                    user_product_stocks,
                    warehouse_management,
                )
            )

        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in local_occurrences:
            by_key[entry["comparison_key"]].append(entry)
        for same_identity in by_key.values():
            if len(same_identity) == 1:
                occurrences.append(same_identity[0])
                continue
            counts["duplicate_sku"] += 1
            skipped.append({
                "reason": "sku_duplicate_within_listing",
                "mlb": item_id,
                "sku": same_identity[0]["sku"],
                "identities": [
                    {"variation_id": entry["variation_id"], "sources": entry["sku_sources"]}
                    for entry in same_identity
                ],
            })
    return occurrences, skipped, counts, False


def _distinct(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if not _value_present(value):
            continue
        key = _canonical_json(value) if isinstance(value, (dict, list)) else str(value).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _distinct_including_missing(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _quantity_as_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not _value_present(value):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _quantity_conflict(
    entries: list[dict[str, Any]],
    field: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "reason": reason,
        "values": [entry.get(field) for entry in entries],
        "mlb_ids": _distinct(entry.get("mlb") for entry in entries),
    }


def _available_stock_for_group(
    entries: list[dict[str, Any]],
) -> tuple[int | None, dict[str, Any] | None]:
    field = "available_quantity"
    if entries and any(
        bool(entry.get("warehouse_management"))
        or bool(entry.get("user_product_stock_authoritative"))
        for entry in entries
    ):
        user_product_ids = [str(entry.get("user_product_id") or "").strip() for entry in entries]
        stock_values = [
            _quantity_as_nonnegative_int(entry.get("user_product_stock_total"))
            for entry in entries
        ]
        if not all(user_product_ids) or any(value is None for value in stock_values):
            return None, _quantity_conflict(
                entries,
                "user_product_stock_total",
                "user_product_stock_unproven",
            )
        quantities_by_user_product: dict[str, list[int]] = defaultdict(list)
        for user_product_id, value in zip(user_product_ids, stock_values):
            quantities_by_user_product[user_product_id].append(int(value))
        representatives: list[int] = []
        for values_for_user_product in quantities_by_user_product.values():
            if len(set(values_for_user_product)) != 1:
                return None, _quantity_conflict(
                    entries,
                    "user_product_stock_total",
                    "shared_user_product_stock_values_diverge",
                )
            representatives.append(values_for_user_product[0])
        return sum(representatives), None

    present = [entry for entry in entries if _value_present(entry.get(field))]
    if not present:
        return None, None
    if len(present) != len(entries):
        return None, _quantity_conflict(entries, field, "missing_value")
    values = [_quantity_as_nonnegative_int(entry.get(field)) for entry in entries]
    if any(value is None for value in values):
        return None, _quantity_conflict(entries, field, "invalid_value")
    numeric_values = [int(value) for value in values if value is not None]
    if len(entries) == 1:
        return numeric_values[0], None

    inventory_ids = [
        str(entry.get("inventory_id") or entry.get("user_product_id") or "").strip()
        for entry in entries
    ]
    if all(inventory_ids):
        quantities_by_inventory: dict[str, list[int]] = defaultdict(list)
        for inventory_id, value in zip(inventory_ids, numeric_values):
            quantities_by_inventory[inventory_id].append(value)
        representatives: list[int] = []
        for values_for_inventory in quantities_by_inventory.values():
            if len(set(values_for_inventory)) != 1:
                return None, _quantity_conflict(
                    entries, field, "shared_inventory_values_diverge"
                )
            representatives.append(values_for_inventory[0])
        return sum(representatives), None

    if len(set(numeric_values)) == 1:
        # Without an inventory identity, equal values are consistent evidence,
        # but summing them could count the same physical stock more than once.
        return numeric_values[0], None
    return None, _quantity_conflict(entries, field, "inventory_identity_unproven")


def _sold_quantity_for_group(
    entries: list[dict[str, Any]],
) -> tuple[int | None, dict[str, Any] | None]:
    field = "sold_quantity"
    present = [entry for entry in entries if _value_present(entry.get(field))]
    if not present:
        return None, None
    if len(present) != len(entries):
        return None, _quantity_conflict(entries, field, "missing_value")
    values = [_quantity_as_nonnegative_int(entry.get(field)) for entry in entries]
    if any(value is None for value in values):
        return None, _quantity_conflict(entries, field, "invalid_value")

    quantities_by_listing: dict[tuple[str, str], list[int]] = defaultdict(list)
    for entry, value in zip(entries, values):
        identity = (
            str(entry.get("mlb") or "").strip(),
            str(entry.get("variation_id") or "").strip(),
        )
        quantities_by_listing[identity].append(int(value))
    representatives: list[int] = []
    for values_for_listing in quantities_by_listing.values():
        if len(set(values_for_listing)) != 1:
            return None, _quantity_conflict(
                entries, field, "listing_values_diverge"
            )
        representatives.append(values_for_listing[0])
    return sum(representatives), None


def _build_catalog_items(
    occurrences: list[dict[str, Any]],
    consulted_at: str,
    category_names: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    category_names = category_names or {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[occurrence["comparison_key"]].append(occurrence)
    result: list[dict[str, Any]] = []
    duplicate_groups = 0
    warnings: list[str] = []

    for comparison_key in sorted(grouped):
        entries = grouped[comparison_key]
        entries.sort(
            key=lambda entry: (
                _STATUS_RANK.get(str(entry.get("status") or "").lower(), 99),
                str(entry.get("mlb") or ""),
                str(entry.get("variation_id") or ""),
            )
        )
        primary = entries[0]
        listing_ids = _distinct(entry["mlb"] for entry in entries)
        titles_by_id: dict[str, str] = {}
        for entry in entries:
            titles_by_id.setdefault(entry["mlb"], entry["title"])
        listing_titles = [titles_by_id[item_id] for item_id in listing_ids]
        conflicts: dict[str, Any] = {}
        if len(listing_ids) > 1:
            duplicate_groups += 1
            conflicts["duplicate_listing_sku"] = list(listing_ids)
            warnings.append(
                f"O SKU {primary['sku']} aparece em {len(listing_ids)} anuncios e foi mantido como um grupo revisavel."
            )
        for field, conflict_key in (
            ("category_id", "categoria_id_mlb"),
            ("brand", "marca"),
            ("model", "modelo"),
            ("price", "preco_ml"),
            ("currency_id", "moeda_ml"),
            ("base_price", "preco_base_ml"),
            ("original_price", "preco_original_ml"),
            ("sale_terms", "termos_venda_ml_json"),
            ("shipping", "envio_ml_json"),
            ("condition", "condicao_ml"),
            ("condition_name", "condicao_nome_ml"),
            ("warranty", "garantia_ml"),
            ("date_created", "criado_em_ml"),
            ("last_updated", "atualizado_em_ml"),
            ("channels", "canais_ml_json"),
            ("tags", "tags_ml_json"),
            ("family_name", "familia_nome_ml"),
            ("family_id", "familia_id_ml"),
            ("domain_id", "dominio_id_ml"),
            ("site_id", "site_id_ml"),
            ("user_product_name", "user_product_nome_ml"),
            ("user_product_site_id", "site_id_user_product_ml"),
            ("user_product_catalog_product_id", "catalog_product_id_user_product_ml"),
            ("user_product_date_created", "criado_em_user_product_ml"),
            ("user_product_last_updated", "atualizado_em_user_product_ml"),
            ("user_product_attributes", "atributos_user_product_ml_json"),
            ("user_product_pictures", "imagens_user_product_ml_json"),
            ("user_product_thumbnail", "miniatura_user_product_ml_json"),
            ("user_product_tags", "tags_user_product_ml_json"),
            ("user_product_bundle", "bundle_user_product_ml_json"),
            ("catalog_listing", "anuncio_catalogo_ml"),
            ("buying_mode", "modo_compra_ml"),
            ("status", "status_ml"),
        ):
            values = _distinct(entry.get(field) for entry in entries)
            if len(values) > 1:
                conflicts[conflict_key] = values
                if field == "category_id":
                    conflicts["categoria"] = values

        for field, conflict_key in (
            ("variation_id", "variacao_id_ml"),
            ("catalog_product_id", "catalog_product_id_ml"),
            ("user_product_id", "user_product_id_ml"),
            ("inventory_id", "inventory_id_ml"),
        ):
            values = _distinct_including_missing(entry.get(field) for entry in entries)
            if len(values) > 1:
                conflicts[conflict_key] = [value or "<ausente>" for value in values]

        available_stock, stock_conflict = _available_stock_for_group(entries)
        if stock_conflict:
            conflicts["estoque_disponivel_ml"] = stock_conflict
        sold_quantity, sold_conflict = _sold_quantity_for_group(entries)
        if sold_conflict:
            conflicts["vendidos_acumulados_ml"] = sold_conflict
        provider_identity_conflicts = [
            entry.get("user_product_identity_conflicts")
            for entry in entries
            if entry.get("user_product_identity_conflicts")
        ]
        if provider_identity_conflicts:
            conflicts["identidade_item_user_product_ml"] = provider_identity_conflicts

        gtins = _distinct(value for entry in entries for value in entry.get("gtins") or [])
        variation_ids = _distinct(entry.get("variation_id") for entry in entries)
        catalog_product_ids = _distinct(entry.get("catalog_product_id") for entry in entries)
        user_product_catalog_product_ids = _distinct(
            entry.get("user_product_catalog_product_id") for entry in entries
        )
        user_product_ids = _distinct(entry.get("user_product_id") for entry in entries)
        inventory_ids = _distinct(entry.get("inventory_id") for entry in entries)
        family_ids = _distinct(entry.get("family_id") for entry in entries)
        stock_by_user_product: dict[str, dict[str, Any]] = {}
        for entry in entries:
            user_product_id = str(entry.get("user_product_id") or "").strip()
            stock_total = entry.get("user_product_stock_total")
            if not user_product_id or not _value_present(stock_total):
                continue
            stock_by_user_product.setdefault(
                user_product_id,
                {
                    "user_product_id": user_product_id,
                    "total": stock_total,
                    "locations": entry.get("user_product_stock_locations") or [],
                },
            )
        stock_summary = [stock_by_user_product[key] for key in sorted(stock_by_user_product)]
        user_product_stock_total = (
            sum(int(entry["total"]) for entry in stock_summary)
            if stock_summary
            else None
        )
        listing_summary = [
            {
                key: entry[key]
                for key in (
                    "mlb", "title", "status", "variation_id", "catalog_product_id",
                    "user_product_id", "inventory_id", "price", "available_quantity",
                    "sold_quantity",
                )
                if _value_present(entry.get(key))
            }
            for entry in entries
        ]
        fields: dict[str, Any] = {
            "titulo_ml": primary["title"],
            "mlb_principal": primary["mlb"],
            "mlb_ids": "|".join(str(item_id) for item_id in listing_ids),
            "titulos_anuncios_mlb": " || ".join(str(title) for title in listing_titles),
            "qtd_anuncios_mlb": len(listing_ids),
            "categoria_id_mlb": primary["category_id"],
            "categoria": category_names.get(primary["category_id"]),
            "marca": primary["brand"],
            "modelo": primary["model"],
            "gtins_mlb": "|".join(str(value) for value in gtins),
            "descricao": primary["description"],
            "preco_ml": primary["price"],
            "moeda_ml": primary["currency_id"],
            "preco_base_ml": primary["base_price"],
            "preco_original_ml": primary["original_price"],
            "estoque_disponivel_ml": available_stock,
            "vendidos_acumulados_ml": sold_quantity,
            "termos_venda_ml_json": _canonical_json_if_present(primary["sale_terms"]),
            "envio_ml_json": _canonical_json_if_present(primary["shipping"]),
            "condicao_ml": primary["condition"],
            "condicao_nome_ml": primary["condition_name"],
            "garantia_ml": primary["warranty"],
            "criado_em_ml": primary["date_created"],
            "atualizado_em_ml": primary["last_updated"],
            "canais_ml_json": _canonical_json_if_present(primary["channels"]),
            "tags_ml_json": _canonical_json_if_present(primary["tags"]),
            "familia_nome_ml": primary["family_name"],
            "familia_id_ml": primary["family_id"],
            "familia_ids_ml": "|".join(str(value) for value in family_ids),
            "dominio_id_ml": primary["domain_id"],
            "site_id_ml": primary["site_id"],
            "user_product_nome_ml": primary["user_product_name"],
            "site_id_user_product_ml": primary["user_product_site_id"],
            "catalog_product_id_user_product_ml": primary["user_product_catalog_product_id"],
            "catalog_product_ids_user_product_ml": "|".join(
                str(value) for value in user_product_catalog_product_ids
            ),
            "criado_em_user_product_ml": primary["user_product_date_created"],
            "atualizado_em_user_product_ml": primary["user_product_last_updated"],
            "atributos_user_product_ml_json": _canonical_json_if_present(
                primary["user_product_attributes"]
            ),
            "imagens_user_product_ml_json": _canonical_json_if_present(
                primary["user_product_pictures"]
            ),
            "miniatura_user_product_ml_json": _canonical_json_if_present(
                primary["user_product_thumbnail"]
            ),
            "miniatura_url_user_product_ml": str(
                (primary["user_product_thumbnail"] or {}).get("secure_url") or ""
            ).strip(),
            "tags_user_product_ml_json": _canonical_json_if_present(primary["user_product_tags"]),
            "bundle_user_product_ml_json": _canonical_json_if_present(primary["user_product_bundle"]),
            "estoque_user_product_total_ml": user_product_stock_total,
            "estoque_localizacoes_ml_json": _canonical_json_if_present(stock_summary),
            "estoque_multiorigem_ml": (
                bool(primary["warehouse_management"]) if user_product_ids else None
            ),
            "anuncio_catalogo_ml": primary["catalog_listing"],
            "modo_compra_ml": primary["buying_mode"],
            "status_ml": primary["status"],
            "variacao_id_ml": primary["variation_id"],
            "variacao_ids_ml": "|".join(str(value) for value in variation_ids),
            "listing_type_ml": primary["listing_type"],
            "catalog_product_id_ml": primary["catalog_product_id"],
            "catalog_product_ids_ml": "|".join(str(value) for value in catalog_product_ids),
            "user_product_id_ml": primary["user_product_id"],
            "user_product_ids_ml": "|".join(str(value) for value in user_product_ids),
            "inventory_id_ml": primary["inventory_id"],
            "inventory_ids_ml": "|".join(str(value) for value in inventory_ids),
            "link_ml": primary["permalink"],
            "foto_url_ml": primary["photo_url"],
            "imagens_ml_json": _canonical_json_if_present(primary["pictures"]),
            "atributos_ml_json": _canonical_json(primary["attributes"]),
            "anuncios_ml_json": _canonical_json(listing_summary),
            "consultado_em_utc": consulted_at,
        }
        fields = {key: value for key, value in fields.items() if _value_present(value)}
        for ambiguous_identity in (
            "variacao_id_ml",
            "catalog_product_id_ml",
            "user_product_id_ml",
            "inventory_id_ml",
        ):
            if ambiguous_identity in conflicts:
                fields.pop(ambiguous_identity, None)
        # Explicitly guard against accidental mapping to canonical commercial
        # or media fields owned by Cadastro/Estoque.
        for forbidden in ("preco", "estoque", "foto", "custo", "imposto"):
            fields.pop(forbidden, None)

        result.append({
            "sku": primary["sku"],
            "sku_normalizado": primary["sku_normalizado"],
            "fields": fields,
            "listings": listing_summary,
            "conflicts": conflicts,
            "warnings": [],
        })
    return result, duplicate_groups, warnings


def coletar_catalogo_mercadolivre(
    client_id: str,
    store_id: str,
    *,
    progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    cancel_event: Any = None,
    expected_config_fingerprint: str = "",
) -> dict[str, Any]:
    """Collect every confirmed explicit Mercado Livre SKU for one store.

    Provider or coverage failures are represented by ``coverage_complete=false``
    so the orchestration layer can keep the preview fail-closed.  Invalid store
    identity, OAuth seller mismatch, and missing authorization are hard errors.
    """

    store, cfg, configured_seller, persisted_cfg = _resolve_store(client_id, store_id)
    started_config_fingerprint = configuracao_catalogo_fingerprint(
        "mercadolivre",
        store["store_id"],
        store["store_name"],
        persisted_cfg,
    )
    expected = str(expected_config_fingerprint or "").strip().lower()
    if expected and not secrets.compare_digest(started_config_fingerprint, expected):
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "store_config_changed",
                "A configuracao Mercado Livre mudou antes do inicio da coleta.",
            ),
        )
    initial_configuration_fingerprint = _configuration_fingerprint(
        store, cfg, configured_seller
    )
    initial_account_fingerprint = _account_fingerprint(
        store, cfg, configured_seller
    )
    deadline = time.monotonic() + ML_CATALOG_TIMEOUT_SECONDS
    result = _base_result(store, configured_seller)
    result["started_config_fingerprint"] = started_config_fingerprint
    if _cancelled(cancel_event):
        result["coverage_complete"] = False
        result["sku_coverage_complete"] = False
        result["cancelled"] = True
        result["warnings"].append("Coleta do Mercado Livre cancelada antes da consulta.")
        result["config_fingerprint"] = _revalidate_configuration(
            client_id,
            store,
            cfg,
            configured_seller,
            initial_configuration_fingerprint,
            initial_account_fingerprint,
        )
        return result

    _emit_progress(progress_callback, "preflight", 0, 0, "Confirmando a conta Mercado Livre da loja.")
    cfg, seller_tags, seller_tags_known = _preflight_seller(
        client_id,
        store,
        cfg,
        configured_seller,
        cancel_event,
        deadline,
    )
    warehouse_management = "warehouse_management" in seller_tags
    result["stats"]["warehouse_management"] = warehouse_management
    if _cancelled(cancel_event):
        result["coverage_complete"] = False
        result["sku_coverage_complete"] = False
        result["cancelled"] = True
        result["warnings"].append("Coleta do Mercado Livre cancelada apos confirmar a conta.")
        result["config_fingerprint"] = _revalidate_configuration(
            client_id,
            store,
            cfg,
            configured_seller,
            initial_configuration_fingerprint,
            initial_account_fingerprint,
        )
        return result

    item_ids, cfg, listing_complete, listing_warnings, pages = _list_all_ids(
        client_id,
        store,
        cfg,
        configured_seller,
        progress_callback,
        cancel_event,
        deadline,
    )
    result["stats"]["pages_fetched"] = pages
    result["stats"]["listed_ids"] = len(item_ids)
    result["warnings"].extend(listing_warnings)
    result["coverage_complete"] = bool(listing_complete)
    result["sku_coverage_complete"] = bool(listing_complete)
    if _cancelled(cancel_event):
        result["cancelled"] = True

    details, cfg, details_complete, skipped, detail_warnings = _fetch_details(
        client_id,
        store,
        cfg,
        item_ids,
        progress_callback,
        cancel_event,
        deadline,
    )
    result["coverage_complete"] = bool(result["coverage_complete"] and details_complete)
    result["sku_coverage_complete"] = bool(
        result["sku_coverage_complete"] and details_complete
    )
    result["warnings"].extend(detail_warnings)
    result["skipped"].extend(skipped)
    result["stats"]["missing_details"] += sum(
        1 for entry in skipped if entry.get("reason") == "listing_details_missing"
    )

    verified: list[dict[str, Any]] = []
    descriptions: dict[str, str] = {}
    for index, raw_item in enumerate(details, start=1):
        _check_catalog_limits(cancel_event, deadline)
        if _cancelled(cancel_event):
            result["coverage_complete"] = False
            result["sku_coverage_complete"] = False
            result["cancelled"] = True
            result["warnings"].append("Coleta do Mercado Livre cancelada durante a validacao.")
            break
        item = dict(raw_item)
        item_id = _item_id(item.get("id"))
        missing_fields = _listing_detail_missing_fields(item)
        if missing_fields:
            result["coverage_complete"] = False
            result["sku_coverage_complete"] = False
            result["stats"]["incomplete_details"] += 1
            result["skipped"].append({
                "reason": "listing_detail_incomplete",
                "mlb": item_id,
                "missing_fields": missing_fields,
            })
            continue
        observed_seller = str(item.get("seller_id") or "").strip()
        if observed_seller != configured_seller:
            result["coverage_complete"] = False
            result["sku_coverage_complete"] = False
            result["stats"]["owner_mismatch"] += 1
            result["skipped"].append({"reason": "seller_mismatch", "mlb": item_id})
            continue
        item, cfg, variations_complete = _merge_variation_details(
            client_id,
            store,
            cfg,
            item,
            cancel_event,
            deadline,
        )
        if _cancelled(cancel_event):
            result["coverage_complete"] = False
            result["sku_coverage_complete"] = False
            result["cancelled"] = True
            result["warnings"].append("Coleta do Mercado Livre cancelada durante o detalhamento de variacoes.")
            break
        if not variations_complete:
            result["coverage_complete"] = False
            result["sku_coverage_complete"] = False
            result["stats"]["variation_detail_failures"] += 1
            result["warnings"].append(f"As variacoes do anuncio {item_id} nao puderam ser comprovadas integralmente.")
            result["skipped"].append({"reason": "variation_details_incomplete", "mlb": item_id})
            # Parent data can remain visible in an incomplete preview, but no
            # variation from this item is eligible for a catalogue identity.
            item = dict(item)
            item["variations"] = []

        description_payload, cfg, status, _error = _request_json(
            client_id,
            store["store_name"],
            cfg,
            f"{ML_API_BASE}/items/{item_id}/description",
            timeout=20,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        if _cancelled(cancel_event):
            result["coverage_complete"] = False
            result["sku_coverage_complete"] = False
            result["cancelled"] = True
            result["warnings"].append("Coleta do Mercado Livre cancelada durante o detalhamento de descricoes.")
            break
        if status == 200 and isinstance(description_payload, dict):
            description = str(
                description_payload.get("plain_text") or description_payload.get("text") or ""
            ).strip()
            descriptions[item_id] = description[:ML_DESCRIPTION_LIMIT]
        elif status == 404:
            result["stats"]["descriptions_absent"] += 1
            result["warnings"].append(f"O anuncio {item_id} nao possui descricao publicada.")
        else:
            result["coverage_complete"] = False
            result["stats"]["description_failures"] += 1
            result["warnings"].append(f"A descricao do anuncio {item_id} nao foi detalhada.")
        verified.append(item)
        _emit_progress(
            progress_callback,
            "normalization",
            index,
            len(details),
            "Validando SKUs explicitos do Mercado Livre.",
        )

    result["stats"]["detailed_listings"] = len(verified)
    requested_user_products = _user_product_ids(verified)
    result["stats"]["user_products_requested"] = len(requested_user_products)
    (
        user_products,
        user_product_stocks,
        cfg,
        user_products_complete,
        user_product_warnings,
        user_product_stats,
    ) = _fetch_user_products(
        client_id,
        store,
        cfg,
        requested_user_products,
        configured_seller,
        warehouse_management,
        seller_tags_known and not warehouse_management,
        progress_callback,
        cancel_event,
        deadline,
    )
    result["stats"]["user_products_complete"] = user_product_stats["details"]
    result["stats"]["user_product_stocks_complete"] = user_product_stats["stocks"]
    result["stats"]["user_product_stocks_absent"] = user_product_stats["stocks_absent"]
    result["stats"]["user_product_failures"] = user_product_stats["failures"]
    # User Product details may contribute the explicit SELLER_SKU. Stock is
    # optional for SKU identity, so a stock-only failure must not disable an
    # otherwise safe description/photo import.
    user_product_details_complete = (
        user_product_stats["details"] == len(requested_user_products)
    )
    result["sku_coverage_complete"] = bool(
        result["sku_coverage_complete"] and user_product_details_complete
    )
    warehouse_detected_from_stock = any(
            str(location.get("type") or "") == "seller_warehouse"
            for stock in user_product_stocks.values()
            for location in stock.get("locations") or []
            if isinstance(location, dict)
    )
    warehouse_management = bool(
        warehouse_management or warehouse_detected_from_stock
    )
    if warehouse_detected_from_stock and user_product_stats["stocks_absent"]:
        user_products_complete = False
        result["stats"]["user_product_failures"] += int(
            user_product_stats["stocks_absent"]
        )
        user_product_warnings.append(
            "A conta usa estoque por armazem, mas ha User Product sem estoque comprovado."
        )
    result["stats"]["warehouse_management"] = warehouse_management
    result["coverage_complete"] = bool(
        result["coverage_complete"] and user_products_complete
    )
    result["warnings"].extend(user_product_warnings)
    category_ids = _distinct(item.get("category_id") for item in verified)
    result["stats"]["categories_requested"] = len(category_ids)
    category_names, cfg, categories_complete, category_warnings = _fetch_category_names(
        client_id,
        store,
        cfg,
        category_ids,
        progress_callback,
        cancel_event,
        deadline,
    )
    result["stats"]["categories_complete"] = len(category_names)
    result["stats"]["categories_failed"] = len(category_ids) - len(category_names)
    result["coverage_complete"] = bool(result["coverage_complete"] and categories_complete)
    result["warnings"].extend(category_warnings)
    if _cancelled(cancel_event):
        result["cancelled"] = True
        result["sku_coverage_complete"] = False
    _check_catalog_limits(cancel_event, deadline)
    occurrences, identity_skipped, identity_counts, row_limit_exceeded = _collect_occurrences(
        verified,
        descriptions,
        max_rows=ML_CATALOG_MAX_ROWS,
        user_products=user_products,
        user_product_stocks=user_product_stocks,
        warehouse_management=warehouse_management,
    )
    result["skipped"].extend(identity_skipped)
    for key in (
        "missing_sku",
        "ambiguous_sku",
        "duplicate_sku",
        "user_product_sku_conflicts",
    ):
        result["stats"][key] += int(identity_counts.get(key) or 0)
    ambiguous_sku_count = int(identity_counts.get("ambiguous_sku") or 0)
    duplicate_within_listing_count = int(identity_counts.get("duplicate_sku") or 0)
    if ambiguous_sku_count or duplicate_within_listing_count:
        result["sku_coverage_complete"] = False
        result["coverage_complete"] = False
        result["warnings"].append(
            "Ha anuncio com identidade de SKU ambigua ou repetida entre variacoes; "
            "a aplicacao foi bloqueada."
        )
    if result["stats"]["user_product_sku_conflicts"]:
        result["coverage_complete"] = False
        result["sku_coverage_complete"] = False
        result["warnings"].append(
            "SKU divergente entre anuncio e User Product; a aplicacao foi bloqueada."
        )

    result["stats"]["user_product_identity_conflicts"] = sum(
        1 for entry in occurrences if entry.get("user_product_identity_conflicts")
    )
    if result["stats"]["user_product_identity_conflicts"]:
        result["coverage_complete"] = False
        result["sku_coverage_complete"] = False
        result["warnings"].append(
            "Identidade divergente entre anuncio e User Product; a aplicacao foi bloqueada."
        )

    consulted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    catalog_items, duplicate_groups, duplicate_warnings = _build_catalog_items(
        occurrences,
        consulted_at,
        category_names,
    )
    if row_limit_exceeded or len(catalog_items) > ML_CATALOG_MAX_ROWS:
        result["coverage_complete"] = False
        result["sku_coverage_complete"] = False
        result["warnings"].append(_catalog_limit_warning("linhas agregadas"))
        catalog_items = catalog_items[:ML_CATALOG_MAX_ROWS]
    result["stats"]["duplicate_sku"] += duplicate_groups
    result["warnings"].extend(duplicate_warnings)
    result["items"] = catalog_items
    result["stats"]["catalog_items"] = len(catalog_items)
    result["stats"]["skipped"] = len(result["skipped"])
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    _check_catalog_limits(cancel_event, deadline)
    result["config_fingerprint"] = _revalidate_configuration(
        client_id,
        store,
        cfg,
        configured_seller,
        initial_configuration_fingerprint,
        initial_account_fingerprint,
    )
    _emit_progress(
        progress_callback,
        "complete" if result["coverage_complete"] else "incomplete",
        len(catalog_items),
        len(catalog_items),
        "Catalogo Mercado Livre coletado para revisao.",
    )
    return result


__all__ = ["coletar_catalogo_mercadolivre"]
