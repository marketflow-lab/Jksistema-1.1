from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from backend.services import remote_auth_verification as verification
from tests.test_remote_auth_client import NOW, NONCE, success_payload, token_claims, ENV


STORE = {'store_id': 'a' * 32, 'nome': 'Shop', 'owner_client_id': 'tenant-a', 'access': 'owner',
         'integracoes': {'bling': {'central': True, 'connected': True, 'user_id': '123', 'site_id': ''}}}


def payload():
    value = success_payload()
    value['central'] = {'protocol': 1, 'sync_mode': 'provider', 'providers': ['bling'],
                        'session': 's' * 200, 'expires_at': NOW + 28800, 'stores': [copy.deepcopy(STORE)]}
    return value


def validate(value):
    return verification.validate_success_payload(value, 'usuario', 'pc:maquina-1', NONCE, 'authenticated')


def test_provider_scope_is_part_of_signed_login_and_cannot_be_changed():
    import requests
    from backend.services.remote_auth_contracts import load_remote_auth_configuration
    value = payload()
    claims = token_claims(value)
    claims['jk_response_hash'] = verification.canonical_hash({
        key: value[key] for key in ('user_data', 'permissions', 'policy', 'request_nonce', 'central')})
    verified = validate(value)
    verification.verify_signed_response(verified, load_remote_auth_configuration(ENV), requests.Session(),
                                         verifier=lambda *a, **k: claims, now=NOW)
    changed = copy.deepcopy(value)
    changed['central'].pop('providers')
    changed['central']['sync_mode'] = 'manual'
    with pytest.raises(ValueError, match='invalid_response_claim'):
        verification.verify_signed_response(validate(changed), load_remote_auth_configuration(ENV),
                                             requests.Session(), verifier=lambda *a, **k: claims, now=NOW)


@pytest.mark.parametrize('change', ['scope', 'other_provider', 'extra_field', 'scope_without_mode'])
def test_closed_scope_rejects_unsupported_or_mismatched_bootstrap(change):
    value = payload()
    if change == 'scope':
        value['central']['providers'] = ['mercadolivre']
    elif change == 'other_provider':
        value['central']['stores'][0]['integracoes']['mercadolivre'] = copy.deepcopy(STORE['integracoes']['bling'])
    elif change == 'extra_field':
        value['central']['refresh_token'] = 'forbidden'
    else:
        value['central']['sync_mode'] = 'manual'
    with pytest.raises(ValueError):
        validate(value)


def test_provider_login_registers_central_and_keeps_legacy_firebase_and_presence(monkeypatch):
    from backend.services import admin_usuarios_auth as auth, central_accounts_client as client
    from backend.services import central_accounts_migration as migration, central_accounts_store_index as index
    from backend.services.remote_auth_contracts import RemoteAuthAttempt, RemoteAuthState
    from backend.schemas import LoginResponse
    from tests.test_admin_usuarios_remote_auth import configure_common, request
    configure_common(monkeypatch)
    attempt = RemoteAuthAttempt(RemoteAuthState.SUCCESS,
        user_data={'username': 'owner', 'client_id': 'tenant-a', 'machine_id': 'machine-a'},
        permissions={'integracao': True}, policy={'active': True, 'max_machines': 2},
        central=payload()['central'])
    calls = []
    monkeypatch.setattr(auth, '_salvar_perfil_autenticado_remoto', lambda *a: {})
    monkeypatch.setattr(auth, '_montar_resposta_login_sucesso', lambda *a: LoginResponse(
        success=True, message='ok', user_data={'username': 'owner'}, access_token='local-jwt'), raising=False)
    monkeypatch.setattr(client, 'register_login', lambda *a: SimpleNamespace(
        provider_scoped=True, expires_at=NOW + 28800, public_stores=lambda: [STORE]))
    monkeypatch.setattr(migration, 'finalize_after_central_login', lambda *a, **k: calls.append(('finalize', k)))
    monkeypatch.setattr(index, 'materialize_store_index', lambda *a, **k: calls.append(('index', k)))
    monkeypatch.setattr(auth, '_registrar_login_maquina', lambda *a: calls.append(('audit', {})))
    monkeypatch.setattr(auth, '_machine_presence_save', lambda *a: calls.append(('presence', {})))
    monkeypatch.setattr(auth, '_conectar_firebase_apos_login', lambda response, _: (
        calls.append(('firebase', {})) or response))
    result = auth._complete_remote_login(attempt, request(), '1.0.347')
    assert result.success
    assert result.user_data['central']['sync_mode'] == 'provider'
    assert result.user_data['central']['providers'] == ['bling']
    assert calls == [('finalize', {'providers': ['bling']}), ('index', {'providers': ['bling']}),
                     ('audit', {}), ('presence', {}), ('firebase', {})]
