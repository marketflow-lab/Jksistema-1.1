import threading
import time

import pytest
from fastapi import HTTPException
from requests import Timeout

from test_perguntas_loading_api import env, clean_cache, request, question
from backend.modules.perguntas_pos_venda.endpoints import questions_loading as api
from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support as support
from backend.services import perguntas_loading_cache as cache
from backend.services import perguntas_generation_preflight as preflight


@pytest.mark.parametrize("history_denied", [False, True])
def test_completed_complement_keeps_refreshed_connection_for_next_read(env, monkeypatch, history_denied):
    observed = []
    def remote(scope, cfg, path, **kwargs):
        if path == '/questions/1':
            cfg['synthetic_connection_generation'] = 1
            return question()
        if path == '/questions/search':
            cfg['synthetic_connection_generation'] = 2
            if history_denied:
                raise support.loading_error(403, 'Recurso restrito.')
            return {'questions': [question()], 'total': 1}
        observed.append(cfg.get('synthetic_connection_generation'))
        return {'id': 20, 'nickname': 'Comprador ficticio'}
    monkeypatch.setattr(support, 'remote', remote)
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert observed == [2]


def test_history_timeout_preserves_question_and_is_not_success(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            raise Timeout('history')
        return {'id': 20, 'nickname': 'buyer'}
    env.handler = handler
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert result['question']['id'] == '1'
    assert result['components']['question']['state'] == 'ready'
    assert result['components']['history']['state'] == 'unavailable'
    assert result['components']['history']['retryable'] is True


def test_buyer_denied_preserves_history_and_question(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 100, 'questions': [question()]}
        raise HTTPException(403, 'buyer denied')
    env.handler = handler
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert result['components']['history']['state'] == 'ready'
    assert result['components']['history']['truncated'] is True
    assert result['components']['buyer']['state'] == 'blocked'
    assert result['components']['buyer']['scope'] == 'resource'


def test_items_report_success_and_missing_individually(env):
    env.handler = lambda path, params: [
        {'code': 200, 'body': {'id': 'MLB123', 'seller_id': '10'}},
        {'code': 403, 'body': {'id': 'MLB456'}}]
    result = api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123,MLB456,MLB789', client_id='tenant')
    assert result['item_states']['MLB123']['state'] == 'ready'
    assert result['item_states']['MLB456']['state'] == 'blocked'
    assert result['item_states']['MLB789']['state'] == 'unavailable'
    assert result['missing_item_ids'] == ['MLB456', 'MLB789']


def test_primary_timeout_remains_504(env):
    def timeout(path, params):
        raise Timeout('primary')
    env.handler = timeout
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert error.value.status_code == 504


def test_hanging_history_cannot_consume_parent_deadline(env, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(api, '_HISTORY_SECONDS', .05)
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            release.wait(2)
            return {'total': 1, 'questions': [question()]}
        return {'id': 20}
    env.handler = handler
    try:
        started = time.monotonic()
        result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
        assert time.monotonic() - started < .5
        assert result['question']['id'] == '1'
        assert result['components']['history']['state'] == 'unavailable'
    finally:
        release.set()


def test_canonical_context_binds_all_identity_dimensions_and_ignores_browser(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 51, 'questions': [question()]}
        if path == '/items':
            return [{'code': 200, 'body': {'id': 'MLB123', 'seller_id': '10'}}]
        return {'id': 20}
    env.handler = handler
    result = api.canonical_context(request(), 'tenant', 'A', '1')
    assert result['scope'] == {'client_id': 'tenant', 'username': 'alice', 'store_id': 'A',
                              'seller_id': '10', 'site_id': 'MLB', 'name': 'Loja A'}
    assert all(result['components'][name]['state'] == 'ready' for name in ['question', 'history', 'item'])
    assert result['history_truncated'] is True
    env.rows.clear()
    with pytest.raises(HTTPException) as denied:
        api.canonical_context(request(), 'tenant', 'A', '1')
    assert denied.value.headers['X-JK-Error-Scope'] == 'store'


def _canonical_handler(path, params):
    if path == '/questions/1':
        return question()
    if path == '/questions/search':
        return {'total': 1, 'questions': [question()]}
    if path == '/items':
        return [{'code': 200, 'body': {'id': 'MLB123', 'seller_id': '10'}}]
    return {'id': 20, 'nickname': 'buyer'}


def test_fresh_canonical_context_reuses_browser_cache_without_provider_reads(env):
    env.handler = _canonical_handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    env.calls.clear()
    result = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert env.calls == []
    assert result['context_status']['source'] == 'fresh_cache'
    assert result['cache_revision'] == cache.revision('tenant', 'A')


def test_partial_multiget_ready_item_supports_two_generation_clicks(env):
    def partial_handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 1, 'questions': [question()]}
        if path == '/items':
            return [
                {'code': 200, 'body': {'id': 'MLB123', 'seller_id': '10'}},
                {'code': 429, 'body': {'id': 'MLB456'}},
            ]
        return {'id': 20, 'nickname': 'buyer'}
    env.handler = partial_handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    batch = api.ml_perguntas_itens_rapidos(
        request(), 'A', 'MLB123,MLB456', client_id='tenant',
    )
    assert batch['partial'] is True
    assert batch['item_states']['MLB123']['state'] == 'ready'
    assert batch['item_states']['MLB456']['retryable'] is True
    env.calls.clear()
    first = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    second = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert env.calls == []
    assert first['item']['id'] == second['item']['id'] == 'MLB123'
    assert first['components']['item']['state'] == second['components']['item']['state'] == 'ready'


def test_partial_multiget_refresh_retains_previous_ready_item(env):
    batches = iter([
        [
            {'code': 200, 'body': {'id': 'MLB123', 'seller_id': '10', 'title': 'original'}},
            {'code': 429, 'body': {'id': 'MLB456'}},
        ],
        [
            {'code': 503, 'body': {'id': 'MLB123'}},
            {'code': 200, 'body': {'id': 'MLB456', 'seller_id': '10', 'title': 'recuperado'}},
        ],
    ])
    env.handler = lambda path, _params: next(batches) if path == '/items' else {'id': 20}
    first = api.ml_perguntas_itens_rapidos(
        request(), 'A', 'MLB123,MLB456', client_id='tenant',
    )
    assert first['item_states']['MLB123']['state'] == 'ready'
    refreshed = api.ml_perguntas_itens_rapidos(
        request(), 'A', 'MLB123,MLB456', forcar=True, client_id='tenant',
    )
    by_id = {item['id']: item for item in refreshed['items']}
    assert by_id['MLB123']['title'] == 'original'
    assert by_id['MLB456']['title'] == 'recuperado'
    assert refreshed['item_states']['MLB123']['state'] == 'ready'
    assert refreshed['fallback_item_ids'] == ['MLB123']


def test_only_expired_item_is_refreshed(env):
    env.handler = _canonical_handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    with cache._LOCK:
        next(entry for key, entry in cache._ENTRIES.items() if key[5] == 'items').monotonic_at -= 61
    env.calls.clear()
    result = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert [path for path, _params in env.calls] == ['/items']
    assert result['context_status']['source'] == 'refreshed'


def test_only_expired_history_is_refreshed(env):
    env.handler = _canonical_handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    with cache._LOCK:
        detail = next(entry for key, entry in cache._ENTRIES.items() if key[5] == 'detail')
        detail.value['components']['history']['consultado_em'] -= 61_000
    env.calls.clear()
    result = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert [path for path, _params in env.calls] == ['/questions/search']
    assert result['components']['question']['state'] == 'ready'
    assert result['components']['history']['state'] == 'ready'


def test_transient_refresh_uses_valid_context_for_at_most_five_minutes(env):
    env.handler = _canonical_handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    with cache._LOCK:
        for entry in cache._ENTRIES.values():
            if entry.value.get('question') or entry.value.get('items'):
                entry.monotonic_at -= 120
            for component in (entry.value.get('components') or {}).values():
                if isinstance(component, dict) and component.get('consultado_em'):
                    component['consultado_em'] -= 120_000
    env.handler = lambda _path, _params: (_ for _ in ()).throw(Timeout('transient'))
    result = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert result['components']['history']['state'] == 'ready'
    assert result['components']['item']['state'] == 'ready'
    assert result['context_status']['source'] == 'stale_fallback'
    assert result['context_status']['warning']
    assert 119 <= result['context_status']['age_seconds'] <= 121
    repeated = api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert repeated['components']['history']['state'] == 'ready'
    assert repeated['components']['item']['state'] == 'ready'
    assert repeated['context_status']['source'] == 'stale_fallback'


def test_context_older_than_five_minutes_cannot_fallback(env):
    env.handler = _canonical_handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    with cache._LOCK:
        for entry in cache._ENTRIES.values():
            entry.monotonic_at -= 301
            for component in (entry.value.get('components') or {}).values():
                if isinstance(component, dict) and component.get('consultado_em'):
                    component['consultado_em'] -= 301_000
    env.handler = lambda _path, _params: (_ for _ in ()).throw(Timeout('transient'))
    with pytest.raises(HTTPException) as caught:
        api.canonical_context(request(), 'tenant', 'A', '1', force=False)
    assert caught.value.status_code == 503
    assert caught.value.headers['X-JK-Retryable'] == 'true'


def test_item_404_invalidates_cached_context_and_frozen_session(env):
    env.handler = _canonical_handler
    req = request()
    req.state.auth_payload = {'exp': time.time() + 3600}
    api.ml_perguntas_detalhe_rapido(req, 'A', '1', client_id='tenant')
    api.ml_perguntas_itens_rapidos(req, 'A', 'MLB123', client_id='tenant')
    canonical = api.canonical_context(req, 'tenant', 'A', '1', force=False)
    scope = support.resolve_scope(req, 'tenant', 'A')
    handle = preflight.remember_session(req, scope, '1', canonical)
    with cache._LOCK:
        next(entry for key, entry in cache._ENTRIES.items() if key[5] == 'items').monotonic_at -= 61
    env.handler = lambda path, _params: (
        [{'code': 404, 'body': {'id': 'MLB123'}}] if path == '/items' else _canonical_handler(path, {})
    )
    with pytest.raises(preflight.GenerationContextUnavailable) as caught:
        preflight.load_context(req, scope, '1')
    assert caught.value.headers['X-JK-Error-Component'] == 'item'
    assert caught.value.headers['X-JK-Error-Reason'] == 'not_found'
    assert handle not in preflight._SESSIONS
    assert not any(key[:2] == ('tenant', 'A') for key in cache._ENTRIES)


def test_optional_buyer_denial_does_not_clear_valid_list_cache(env):
    env.handler = lambda path, params: {'total': 1, 'questions': [question()]}
    api.ml_perguntas_lista_rapida(request(), 'A', client_id='tenant')
    list_key = next(key for key in cache._ENTRIES if key[5] == 'list')
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 1, 'questions': [question()]}
        raise HTTPException(401, 'buyer unavailable')
    env.handler = handler
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert result['components']['history']['state'] == 'ready'
    assert result['components']['buyer']['scope'] == 'resource'
    assert list_key in cache._ENTRIES


def test_remote_error_headers_preserve_safe_retry_after(env, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(support, '_ml_api_request', lambda *args, **kwargs: (
        SimpleNamespace(status_code=429, headers={'Retry-After': '9', 'Authorization': 'never-copy'}), {}))
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert error.value.headers == {'X-JK-Error-Scope': 'resource', 'X-JK-Error-Code': 'rate_limited',
                                   'X-JK-Retryable': 'true', 'Retry-After': '9'}


def test_confirmed_store_revocation_cannot_be_swallowed_as_optional(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        raise support.loading_error(403, 'revoked', scope='store')
    env.handler = handler
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert error.value.headers['X-JK-Error-Scope'] == 'store'


def test_explicit_invalid_token_is_store_revocation_even_for_optional_buyer(env, monkeypatch):
    from types import SimpleNamespace
    def remote(*args, **kwargs):
        path = args[4]
        if path.endswith('/questions/1'):
            payload = question()
        elif path.endswith('/questions/search'):
            payload = {'total': 1, 'questions': [question()]}
        else:
            return SimpleNamespace(status_code=401, json=lambda: {'error': 'invalid_token'}), {}
        return SimpleNamespace(status_code=200, json=lambda: payload), {}
    monkeypatch.setattr(support, '_ml_api_request', remote)
    with pytest.raises(HTTPException) as error:
        api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert error.value.headers['X-JK-Error-Scope'] == 'store'


def test_timed_out_complement_keeps_admission_until_network_worker_finishes():
    from backend.services.perguntas_loading_scheduler import ReadScheduler
    scheduler = ReadScheduler(workers=1)
    release, successor_started = threading.Event(), threading.Event()
    def slow():
        release.wait(2)
        return {}
    def parent():
        try:
            api._complement(slow, .05)
        except HTTPException as error:
            return support.component(error)
    try:
        first = scheduler.submit(('tenant', 'A', 'alice', '10', 'MLB', 'detail'), parent)
        assert first.result(1)['code'] == 'component_timeout'
        second = scheduler.submit(('tenant', 'B', 'alice', '11', 'MLB', 'detail'),
                                  lambda: successor_started.set())
        assert not successor_started.wait(.05)
        release.set()
        second.result(1)
        assert successor_started.is_set()
    finally:
        release.set()
        scheduler.shutdown()


@pytest.mark.parametrize('malformed', [{}, {'questions': {}}, {'questions': None},
                                      {'results': {}}, {'questions': [None]},
                                      {'questions': {}, 'results': []}])
def test_http_200_malformed_history_is_not_considered_loaded(env, malformed):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return malformed
        return {'id': 20}
    env.handler = handler
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert result['question']['id'] == '1'
    assert result['components']['history']['state'] == 'unavailable'
    assert result['components']['history']['retryable'] is True


@pytest.mark.parametrize('payload', [{'questions': []}, {'results': []}])
def test_explicit_empty_history_is_successful(env, payload):
    env.handler = lambda path, params: (question() if path == '/questions/1' else
                                        payload if path == '/questions/search' else {'id': 20})
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert result['components']['history']['state'] == 'ready'


@pytest.mark.parametrize('disconnected', [False, True])
def test_scope_revocation_discards_all_cached_data_before_reconnection(env, disconnected):
    from test_perguntas_loading_api import store
    env.handler = lambda path, params: [{'code': 200, 'body': {'id': 'MLB123', 'seller_id': '10'}}]
    api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    prior_revision = cache.revision('tenant', 'A')
    if disconnected:
        env.rows[0]['integracoes']['mercadolivre']['access_token'] = ''
    else:
        env.rows.clear()
    with pytest.raises(HTTPException):
        api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    assert not cache._ENTRIES
    assert cache.revision('tenant', 'A') != prior_revision
    env.rows = [store()]
    result = api.ml_perguntas_itens_rapidos(request(), 'A', 'MLB123', client_id='tenant')
    assert result['source'] == 'mercadolivre'
    assert len(env.calls) == 2


def test_history_retry_reads_only_history_and_preserves_original_cache_age(env, monkeypatch):
    normalizer = support._ml_perguntas_normalizar
    monkeypatch.setattr(support, '_ml_perguntas_normalizar', lambda q, items, users: {
        key: value for key, value in normalizer(q, items, users).items() if key != 'from'})
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            raise Timeout('first history attempt')
        return {'id': 20, 'nickname': 'buyer'}
    env.handler = handler
    first = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    key = next(key for key in cache._ENTRIES if key[5] == 'detail')
    cache._ENTRIES[key].monotonic_at -= 40
    origin = cache._ENTRIES[key].monotonic_at
    env.calls.clear()
    env.handler = lambda path, params: {'total': 1, 'questions': [question()]}
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant', componentes='history')
    assert [call[0] for call in env.calls] == ['/questions/search']
    assert result['components']['history']['state'] == 'ready'
    assert result['components']['question']['consultado_em'] == first['components']['question']['consultado_em']
    assert result['question']['buyer_name'] == 'buyer'
    assert cache._ENTRIES[key].monotonic_at == origin
    assert result['consultado_em'] == first['consultado_em']
    assert '_cache_origin' not in result
    assert '_cache_origin' not in cache._ENTRIES[key].value


def test_component_retry_never_reuses_expired_snapshot(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 1, 'questions': [question()]}
        return {'id': 20}
    env.handler = handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    key = next(key for key in cache._ENTRIES if key[5] == 'detail')
    cache._ENTRIES[key].monotonic_at -= 61
    env.calls.clear()
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant', componentes='history')
    assert [call[0] for call in env.calls] == ['/questions/1', '/questions/search', '/users/20']


def test_full_force_still_refreshes_every_detail_component(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 1, 'questions': [question()]}
        return {'id': 20}
    env.handler = handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    env.calls.clear()
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant', forcar=True)
    assert [call[0] for call in env.calls] == ['/questions/1', '/questions/search', '/users/20']


def test_component_retry_cannot_reuse_another_users_snapshot(env):
    def handler(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 1, 'questions': [question()]}
        return {'id': 20}
    env.handler = handler
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    env.calls.clear()
    api.ml_perguntas_detalhe_rapido(request(user='bob'), 'A', '1', client_id='tenant', componentes='history')
    assert [call[0] for call in env.calls] == ['/questions/1', '/questions/search', '/users/20']


def test_full_refresh_does_not_join_or_get_overwritten_by_partial_retry(env):
    from concurrent.futures import ThreadPoolExecutor
    def initial(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'total': 1, 'questions': [question()]}
        return {'id': 20}
    env.handler = initial
    api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    key = next(key for key in cache._ENTRIES if key[5] == 'detail')
    cache._ENTRIES[key].monotonic_at -= 30
    old_origin = cache._ENTRIES[key].monotonic_at
    partial_entered, release_partial = threading.Event(), threading.Event()
    search_calls = 0
    lock = threading.Lock()
    def handler(path, params):
        nonlocal search_calls
        if path == '/questions/1':
            return {**question(), 'text': 'Fresh primary from full refresh'}
        if path == '/questions/search':
            with lock:
                search_calls += 1
                current = search_calls
            if current == 1:
                partial_entered.set()
                assert release_partial.wait(2)
            return {'total': 1, 'questions': [question()]}
        return {'id': 20}
    env.handler = handler
    with ThreadPoolExecutor(max_workers=1) as executor:
        partial = executor.submit(api.ml_perguntas_detalhe_rapido, request(), 'A', '1',
                                  client_id='tenant', componentes='history')
        try:
            assert partial_entered.wait(1)
            full = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant', forcar=True)
            assert full['question']['text'] == 'Fresh primary from full refresh'
            assert search_calls == 2
        finally:
            release_partial.set()
        late = partial.result(2)
    assert late['question']['text'] == full['question']['text']
    assert cache._ENTRIES[key].monotonic_at > old_origin + 20
    assert cache._ENTRIES[key].value['question']['text'] == full['question']['text']


def test_missing_buyer_identity_preserves_question_but_cannot_confirm_history(env):
    incomplete = question()
    incomplete.pop('from')
    env.handler = lambda path, params: incomplete if path == '/questions/1' else {'questions': []}
    result = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant')
    assert result['question']['id'] == '1'
    assert result['components']['question']['state'] == 'ready'
    assert result['components']['history']['state'] == 'unavailable'
    assert result['components']['history']['code'] == 'buyer_identity_missing'
    assert result['components']['history']['retryable'] is True
    assert [call[0] for call in env.calls] == ['/questions/1']
    env.calls.clear()
    def recovered(path, params):
        if path == '/questions/1':
            return question()
        if path == '/questions/search':
            return {'questions': []}
        return {'id': 20}
    env.handler = recovered
    retry = api.ml_perguntas_detalhe_rapido(request(), 'A', '1', client_id='tenant', componentes='history')
    assert retry['components']['history']['state'] == 'ready'
    assert '/questions/1' in [call[0] for call in env.calls]
