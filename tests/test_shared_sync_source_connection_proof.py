"""Account proof is frozen with each user package and never exposes OAuth data."""
from contextlib import contextmanager
import io
import json
import zipfile

from test_shared_sync_photo_config_export import source  # noqa: F401
from backend.services import integracoes
from backend.services import shared_sync_bundle as bundle
from backend.services import shared_sync_store_bindings as bindings


def _connections(source):
    stores = source.stores
    stores[0]['integracoes'] = {'bling': {
        'id': 'synthetic-app', 'secret': 'SENTINEL_APP_SECRET',
        'access_token': 'SENTINEL_ACCESS_TOKEN', 'refresh_token': 'SENTINEL_REFRESH_TOKEN',
        'connected': True,
    }}
    path = source.tenant / 'lojas_config.json'
    path.write_text(json.dumps(stores), encoding='utf-8')
    return path, stores


def test_connection_proof_is_frozen_and_has_no_credentials(source):
    path, stores = _connections(source)
    captured = bundle._shared_sync_capture_entries(
        'source', 'cadastro', 'alice', user_only=True, capture_only=True,
    )
    expected = {row['store_id']: bindings._connection_hash(row) for row in stores}
    stores[0]['integracoes']['bling']['access_token'] = 'OTHER_ACCOUNT_TOKEN'
    path.write_text(json.dumps(stores), encoding='utf-8')
    payload, manifest, _ = bundle._shared_sync_montar_pacote_locked(
        'source', 'cadastro', 'alice', user_only=True, _captured=captured,
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        wire_manifest = json.loads(archive.read('manifest.json'))
    assert wire_manifest['store_connection_hashes'] == expected
    assert manifest['store_connection_hashes'] == expected
    proof_json = json.dumps(wire_manifest['store_connection_hashes'])
    assert all(value not in proof_json for value in (
        'SENTINEL_APP_SECRET', 'SENTINEL_ACCESS_TOKEN', 'SENTINEL_REFRESH_TOKEN', 'OTHER_ACCOUNT_TOKEN',
    ))
    assert all(len(value) == 64 for value in expected.values())
    assert wire_manifest['snapshot_hash'] == bundle._shared_sync_snapshot_hash(wire_manifest['files'])
    assert len(wire_manifest['files']) == wire_manifest['file_count']


def test_user_favorites_capture_connection_proof_under_store_lock(source, monkeypatch):
    _connections(source)
    active = []
    real_proof = bindings.source_connection_hashes

    @contextmanager
    def locked(_client):
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    def proof(data):
        assert active
        return real_proof(data)

    monkeypatch.setattr(integracoes, '_integracoes_bloquear_rmw_lojas', locked)
    monkeypatch.setattr(bindings, 'source_connection_hashes', proof)
    _, manifest, _ = bundle._shared_sync_montar_pacote(
        'source', 'favoritos_planilhas', 'alice', user_only=True,
    )
    assert set(manifest['store_connection_hashes']) == {'store-a', 'store-b'}
    assert not active


def test_oauth_transient_metadata_does_not_invalidate_account_proof(source):
    _, stores = _connections(source)
    expected = bindings._connection_hash(stores[0])
    stores[0]['integracoes']['bling'].update(
        oauth_connection_id='transient-nonce', _sync_received_at='synthetic-time',
        oauth_state='transient-auth-state',
    )
    assert bindings._connection_hash(stores[0]) == expected
