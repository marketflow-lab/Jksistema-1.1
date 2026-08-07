"""Marketplace tool domain extracted from the legacy service."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from fastapi import HTTPException

from . import runtime as _runtime
from . import analytics as _analytics
from . import client as _client
from . import items as _items
from . import listing_search as _listing_search

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ListingQuery:
    client_id: str
    message: str
    store: str
    sku: str
    item_ids: list[str]
    status: str
    offset: int
    limit: int
    details_requested: bool
    commercial_requested: bool
    deadline: float
    arguments: dict[str, Any]

def list_ads(
    client_id: str,
    loja: str,
    cfg: dict,
    status_item: str,
    limite: int = 10,
    offset: int = 0,
    sku: str = "",
) -> tuple[list[dict], dict]:
    """Wrapper legado; agora pagina em blocos de 20 e continua somente read-only."""
    ids, cfg, _paging, failure = _listing_search.search_listing_ids(
        client_id,
        loja,
        cfg,
        status_item=status_item,
        offset=_client._ia_ml_int(offset, 0, 0, 1_000_000),
        limite=_client._ia_ml_int(limite, 10, 1, 100),
        sku=str(sku or "").strip(),
    )
    if failure or not ids:
        return [], cfg
    items, cfg, _failure = _listing_search.fetch_listing_items(client_id, loja, cfg, ids)
    return items, cfg

def _request_values(client_id, mensagem, produto_tool, limite, incluir_descricao, status, sku, item_id, offset, incluir_detalhes, mlb, incluir_comercial):
    item_ids = _items._ia_ml_normalizar_item_ids(item_id, mlb) or _items._ia_ml_normalizar_item_ids(mensagem)
    sku_value = str(sku or "").strip()
    if not sku_value and not item_ids:
        sku_value = str(_runtime.resolve_sku(client_id, mensagem, produto_tool) or "").strip()
        reference = _runtime.extract_product_reference(mensagem)
        if not sku_value and isinstance(reference, dict) and reference.get("sku"):
            sku_value = str(reference.get("sku") or "").strip()
    if _items._ia_ml_sku_parece_data(sku_value):
        sku_value = ""
    text = _runtime.normalize_text(mensagem)
    list_without_reference = bool(not item_ids and not sku_value and any(
        key in text for key in ("ANUNCIOS", "ANUNCIO", "ITENS ATIVOS", "ITENS PAUSADOS", "LISTE", "LISTAR")
    ))
    if not item_ids and not sku_value and not list_without_reference:
        return None
    status_value = str(status or "").strip().lower() or ("paused" if "PAUSAD" in text else "active")
    if status_value not in _client.ML_IA_LISTING_STATUSES:
        status_value = "active"
    return {
        "item_ids": item_ids, "sku": sku_value, "status": status_value,
        "offset": _client._ia_ml_int(offset, 0, 0, 1_000_000), "limit": _client._ia_ml_int(limite, 8, 1, 100),
        "details": bool(incluir_descricao or incluir_detalhes or _items.needs_description(mensagem)),
        "commercial": _listing_search.commercial_requested(mensagem, incluir_comercial),
        "mode": "lista" if list_without_reference else ("item_id" if item_ids else "sku"),
    }


def _base_result(context: _ListingQuery) -> dict:
    result = _client._ia_ml_base_result()
    result.update({
        "found": False, "store": context.store, "coverage": "mercado_livre_api_items", "matches": [],
        "chart_data": _analytics._ia_ml_listing_chart_data([], coverage_complete=False),
        "paging": {"offset": context.offset, "limit": context.limit, "returned": 0, "total": 0,
                   "next_offset": None, "has_more": False}, "truncated": False,
        "sources": [
            {"provider": "mercado_livre", "resource": "users/{seller_id}/items/search", "method": "GET", "store": context.store},
            {"provider": "mercado_livre", "resource": "items multiget", "method": "GET", "store": context.store},
        ],
    })
    return result


def _load_items(context: _ListingQuery, cfg: dict, result: dict) -> dict:
    if context.item_ids:
        expected_seller = str((cfg or {}).get("user_id") or "").strip()
        if not expected_seller:
            result.update({
                "error": "seller_id_missing",
                "message": "A conta Mercado Livre nao informou o seller id necessario para validar o MLB nesta loja.",
                "ownership_validation": {"complete": False, "expected_seller_id": "", "accepted_ids": [],
                                         "rejected_ids": [], "unverified_ids": list(context.item_ids)},
            })
            return {"response": {"function": "get_mercado_livre_listing", "arguments": context.arguments, "result": result}}
        ids = context.item_ids[context.offset : context.offset + context.limit]
        total = len(context.item_ids)
        next_offset = context.offset + len(ids)
        paging = {"offset": context.offset, "limit": context.limit, "returned": len(ids), "total": total,
                  "next_offset": next_offset if next_offset < total else None, "has_more": next_offset < total,
                  "pages_fetched": 0}
        failure = None
    else:
        ids, cfg, paging, failure = _listing_search.search_listing_ids(
            context.client_id, context.store, cfg, status_item=context.status, offset=context.offset,
            limite=context.limit, sku=context.sku, deadline=context.deadline,
        )
    if failure:
        result.update({"error": failure["code"], "message": failure["message"],
                       "reconnect_required": bool(failure.get("reconnect_required")), "paging": paging})
        return {"response": {"function": "get_mercado_livre_listing", "arguments": context.arguments, "result": result}}
    items, cfg, detail_failure = _listing_search.fetch_listing_items(
        context.client_id, context.store, cfg, ids, deadline=context.deadline,
    )
    if detail_failure:
        result.update({"error": detail_failure["code"], "message": detail_failure["message"],
                       "reconnect_required": bool(detail_failure.get("reconnect_required"))})
    return {"cfg": cfg, "items": items, "paging": paging, "detail_failure": detail_failure}


def _validate_ownership(context: _ListingQuery, cfg: dict, result: dict, items: list[dict]) -> list[dict]:
    if not context.item_ids or not items:
        return items
    expected = str((cfg or {}).get("user_id") or "").strip()
    accepted, rejected, unverified = [], [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        current_id = str(item.get("id") or "").strip()
        seller = str(item.get("seller_id") or "").strip()
        if not seller:
            unverified.append(current_id)
        elif seller != expected:
            rejected.append(current_id)
        else:
            accepted.append(item)
    result["ownership_validation"] = {
        "complete": not bool(unverified), "expected_seller_id": expected,
        "accepted_ids": [str(item.get("id") or "").strip() for item in accepted],
        "rejected_ids": rejected, "unverified_ids": unverified,
    }
    if rejected:
        result["warnings"].append(f"{len(rejected)} MLB(s) foram descartados porque pertencem a outra conta do Mercado Livre.")
    if unverified:
        result["warnings"].append(f"{len(unverified)} MLB(s) foram descartados porque a API nao confirmou o seller da loja escolhida.")
    if not accepted and (rejected or unverified) and not result.get("error"):
        result.update({"error": "listing_not_in_store",
                       "message": "O MLB informado nao pertence, ou nao pode ser confirmado, na loja Mercado Livre escolhida."})
    return accepted


def _validate_sku(context: _ListingQuery, cfg: dict, result: dict, items: list[dict]) -> tuple[list[dict], dict, dict]:
    if not context.sku or not items:
        return items, cfg, {}
    items, cfg, validation = _listing_search.validate_sku_matches(
        context.client_id, context.store, cfg, items, context.sku, deadline=context.deadline,
    )
    result["sku_validation"] = validation
    if validation.get("variation_requests"):
        result["sources"].append({"provider": "mercado_livre", "resource": "items/{item_id}/variations?include_attributes=all",
                                  "method": "GET", "store": context.store})
    rejected = len(validation.get("rejected_ids") or [])
    if rejected:
        result["warnings"].append(
            f"{rejected} anuncio(s) devolvido(s) pela busca foram descartados porque os detalhes nao confirmaram o SKU {context.sku}."
        )
    if validation.get("unverified_ids"):
        result["warnings"].append("Alguns anuncios foram omitidos porque a API nao permitiu validar o SKU de suas variacoes.")
    return items, cfg, validation


def _build_matches(context: _ListingQuery, cfg: dict, result: dict, items: list[dict]) -> tuple[list[dict], dict, bool]:
    matches, commercial_partial = [], False
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        description, details = "", None
        if context.details_requested and index < 10:
            description, cfg = _items.get_item_description(
                context.client_id, context.store, cfg, str(item.get("id") or ""),
                timeout=_client.remaining_timeout(context.deadline, 15),
            )
            details = _listing_search.details_from_item(item)
        if context.commercial_requested and index < 5:
            details = details or _listing_search.details_from_item(item)
            commercial, cfg, sources = _listing_search.commercial_details(
                context.client_id, context.store, cfg, item, deadline=context.deadline,
            )
            details["commercial"] = commercial
            commercial_partial = commercial_partial or commercial.get("coverage_complete") is not True
            result["sources"].extend(sources)
        matches.append(_items.summarize_item(item, context.store, descricao=description,
                                             detalhes=details, requested_sku=context.sku))
    if context.details_requested and len(items) > 10:
        result["warnings"].append("Detalhes e descricoes foram limitados aos primeiros 10 anuncios.")
    if context.commercial_requested and len(items) > 5:
        result["warnings"].append("Detalhes comerciais foram limitados aos primeiros 5 anuncios para preservar o limite da API.")
    if context.details_requested:
        result["details_coverage"] = {"limit": 10, "description": "items/{id}/description", "shipping": "item_resource",
                                      "fees": "item_resource_when_available", "promotions": "price_fields_only"}
        result["warnings"].append(
            "Tarifas completas e campanhas promocionais nao foram consultadas em endpoints adicionais; campos ausentes permanecem indisponiveis."
        )
    return matches, cfg, commercial_partial


def _finalize(context: _ListingQuery, result: dict, matches: list[dict], paging: dict,
              detail_failure: dict | None, commercial_partial: bool, sku_validation: dict) -> dict:
    ownership = result.get("ownership_validation")
    partial = bool(paging.get("partial_response") or detail_failure or commercial_partial
                   or (sku_validation and not sku_validation.get("complete"))
                   or (isinstance(ownership, dict) and ownership.get("complete") is not True))
    if partial:
        missing = ", ".join(paging.get("partial_content") or [])
        suffix = f" Campos omitidos: {missing}." if missing else ""
        result["warnings"].append(f"O Mercado Livre retornou resposta parcial de anuncios (HTTP 206).{suffix}")
    result["warnings"].append("sold_quantity representa o total acumulado do anuncio, nao as vendas do periodo.")
    complete = not bool(paging.get("has_more") or partial)
    returned_paging = {**paging, "returned": len(matches)}
    result.update({
        "found": bool(matches), "matches": matches, "paging": returned_paging,
        "chart_data": _analytics._ia_ml_listing_chart_data(matches, coverage_complete=complete, paging=returned_paging),
        "truncated": bool(paging.get("has_more") or partial), "partial_response": partial, "coverage_complete": complete,
    })
    if not matches and not result.get("error"):
        warning = f"Nenhum anuncio retornado pela busca confirmou exatamente o SKU {context.sku} nos detalhes do item ou de suas variacoes." if sku_validation and sku_validation.get("search_results") else "A API do Mercado Livre retornou zero anuncios para os filtros informados."
        result["warnings"].append(warning)
    return {"function": "get_mercado_livre_listing", "arguments": context.arguments, "result": result}


def _execute(context: _ListingQuery, result: dict) -> dict:
    cfg = _runtime.ml_config(context.client_id, context.store)
    loaded = _load_items(context, cfg, result)
    if loaded.get("response"):
        return loaded["response"]
    items = _validate_ownership(context, loaded["cfg"], result, loaded["items"])
    items, cfg, sku_validation = _validate_sku(context, loaded["cfg"], result, items)
    matches, _cfg, commercial_partial = _build_matches(context, cfg, result, items)
    return _finalize(context, result, matches, loaded["paging"], loaded["detail_failure"], commercial_partial, sku_validation)


def query(
    client_id: str, mensagem: str, loja: Optional[str] = None, produto_tool: Optional[dict] = None,
    limite: int = 8, incluir_descricao: bool = False, status: Optional[str] = None,
    sku: Optional[str] = None, item_id: Optional[str] = None, offset: int = 0,
    incluir_detalhes: bool = False, force_refresh: bool = False, mlb: Optional[str] = None,
    query_deadline: Optional[float] = None, incluir_comercial: bool = False,
) -> Optional[dict]:
    del force_refresh
    try:
        values = _request_values(client_id, mensagem, produto_tool, limite, incluir_descricao, status, sku,
                                 item_id, offset, incluir_detalhes, mlb, incluir_comercial)
        if values is None:
            return None
        arguments = {
            "sku": values["sku"], "item_ids": values["item_ids"], "loja": str(loja or "").strip(),
            "status": values["status"], "offset": values["offset"], "limit": values["limit"],
            "include_details": values["details"], "include_commercial": values["commercial"], "modo": values["mode"],
        }
        store, failure = _client.resolve_store(client_id, loja)
        if not store:
            return _client._ia_ml_store_failure("get_mercado_livre_listing", arguments, failure)
        deadline = time.monotonic() + _client.ML_IA_QUERY_TIMEOUT_SECONDS
        if query_deadline is not None:
            try:
                deadline = min(deadline, float(query_deadline))
            except (TypeError, ValueError):
                pass
        context = _ListingQuery(client_id, mensagem, store, values["sku"], values["item_ids"], values["status"],
                                values["offset"], values["limit"], values["details"], values["commercial"],
                                deadline, arguments)
        result = _base_result(context)
        try:
            return _execute(context, result)
        except requests.exceptions.Timeout:
            result.update({"error": "timeout", "message": "A consulta completa do Mercado Livre excedeu 60 segundos."})
        except HTTPException as exc:
            reconnect = int(getattr(exc, "status_code", 0) or 0) == 401
            result.update({"error": "reconnect_required" if reconnect else "integration_error",
                           "message": str(getattr(exc, "detail", None) or exc)[:240], "reconnect_required": reconnect})
        except Exception as exc:
            logger.warning("[IA TOOLS] Falha ao consultar Mercado Livre para IA (%s): %s", store, exc)
            result.update({"error": "provider_unavailable", "message": "Nao foi possivel consultar os anuncios do Mercado Livre agora."})
        return {"function": "get_mercado_livre_listing", "arguments": arguments, "result": result}
    except Exception as exc:
        logger.warning("[IA TOOLS] Falha geral ao consultar Mercado Livre: %s", exc)
        return None
