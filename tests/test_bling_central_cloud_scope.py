"""Provider-only rollout keeps the legacy integrations and one refresh authority."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from cloud.auth_gateway.app.central_accounts import CentralError, key_for
from cloud.auth_gateway.app.central_contracts import LegacyAdoptionRequest, ProviderRequest, StoreGrant
from cloud.auth_gateway.app.contracts import GatewayLoginRequest
from cloud.auth_gateway.app.domain import AuthenticationService
from cloud.auth_gateway.app.errors import AuthRejected
from cloud.auth_gateway.app.main import create_app
from tests.test_auth_gateway_domain import FakeIssuer
from tests.test_central_accounts import central, legacy_request


@pytest.fixture
def scoped(central, monkeypatch):
    central.enrollment_required = True

    def enroll(username, tenant, machine, epoch, providers):
        row = central.users.rows[username]
        assert row['client_id'] == tenant and machine in row['machine_ids']
        assert key_for(row['password_hash']) == epoch
        assert providers == ['bling']
        row['central_accounts_providers'] = ['bling']
        return copy.deepcopy(row)

    def enroll_shared(username, tenant):
        row = central.users.rows[username]
        assert row['client_id'] == tenant
        row['central_accounts_providers'] = ['bling']
        return copy.deepcopy(row)

    monkeypatch.setattr(central.users, 'enable_central_accounts_providers', enroll, raising=False)
    monkeypatch.setattr(central.users, 'enable_central_provider_access', enroll_shared, raising=False)
    return central


def adoption(operation='c', count=1):
    data = legacy_request(count).model_dump()
    data.update(operation_id=operation * 32, providers=['bling'], legacy_copies_stopped=True)
    for row in data['stores']:
        row['connections'] = [cfg for cfg in row['connections'] if cfg['provider'] == 'bling']
    return LegacyAdoptionRequest(**data)


def migration_actor(central):
    value = central.migration_session(central.users.get_user('owner'), 'owner', 'machine-a')
    return central.authenticate_migration(value['session'], 'machine-a')


def actor(central, username='owner', machine='machine-a'):
    value = central.bootstrap_session(central.users.get_user(username), username, machine)
    return central.authenticate(value['session'], machine)


def query():
    return ProviderRequest(provider='bling', method='GET', path='/pedidos/vendas', request_id='b' * 32)


def test_scoped_adoption_keeps_other_provider_grants_and_store_metadata(scoped):
    payload = adoption()
    store_id = payload.stores[0].store_id
    owner = migration_actor(scoped)
    before = {'store_id': store_id, 'tenant': owner.tenant, 'owner': owner.key, 'name': 'Existing',
              'connections': {'mercadolivre': {'id': 'ml-id', 'seller_id': 'ml-seller', 'site_id': 'MLB'}},
              'grants': {owner.key: 'owner', 'reader-key': 'read'},
              'access_keys': [owner.key, 'reader-key'], 'custom_metadata': {'preserve': True}}
    scoped.documents.change('stores', key_for('store', store_id), lambda _: copy.deepcopy(before))

    scoped.adopt_legacy(owner, payload)

    saved = scoped.documents.get('stores', key_for('store', store_id))
    assert saved['connections']['mercadolivre'] == before['connections']['mercadolivre']
    assert saved['grants'] == before['grants'] and saved['access_keys'] == before['access_keys']
    assert saved['custom_metadata'] == before['custom_metadata']
    user = scoped.users.rows['owner']
    assert user['central_accounts_providers'] == ['bling']
    assert user.get('central_accounts_enabled') is not True
    bootstrap = scoped.bootstrap_session(user, 'owner', 'machine-b')
    assert bootstrap['sync_mode'] == 'provider' and bootstrap['providers'] == ['bling']
    assert set(bootstrap['stores'][0]['integracoes']) == {'bling'}


def test_scoped_adoption_requires_stopped_legacy_copies_before_any_provider_call(scoped):
    payload = adoption().model_copy(update={'legacy_copies_stopped': False})
    with pytest.raises(CentralError, match='central_legacy_copies_active'):
        scoped.adopt_legacy(migration_actor(scoped), payload)
    assert not scoped.documents.accessible(key_for('tenant-a', 'owner'))
    assert not scoped.provider.requests and scoped.provider.refreshes == 0


def test_provider_scope_rejects_ml_operations_and_cannot_be_elevated(scoped):
    scoped.adopt_legacy(migration_actor(scoped), adoption())
    bootstrap = scoped.bootstrap_session(scoped.users.get_user('owner'), 'owner', 'machine-a')
    owner = scoped.authenticate(bootstrap['session'], 'machine-a')
    payload = ProviderRequest(provider='mercadolivre', method='GET', path='/orders/search', request_id='e' * 32)
    with pytest.raises(CentralError, match='central_provider_denied'):
        scoped.request(owner, adoption().stores[0].store_id, payload)
    scoped.users.rows['owner']['central_accounts_enabled'] = True
    with pytest.raises(CentralError, match='central_session_expired'):
        scoped.authenticate(bootstrap['session'], 'machine-a')
    assert not scoped.provider.requests


def test_scoped_connections_have_one_refresh_across_computers(scoped):
    payload = adoption()
    scoped.adopt_legacy(migration_actor(scoped), payload)
    owner, other_machine = actor(scoped), actor(scoped, machine='machine-b')
    store_id = payload.stores[0].store_id
    connection_id = scoped.documents.get('stores', key_for('store', store_id))['connections']['bling']['id']
    row = scoped.documents.get('connections', connection_id)
    credentials = scoped.vault.open('connection:' + connection_id, row['sealed'])
    credentials['expires_at'] = scoped.clock() - 1
    scoped.documents.change('connections', connection_id, lambda current: {
        **current, 'sealed': scoped.vault.seal('connection:' + connection_id, credentials)})
    scoped.provider.entered, scoped.provider.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(scoped.request, owner, store_id, query())
        assert scoped.provider.entered.wait(5)
        try:
            with pytest.raises(CentralError, match='central_refresh_busy'):
                scoped.request(other_machine, store_id, query())
        finally:
            scoped.provider.release.set()
        assert first.result()['status'] == 200
    assert scoped.request(other_machine, store_id, query())['status'] == 200
    assert scoped.provider.refreshes == 1


def test_scoped_grant_enrolls_only_bling_for_recipient_and_revocation_is_immediate(scoped):
    payload = adoption()
    scoped.adopt_legacy(migration_actor(scoped), payload)
    owner, store_id = actor(scoped), payload.stores[0].store_id
    scoped.grant(owner, store_id, StoreGrant(username='other', access='read'))
    assert scoped.users.rows['other']['central_accounts_providers'] == ['bling']
    assert scoped.users.rows['other'].get('central_accounts_enabled') is not True
    recipient = actor(scoped, username='other')
    assert scoped.request(recipient, store_id, query())['status'] == 200
    scoped.grant(owner, store_id, StoreGrant(username='other', access='revoke'))
    with pytest.raises(CentralError, match='central_store_denied'):
        scoped.request(recipient, store_id, query())


def test_scoped_user_requires_new_capability_and_gets_signed_provider_extension(scoped):
    scoped.adopt_legacy(migration_actor(scoped), adoption())
    issuer = FakeIssuer()
    service = AuthenticationService(scoped.users, issuer, '1.0.133', central=scoped)
    login = GatewayLoginRequest(username='owner', password='password', machine_id='machine-b',
                                app_version='1.0.347', request_nonce='n' * 43)
    for flags in ({}, {'central_protocol': True}):
        with pytest.raises(AuthRejected) as error:
            service.authenticate(login, **flags)
        assert error.value.code == 'update_required'
    result = service.authenticate(login, central_protocol=True, bling_protocol=True)
    assert result['central']['sync_mode'] == 'provider'
    from cloud.auth_gateway.app.policy import canonical_hash
    assert issuer.calls[-1][1]['jk_response_hash'] == canonical_hash({
        key: result[key] for key in ('user_data', 'permissions', 'policy', 'request_nonce', 'central')})


def test_scoped_http_migration_accepts_desktop_token_for_next_pilot(scoped):
    scoped.adopt_legacy(migration_actor(scoped), adoption())
    capability = scoped.bootstrap_session(scoped.users.get_user('owner'), 'owner', 'machine-a')
    session = TestClient(create_app(central=scoped))
    headers = {'Authorization': 'Bearer ' + capability['session'], 'X-JK-Machine': 'machine-a'}
    payload = adoption('d', 2).model_dump(mode='json')
    # The second pilot does not resubmit or replace the first connection.
    payload['stores'] = payload['stores'][1:]
    response = session.post('/api/central/v1/migrations/legacy', headers=headers, json=payload)
    assert response.status_code == 200, response.json()
    assert len(session.get('/api/central/v1/bootstrap', headers=headers).json()['stores']) == 2


@pytest.mark.parametrize('operation', ['grant', 'revoke', 'delete'])
def test_provider_scoped_store_actions_cannot_modify_a_mixed_store(scoped, operation):
    payload = adoption()
    scoped.adopt_legacy(migration_actor(scoped), payload)
    owner, store_id = actor(scoped), payload.stores[0].store_id
    # A recipient already using the full Central must not gain the unrelated ML
    # connection through a grant made from a Bling-only session.
    scoped.users.rows['other']['central_accounts_enabled'] = True
    scoped.documents.change('stores', key_for('store', store_id), lambda current: {
        **current, 'connections': {**current['connections'],
                                  'mercadolivre': {'id': 'ml-id', 'seller_id': 'ml-seller', 'site_id': 'MLB'}}})
    before = scoped.documents.get('stores', key_for('store', store_id))
    users_before = copy.deepcopy(scoped.users.rows)
    with pytest.raises(CentralError, match='central_provider_scope_conflict'):
        if operation == 'delete':
            scoped.delete_store(owner, store_id)
        else:
            scoped.grant(owner, store_id, StoreGrant(username='other', access='revoke' if operation == 'revoke' else 'read'))
    assert scoped.documents.get('stores', key_for('store', store_id)) == before
    assert scoped.users.rows == users_before


def alias_adoption():
    data = adoption(count=2).model_dump()
    data['stores'][1]['connections'] = copy.deepcopy(data['stores'][0]['connections'])
    return data


@pytest.mark.parametrize('different_access', [False, True])
def test_scoped_aliases_create_one_connection_and_share_the_same_refresh_authority(scoped, monkeypatch, different_access):
    data = alias_adoption()
    data['stores'][0]['connections'][0]['expires_at'] = 1
    data['stores'][1]['connections'][0]['expires_at'] = 1
    if different_access:
        data['stores'][1]['connections'][0]['access_token'] = 'newer-valid-access'
        data['stores'][1]['connections'][0]['expires_at'] = int(scoped.clock()) + 5000
    payload = LegacyAdoptionRequest(**data)
    writes = []
    change = scoped.documents.change

    def record(group, key, transform):
        if group == 'connections':
            writes.append(key)
        return change(group, key, transform)

    monkeypatch.setattr(scoped.documents, 'change', record)
    scoped.adopt_legacy(migration_actor(scoped), payload)
    stores = scoped.list_stores(actor(scoped))
    assert len(stores) == 2
    assert len(writes) == 1
    rows = [scoped.documents.get('stores', key_for('store', row.store_id)) for row in payload.stores]
    assert rows[0]['connections']['bling']['id'] == rows[1]['connections']['bling']['id'] == writes[0]
    connection = scoped.documents.get('connections', writes[0])
    credentials = scoped.vault.open('connection:' + writes[0], connection['sealed'])
    expected = payload.stores[1].connections[0] if different_access else payload.stores[0].connections[0]
    assert credentials['access_token'] == expected.access_token.get_secret_value()
    assert credentials['expires_at'] == expected.expires_at
    assert not scoped.provider.refreshes


@pytest.mark.parametrize('field', ['refresh_token', 'app_secret'])
def test_scoped_alias_conflict_is_rejected_before_connection_store_or_user_writes(scoped, monkeypatch, field):
    data = adoption(count=3).model_dump()
    # The first, unrelated connection must not be partially published either.
    data['stores'][2]['connections'] = copy.deepcopy(data['stores'][1]['connections'])
    data['stores'][2]['connections'][0][field] = 'divergent-credential'
    before_users = copy.deepcopy(scoped.users.rows)
    writes = []
    change = scoped.documents.change

    def record(group, key, transform):
        if group in {'connections', 'stores'}:
            writes.append((group, key))
        return change(group, key, transform)

    monkeypatch.setattr(scoped.documents, 'change', record)
    with pytest.raises(CentralError, match='central_alias_credentials_conflict'):
        scoped.adopt_legacy(migration_actor(scoped), LegacyAdoptionRequest(**data))
    assert not writes
    assert scoped.users.rows == before_users
    assert not scoped.provider.refreshes


@pytest.mark.parametrize('failure_stage', ['store', 'enrollment'])
def test_scoped_unknown_expiry_adoption_resumes_without_replacing_saved_credentials(scoped, monkeypatch, failure_stage):
    data = adoption().model_dump()
    data['stores'][0]['connections'][0]['expires_at'] = 1
    payload = LegacyAdoptionRequest(**data)
    owner = migration_actor(scoped)
    pending_failure = [True]
    change = scoped.documents.change
    enroll = scoped.users.enable_central_accounts_providers

    def fail_store_once(group, key, transform):
        if failure_stage == 'store' and group == 'stores' and pending_failure[0]:
            pending_failure[0] = False
            raise RuntimeError('synthetic store outage')
        return change(group, key, transform)

    def fail_enrollment_once(*args):
        if failure_stage == 'enrollment' and pending_failure[0]:
            pending_failure[0] = False
            raise RuntimeError('synthetic enrollment outage')
        return enroll(*args)

    monkeypatch.setattr(scoped.documents, 'change', fail_store_once)
    monkeypatch.setattr(scoped.users, 'enable_central_accounts_providers', fail_enrollment_once)
    with pytest.raises(CentralError, match='central_migration_unavailable'):
        scoped.adopt_legacy(owner, payload)
    connection_id = key_for('bling', payload.stores[0].connections[0].app_id, 'account-0')
    saved = scoped.documents.get('connections', connection_id)
    assert saved['status'] == 'ready'
    assert scoped.vault.open('connection:' + connection_id, saved['sealed'])['expires_at'] == 1

    result = scoped.adopt_legacy(owner, payload)

    assert result['success'] and result['status'] == 'completed'
    assert scoped.documents.get('connections', connection_id) == saved
    assert len(scoped.list_stores(actor(scoped))) == 1
    assert scoped.provider.refreshes == 0
