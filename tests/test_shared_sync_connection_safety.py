import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401
from backend.services import shared_sync_machine as machine
from backend.services import shared_sync_merge_integracoes as merge
from backend.services import shared_sync_apply_scope as apply
from backend.services import integracoes
from backend.services import shared_sync_remote as remote
from backend.services import shared_sync_config as config_module


@pytest.mark.parametrize('provider', ['bling', 'mercadolivre', 'mercadoturbo'])
@pytest.mark.parametrize('remote', [
    {}, {'connected': False, 'oauth_invalid': True},
    {'access_token': 'other', 'refresh_token': 'other-refresh', 'user_id': 'other-account'},
])
def test_machine_sync_never_replaces_existing_connection(provider, remote):
    local = {'access_token': 'local', 'refresh_token': 'local-refresh',
             'connected': True, 'oauth_invalid': False, 'oauth_pending_state': 'pending',
             'user_id': 'local-account', 'updated_at': '10'}
    before = copy.deepcopy(local)
    result = merge._shared_sync_merge_integracao_loja(
        local, remote, servico_key=provider, strict_oauth_conflicts=True,
        preserve_local_connections=True,
    )
    assert result == before
    assert local == before


def test_explicit_local_disconnect_is_not_reconnected_by_sync():
    local = {'connected': False, 'oauth_invalid': True}
    result = merge._shared_sync_merge_integracao_loja(
        local, {'access_token': 'old'}, servico_key='bling',
        preserve_local_connections=True,
    )
    assert result == local


def test_machine_auto_cannot_apply_connections_even_with_old_config(monkeypatch):
    monkeypatch.setattr(machine, '_shared_sync_machine_config_read', lambda _: {'enabled': True, 'auto_pull': True})
    monkeypatch.setattr(machine, '_shared_sync_machine_pull_scope', lambda *a, **k: pytest.fail('automatic pull must not apply connections'))
    result = machine._shared_sync_machine_auto_run({'client_id': 'test', 'username': 'test'}, 'this-machine')
    assert result['results'] == []
    assert result['skipped'][0]['reason'] == 'manual_only'


def test_public_routes_and_request_schemas_remain_compatible():
    contract = json.loads(Path('tests/contracts/shared_sync_connection_safety_v1.json').read_text())
    for filename, digest in contract['unchanged_contracts'].items():
        assert hashlib.sha256(Path(filename).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest


@pytest.mark.parametrize('failure', [False, 'io', 'connection_loss'])
def test_transaction_preserves_credentials_and_rolls_back(tmp_path, monkeypatch, failure):
    tenant = tmp_path / 'info' / 'tenant-a'
    tenant.mkdir(parents=True)
    integracoes.configure_integracoes_context(
        pasta_info=str(tenant.parent), get_tenant_path=lambda _: str(tenant),
        normalizar_integracao_conectada=lambda _service, data: data,
    )
    config = {'id': 'app', 'secret': 'secret', 'access_token': 'local-access',
              'refresh_token': 'local-refresh', 'connected': True, 'oauth_invalid': False}
    local = [{'store_id': 'a' * 32, 'nome': 'Store A', 'integracoes': {'bling': config}}]
    target = tenant / 'lojas_config.json'
    target.write_text(json.dumps(local), encoding='utf-8')
    legacy = tenant / 'integracoes.json'
    legacy.write_text(json.dumps({'Store A': {'bling': config}}), encoding='utf-8')
    original = target.read_bytes()
    original_legacy = legacy.read_bytes()
    remote = [{'store_id': 'a' * 32, 'nome': 'Store A', 'integracoes': {'bling': {
        **config, 'access_token': 'different', 'refresh_token': 'different-refresh',
        'connected': False, 'oauth_invalid': True}}},
        {'store_id': 'b' * 32, 'nome': 'Store B', 'integracoes': {}}]
    sources = [('lojas_config.json', json.dumps(remote).encode()),
               ('integracoes.json', json.dumps({'Store A': {'bling': remote[0]['integracoes']['bling']}}).encode())]
    write = apply._shared_sync_atomic_write
    if failure == 'connection_loss':
        commit = integracoes._integracoes_commit_lojas_tombstones
        def corrupt_after_commit(*args, **kwargs):
            commit(*args, **kwargs)
            corrupted = json.loads(target.read_bytes())
            corrupted[0]['integracoes']['bling']['connected'] = False
            target.write_text(json.dumps(corrupted), encoding='utf-8')
        monkeypatch.setattr(integracoes, '_integracoes_commit_lojas_tombstones', corrupt_after_commit)
    if failure:
        failed = False
        def fail_once(path, data):
            nonlocal failed
            if failure == 'io' and Path(path) == legacy and not failed:
                failed = True
                raise OSError('synthetic failure')
            return write(path, data)
        monkeypatch.setattr(apply, '_shared_sync_atomic_write', fail_once)
        with pytest.raises(HTTPException):
            apply._shared_sync_aplicar_lojas_integracoes('tenant-a', sources, str(tenant), str(tenant / '_backup'),
                add_only=True, strict_oauth_conflicts=True, preserve_local_connections=True)
        assert target.read_bytes() == original
    else:
        result = apply._shared_sync_aplicar_lojas_integracoes('tenant-a', sources, str(tenant), str(tenant / '_backup'),
            add_only=True, strict_oauth_conflicts=True, preserve_local_connections=True)
        saved = json.loads(target.read_bytes())
        assert len(saved) == 2
        assert saved[0]['integracoes']['bling']['access_token'] == config['access_token']
        assert saved[0]['integracoes']['bling']['refresh_token'] == config['refresh_token']
        assert saved[0]['integracoes']['bling']['connected'] is True
        assert result['connection_conflicts'] == 1
        assert result['connection_verification'] == 'not_performed'
    assert legacy.read_bytes() == original_legacy


def test_receipt_identity_cannot_be_spoofed():
    with pytest.raises(HTTPException) as caught:
        machine._shared_sync_machine_receipt_identity({'machine_id': 'signed-device'}, 'other-device')
    assert caught.value.status_code == 403


def test_local_stamp_detects_deleted_products(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, 'get_tenant_path', lambda _: str(tmp_path), raising=False)
    product = tmp_path / 'cadastro_produtos_lojas.csv'
    product.write_text('store_id,sku\nstore-a,001\n', encoding='utf-8')
    before = machine._shared_sync_machine_local_stamp({'client_id': 'test'}, 'cadastro')
    product.unlink()
    assert machine._shared_sync_machine_local_stamp({'client_id': 'test'}, 'cadastro') != before


def test_receipt_is_per_snapshot_tenant_user_and_signed_machine(monkeypatch):
    from types import SimpleNamespace
    data = {}
    class Ref:
        def __init__(self, path=''):
            self.path = path
        def collection(self, name):
            return Ref(self.path + '/' + name)
        def document(self, name):
            return Ref(self.path + '/' + name)
        def set(self, value, **kwargs):
            data[self.path] = copy.deepcopy(value)
        def limit(self, count):
            return self
        def stream(self, **kwargs):
            for key, value in data.items():
                if key.rsplit('/', 1)[0] == self.path:
                    yield SimpleNamespace(id=key.rsplit('/', 1)[1], to_dict=lambda v=value: v)
    monkeypatch.setattr(remote, '_shared_sync_firestore_required', lambda: Ref())
    monkeypatch.setattr(config_module, '_firebase_shared_sync_collection_name', lambda: 'synthetic-sync')
    a = {'client_id': 'tenant-a', 'username': 'user', 'machine_id': 'device-a'}
    b = {**a, 'machine_id': 'device-b'}
    meta = {'snapshot_id': 'snapshot-a', 'snapshot_hash': 'a' * 64}
    assert machine._shared_sync_machine_receipt(a, 'cadastro', 'device-a', meta)['state'] == 'awaiting_receipt'
    assert machine._shared_sync_machine_receipt(b, 'cadastro', 'device-b', meta, publish=True)['state'] == 'confirmed'
    assert machine._shared_sync_machine_receipt(a, 'cadastro', 'device-a', meta)['state'] == 'received'
    assert machine._shared_sync_machine_receipt(a, 'cadastro', 'device-a', {**meta, 'snapshot_hash': 'b' * 64})['state'] == 'awaiting_receipt'
    assert machine._shared_sync_machine_receipt({**a, 'client_id': 'tenant-b'}, 'cadastro', 'device-a', meta)['state'] == 'awaiting_receipt'
    assert machine._shared_sync_machine_receipt({**a, 'username': 'other'}, 'cadastro', 'device-a', meta)['state'] == 'awaiting_receipt'
    before = copy.deepcopy(data)
    assert machine._shared_sync_machine_receipt(a, 'cadastro', 'device-b', meta, publish=True)['state'] == 'confirmation_pending'
    assert data == before
    for value in data.values():
        assert set(value) == {'protocol', 'snapshot_hash', 'snapshot_id', 'applied_at', 'connection_conflicts', 'connection_verification'}


def test_receipt_outage_is_pending_and_never_disconnection(monkeypatch):
    monkeypatch.setattr(remote, '_shared_sync_firestore_required', lambda: (_ for _ in ()).throw(HTTPException(503)))
    result = machine._shared_sync_machine_receipt(
        {'client_id': 'test', 'username': 'test', 'machine_id': 'signed'}, 'lojas_integracoes', 'signed',
        {'snapshot_hash': 'a' * 64, 'snapshot_id': 'snapshot'}, publish=True)
    assert result['state'] == 'confirmation_pending'
