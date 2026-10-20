"""A repeated account import keeps the destination's durable store identity."""
import json
import hashlib
import io
import zipfile

import pandas as pd
import pytest
from fastapi import HTTPException

from test_shared_sync_user_pull_order import new_machine  # noqa: F401
from backend.services import central_accounts_migration
from backend.services import shared_sync_store_bindings as bindings


def store(store_id, prefix, *, seller='123'):
    return {'store_id': store_id, 'nome': 'Synthetic store', 'integracoes': {
        'mercadolivre': {'app_id': 'synthetic-app', 'client_secret': 'synthetic-secret',
                        'access_token': prefix + '-ml', 'refresh_token': prefix + '-ml-refresh',
                        'user_id': seller, 'site_id': 'MLB', 'connected': True},
        'bling': {'id': 'synthetic-bling-app', 'secret': 'synthetic-bling-secret',
                  'access_token': prefix + '-bling', 'refresh_token': prefix + '-bling-refresh',
                  'connected': True},
    }}


@pytest.fixture
def existing_account(new_machine, monkeypatch):
    env = new_machine
    local = store('local-original', 'local')
    remote = store('store-a', 'remote')
    original_publish = env.publish

    def publish(scope, files, *, source_connections=...):
        original_publish(scope, files)
        key = 'remote-' + scope
        with zipfile.ZipFile(io.BytesIO(env.bundles[key])) as archive:
            payloads = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(payloads['manifest.json'])
        if source_connections is ...:
            source_connections = {remote['store_id']: bindings._connection_hash(remote)}
        if source_connections is not None:
            manifest['store_connection_hashes'] = source_connections
        payloads['manifest.json'] = json.dumps(manifest).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in payloads.items():
                archive.writestr(name, data)
        env.bundles[key] = output.getvalue()
        digest = hashlib.sha256(env.bundles[key]).hexdigest()
        env.metas[key].update(snapshot_hash=digest, bundle_sha256=digest)

    env.publish = publish
    env.source_connections = lambda: {remote['store_id']: bindings._connection_hash(remote)}
    env.publish('cadastro', env.product_files)
    (env.tenant / 'lojas_config.json').write_text(json.dumps([local]))
    env.publish('lojas_integracoes', [('lojas_config.json', json.dumps([remote]).encode())])
    calls = []

    def identity(provider, token):
        calls.append((provider, token))
        return ('123', 'MLB') if provider == 'mercadolivre' else ('company-123', '')

    monkeypatch.setattr(central_accounts_migration, '_identity', identity)
    return env, local, remote, calls


def test_same_authenticated_accounts_preserve_local_store_and_translate_cadastro(existing_account):
    env, local, remote, calls = existing_account
    remote_bytes = dict(env.bundles)
    result = env.execute(env.preview())
    assert result['success']
    saved = json.loads((env.tenant / 'lojas_config.json').read_text())
    assert len(saved) == 1
    assert saved[0]['store_id'] == local['store_id']
    for provider in ('mercadolivre', 'bling'):
        assert saved[0]['integracoes'][provider]['access_token'] == local['integracoes'][provider]['access_token']
    frame = pd.read_csv(env.tenant / 'cadastro_produtos_lojas.csv', dtype=str)
    assert frame['store_id'].tolist() == ['local-original']
    assert calls
    assert env.bundles == remote_bytes
    env.execute(env.preview())
    assert len(json.loads((env.tenant / 'lojas_config.json').read_text())) == 1
    assert pd.read_csv(env.tenant / 'cadastro_produtos_lojas.csv', dtype=str)['store_id'].tolist() == ['local-original']


def test_same_name_different_authenticated_accounts_remain_separate(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    remote['integracoes']['mercadolivre']['user_id'] = '456'
    env.publish('lojas_integracoes', [('lojas_config.json', json.dumps([remote]).encode())])

    def identity(provider, token):
        account = '456' if token.startswith('remote') else '123'
        return (account, 'MLB') if provider == 'mercadolivre' else ('company-' + account, '')

    monkeypatch.setattr(central_accounts_migration, '_identity', identity)
    assert env.execute(env.preview())['success']
    assert {row['store_id'] for row in json.loads((env.tenant / 'lojas_config.json').read_text())} == {'local-original', 'store-a'}


def test_identity_lookup_failure_cannot_append_or_rewrite_store(existing_account, monkeypatch):
    env, *_ = existing_account
    original = (env.tenant / 'lojas_config.json').read_bytes()

    def unavailable(*args):
        raise HTTPException(503, 'synthetic unavailable')

    monkeypatch.setattr(central_accounts_migration, '_identity', unavailable)
    with pytest.raises(HTTPException):
        env.execute(env.preview())
    assert (env.tenant / 'lojas_config.json').read_bytes() == original
    assert not (env.tenant / 'cadastro_produtos_lojas.csv').exists()


def test_existing_duplicates_require_separate_consolidation_even_with_backup(existing_account):
    env, local, remote, _ = existing_account
    (env.tenant / 'lojas_config.json').write_text(json.dumps([local, remote]))
    (env.tenant / 'lojas_config.json.bak').write_text(json.dumps([local, remote]))
    backup = env.tenant / '_shared_sync_backups' / 'lojas_integracoes_20261020_000000'
    backup.mkdir(parents=True)
    (backup / 'lojas_config.json').write_text(json.dumps([local]))
    with pytest.raises(HTTPException) as caught:
        env.execute(env.preview())
    assert caught.value.detail['code'] == 'shared_sync_store_consolidation_required'
    for filename in ('lojas_config.json', 'lojas_config.json.bak'):
        rows = json.loads((env.tenant / filename).read_text())
        assert [row['store_id'] for row in rows] == ['local-original', 'store-a']
    assert not (env.tenant / 'cadastro_produtos_lojas.csv').exists()
    assert (backup / 'lojas_config.json').exists()


def test_duplicate_with_existing_product_data_requires_consolidation(existing_account):
    env, local, remote, _ = existing_account
    original = json.dumps([local, remote]).encode()
    (env.tenant / 'lojas_config.json').write_bytes(original)
    backup = env.tenant / '_shared_sync_backups' / 'lojas_integracoes_20261020_000000'
    backup.mkdir(parents=True)
    (backup / 'lojas_config.json').write_text(json.dumps([local]))
    costs = b'store_id,sku,custo\nstore-a,001,42\n'
    (env.tenant / 'cadastro_custos_lojas.csv').write_bytes(costs)
    with pytest.raises(HTTPException) as caught:
        env.execute(env.preview())
    assert caught.value.status_code == 409
    assert (env.tenant / 'lojas_config.json').read_bytes() == original
    assert (env.tenant / 'cadastro_custos_lojas.csv').read_bytes() == costs
