"""Progressive public-question endpoints; the legacy listing stays unchanged."""

from __future__ import annotations

import contextvars
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, wait
from typing import Optional

from fastapi import Depends, HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support as support
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import perguntas_loading_cache as cache
from backend.services.perguntas_loading_transport import check_budget, read_budget, remaining

_ml_extrair_sku = runtime_adapter("_ml_extrair_sku")
_SUMMARY_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="questions-summary")
_COMPONENT_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="questions-components")
_COMPONENT_SLOTS = threading.BoundedSemaphore(16)
_HISTORY_SECONDS = 6.0


def _complement(loader, seconds):
    """Reserve response time even when an upstream peer ignores socket timeouts."""
    allowance = min(seconds, max(0, (remaining() if remaining() is not None else 15) - .5))
    if allowance <= 0 or not _COMPONENT_SLOTS.acquire(blocking=False):
        raise support.loading_error(503, "Consulta complementar temporariamente ocupada.", code="component_busy")
    release_admission = cache.retain_current_admission()
    def run():
        try:
            with read_budget(seconds=allowance):
                return loader()
        finally:
            _COMPONENT_SLOTS.release()
            release_admission()
    try:
        future = _COMPONENT_POOL.submit(contextvars.copy_context().run, run)
    except Exception:
        _COMPONENT_SLOTS.release()
        release_admission()
        raise
    try:
        return future.result(timeout=allowance)
    except FutureTimeout as error:
        # Workers release their slot; late results have no reference to the public response.
        raise support.loading_error(504, "Tempo da consulta complementar esgotado.", code="component_timeout") from error


def _complement_config(cfg, loader, seconds):
    """Publish refreshed OAuth state only after the isolated worker has finished."""
    child_cfg = dict(cfg)
    def consult():
        try:
            return loader(child_cfg), None
        except Exception as error:
            return None, error
    value, error = _complement(consult, seconds)
    cfg.update(child_cfg)
    if error is not None:
        raise error
    return value


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
    questions = data.get("questions") if "questions" in data else data.get("results")
    if not isinstance(questions, list) or any(not isinstance(row, dict) for row in questions):
        raise support.loading_error(502, "Lista de perguntas invalida.", code="invalid_question_list")
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
    states = {item_id: support.component(support.loading_error(
        502, "Anuncio ausente na resposta.", code="item_missing")) for item_id in ids}
    partial = False
    for index, entry in enumerate(data):
        body = entry.get("body") if isinstance(entry, dict) else None
        code = int(entry.get("code") or 200) if isinstance(entry, dict) else 502
        entry_id = str((body or {}).get("id") or "") if isinstance(body, dict) else ""
        if not entry_id and isinstance(entry, dict):
            entry_id = str(entry.get("id") or "")
        # ML multiget preserves request ordering, including error envelopes without an ID.
        if not entry_id and index < len(ids):
            entry_id = ids[index]
        if code != 200 or not isinstance(body, dict):
            partial = True
            if entry_id in states:
                states[entry_id] = support.component(support.loading_error(code if 400 <= code <= 599 else 502,
                    "Nao foi possivel consultar este anuncio."))
            continue
        if str(body.get("id")) not in missing:
            raise HTTPException(502, "O Mercado Livre devolveu um anuncio inesperado.")
        support.verify_seller(body, scope)
        missing.discard(str(body["id"]))
        states[str(body["id"])] = support.component()
        items.append(body)
    # Variation completion is confined to this batch and the same total deadline.
    complement_pending = False
    for item in items:
        try:
            check_budget()
            if any(isinstance(variation, dict) and not _ml_extrair_sku(variation)
                   for variation in (item.get("variations") or [])):
                if complement_pending:
                    raise support.loading_error(503, "Variacoes pendentes de nova tentativa.", code="component_deferred")
                variations = _complement_config(cfg, lambda child_cfg, item=item: support.remote(scope, child_cfg, f"/items/{item['id']}/variations",
                                            params={"include_attributes": "all"}), 2)
                if isinstance(variations, list):
                    item["variations"] = variations
                else:
                    partial = True
                    states[str(item["id"])] = support.component(support.loading_error(502, "Variacoes invalidas."))
        except HTTPException as error:
            if support.error_scope(error) in {"session", "store"}:
                raise
            partial = True
            states[str(item["id"])] = support.component(error)
            complement_pending = complement_pending or states[str(item["id"])]["code"] == "component_timeout"
        except Exception as error:
            partial = True
            states[str(item["id"])] = support.component(error)
        parent = dict(item)
        parent.pop("variations", None)
        item["item_sku"] = _ml_extrair_sku(parent) or ""
    return {"items": items, "missing_item_ids": sorted(missing), "partial": partial or bool(missing),
            "item_states": states}


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


def _load_detail(scope, request, question_id, requested=None, *, snapshot_max_age=60):
    cfg = support.config_for(scope, request)
    snapshot = cache.peek_current(
        scope.key("detail", question_id), max_age=snapshot_max_age,
    ) if requested else None
    previous = snapshot["value"] if snapshot else {}
    old_components = previous.get("components") or {}
    reuse_question = (requested and "question" not in requested
                      and old_components.get("question", {}).get("state") == "ready"
                      and bool(support.buyer_identity(previous.get("question") or {})))
    if reuse_question:
        question = dict(previous["question"])
        question["from"] = question.get("from") or {"id": question.get("buyer_id") or question.get("from_id")}
    else:
        question = support.remote(scope, cfg, f"/questions/{question_id}", params={"api_version": 4})
    if not isinstance(question, dict) or str(question.get("id")) != question_id:
        raise HTTPException(502, "Resposta da pergunta invalida.")
    support.verify_seller(question, scope)
    item_id = support.validate_id(question.get("item_id"), "ID do anuncio", r"[A-Z]{3}\d{1,20}")
    buyer_id = support.buyer_identity(question)
    history, history_truncated = [], False
    components = {"question": dict(old_components["question"]) if reuse_question else support.component(),
                  "buyer": support.component(code="not_required")}
    preserve_history = reuse_question and "history" not in requested and "history" in old_components
    if not buyer_id:
        components["history"] = support.component(support.loading_error(
            502, "Identidade do comprador ausente na pergunta.", code="buyer_identity_missing"), truncated=False)
        normalized = support.normalized_history([], question)
    elif preserve_history:
        components["history"] = dict(old_components["history"])
        history_truncated = bool(previous.get("history_truncated"))
        normalized = dict(previous["question"])
    else:
        try:
            data, history = _complement_config(cfg, lambda child_cfg: _search(scope, child_cfg, limit=50, item_id=item_id), _HISTORY_SECONDS)
            history_truncated = _total(data, history) > len(history)
            components["history"] = support.component(truncated=history_truncated)
        except HTTPException as error:
            if support.error_scope(error) in {"session", "store"}:
                raise
            components["history"] = support.component(error, truncated=False)
        except Exception as error:
            components["history"] = support.component(error, truncated=False)
        normalized = support.normalized_history(history, question)
    if not buyer_id:
        components["buyer"] = support.component(support.loading_error(
            502, "Identidade do comprador ausente na pergunta.", code="buyer_identity_missing"))
    elif reuse_question and "buyer" not in requested and "buyer" in old_components:
        components["buyer"] = dict(old_components["buyer"])
        for field in ("buyer_name", "buyer_nickname"):
            if field in previous["question"]:
                normalized[field] = previous["question"][field]
    elif components["history"].get("code") == "component_timeout":
        components["buyer"] = support.component(support.loading_error(
            503, "Comprador pendente de nova tentativa.", code="component_deferred"))
    elif buyer_id.isdigit():
        try:
            buyer = _complement_config(cfg, lambda child_cfg: support.remote(scope, child_cfg, f"/users/{buyer_id}"), 1)
            if isinstance(buyer, dict) and str(buyer.get("id")) == buyer_id:
                normalized.update(buyer_name=buyer.get("nickname") or buyer_id,
                                  buyer_nickname=buyer.get("nickname") or "")
                components["buyer"] = support.component()
            else:
                raise support.loading_error(502, "Resposta do comprador invalida.")
        except HTTPException as error:
            if support.error_scope(error) in {"session", "store"}:
                raise
            components["buyer"] = support.component(error)
        except Exception as error:
            components["buyer"] = support.component(error)
    normalized["history_truncated"] = history_truncated
    result = {"question": normalized, "partial": any(value["state"] != "ready" for value in components.values()),
              "history_truncated": history_truncated, "components": components}
    if reuse_question:
        result["_cache_origin"] = snapshot["origin"]
    return result


def _cached_detail(scope, request, question_id, requested, *, force, snapshot_max_age=60):
    return support.cached(
        scope, "detail", (question_id,),
        lambda: _load_detail(
            scope, request, question_id, requested=requested, snapshot_max_age=snapshot_max_age,
        ),
        ttl=60, force=force or bool(requested), flight_variant=tuple(sorted(requested)),
    )


def _generation_age(origin) -> float:
    return max(0.0, time.monotonic() - origin[0]) if origin else 0.0


def _generation_component_age(value, name, fallback_age) -> float:
    consulted = ((value.get("components") or {}).get(name) or {}).get("consultado_em")
    if isinstance(consulted, (int, float)) and consulted > 0:
        return max(0.0, time.time() - consulted / 1000.0)
    return fallback_age


def _generation_detail_ready(value) -> bool:
    components = value.get("components") or {}
    return all((components.get(name) or {}).get("state") == "ready"
               for name in ("question", "history"))


def _generation_detail_retryable(value) -> bool:
    components = value.get("components") or {}
    failed = [(components.get(name) or {}) for name in ("question", "history")
              if (components.get(name) or {}).get("state") != "ready"]
    return bool(failed) and all(component.get("retryable") for component in failed)


def _generation_error_transient(error) -> bool:
    return error.status_code in {408, 429} or error.status_code >= 500


def canonical_context(request: Request, client_id: str, store_id: str, question_id: str, *, force=True):
    """Return one authorized generation snapshot with a five-minute safety bound."""
    warning = "Resposta gerada com contexto validado há até 5 minutos. Revise antes de enviar."

    with support.api_budget():
        scope = support.resolve_scope(request, client_id, store_id)
        question_id = support.validate_id(question_id, "ID da pergunta", r"\d{1,30}")
        support.config_for(scope, request)
        detail_snapshot = cache.peek_current(scope.key("detail", question_id), max_age=300)
        detail = detail_snapshot["value"] if detail_snapshot else None
        detail_fallback = bool(detail and _generation_detail_ready(detail))
        detail_origin_age = _generation_age(detail_snapshot["origin"]) if detail_snapshot else float("inf")
        question_age = _generation_component_age(detail or {}, "question", detail_origin_age)
        history_age = _generation_component_age(detail or {}, "history", detail_origin_age)
        used_fallback = False
        refreshed = False

        detail_components = ""
        if not force and detail_snapshot and question_age <= 60 < history_age:
            detail_components = "history"
        if force or not detail_snapshot or question_age > 60 or history_age > 60:
            try:
                with read_budget(seconds=10):
                    loaded = _cached_detail(
                        scope, request, question_id,
                        {detail_components} if detail_components else set(),
                        force=not detail_components, snapshot_max_age=300,
                    )
                refreshed = True
                if loaded.get("stale"):
                    if detail_fallback:
                        used_fallback = True
                    else:
                        raise support.loading_error(
                            503, "O contexto da pergunta expirou antes da atualizacao.",
                            code="context_expired",
                        )
                elif _generation_detail_ready(loaded):
                    detail = loaded
                    detail_snapshot = cache.peek_current(scope.key("detail", question_id), max_age=300)
                elif detail_fallback and _generation_detail_retryable(loaded):
                    used_fallback = True
                else:
                    detail = loaded
                    detail_snapshot = None
            except HTTPException as error:
                if detail_fallback and _generation_error_transient(error):
                    used_fallback = True
                else:
                    raise

        if not isinstance(detail, dict) or not isinstance(detail.get("question"), dict):
            raise support.loading_error(502, "Contexto canonico da pergunta invalido.", code="context_invalid")
        question = detail["question"]
        item_id = support.validate_id(question.get("item_id"), "ID do anuncio", r"[A-Z]{3}\d{1,20}")
        item_entries = cache.peek_item_entries(scope.key("items"), [item_id], max_age=300)
        item_snapshot = item_entries.get(item_id)
        item = item_snapshot["value"] if item_snapshot else None
        item_component = item_snapshot["component"] if item_snapshot else None
        item_fallback = bool(item and (item_component or {}).get("state") == "ready")

        needs_item_refresh = (force or not item_snapshot or _generation_age(item_snapshot["origin"]) > 60)
        if detail.get("components", {}).get("history", {}).get("state") == "ready":
            if needs_item_refresh:
                try:
                    loaded_items = ml_perguntas_itens_rapidos(
                        request, store_id, item_id, forcar=True, client_id=client_id,
                    )
                    refreshed = True
                    loaded_component = (loaded_items.get("item_states") or {}).get(item_id) or {}
                    loaded_item = next((row for row in loaded_items.get("items", [])
                                        if str(row.get("id") or "") == item_id), None)
                    if item_id in (loaded_items.get("fallback_item_ids") or []):
                        used_fallback = True
                    elif loaded_items.get("stale"):
                        if item_fallback:
                            used_fallback = True
                        else:
                            raise support.loading_error(
                                503, "O contexto do anuncio expirou antes da atualizacao.",
                                code="context_expired",
                            )
                    elif loaded_item and loaded_component.get("state") == "ready":
                        item, item_component, item_snapshot = loaded_item, loaded_component, None
                    elif item_fallback and loaded_component.get("retryable"):
                        used_fallback = True
                    else:
                        if (loaded_component.get("state") == "blocked"
                                or loaded_component.get("code") in {"access_denied", "not_found"}):
                            cache._discard_scope((scope.client_id, scope.store_id))
                        item, item_component, item_snapshot = loaded_item, loaded_component, None
                except HTTPException as error:
                    if item_fallback and _generation_error_transient(error):
                        used_fallback = True
                    else:
                        raise
        else:
            item, item_component = {}, support.component(support.loading_error(
                503, "Historico necessario antes de carregar contexto da IA.", code="component_deferred"))

        detail_origin_age = _generation_age(detail_snapshot["origin"]) if detail_snapshot else 0.0
        ages = [_generation_component_age(detail, name, detail_origin_age)
                for name in ("question", "history")]
        if item_snapshot:
            ages.append(_generation_age(item_snapshot["origin"]))
        source = "stale_fallback" if used_fallback else "refreshed" if refreshed else "fresh_cache"
        context_status = {
            "source": source,
            "age_seconds": min(300, int(max(ages, default=0))),
            "warning": warning if used_fallback else None,
        }
        return {"scope": {"client_id": scope.client_id, "username": scope.username,
                "store_id": scope.store_id, "seller_id": scope.seller_id, "site_id": scope.site_id,
                "name": scope.name}, "loja": scope.name, "question": question,
                "item": item or {}, "components": {**(detail.get("components") or {}),
                "item": item_component or support.component(support.loading_error(
                    502, "Anuncio canonico indisponivel.", code="component_not_ready"))},
                "history_truncated": bool(detail.get("history_truncated")), "stale": False,
                "cache_revision": cache.revision(scope.client_id, scope.store_id),
                "context_status": context_status}


def ml_perguntas_detalhe_rapido(request: Request, store_id: str, question_id: str,
                              forcar: bool = False, client_id: str = Depends(get_tenant_id),
                              componentes: str = ""):
    with support.api_budget():
        scope = support.resolve_scope(request, client_id, store_id)
        question_id = support.validate_id(question_id, "ID da pergunta", r"\d{1,30}")
        requested = {part.strip() for part in componentes.split(",") if part.strip()}
        if requested - {"question", "history", "buyer"}:
            raise HTTPException(400, "Componente de pergunta invalido.")
        return _cached_detail(scope, request, question_id, requested, force=forcar)


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
                if isinstance(error, HTTPException) and support.error_scope(error) == "session":
                    raise
                result.append({"store_id": scope.store_id, "loja": scope.name, "perguntas": None,
                               "erro": "Nao foi possivel atualizar esta loja.", "consultado_em": None,
                               "stale": False, "partial": True, "component": support.component(error)})
        metadata = {"consultado_em": int(time.time() * 1000), "source": "aggregate",
                    "stale": any(row.get("stale") for row in result),
                    "partial": any(row.get("partial") for row in result)}
        response = {"success": True, "lojas": result, "metadata": metadata, **metadata}
        if metric_future and metric_future.done():
            try:
                response["tempo_resposta_ml"] = metric_future.result()["tempo_resposta_ml"]
            except Exception as error:
                if isinstance(error, HTTPException) and support.error_scope(error) == "session":
                    raise
                response["tempo_resposta_ml"] = {"available": False, "component": support.component(error)}
        elif metric_future:
            metric_future.cancel()
            response["tempo_resposta_ml"] = {"available": False}
        return response
