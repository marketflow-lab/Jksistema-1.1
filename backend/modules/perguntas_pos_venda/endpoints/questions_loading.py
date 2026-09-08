"""Progressive public-question endpoints; the legacy listing stays unchanged."""

from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Optional

from fastapi import Depends, HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support as support
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import perguntas_loading_cache as cache
from backend.services.perguntas_loading_transport import check_budget, read_budget, remaining

_ml_extrair_sku = runtime_adapter("_ml_extrair_sku")
_SUMMARY_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="questions-summary")


def _search(scope, cfg, *, offset=0, limit=20, status="", item_id=""):
    params = {"seller_id": scope.seller_id, "api_version": 4, "offset": offset,
              "limit": limit, "sort_fields": "date_created", "sort_types": "DESC"}
    if status:
        params["status"] = status
    if item_id:
        params["item_id"] = item_id
    data = support.remote(scope, cfg, "/questions/search", params=params)
    if not isinstance(data, dict):
        raise HTTPException(502, "Resposta de perguntas invalida.")
    questions = data.get("questions") or data.get("results") or []
    if not isinstance(questions, list):
        raise HTTPException(502, "Lista de perguntas invalida.")
    questions = [row for row in questions if isinstance(row, dict)]
    for question in questions:
        support.verify_seller(question, scope)
    return data, questions


def _total(data: dict, rows: list) -> int:
    total = data.get("total")
    if total is None:
        total = (data.get("paging") or {}).get("total")
    return max(0, int(total if total is not None else len(rows)))


def ml_perguntas_lista_rapida(request: Request, store_id: str, loja: Optional[str] = None,
                            status: str = "", offset: int = 0, limit: int = 20,
                            forcar: bool = False, client_id: str = Depends(get_tenant_id)):
    with support.api_budget():
        scope = support.resolve_scope(request, client_id, store_id)
        status = str(status or "").strip().upper()
        if status in {"ALL", "TODAS", "TODOS"}:
            status = ""
        if status not in {"", "ANSWERED", "UNANSWERED", "CLOSED_UNANSWERED", "UNDER_REVIEW", "BANNED", "DELETED"}:
            raise HTTPException(400, "Filtro de status invalido.")
        if offset < 0 or offset >= 1000 or not 1 <= limit <= 100:
            raise HTTPException(400, "Paginacao invalida. Use offset abaixo de 1000 e limite de 1 a 100.")
        effective_limit = min(limit, 1000 - offset)

        def load():
            cfg = support.config_for(scope, request)
            data, questions = _search(scope, cfg, offset=offset, limit=effective_limit, status=status)
            items = cache.peek_items(scope.key("items"), [str(q.get("item_id") or "") for q in questions])
            normalized = [support._ml_perguntas_normalizar(q, items, {}) for q in questions]
            total = _total(data, questions)
            following = offset + len(questions)
            interrupted = following >= 1000 and following < total
            return {"questions": normalized, "total": total, "retornadas": len(normalized),
                    "offset": offset, "limit": effective_limit,
                    "next_offset": following if questions and following < min(total, 1000) else None,
                    "interrompido": interrupted, "partial": interrupted,
                    "modo_busca": "offset", "modo_resumo_rapido": True,
                    "status_resumo": support._ml_perguntas_resumir_status(normalized),
                    "tempo_resposta_ml": {}, "filters": data.get("filters") or {}}

        return support.cached(scope, "list", (status, offset, effective_limit), load,
                              ttl=30, force=forcar)


def _load_items(scope, request, ids):
    cfg = support.config_for(scope, request)
    data = support.remote(scope, cfg, "/items", params={"ids": ",".join(ids), "include_attributes": "all"})
    if not isinstance(data, list):
        raise HTTPException(502, "Resposta de anuncios invalida.")
    items = []
    missing = set(ids)
    partial = False
    for entry in data:
        body = entry.get("body") if isinstance(entry, dict) else None
        code = int(entry.get("code") or 200) if isinstance(entry, dict) else 502
        if code in {401, 403}:
            raise HTTPException(code, "O anuncio nao esta autorizado para esta loja.")
        if code != 200 or not isinstance(body, dict):
            partial = True
            continue
        if str(body.get("id")) not in missing:
            raise HTTPException(502, "O Mercado Livre devolveu um anuncio inesperado.")
        support.verify_seller(body, scope)
        missing.discard(str(body["id"]))
        items.append(body)
    # Variation completion is confined to this batch and the same total deadline.
    for item in items:
        try:
            check_budget()
            if any(isinstance(variation, dict) and not _ml_extrair_sku(variation)
                   for variation in (item.get("variations") or [])):
                variations = support.remote(scope, cfg, f"/items/{item['id']}/variations",
                                            params={"include_attributes": "all"})
                if isinstance(variations, list):
                    item["variations"] = variations
                else:
                    partial = True
        except HTTPException as error:
            if error.status_code in {401, 403}:
                raise
            partial = True
        except Exception:
            partial = True
        parent = dict(item)
        parent.pop("variations", None)
        item["item_sku"] = _ml_extrair_sku(parent) or ""
    return {"items": items, "missing_item_ids": sorted(missing), "partial": partial or bool(missing)}


def ml_perguntas_itens_rapidos(request: Request, store_id: str, item_ids: str,
                             forcar: bool = False, client_id: str = Depends(get_tenant_id)):
    with support.api_budget():
        scope = support.resolve_scope(request, client_id, store_id)
        ids = list(dict.fromkeys(part.strip() for part in item_ids.split(",") if part.strip()))
        if not 1 <= len(ids) <= 20:
            raise HTTPException(400, "Informe de 1 a 20 anuncios por consulta.")
        for item_id in ids:
            support.validate_id(item_id, "ID do anuncio", r"[A-Z]{3}\d{1,20}")
        return support.cached(scope, "items", (tuple(sorted(ids)),),
                              lambda: _load_items(scope, request, ids), ttl=900,
                              stale_seconds=900, force=forcar)


def _load_detail(scope, request, question_id):
    cfg = support.config_for(scope, request)
    question = support.remote(scope, cfg, f"/questions/{question_id}", params={"api_version": 4})
    if not isinstance(question, dict) or str(question.get("id")) != question_id:
        raise HTTPException(502, "Resposta da pergunta invalida.")
    support.verify_seller(question, scope)
    item_id = support.validate_id(question.get("item_id"), "ID do anuncio", r"[A-Z]{3}\d{1,20}")
    history, partial, history_truncated = [], False, False
    try:
        data, history = _search(scope, cfg, limit=50, item_id=item_id)
        history_truncated = _total(data, history) > len(history)
    except HTTPException as error:
        if error.status_code in {401, 403}:
            raise
        partial = True
    except Exception:
        partial = True
    normalized = support.normalized_history(history, question)
    buyer_id = str((question.get("from") or {}).get("id") or "")
    if buyer_id.isdigit():
        try:
            buyer = support.remote(scope, cfg, f"/users/{buyer_id}")
            if isinstance(buyer, dict) and str(buyer.get("id")) == buyer_id:
                normalized.update(buyer_name=buyer.get("nickname") or buyer_id,
                                  buyer_nickname=buyer.get("nickname") or "")
        except HTTPException as error:
            if error.status_code in {401, 403}:
                raise
            partial = True
        except Exception:
            partial = True
    normalized["history_truncated"] = history_truncated
    return {"question": normalized, "partial": partial, "history_truncated": history_truncated}


def ml_perguntas_detalhe_rapido(request: Request, store_id: str, question_id: str,
                              forcar: bool = False, client_id: str = Depends(get_tenant_id)):
    with support.api_budget():
        scope = support.resolve_scope(request, client_id, store_id)
        question_id = support.validate_id(question_id, "ID da pergunta", r"\d{1,30}")
        return support.cached(scope, "detail", (question_id,),
                              lambda: _load_detail(scope, request, question_id),
                              ttl=60, force=forcar)


def _count(scope, request, force):
    if not scope.site_id:
        scope = support.resolve_scope(request, scope.client_id, scope.store_id)
    def load():
        cfg = support.config_for(scope, request)
        data, questions = _search(scope, cfg, limit=1, status="UNANSWERED")
        return {"perguntas": _total(data, questions)}
    return support.cached(scope, "count", (), load, ttl=60, force=force)


def _metrics(scope, request, force):
    if not scope.site_id:
        scope = support.resolve_scope(request, scope.client_id, scope.store_id)
    def load():
        cfg = support.config_for(scope, request)
        try:
            data = support.remote(scope, cfg, f"/users/{scope.seller_id}/questions/response_time")
            return {"tempo_resposta_ml": {"available": True, **data}}
        except HTTPException as error:
            if error.status_code != 404:
                raise
            return {"tempo_resposta_ml": {"available": False}}
    return support.cached(scope, "metrics", (), load, ttl=3600, stale_seconds=3600, force=force)


def ml_perguntas_resumo_rapido(request: Request, store_ids: str = "", store_id: str = "",
                             metricas: bool = False, forcar: bool = False,
                             client_id: str = Depends(get_tenant_id)):
    with support.api_budget():
        rows = support.authorized_stores(request, client_id)
        ids = ([store_id] if store_id else
               list(dict.fromkeys(part.strip() for part in store_ids.split(",") if part.strip())))
        if not ids:
            ids = [str(row.get("store_id") or "") for row in rows
                   if support._ml_oauth_status((row.get("integracoes") or {}).get("mercadolivre")).get("conectado")]
        if len(ids) > 100 or (metricas and len(ids) != 1):
            raise HTTPException(400, "Consulte ate 100 lojas; metricas exigem uma unica loja.")
        scopes = [support.resolve_scope(request, client_id, exact, rows=rows, resolve_site=False) for exact in ids]
        futures = {scope.store_id: _SUMMARY_POOL.submit(contextvars.copy_context().run, _count, scope, request, forcar)
                   for scope in scopes}
        metric_future = (_SUMMARY_POOL.submit(contextvars.copy_context().run, _metrics, scopes[0], request, forcar)
                         if metricas else None)
        tasks = list(futures.values()) + ([metric_future] if metric_future else [])
        if tasks:
            wait(tasks, timeout=max(0, (remaining() or 0) - 0.05))
        result = []
        for scope in scopes:
            future = futures[scope.store_id]
            try:
                if not future.done():
                    future.cancel()
                    raise HTTPException(504, "Consulta ainda nao concluida.")
                result.append(future.result())
            except Exception as error:
                if isinstance(error, HTTPException) and error.status_code in {401, 403}:
                    raise
                result.append({"store_id": scope.store_id, "loja": scope.name, "perguntas": None,
                               "erro": "Nao foi possivel atualizar esta loja.", "consultado_em": None,
                               "stale": False, "partial": True})
        metadata = {"consultado_em": int(time.time() * 1000), "source": "aggregate",
                    "stale": any(row.get("stale") for row in result),
                    "partial": any(row.get("partial") for row in result)}
        response = {"success": True, "lojas": result, "metadata": metadata, **metadata}
        if metric_future and metric_future.done():
            try:
                response["tempo_resposta_ml"] = metric_future.result()["tempo_resposta_ml"]
            except Exception as error:
                if isinstance(error, HTTPException) and error.status_code in {401, 403}:
                    raise
                response["tempo_resposta_ml"] = {"available": False}
        elif metric_future:
            metric_future.cancel()
            response["tempo_resposta_ml"] = {"available": False}
        return response
