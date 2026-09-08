from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from backend.modules.perguntas_pos_venda.endpoints import questions_loading as api
from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support as support
from backend.services import perguntas_loading_cache as cache


def request(user="alice", tenant="tenant"):
    result = Request({"type": "http", "method": "GET", "path": "/"})
    result.state.username = user
    result.state.client_id = tenant
    return result


def store(exact="A", seller="10", site="MLB"):
    return {"store_id": exact, "nome": "Loja " + exact,
            "integracoes": {"mercadolivre": {"user_id": seller, "site_id": site, "access_token": "test"}}}


def question(qid="1", seller="10", buyer="20", item="MLB123"):
    return {"id": qid, "seller_id": seller, "item_id": item, "from": {"id": buyer},
            "text": "Pergunta", "status": "UNANSWERED", "date_created": "2026-09-08T12:00:00Z"}


@pytest.fixture(autouse=True)
def clean_cache():
    with cache._LOCK:
        assert not cache._RUNNING
        cache._ENTRIES.clear()
        cache._REVISIONS.clear()
        cache._CLIENT_SLOTS.clear()
    yield
    with cache._LOCK:
        pending = list(cache._RUNNING.values())
    for future in pending:
        try:
            future.result(timeout=3)
        except Exception:
            pass


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(rows=[store()], calls=[], handler=None)
    monkeypatch.setattr(support, "carregar_lojas", lambda tenant: copy.deepcopy(state.rows))
    monkeypatch.setattr(support, "_ml_oauth_status", lambda cfg: {"conectado": bool(cfg and cfg.get("access_token"))})
    monkeypatch.setattr(support, "_obter_cfg_ml", lambda tenant, name, store_id=None: {"_store_id_context": store_id})
    monkeypatch.setattr(support, "_ml_perguntas_normalizar", lambda q, items, users: {
        **copy.deepcopy(q), "buyer_id": (q.get("from") or {}).get("id"), "item_title": ""})
    monkeypatch.setattr(support, "_ml_perguntas_resumir_status", lambda rows: {"unanswered": len(rows)})
    monkeypatch.setattr(api, "_ml_extrair_sku", lambda item: item.get("seller_custom_field") or "")

    def remote(tenant, name, cfg, method, url, **kwargs):
        path = url.removeprefix("https://api.mercadolibre.com")
        state.calls.append((path, kwargs.get("params") or {}))
        payload = state.handler(path, kwargs.get("params") or {})
        return SimpleNamespace(status_code=200, json=lambda: copy.deepcopy(payload)), cfg

    monkeypatch.setattr(support, "_ml_api_request", remote)
    return state


def test_list_is_one_search_and_cache_is_authorized_before_access(env):
    env.handler = lambda path, params: {"total": 1221, "questions": [question()]}
    first = api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    second = api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    assert len(env.calls) == 1
    assert env.calls[0][0] == "/questions/search"
    assert first["total"] == 1221 and first["next_offset"] == 1
    assert second["source"] == "memory_cache" and not second["stale"]
    env.rows.clear()
    with pytest.raises(HTTPException) as denied:
        api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    assert denied.value.status_code == 403
    assert len(env.calls) == 1


def test_user_tenant_exact_store_and_filters_do_not_share_cache(env):
    env.rows.append(store("a", "11"))
    env.handler = lambda path, params: {"total": 0, "questions": []}
    for user, tenant, exact, status in [("alice", "tenant", "A", ""), ("bob", "tenant", "A", ""),
                                         ("alice", "other", "A", ""), ("alice", "tenant", "a", ""),
                                         ("alice", "tenant", "A", "UNANSWERED")]:
        api.ml_perguntas_lista_rapida(request(user, tenant), exact, status=status, client_id=tenant)
    assert len(env.calls) == 5


def test_authenticated_username_required_and_tenant_cannot_be_supplied(env):
    for req in [request(""), request(tenant="other")]:
        with pytest.raises(HTTPException) as denied:
            api.ml_perguntas_lista_rapida(req, "A", client_id="tenant")
        assert denied.value.status_code == 401
    assert env.calls == []


def test_site_missing_is_resolved_once_without_guessing(env):
    env.rows = [store(site="")]
    def handler(path, params):
        if path == "/users/10":
            return {"id": 10, "site_id": "MLA"}
        return {"total": 0, "questions": []}
    env.handler = handler
    first = api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    second = api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    assert first["site_id"] == second["site_id"] == "MLA"
    assert [call[0] for call in env.calls].count("/users/10") == 1


def test_site_identity_must_match_seller(env):
    env.rows = [store(site="")]
    env.handler = lambda path, params: {"id": 999, "site_id": "MLB"}
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    assert error.value.status_code == 502
    assert not cache._ENTRIES


def test_items_are_batched_and_reject_foreign_seller(env):
    env.handler = lambda path, params: [{"code": 200, "body": {
        "id": "MLB123", "seller_id": "10", "seller_custom_field": "00406", "variations": []}}]
    result = api.ml_perguntas_itens_rapidos(request(), "A", "MLB123", client_id="tenant")
    assert result["items"][0]["item_sku"] == "00406"
    assert len(env.calls) == 1
    env.handler = lambda path, params: [{"code": 200, "body": {"id": "MLB999", "seller_id": "11"}}]
    with pytest.raises(HTTPException) as denied:
        api.ml_perguntas_itens_rapidos(request(), "A", "MLB999", client_id="tenant")
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as too_many:
        api.ml_perguntas_itens_rapidos(request(), "A", ",".join(f"MLB{i}" for i in range(21)), client_id="tenant")
    assert too_many.value.status_code == 400


def test_detail_keeps_only_selected_buyer_and_rejects_other_seller(env):
    def handler(path, params):
        if path == "/questions/1":
            return question()
        if path == "/questions/search":
            return {"total": 2, "questions": [question("2"), question("3", buyer="99")]}
        if path == "/users/20":
            return {"id": 20, "nickname": "comprador"}
        raise AssertionError(path)
    env.handler = handler
    result = api.ml_perguntas_detalhe_rapido(request(), "A", "1", client_id="tenant")
    assert {q["id"] for q in result["question"]["buyer_question_history"]} == {"1", "2"}
    assert result["question"]["buyer_name"] == "comprador"
    env.handler = lambda path, params: question("4", seller="11")
    with pytest.raises(HTTPException) as denied:
        api.ml_perguntas_detalhe_rapido(request(), "A", "4", client_id="tenant")
    assert denied.value.status_code == 403


def test_summary_uses_only_counts_and_failed_store_is_not_zero(env):
    env.rows.append(store("B", "11"))
    def handler(path, params):
        assert path == "/questions/search" and params["limit"] == 1 and params["status"] == "UNANSWERED"
        if params["seller_id"] == "11":
            raise HTTPException(429, "rate limit")
        return {"total": 0, "questions": []}
    env.handler = handler
    result = api.ml_perguntas_resumo_rapido(request(), client_id="tenant")
    by_id = {row["store_id"]: row for row in result["lojas"]}
    assert by_id["A"]["perguntas"] == 0
    assert by_id["B"]["perguntas"] is None and by_id["B"]["partial"]


def test_cache_stale_is_immediate_and_refresh_singleflight():
    key = ("tenant", "A", "alice", "10", "MLB", "list")
    first = cache.read(key, lambda: {"questions": [1]}, ttl=30)
    with cache._LOCK:
        cache._ENTRIES[key].monotonic_at -= 31
    started, finish = threading.Event(), threading.Event()
    calls = []
    def slow():
        calls.append(1)
        started.set()
        assert finish.wait(2)
        return {"questions": [2]}
    try:
        stale = cache.read(key, slow, ttl=30)
        assert stale["stale"] and stale["questions"] == [1]
        assert started.wait(1)
        again = cache.read(key, slow, ttl=30)
        assert again["consultado_em"] == first["consultado_em"]
        assert len(calls) == 1
    finally:
        finish.set()
    fresh = cache.read(key, slow, ttl=30, force=True)
    assert fresh["questions"] == [2] and not fresh["stale"]


def test_invalidation_fences_inflight_and_preserves_items():
    key = ("tenant", "A", "alice", "10", "MLB", "list")
    item_key = (*key[:5], "items", ("MLB123",))
    cache.read(item_key, lambda: {"items": [1]}, ttl=900)
    started, finish = threading.Event(), threading.Event()
    def slow():
        started.set()
        assert finish.wait(2)
        return {"questions": ["old"]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.read, key, slow, ttl=30)
        assert started.wait(1)
        cache.invalidate_store("tenant", "A")
        finish.set()
        with pytest.raises(HTTPException) as invalidated:
            future.result(2)
        assert invalidated.value.status_code == 409
    assert key not in cache._ENTRIES and item_key in cache._ENTRIES


def test_metadata_rejects_old_entry_even_after_worker_completed():
    key = ("tenant", "A", "alice", "10", "MLB", "detail", "1")
    cache.read(key, lambda: {"question": {"status": "UNANSWERED"}}, ttl=60)
    entry = cache._ENTRIES[key]
    cache.invalidate_store("tenant", "A")
    with pytest.raises(HTTPException) as invalidated:
        cache._metadata(entry, key, stale=False, source="mercadolivre")
    assert invalidated.value.status_code == 409


def test_expired_stale_and_provider_denial_are_never_served():
    key = ("tenant", "A", "alice", "10", "MLB", "list")
    cache.read(key, lambda: {"questions": [1]}, ttl=30)
    cache._ENTRIES[key].monotonic_at -= 601
    def unavailable():
        raise HTTPException(503, "unavailable")
    with pytest.raises(HTTPException):
        cache.read(key, unavailable, ttl=30)
    cache._ENTRIES[key].monotonic_at += 570
    def denied():
        raise HTTPException(403, "denied")
    with pytest.raises(HTTPException) as error:
        cache.read(key, denied, ttl=30, force=True)
    assert error.value.status_code == 403 and key not in cache._ENTRIES


def test_lru_is_bounded_and_results_are_detached(monkeypatch):
    monkeypatch.setattr(cache, "MAX_ENTRIES", 2)
    base = ("tenant", "A", "alice", "10", "MLB", "list")
    for index in range(3):
        result = cache.read((*base, index), lambda: {"questions": [1]}, ttl=30)
        result["questions"].append(2)
    assert len(cache._ENTRIES) == 2 and (*base, 0) not in cache._ENTRIES
    assert all(entry.value["questions"] == [1] for entry in cache._ENTRIES.values())


def test_history_truncation_does_not_make_detail_unusable(env):
    def handler(path, params):
        if path == "/questions/1":
            return question()
        if path == "/questions/search":
            return {"total": 1200, "questions": [question()]}
        return {"id": 20, "nickname": "comprador"}
    env.handler = handler
    result = api.ml_perguntas_detalhe_rapido(request(), "A", "1", client_id="tenant")
    assert result["history_truncated"] and not result["partial"]


def test_cached_items_are_reused_without_new_network_calls(env, monkeypatch):
    env.handler = lambda path, params: [{"code": 200, "body": {
        "id": "MLB123", "seller_id": "10", "title": "Titulo", "seller_custom_field": "00406"}}]
    api.ml_perguntas_itens_rapidos(request(), "A", "MLB123", client_id="tenant")
    env.handler = lambda path, params: {"total": 1, "questions": [question()]}
    monkeypatch.setattr(support, "_ml_perguntas_normalizar", lambda q, items, users: {
        **q, "item_title": (items.get(q["item_id"]) or {}).get("title", "")})
    result = api.ml_perguntas_lista_rapida(request(), "A", client_id="tenant")
    assert result["questions"][0]["item_title"] == "Titulo"
    assert [call[0] for call in env.calls] == ["/items", "/questions/search"]
    assert cache.peek_items(("tenant", "A", "other-user", "10", "MLB"), ["MLB123"]) == {}


def test_four_client_reads_run_and_remaining_work_queues():
    release, four_started = threading.Event(), threading.Event()
    lock = threading.Lock()
    active, maximum = 0, 0
    def load():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                four_started.set()
        assert release.wait(2)
        with lock:
            active -= 1
        return {"perguntas": 1}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.read, ("tenant", str(i), "alice", str(i), "MLB", "count"),
                               load, ttl=60) for i in range(8)]
        try:
            assert four_started.wait(1)
        finally:
            release.set()
        assert all(future.result(2)["perguntas"] == 1 for future in futures)
    assert maximum == 4


def test_summary_returns_partial_before_shared_deadline(env, monkeypatch):
    from contextlib import contextmanager
    from backend.services.perguntas_loading_transport import read_budget
    env.rows = [store("A", "10"), store("B", "11")]
    release = threading.Event()
    @contextmanager
    def small_budget():
        with read_budget(seconds=0.2):
            yield
    monkeypatch.setattr(support, "api_budget", small_budget)
    def handler(path, params):
        assert release.wait(2)
        return {"total": 1, "questions": []}
    env.handler = handler
    try:
        result = api.ml_perguntas_resumo_rapido(request(), client_id="tenant")
        assert result["partial"] and all(row["perguntas"] is None for row in result["lojas"])
    finally:
        release.set()


def test_denial_fences_other_inflight_reads_including_items():
    base = ("tenant", "A", "alice", "10", "MLB")
    started, release = threading.Event(), threading.Event()
    def slow_items():
        started.set()
        assert release.wait(2)
        return {"items": [1]}
    def denied():
        raise HTTPException(403, "access revoked")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.read, (*base, "items"), slow_items, ttl=900)
        assert started.wait(1)
        try:
            with pytest.raises(HTTPException) as error:
                cache.read((*base, "detail", "1"), denied, ttl=60)
            assert error.value.status_code == 403
        finally:
            release.set()
        with pytest.raises(HTTPException) as old_items:
            future.result(2)
        assert old_items.value.status_code == 409
    assert not cache._ENTRIES


def test_summary_propagates_provider_denial(env):
    def denied(path, params):
        raise HTTPException(403, "revoked")
    env.handler = denied
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_resumo_rapido(request(), client_id="tenant")
    assert error.value.status_code == 403


def test_variations_are_completed_even_with_parent_sku(env):
    def handler(path, params):
        if path == "/items":
            return [{"code": 200, "body": {"id": "MLB123", "seller_id": "10",
                    "seller_custom_field": "PARENT", "variations": [{"id": 1}]}}]
        assert path == "/items/MLB123/variations"
        return [{"id": 1, "seller_custom_field": "CHILD"}]
    env.handler = handler
    result = api.ml_perguntas_itens_rapidos(request(), "A", "MLB123", client_id="tenant")
    item = result["items"][0]
    assert item["item_sku"] == "PARENT" and item["variations"][0]["seller_custom_field"] == "CHILD"
