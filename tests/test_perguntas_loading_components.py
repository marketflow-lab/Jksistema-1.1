import threading
import time

import pytest
from fastapi import HTTPException
from requests import Timeout

from test_perguntas_loading_api import env, clean_cache, request, question
from backend.modules.perguntas_pos_venda.endpoints import questions_loading as api
from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support as support
from backend.services import perguntas_loading_cache as cache


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
