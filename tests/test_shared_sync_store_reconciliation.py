"""Durable IDs and historical bindings survive receipt without OAuth lookups."""
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


def seed_binding(env, local, remote, *, unverified=(), accounts=None):
    """Represent a binding authenticated by an earlier release, not by receipt."""
    peer = {
        'link_id': env.link['id'], 'sync_direction': 'source_to_target',
        'source_client_id': env.link['source_client_id'], 'source_username': env.link['source_username'],
        'target_client_id': env.link['target_client_id'], 'target_username': env.link['target_username'],
    }
    path = env.tenant / bindings.BINDINGS_FILE
    ledger = json.loads(path.read_bytes()) if path.exists() else {'schema': 1, 'peers': {}}
    record = {
        'local_id': local['store_id'], 'local_name': local['nome'], 'remote_name': remote['nome'],
        'accounts': accounts if accounts is not None else {
            'mercadolivre': ['123', 'MLB'], 'bling': ['company-123', ''],
        },
        'connection_hash': bindings._connection_hash(local),
        'remote_connection_hash': bindings._connection_hash(remote),
    }
    if unverified:
        record['unverified_providers'] = sorted(set(unverified))
    ledger['peers'][bindings._hash(peer)] = {'identity': peer, 'stores': {remote['store_id']: record}}
    path.write_text(json.dumps(ledger), encoding='utf-8')
    return ledger, ledger['peers'][bindings._hash(peer)]


@pytest.fixture
def bound_account(existing_account):
    env, local, remote, calls = existing_account
    seed_binding(env, local, remote)
    return env, local, remote, calls


def test_historical_binding_preserves_local_store_and_translates_cadastro(bound_account):
    env, local, remote, calls = bound_account
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
    assert calls == []
    assert env.bundles == remote_bytes
    env.execute(env.preview())
    assert len(json.loads((env.tenant / 'lojas_config.json').read_text())) == 1
    assert pd.read_csv(env.tenant / 'cadastro_produtos_lojas.csv', dtype=str)['store_id'].tolist() == ['local-original']


def test_same_name_different_account_claims_remain_separate(existing_account):
    env, local, remote, calls = existing_account
    remote['integracoes']['mercadolivre']['user_id'] = '456'
    env.publish('lojas_integracoes', [('lojas_config.json', json.dumps([remote]).encode())])
    assert env.execute(env.preview())['success']
    assert {row['store_id'] for row in json.loads((env.tenant / 'lojas_config.json').read_text())} == {'local-original', 'store-a'}
    assert calls == []
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()


def test_identity_lookup_unavailability_does_not_block_distinct_store_ids(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise HTTPException(503, 'synthetic unavailable')

    monkeypatch.setattr(central_accounts_migration, '_identity', unavailable)
    assert env.execute(env.preview())['success']
    saved = {row['store_id']: row for row in json.loads((env.tenant / 'lojas_config.json').read_bytes())}
    assert set(saved) == {local['store_id'], remote['store_id']}
    assert saved[local['store_id']]['integracoes']['bling']['access_token'] == local['integracoes']['bling']['access_token']
    assert pd.read_csv(env.tenant / 'cadastro_produtos_lojas.csv', dtype=str)['store_id'].tolist() == [remote['store_id']]
    assert calls == []


def test_existing_distinct_ids_are_not_consolidated_even_with_backup(existing_account):
    env, local, remote, _ = existing_account
    (env.tenant / 'lojas_config.json').write_text(json.dumps([local, remote]))
    (env.tenant / 'lojas_config.json.bak').write_text(json.dumps([local, remote]))
    backup = env.tenant / '_shared_sync_backups' / 'lojas_integracoes_20261020_000000'
    backup.mkdir(parents=True)
    (backup / 'lojas_config.json').write_text(json.dumps([local]))
    assert env.execute(env.preview())['success']
    for filename in ('lojas_config.json', 'lojas_config.json.bak'):
        rows = json.loads((env.tenant / filename).read_text())
        assert [row['store_id'] for row in rows] == ['local-original', 'store-a']
    assert pd.read_csv(env.tenant / 'cadastro_produtos_lojas.csv', dtype=str)['store_id'].tolist() == ['store-a']
    assert (backup / 'lojas_config.json').exists()


def test_distinct_store_with_existing_product_costs_remains_separate(existing_account):
    env, local, remote, _ = existing_account
    original = json.dumps([local, remote]).encode()
    (env.tenant / 'lojas_config.json').write_bytes(original)
    backup = env.tenant / '_shared_sync_backups' / 'lojas_integracoes_20261020_000000'
    backup.mkdir(parents=True)
    (backup / 'lojas_config.json').write_text(json.dumps([local]))
    costs = b'store_id,sku,custo\nstore-a,001,42\n'
    (env.tenant / 'cadastro_custos_lojas.csv').write_bytes(costs)
    assert env.execute(env.preview())['success']
    assert {row['store_id'] for row in json.loads((env.tenant / 'lojas_config.json').read_bytes())} == {local['store_id'], remote['store_id']}
    assert (env.tenant / 'cadastro_custos_lojas.csv').read_bytes() == costs
