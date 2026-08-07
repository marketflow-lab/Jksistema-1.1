"""Public-question listing and scan workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id

_ml_api_request = runtime_adapter("_ml_api_request")
_ml_buscar_itens_batch = runtime_adapter("_ml_buscar_itens_batch")
_ml_parse_error_detail = runtime_adapter("_ml_parse_error_detail")
_ml_perguntas_anexar_historico_comprador = runtime_adapter("_ml_perguntas_anexar_historico_comprador")
_ml_perguntas_buscar_usuarios = runtime_adapter("_ml_perguntas_buscar_usuarios")
_ml_perguntas_completar_skus_itens = runtime_adapter("_ml_perguntas_completar_skus_itens")
_ml_perguntas_normalizar = runtime_adapter("_ml_perguntas_normalizar")
_ml_perguntas_resumir_status = runtime_adapter("_ml_perguntas_resumir_status")
_ml_perguntas_tempo_resposta = runtime_adapter("_ml_perguntas_tempo_resposta")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
logger = runtime_adapter("logger")


@dataclass
class _QuestionCollection:
    cfg: dict
    questions: list[dict] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    total: int = 0
    filters: dict = field(default_factory=dict)
    pages: int = 0
    next_offset: int = 0
    interrupted: bool = False
    scroll_id: str = ""
    scroll_seen: set[str] = field(default_factory=set)
    scan_failed: bool = False
    using_scan: bool = False


def _question_collection_append(collection: _QuestionCollection, batch: Any) -> list[dict]:
    batch = batch if isinstance(batch, list) else []
    for question in batch:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("id") or "").strip()
        key = question_id or json.dumps(question, sort_keys=True, default=str)
        if key in collection.seen:
            continue
        collection.seen.add(key)
        collection.questions.append(question)
    return batch


def _question_collection_scan(
    collection: _QuestionCollection,
    *,
    client_id: str,
    store: str,
    seller_id: str,
    url: str,
    limit: int,
    max_pages: int,
    timeout: int,
    status_filter: str,
) -> None:
    collection.using_scan = True
    while True:
        if collection.scroll_id:
            if collection.scroll_id in collection.scroll_seen:
                collection.interrupted = True
                break
            collection.scroll_seen.add(collection.scroll_id)
            params = {
                "search_type": "scan", "scroll_id": collection.scroll_id, "limit": limit,
            }
        else:
            params = {
                "seller_id": seller_id, "api_version": 4,
                "search_type": "scan", "limit": limit,
            }
        response, collection.cfg = _ml_api_request(
            client_id, store, collection.cfg, "GET", url, params=params, timeout=timeout
        )
        if response.status_code != 200:
            detail = _ml_parse_error_detail(response, "Erro ao buscar perguntas do Mercado Livre")
            if response.status_code == 400 and "invalid client parameter" in str(detail).lower():
                collection.questions.clear()
                collection.seen.clear()
                collection.total = 0
                collection.filters = {}
                collection.pages = 0
                collection.interrupted = False
                collection.scan_failed = True
                collection.using_scan = False
                return
            raise HTTPException(status_code=response.status_code, detail=detail)
        data = response.json() or {}
        batch = _question_collection_append(
            collection, data.get("questions") or data.get("results") or []
        )
        try:
            remote_total = data.get("total") or data.get("paging", {}).get("total")
            collection.total = max(collection.total, int(remote_total or len(collection.questions) or 0))
        except Exception:
            collection.total = max(collection.total, len(collection.questions))
        collection.filters = data.get("filters") or collection.filters
        collection.pages += 1
        collection.scroll_id = str(data.get("scroll_id") or "").strip()
        if not batch or not collection.scroll_id:
            break
        if collection.pages >= max_pages:
            collection.interrupted = True
            break
    if status_filter:
        collection.questions = [
            question for question in collection.questions
            if str(question.get("status") or "").strip().upper() == status_filter
        ]
        collection.total = len(collection.questions)


def _question_collection_offset_all(
    collection: _QuestionCollection,
    *,
    client_id: str,
    store: str,
    seller_id: str,
    url: str,
    limit: int,
    max_pages: int,
    timeout: int,
    status_filter: str,
) -> None:
    while True:
        if collection.next_offset > 1000:
            collection.interrupted = True
            break
        call_limit = min(limit, 1000 - collection.next_offset)
        if call_limit <= 0:
            collection.interrupted = True
            break
        params = {
            "seller_id": seller_id, "api_version": 4,
            "offset": collection.next_offset, "limit": call_limit,
            "sort_fields": "date_created", "sort_types": "DESC",
        }
        if status_filter:
            params["status"] = status_filter
        response, collection.cfg = _ml_api_request(
            client_id, store, collection.cfg, "GET", url, params=params, timeout=timeout
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=_ml_parse_error_detail(response, "Erro ao buscar perguntas do Mercado Livre"),
            )
        data = response.json() or {}
        batch = _question_collection_append(
            collection, data.get("questions") or data.get("results") or []
        )
        collection.total = int(
            data.get("total") or data.get("paging", {}).get("total")
            or len(collection.questions) or 0
        )
        collection.filters = data.get("filters") or collection.filters
        collection.pages += 1
        collection.next_offset += call_limit
        if not batch or collection.next_offset >= collection.total:
            break
        if collection.next_offset >= 1000:
            collection.interrupted = collection.next_offset < collection.total
            break
        if collection.pages >= max_pages:
            collection.interrupted = True
            break


def _question_collection_offset_one(
    collection: _QuestionCollection,
    *, client_id: str, store: str, seller_id: str, url: str,
    limit: int, timeout: int, status_filter: str,
) -> None:
    if collection.next_offset > 1000:
        raise HTTPException(
            status_code=400,
            detail="O Mercado Livre permite offset no \u00c3\u00a1ximo at\u00c3\u00a9 1000. Use carregar_todas=true para buscar com scan.",
        )
    collection.next_offset = min(collection.next_offset, 1000)
    params = {
        "seller_id": seller_id, "api_version": 4, "offset": collection.next_offset,
        "limit": limit, "sort_fields": "date_created", "sort_types": "DESC",
    }
    if status_filter:
        params["status"] = status_filter
    response, collection.cfg = _ml_api_request(
        client_id, store, collection.cfg, "GET", url, params=params, timeout=timeout
    )
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=_ml_parse_error_detail(response, "Erro ao buscar perguntas do Mercado Livre"),
        )
    data = response.json() or {}
    batch = data.get("questions") or data.get("results") or []
    if not isinstance(batch, list):
        batch = []
    collection.questions.extend(item for item in batch if isinstance(item, dict))
    collection.total = int(
        data.get("total") or data.get("paging", {}).get("total")
        or len(collection.questions) or 0
    )
    collection.filters = data.get("filters") or {}
    collection.pages = 1
    collection.next_offset += limit
    collection.interrupted = collection.next_offset < collection.total and collection.next_offset > 1000


def _question_collection_response(
    collection: _QuestionCollection,
    *, client_id: str, store: str, seller_id: str, offset: int, limit: int,
    load_all: bool, fast_summary: bool,
):
    collection.questions.sort(
        key=lambda item: str(item.get("date_created") or item.get("last_updated") or ""),
        reverse=True,
    )
    item_ids = list(dict.fromkeys(
        str(item.get("item_id") or "").strip()
        for item in collection.questions
        if str(item.get("item_id") or "").strip()
    ))[:500]
    user_ids = list(dict.fromkeys(
        str((item.get("from") or {}).get("id") or "").strip()
        for item in collection.questions
        if isinstance(item.get("from"), dict) and str(item["from"].get("id") or "").strip()
    ))[:500]
    items, collection.cfg = _ml_buscar_itens_batch(
        client_id, store, collection.cfg, item_ids
    )
    items = _ml_perguntas_completar_skus_itens(client_id, store, collection.cfg, items)
    item_by_id = {str(item.get("id") or "").strip(): item for item in items if isinstance(item, dict)}
    users = {}
    if not fast_summary:
        users, collection.cfg = _ml_perguntas_buscar_usuarios(
            client_id, store, collection.cfg, user_ids
        )
    normalized = [_ml_perguntas_normalizar(item, item_by_id, users) for item in collection.questions]
    response_time = {}
    if not fast_summary:
        normalized, collection.cfg = _ml_perguntas_anexar_historico_comprador(
            client_id, store, collection.cfg, seller_id, normalized
        )
        response_time, collection.cfg = _ml_perguntas_tempo_resposta(
            client_id, store, collection.cfg, seller_id
        )
    next_offset = None
    if not load_all and collection.next_offset < collection.total and not collection.interrupted:
        next_offset = collection.next_offset
    return jsonable_encoder({
        "success": True, "loja": store, "seller_id": seller_id,
        "total": collection.total, "retornadas": len(normalized),
        "offset": offset, "limit": limit, "next_offset": next_offset,
        "interrompido": collection.interrupted,
        "modo_busca": "scan" if collection.using_scan else "offset",
        "modo_resumo_rapido": bool(fast_summary),
        "status_resumo": _ml_perguntas_resumir_status(normalized),
        "tempo_resposta_ml": response_time, "filters": collection.filters,
        "questions": normalized,
    })




def ml_listar_perguntas(
    loja: str,
    status: Optional[str] = None,
    offset: int = 0,
    limit: int = 50,
    carregar_todas: bool = True,
    max_pages: int = 100,
    modo_resumo_rapido: bool = False,
    request_timeout: int = 20,
    client_id: str = Depends(get_tenant_id),
):
    try:
        nome_loja = str(loja or "").strip()
        if not nome_loja:
            raise HTTPException(status_code=400, detail="Informe a loja para buscar as perguntas.")
        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str(cfg.get("user_id") or "").strip()
        if not seller_id:
            raise HTTPException(
                status_code=400,
                detail="ID do usu\u00c3\u00a1rio do Mercado Livre não encontrado para esta loja.",
            )
        limit = max(1, min(int(limit or 20), 100))
        offset = max(0, int(offset or 0))
        max_pages = max(1, min(int(max_pages or 100), 100))
        request_timeout = max(5, min(int(request_timeout or 20), 25))
        status_filter = str(status or "").strip().upper()
        if status_filter in {"TODAS", "TODOS", "ALL"}:
            status_filter = ""
        url = "https://api.mercadolibre.com/questions/search"
        collection = _QuestionCollection(cfg=cfg, next_offset=offset)
        if carregar_todas:
            _question_collection_scan(
                collection, client_id=client_id, store=nome_loja, seller_id=seller_id,
                url=url, limit=limit, max_pages=max_pages, timeout=request_timeout,
                status_filter=status_filter,
            )
            if not collection.using_scan:
                _question_collection_offset_all(
                    collection, client_id=client_id, store=nome_loja, seller_id=seller_id,
                    url=url, limit=limit, max_pages=max_pages, timeout=request_timeout,
                    status_filter=status_filter,
                )
                if collection.scan_failed:
                    logger.warning(
                        "[ML PERGUNTAS] Scan indisponivel para loja=%s. Usando fallback por offset.",
                        nome_loja,
                    )
        else:
            _question_collection_offset_one(
                collection, client_id=client_id, store=nome_loja, seller_id=seller_id,
                url=url, limit=limit, timeout=request_timeout, status_filter=status_filter,
            )
        return _question_collection_response(
            collection, client_id=client_id, store=nome_loja, seller_id=seller_id,
            offset=offset, limit=limit, load_all=bool(carregar_todas),
            fast_summary=bool(modo_resumo_rapido),
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.exception(
            "[ML PERGUNTAS] Falha inesperada ao listar perguntas da loja %s: %s",
            loja,
            error,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao listar perguntas do Mercado Livre: {str(error)}",
        )


__all__ = [
    "ml_listar_perguntas",
]
