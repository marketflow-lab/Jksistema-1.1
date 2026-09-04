"""Read-only Mercado Livre lookup for one store-scoped Cadastro SKU.

The lookup never writes Cadastro rows.  It resolves the exact internal
``store_id``, validates every returned listing against that store's seller id
and returns a review payload that the frontend may place in the form.  The
existing create/update endpoints remain the only persistence boundary.
"""

from __future__ import annotations

import base64
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from fastapi import Depends, Header, HTTPException, Query, Request

from backend.services.cadastro_common import _normalizar_sku_mes
from backend.services import integracoes
from backend.services import mercadolivre_legacy_core as mercadolivre
from backend.services.runtime_bridge import bind_runtime_globals


logger = logging.getLogger("jk_sistema")

ML_API_BASE = "https://api.mercadolibre.com"
ML_ITEM_ID_RE = re.compile(r"^MLB\d+$", re.IGNORECASE)
ML_MAX_LISTINGS_PER_SKU = 500
ML_SEARCH_PAGE_SIZE = 100
ML_DESCRIPTION_LIMIT = 50_000
ML_LOOKUP_DEADLINE_SECONDS = 60
ML_PHOTO_MAX_BYTES = 8 * 1024 * 1024
ML_PHOTO_HOSTS = {"http2.mlstatic.com"}
ML_PHOTO_MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
GTIN_ATTRIBUTE_IDS = {"GTIN", "EAN", "UPC", "ISBN", "JAN", "GTIN_14", "GTIN14", "ITF14"}


async def get_tenant_id(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    raise RuntimeError("Cadastro Mercado Livre runtime was not configured.")


def configure_cadastro_mercadolivre_runtime(runtime_module=None):
    runtime = bind_runtime_globals(globals(), runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            globals()["logger"] = runtime_logger
        if hasattr(runtime, "get_tenant_id"):
            globals()["get_tenant_id"] = getattr(runtime, "get_tenant_id")
    return runtime


configure_cadastro_mercadolivre_runtime()


def _detail(code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    payload.update(extra)
    return payload


def _normalizar_sku_comparacao(value: Any) -> str:
    canonical = _normalizar_sku_mes(str(value or "").strip())
    return unicodedata.normalize("NFKC", canonical).casefold()


def _normalizar_item_id(value: Any) -> str:
    item_id = re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())
    return item_id if ML_ITEM_ID_RE.fullmatch(item_id) else ""


def _valores_atributo(attribute: Any) -> list[str]:
    if not isinstance(attribute, dict):
        return []
    values: list[str] = []
    value = str(attribute.get("value_name") or attribute.get("value_id") or "").strip()
    if value:
        values.append(value)
    for entry in attribute.get("values") or []:
        if not isinstance(entry, dict):
            continue
        value = str(entry.get("name") or entry.get("id") or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _atributos(entity: Any) -> list[dict[str, Any]]:
    if not isinstance(entity, dict):
        return []
    result: list[dict[str, Any]] = []
    for key in ("attributes", "attribute_combinations"):
        for attribute in entity.get(key) or []:
            if isinstance(attribute, dict):
                result.append(attribute)
    return result


def _sku_candidates(entity: Any) -> list[tuple[str, str]]:
    if not isinstance(entity, dict):
        return []
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(value: Any, source: str) -> None:
        text = str(value or "").strip()
        key = _normalizar_sku_comparacao(text)
        if text and key and key not in seen:
            seen.add(key)
            candidates.append((text, source))

    for attribute in _atributos(entity):
        attribute_id = str(attribute.get("id") or "").strip().upper()
        if attribute_id in {"SELLER_SKU", "SKU"}:
            for value in _valores_atributo(attribute):
                add(value, "seller_sku_attribute")
    for key in ("seller_sku", "sku"):
        add(entity.get(key), key)
    add(entity.get("seller_custom_field"), "seller_custom_field")
    return candidates


def _match_exact_sku(item: dict[str, Any], requested_sku: str) -> dict[str, Any]:
    requested_key = _normalizar_sku_comparacao(requested_sku)
    if not requested_key:
        return {}

    # Variation SKU is more specific than a legacy parent seller_custom_field.
    variation_matches: list[dict[str, Any]] = []
    variation_keys: set[str] = set()
    for index, variation in enumerate(item.get("variations") or []):
        if not isinstance(variation, dict):
            continue
        for candidate, source in _sku_candidates(variation):
            if _normalizar_sku_comparacao(candidate) == requested_key:
                variation_id = str(variation.get("id") or "").strip()
                identity = variation_id or f"index:{index}"
                if identity in variation_keys:
                    break
                variation_keys.add(identity)
                variation_matches.append({
                    "exact": True,
                    "matched_sku": candidate,
                    "matched_by": f"variation_{source}",
                    "variation": variation,
                    "variation_id": variation_id,
                })
                break

    parent = dict(item)
    parent.pop("variations", None)
    parent_match: dict[str, Any] = {}
    for candidate, source in _sku_candidates(parent):
        if _normalizar_sku_comparacao(candidate) == requested_key:
            parent_match = {
                "exact": True,
                "matched_sku": candidate,
                "matched_by": f"item_{source}",
                "variation": None,
                "variation_id": "",
            }
            break
    if len(variation_matches) > 1 or variation_matches and parent_match:
        return {
            "exact": False,
            "ambiguous": True,
            "variation_ids": [entry["variation_id"] for entry in variation_matches],
            "parent_match": bool(parent_match),
        }
    if variation_matches:
        return variation_matches[0]
    if parent_match:
        return parent_match
    return {}


def _resolver_loja_ml_exata(client_id: str, store_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    store_id_alvo = str(store_id or "").strip()
    if not store_id_alvo:
        raise HTTPException(status_code=400, detail=_detail("store_id_required", "store_id e obrigatorio."))
    stores = [
        dict(store)
        for store in integracoes.carregar_lojas(client_id) or []
        if isinstance(store, dict)
        and str(store.get("store_id") or "").strip() == store_id_alvo
    ]
    if not stores:
        raise HTTPException(status_code=404, detail=_detail("store_not_found", "Loja nao encontrada para este cliente."))
    if len(stores) != 1:
        raise HTTPException(
            status_code=409,
            detail=_detail("store_config_ambiguous", "A identidade da loja esta duplicada na configuracao."),
        )

    store = stores[0]
    store_name = str(store.get("nome") or store_id_alvo).strip() or store_id_alvo
    integrations = store.get("integracoes") if isinstance(store.get("integracoes"), dict) else {}
    cfg = dict(integrations.get("mercadolivre") or {})
    if not cfg:
        raise HTTPException(
            status_code=400,
            detail=_detail("ml_not_configured", "Mercado Livre nao configurado para a loja escolhida."),
        )
    cfg["app_id"] = cfg.get("app_id") or cfg.get("id") or cfg.get("client_id")
    cfg["client_secret"] = cfg.get("client_secret") or cfg.get("secret")
    if not str(cfg.get("access_token") or "").strip():
        raise HTTPException(
            status_code=401,
            detail=_detail("ml_reconnect_required", "Token do Mercado Livre ausente. Refaça a autenticacao desta loja."),
        )

    cfg = mercadolivre._ml_cfg_com_store_id_context(cfg, store_id_alvo)
    cfg = mercadolivre._ml_normalizar_oauth_compartilhado(client_id, store_name, cfg)
    cfg = mercadolivre._ml_descobrir_user_id_oauth(client_id, store_name, cfg)
    seller_id = str(cfg.get("user_id") or "").strip()
    if not seller_id:
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "seller_id_missing",
                "A conta Mercado Livre da loja nao informou o seller id. Reconecte a integracao.",
            ),
        )
    return {"store_id": store_id_alvo, "nome": store_name}, cfg


def _provider_error(response: Any, default_message: str) -> HTTPException:
    status = int(getattr(response, "status_code", 0) or 0)
    if status in {401, 403}:
        return HTTPException(
            status_code=401,
            detail=_detail("ml_reconnect_required", "A autorizacao do Mercado Livre desta loja precisa ser renovada."),
        )
    if status == 429:
        return HTTPException(
            status_code=503,
            detail=_detail("ml_rate_limited", "O Mercado Livre limitou temporariamente a consulta. Tente novamente."),
        )
    if status == 404:
        return HTTPException(status_code=404, detail=_detail("ml_resource_not_found", default_message))
    return HTTPException(
        status_code=502,
        detail=_detail("ml_provider_error", default_message, provider_status=status or None),
    )


def _request_json(
    client_id: str,
    store_name: str,
    cfg: dict[str, Any],
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 15,
    deadline: float | None = None,
    expected: set[int] | None = None,
    error_message: str = "Falha ao consultar o Mercado Livre.",
) -> tuple[Any, dict[str, Any], bool]:
    expected_statuses = expected or {200}
    request_timeout = max(1, min(int(timeout or 15), 20))
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPException(
                status_code=504,
                detail=_detail("ml_lookup_deadline", "A consulta ao Mercado Livre excedeu o tempo limite total."),
            )
        request_timeout = max(1, min(request_timeout, int(remaining + 0.999)))
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
    except HTTPException:
        raise
    except requests.exceptions.Timeout as exc:
        raise HTTPException(
            status_code=504,
            detail=_detail("ml_timeout", "A consulta ao Mercado Livre excedeu o tempo limite."),
        ) from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=_detail("ml_connection_error", "Nao foi possivel conectar ao Mercado Livre."),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=_detail("ml_provider_error", error_message),
        ) from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status not in expected_statuses:
        raise _provider_error(response, error_message)
    try:
        payload = response.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=_detail("ml_invalid_response", "O Mercado Livre retornou uma resposta invalida."),
        ) from exc
    return payload, cfg, status == 206


def _buscar_ids_por_sku(
    client_id: str,
    store_name: str,
    cfg: dict[str, Any],
    sku: str,
    deadline: float,
) -> tuple[list[str], dict[str, Any], list[str], bool]:
    seller_id = str(cfg.get("user_id") or "").strip()
    item_ids: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    coverage_complete = True
    offset = 0

    while True:
        page_start_offset = offset
        ids_before_page = len(item_ids)
        payload, cfg, partial = _request_json(
            client_id,
            store_name,
            cfg,
            f"{ML_API_BASE}/users/{seller_id}/items/search",
            params={"seller_sku": sku, "offset": offset, "limit": ML_SEARCH_PAGE_SIZE},
            expected={200, 206},
            timeout=20,
            deadline=deadline,
            error_message="Nao foi possivel buscar os anuncios deste SKU no Mercado Livre.",
        )
        if partial and "A busca do Mercado Livre retornou conteudo parcial." not in warnings:
            warnings.append("A busca do Mercado Livre retornou conteudo parcial.")
            coverage_complete = False
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502,
                detail=_detail("ml_invalid_response", "A busca de anuncios retornou um formato invalido."),
            )
        raw_results = payload.get("results") or []
        if not isinstance(raw_results, list):
            raise HTTPException(
                status_code=502,
                detail=_detail("ml_invalid_response", "A lista de anuncios retornou um formato invalido."),
            )
        invalid_results = 0
        for entry in raw_results:
            item_id = _normalizar_item_id(entry.get("id") if isinstance(entry, dict) else entry)
            if item_id and item_id not in seen:
                seen.add(item_id)
                item_ids.append(item_id)
            elif not item_id:
                invalid_results += 1
        if invalid_results:
            warnings.append(f"{invalid_results} resultado(s) da busca vieram sem MLB valido.")
            coverage_complete = False

        paging = payload.get("paging")
        try:
            if not isinstance(paging, dict) or "total" not in paging:
                raise ValueError("paging_total_missing")
            total = max(0, int(paging["total"]))
            page_limit = max(1, int(paging.get("limit") or ML_SEARCH_PAGE_SIZE))
            reported_offset = int(paging.get("offset", page_start_offset))
        except (TypeError, ValueError):
            warnings.append("A busca do Mercado Livre nao informou uma paginacao valida.")
            coverage_complete = False
            break
        if reported_offset != page_start_offset:
            warnings.append("A busca do Mercado Livre retornou uma pagina fora da sequencia solicitada.")
            coverage_complete = False
            break
        if total > ML_MAX_LISTINGS_PER_SKU:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "too_many_listings_for_sku",
                    "Foram encontrados anuncios demais para este SKU; refine o SKU antes de importar.",
                    total=total,
                ),
            )
        received = len(raw_results)
        remaining_before_page = max(0, total - offset)
        offset += received
        if offset >= total:
            if received > remaining_before_page or len(item_ids) < total:
                warnings.append("A busca do Mercado Livre retornou uma paginacao contraditoria.")
                coverage_complete = False
            break
        if received and len(item_ids) == ids_before_page:
            warnings.append("A busca do Mercado Livre repetiu uma pagina sem novos anuncios.")
            coverage_complete = False
            break
        expected_page = min(page_limit, remaining_before_page)
        if received < expected_page:
            warnings.append("A busca do Mercado Livre terminou antes de cobrir todos os anuncios informados.")
            coverage_complete = False
            break

    if not item_ids:
        if not coverage_complete:
            raise HTTPException(
                status_code=502,
                detail=_detail(
                    "ml_search_incomplete",
                    "A busca do Mercado Livre ficou incompleta; nao foi possivel confirmar a ausencia do SKU.",
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=_detail(
                "sku_not_found_in_store",
                "Nenhum anuncio com este SKU foi encontrado na conta Mercado Livre da loja escolhida.",
            ),
        )
    return item_ids, cfg, warnings, coverage_complete


def _buscar_itens(
    client_id: str,
    store_name: str,
    cfg: dict[str, Any],
    item_ids: list[str],
    deadline: float,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    items_by_id: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for start in range(0, len(item_ids), 20):
        chunk = item_ids[start : start + 20]
        payload, cfg, _partial = _request_json(
            client_id,
            store_name,
            cfg,
            f"{ML_API_BASE}/items",
            params={"ids": ",".join(chunk), "include_attributes": "all"},
            timeout=20,
            deadline=deadline,
            error_message="Nao foi possivel detalhar os anuncios deste SKU.",
        )
        if not isinstance(payload, list):
            raise HTTPException(
                status_code=502,
                detail=_detail("ml_invalid_response", "O detalhe dos anuncios retornou um formato invalido."),
            )
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            body = entry.get("body") if isinstance(entry.get("body"), dict) else entry
            code = int(entry.get("code") or 200) if str(entry.get("code") or "200").isdigit() else 0
            if code in {401, 403}:
                raise HTTPException(
                    status_code=401,
                    detail=_detail("ml_reconnect_required", "A autorizacao do Mercado Livre desta loja precisa ser renovada."),
                )
            if code == 429:
                raise HTTPException(
                    status_code=503,
                    detail=_detail("ml_rate_limited", "O Mercado Livre limitou temporariamente a consulta. Tente novamente."),
                )
            if code != 200 or not isinstance(body, dict):
                continue
            item_id = _normalizar_item_id(body.get("id"))
            if item_id:
                items_by_id[item_id] = body
        missing = [item_id for item_id in chunk if item_id not in items_by_id]
        if missing:
            warnings.append(f"{len(missing)} anuncio(s) nao puderam ser detalhados e foram ignorados.")
    return [items_by_id[item_id] for item_id in item_ids if item_id in items_by_id], cfg, warnings


def _completar_variacoes(
    client_id: str,
    store_name: str,
    cfg: dict[str, Any],
    item: dict[str, Any],
    deadline: float,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    item_id = _normalizar_item_id(item.get("id"))
    if not item_id or not isinstance(item.get("variations"), list) or not item.get("variations"):
        return item, cfg, ""
    try:
        payload, cfg, _partial = _request_json(
            client_id,
            store_name,
            cfg,
            f"{ML_API_BASE}/items/{item_id}/variations",
            params={"include_attributes": "all"},
            timeout=15,
            deadline=deadline,
            error_message="Nao foi possivel validar as variacoes de um anuncio.",
        )
    except HTTPException as exc:
        if exc.status_code in {401, 503, 504}:
            raise
        return item, cfg, "As variacoes de um anuncio nao puderam ser validadas e podem estar incompletas."
    if isinstance(payload, dict):
        detailed = payload.get("variations") or payload.get("results") or []
    else:
        detailed = payload
    if not isinstance(detailed, list):
        return item, cfg, "As variacoes de um anuncio vieram em formato invalido e ele foi ignorado."
    details_by_id = {
        str(variation.get("id") or "").strip(): variation
        for variation in detailed
        if isinstance(variation, dict) and str(variation.get("id") or "").strip()
    }
    if not details_by_id:
        return item, cfg, "As variacoes de um anuncio nao retornaram detalhes e podem estar incompletas."
    merged_variations: list[Any] = []
    for variation in item.get("variations") or []:
        if not isinstance(variation, dict):
            merged_variations.append(variation)
            continue
        detail = details_by_id.get(str(variation.get("id") or "").strip()) or {}
        merged = dict(variation)
        for key, value in detail.items():
            if value not in (None, "", []):
                merged[key] = value
        merged_variations.append(merged)
    enriched = dict(item)
    enriched["variations"] = merged_variations
    expected_ids = {
        str(variation.get("id") or "").strip()
        for variation in item.get("variations") or []
        if isinstance(variation, dict) and str(variation.get("id") or "").strip()
    }
    missing_ids = expected_ids.difference(details_by_id)
    unexpected_ids = set(details_by_id).difference(expected_ids)
    unidentified = sum(
        1
        for variation in item.get("variations") or []
        if isinstance(variation, dict) and not str(variation.get("id") or "").strip()
    )
    warning_parts: list[str] = []
    if missing_ids:
        warning_parts.append(f"{len(missing_ids)} variacao(oes) nao retornaram detalhes.")
    if unexpected_ids:
        warning_parts.append(f"{len(unexpected_ids)} variacao(oes) detalhadas nao constavam no anuncio.")
    if unidentified:
        warning_parts.append(f"{unidentified} variacao(oes) vieram sem identidade.")
    warning = " ".join(warning_parts)
    return enriched, cfg, warning


def _attribute_values(entity: Any, attribute_ids: set[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for attribute in _atributos(entity):
        attribute_id = str(attribute.get("id") or "").strip().upper()
        if attribute_id not in attribute_ids:
            continue
        for value in _valores_atributo(attribute):
            candidates = re.split(r"\s*,\s*", value) if attribute_id in GTIN_ATTRIBUTE_IDS else [value]
            for candidate in candidates:
                candidate = candidate.strip()
                key = candidate.casefold()
                if candidate and key not in seen:
                    seen.add(key)
                    values.append(candidate)
    return values


def _declares_attribute(entity: Any, attribute_ids: set[str]) -> bool:
    for attribute in _atributos(entity):
        attribute_id = str(attribute.get("id") or "").strip().upper()
        if attribute_id in attribute_ids:
            return True
    return False


def _first_attribute(item: dict[str, Any], variation: Any, attribute_id: str) -> str:
    requested = {attribute_id}
    for entity in (variation, item):
        values = _attribute_values(entity, requested)
        if values:
            return values[0]
    return ""


def _listing_picture(item: dict[str, Any], variation: Any) -> str:
    pictures = [picture for picture in item.get("pictures") or [] if isinstance(picture, dict)]
    by_id = {
        str(picture.get("id") or "").strip(): picture
        for picture in pictures
        if str(picture.get("id") or "").strip()
    }
    if isinstance(variation, dict):
        for picture_id in variation.get("picture_ids") or []:
            picture = by_id.get(str(picture_id or "").strip())
            if picture:
                url = str(picture.get("secure_url") or picture.get("url") or "").strip()
                if url:
                    return url
    for picture in pictures:
        url = str(picture.get("secure_url") or picture.get("url") or "").strip()
        if url:
            return url
    return str(item.get("thumbnail") or "").strip()


def _photo_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
        port = parsed.port
    except Exception:
        return False
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    return bool(
        parsed.scheme == "https"
        and port in (None, 443)
        and hostname in ML_PHOTO_HOSTS
        and not parsed.username
        and not parsed.password
    )


def _sniff_image_mime(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _download_photo_data_url(url: str, item_id: str, deadline: float | None = None) -> dict[str, str]:
    if not _photo_url_allowed(url):
        raise ValueError("untrusted_photo_url")
    remaining = 15.0 if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("photo_deadline_exceeded")
    response = requests.get(
        url,
        timeout=(min(3.05, remaining), min(12, max(1, remaining))),
        stream=True,
        allow_redirects=False,
        headers={"Accept": "image/webp,image/png,image/jpeg,image/gif"},
    )
    try:
        if int(response.status_code or 0) != 200:
            raise ValueError("photo_http_error")
        try:
            declared_size = int(response.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > ML_PHOTO_MAX_BYTES:
            raise ValueError("photo_too_large")
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if deadline is not None and time.monotonic() >= deadline:
                raise ValueError("photo_deadline_exceeded")
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > ML_PHOTO_MAX_BYTES:
                raise ValueError("photo_too_large")
        mime = _sniff_image_mime(bytes(content))
        if not mime:
            raise ValueError("invalid_photo_content")
        declared_mime = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if declared_mime and declared_mime != mime:
            raise ValueError("invalid_photo_content_type")
        extension = ML_PHOTO_MIME_EXTENSIONS[mime]
        safe_item_id = _normalizar_item_id(item_id) or "mercado-livre"
        return {
            "url": url,
            "data_url": f"data:{mime};base64,{base64.b64encode(bytes(content)).decode('ascii')}",
            "filename": f"{safe_item_id}.{extension}",
        }
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _primary_sort_key(entry: dict[str, Any], preferred_item_id: str) -> tuple[int, int, str]:
    item = entry["item"]
    item_id = _normalizar_item_id(item.get("id"))
    status = str(item.get("status") or "").strip().lower()
    status_rank = {"active": 0, "paused": 1, "under_review": 2, "closed": 3}.get(status, 4)
    return (0 if preferred_item_id and item_id == preferred_item_id else 1, status_rank, item_id)


def consultar_produto_mercado_livre(
    client_id: str,
    store_id: str,
    sku: str,
    *,
    mlb_principal: str = "",
) -> dict[str, Any]:
    requested_sku = str(sku or "").strip()
    if not requested_sku or not any(character.isalnum() for character in requested_sku):
        raise HTTPException(status_code=400, detail=_detail("invalid_sku", "Informe um SKU valido para consultar."))
    if len(requested_sku) > 120:
        raise HTTPException(status_code=400, detail=_detail("invalid_sku", "O SKU informado e muito longo."))

    deadline = time.monotonic() + ML_LOOKUP_DEADLINE_SECONDS
    store, cfg = _resolver_loja_ml_exata(client_id, store_id)
    item_ids, cfg, warnings, coverage_complete = _buscar_ids_por_sku(
        client_id, store["nome"], cfg, requested_sku, deadline
    )
    items, cfg, item_warnings = _buscar_itens(client_id, store["nome"], cfg, item_ids, deadline)
    warnings.extend(item_warnings)
    if item_warnings:
        coverage_complete = False

    seller_id = str(cfg.get("user_id") or "").strip()
    verified: list[dict[str, Any]] = []
    rejected_owner = 0
    rejected_sku = 0
    ambiguous_sku = 0
    unverified_variations = 0
    for raw_item in items:
        item = dict(raw_item)
        if str(item.get("seller_id") or "").strip() != seller_id:
            rejected_owner += 1
            continue
        variation_warning = ""
        if item.get("variations"):
            item, cfg, variation_warning = _completar_variacoes(
                client_id, store["nome"], cfg, item, deadline
            )
        if variation_warning:
            warnings.append(variation_warning)
            coverage_complete = False
            unverified_variations += 1
            continue
        match = _match_exact_sku(item, requested_sku)
        if match.get("ambiguous"):
            ambiguous_sku += 1
            coverage_complete = False
            continue
        if not match:
            rejected_sku += 1
            continue
        verified.append({"item": item, "match": match})

    if rejected_owner:
        coverage_complete = False
        warnings.append(
            f"{rejected_owner} anuncio(s) foram descartados porque a API nao confirmou a conta da loja escolhida."
        )
    if rejected_sku:
        warnings.append(
            f"{rejected_sku} anuncio(s) foram descartados porque os detalhes nao confirmaram o SKU exato."
        )
    if ambiguous_sku:
        warnings.append(f"{ambiguous_sku} anuncio(s) foram descartados porque o SKU aparece em mais de uma identidade.")
    if unverified_variations:
        warnings.append(f"{unverified_variations} anuncio(s) foram descartados porque as variacoes ficaram incompletas.")
    if not verified:
        if rejected_owner:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "listing_ownership_unconfirmed",
                    "Os anuncios retornados nao puderam ser confirmados como pertencentes a loja escolhida.",
                ),
            )
        if ambiguous_sku:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "sku_identity_ambiguous",
                    "Mais de uma identidade do anuncio (pai ou variacao) usa este SKU; revise no Mercado Livre.",
                ),
            )
        if unverified_variations:
            raise HTTPException(
                status_code=502,
                detail=_detail(
                    "variation_identity_incomplete",
                    "Nao foi possivel confirmar as variacoes retornadas para este SKU. Tente novamente.",
                ),
            )
        if not items:
            raise HTTPException(
                status_code=502,
                detail=_detail(
                    "listing_details_incomplete",
                    "O Mercado Livre encontrou anuncios, mas nao retornou detalhes suficientes para valida-los.",
                ),
            )
        raise HTTPException(
            status_code=409,
            detail=_detail(
                "exact_sku_not_confirmed",
                "A busca retornou anuncios, mas nenhum confirmou o SKU exato nesta loja.",
            ),
        )

    preferred = _normalizar_item_id(mlb_principal)
    verified.sort(key=lambda entry: _primary_sort_key(entry, preferred))
    primary = verified[0]
    primary_item = primary["item"]
    primary_match = primary["match"]
    primary_variation = primary_match.get("variation")
    primary_id = _normalizar_item_id(primary_item.get("id"))

    if len(verified) > 1:
        warnings.append(
            f"Foram encontrados {len(verified)} anuncios para o SKU; campos unicos vieram do MLB principal {primary_id}."
        )

    category_id = str(primary_item.get("category_id") or "").strip().upper()
    category_name = ""
    if category_id:
        try:
            category_payload, cfg, _partial = _request_json(
                client_id,
                store["nome"],
                cfg,
                f"{ML_API_BASE}/categories/{category_id}",
                timeout=10,
                deadline=deadline,
                error_message="Nao foi possivel consultar o nome da categoria.",
            )
            if isinstance(category_payload, dict):
                category_name = str(category_payload.get("name") or "").strip()
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "ml_lookup_deadline":
                raise
            warnings.append("O ID da categoria foi obtido, mas o nome da categoria nao pode ser consultado.")

    description = ""
    try:
        description_payload, cfg, _partial = _request_json(
            client_id,
            store["nome"],
            cfg,
            f"{ML_API_BASE}/items/{primary_id}/description",
            timeout=15,
            deadline=deadline,
            error_message="Nao foi possivel consultar a descricao do anuncio principal.",
        )
        if isinstance(description_payload, dict):
            raw_description = str(
                description_payload.get("plain_text") or description_payload.get("text") or ""
            ).strip()
            description = raw_description[:ML_DESCRIPTION_LIMIT]
            if len(raw_description) > ML_DESCRIPTION_LIMIT:
                warnings.append("A descricao do anuncio excedeu o limite do Cadastro e foi reduzida para revisao.")
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "ml_lookup_deadline":
            raise
        warnings.append(
            "O anuncio principal nao possui descricao disponivel."
            if exc.status_code == 404
            else "A descricao do anuncio principal nao pode ser consultada."
        )

    brands: list[str] = []
    models: list[str] = []
    categories: list[str] = []
    gtins: list[str] = []
    gtin_seen: set[str] = set()
    listings: list[dict[str, Any]] = []
    for entry in verified:
        item = entry["item"]
        match = entry["match"]
        variation = match.get("variation")
        item_id = _normalizar_item_id(item.get("id"))
        brand = _first_attribute(item, variation, "BRAND")
        model = _first_attribute(item, variation, "MODEL")
        item_category = str(item.get("category_id") or "").strip().upper()
        if brand:
            brands.append(brand)
        if model:
            models.append(model)
        if item_category:
            categories.append(item_category)
        variation_gtins = _attribute_values(variation, GTIN_ATTRIBUTE_IDS)
        gtin_values = (
            variation_gtins
            if _declares_attribute(variation, GTIN_ATTRIBUTE_IDS)
            else _attribute_values(item, GTIN_ATTRIBUTE_IDS)
        )
        for value in gtin_values:
            key = re.sub(r"\s+", "", value).casefold()
            if key and key not in gtin_seen:
                gtin_seen.add(key)
                gtins.append(value)
        listings.append(
            {
                "mlb": item_id,
                "titulo": str(item.get("title") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "categoria_id": item_category,
                "marca": brand,
                "modelo": model,
                "variation_id": str(match.get("variation_id") or "").strip(),
                "matched_sku": str(match.get("matched_sku") or "").strip(),
                "matched_by": str(match.get("matched_by") or "").strip(),
            }
        )

    def distinct(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    category_conflicts = distinct(categories)
    brand_conflicts = distinct(brands)
    model_conflicts = distinct(models)
    conflicts: dict[str, list[str]] = {}
    if len(category_conflicts) > 1:
        conflicts["categoria_id_mlb"] = category_conflicts
        warnings.append("Os anuncios do SKU usam categorias diferentes; foi aplicada a categoria do MLB principal.")
    if len(brand_conflicts) > 1:
        conflicts["marca"] = brand_conflicts
        warnings.append("Os anuncios do SKU informam marcas diferentes; foi aplicada a marca do MLB principal.")
    if len(model_conflicts) > 1:
        conflicts["modelo"] = model_conflicts
        warnings.append("Os anuncios do SKU informam modelos diferentes; foi aplicado o modelo do MLB principal.")

    raw_photo_url = _listing_picture(primary_item, primary_variation)
    photo_url = raw_photo_url if _photo_url_allowed(raw_photo_url) else ""
    photo: dict[str, str] = {"url": "", "data_url": "", "filename": ""}
    if raw_photo_url and not photo_url:
        warnings.append("A foto retornada pelo anuncio nao usa um endereco confiavel do Mercado Livre.")
    elif photo_url:
        try:
            photo = _download_photo_data_url(photo_url, primary_id, deadline)
        except (requests.RequestException, ValueError, OSError):
            warnings.append("A URL da foto foi localizada, mas a imagem nao pode ser preparada para salvar.")
            photo_url = ""

    mlb_ids = [listing["mlb"] for listing in listings]
    titles = [listing["titulo"] or "(sem titulo informado)" for listing in listings]
    fields = {
        "foto": photo_url,
        "mlb_principal": primary_id,
        "mlb_ids": "|".join(mlb_ids),
        "qtd_anuncios_mlb": str(len(listings)),
        "titulo_ml": str(primary_item.get("title") or "").strip(),
        "titulos_anuncios_mlb": " || ".join(titles),
        "categoria": category_name,
        "categoria_id_mlb": category_id,
        "marca": _first_attribute(primary_item, primary_variation, "BRAND"),
        "modelo": _first_attribute(primary_item, primary_variation, "MODEL"),
        "gtins_mlb": "|".join(gtins),
        "descricao": description,
    }
    return {
        "success": True,
        "read_only": True,
        "found": True,
        "coverage_complete": coverage_complete,
        "store_id": store["store_id"],
        "loja_sync": store["nome"],
        "seller_id": seller_id,
        "sku": requested_sku,
        "campos": fields,
        "foto": photo,
        "anuncios": listings,
        "matches": listings,
        "avisos": list(dict.fromkeys(warnings)),
        "warnings": list(dict.fromkeys(warnings)),
        "conflicts": conflicts,
        "consultado_em_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "fonte": "mercado_livre_api",
    }


def consultar_produto_mercado_livre_loja(
    store_id: str,
    sku: str = Query(..., min_length=1, max_length=120),
    mlb_principal: str = Query(default="", max_length=32),
    client_id: str = Depends(get_tenant_id),
):
    return consultar_produto_mercado_livre(
        client_id,
        store_id,
        sku,
        mlb_principal=mlb_principal,
    )


__all__ = [
    "configure_cadastro_mercadolivre_runtime",
    "consultar_produto_mercado_livre",
    "consultar_produto_mercado_livre_loja",
]
