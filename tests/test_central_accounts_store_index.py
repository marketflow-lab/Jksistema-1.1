import copy
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import central_accounts_client as client
from backend.services import central_accounts_store_index as index
from backend.services import integracoes


STORE = {"store_id": "a" * 24, "nome": "Same name", "owner_client_id": "tenant-a", "access": "owner",
         "integracoes": {"mercadolivre": {"connected": True, "central": True,
                                         "user_id": "123", "site_id": "MLB"}}}


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda name: str(tmp_path / name))
    path = tmp_path / "tenant-a"
    path.mkdir()
    token = client._current.set(None)
    yield path
    client._current.reset(token)


def test_index_preserves_exact_ids_local_metadata_and_removes_provider_secrets(tenant):
    other = {"store_id": "b" * 24, "nome": STORE["nome"], "integracoes": {}}
    local = {"store_id": STORE["store_id"], "nome": "Old name", "custom": {"photo_group": "group"},
             "integracoes": {"mercadolivre": {"access_token": "private", "refresh_token": "private"},
                             "mercadoturbo": {"token": "local-only"}}}
    path = tenant / "lojas_config.json"
    path.write_text(json.dumps([local, other]))
    index.materialize_store_index("tenant-a", [STORE])
    result = json.loads(path.read_text())
    assert result[0]["store_id"] == STORE["store_id"]
    assert result[0]["custom"] == local["custom"]
    assert result[0]["integracoes"]["mercadoturbo"] == local["integracoes"]["mercadoturbo"]
    assert result[1] == other
    cfg = result[0]["integracoes"]["mercadolivre"]
    assert cfg == {**STORE["integracoes"]["mercadolivre"], "central_migrated": True}
    with pytest.raises(HTTPException):
        index.assert_legacy_sync_allowed("tenant-a")


@pytest.mark.parametrize("restored, materialized", [(False, False), (True, True)])
def test_index_respects_deleted_and_restored_stores(tenant, restored, materialized):
    event = {"type": "store", "store_id": STORE["store_id"],
             "version": 2, "deleted_at": "2026-09-01T00:00:00Z"}
    if restored:
        event.pop("deleted_at")
        event["restored_at"] = "2026-09-02T00:00:00Z"
    (tenant / "lojas_sync_tombstones.json").write_text(json.dumps([event]))
    result = index.materialize_store_index("tenant-a", [STORE])
    assert bool(result) is materialized


def test_failed_central_refresh_preserves_disk_and_memory(tenant, monkeypatch):
    path = tenant / "lojas_config.json"
    path.write_text("[]")
    central = client.CentralClient(
        {"session": "private", "expires_at": int(time.time()) + 60, "stores": [STORE]},
        {"client_id": "tenant-a", "username": "owner", "machine_id": "machine-a"}, {},
        configuration=SimpleNamespace(url="https://central.example.test"))
    monkeypatch.setattr(central, "call", lambda *args: (_ for _ in ()).throw(HTTPException(503, "unavailable")))
    with pytest.raises(HTTPException):
        central.refresh_stores()
    assert path.read_text() == "[]"
    assert central.public_stores() == [STORE]


def test_central_refresh_materializes_new_store_and_legacy_guard_checks_session(tenant, monkeypatch):
    central = client.CentralClient(
        {"session": "private", "expires_at": int(time.time()) + 60, "stores": []},
        {"client_id": "tenant-a", "username": "owner", "machine_id": "machine-a"}, {},
        configuration=SimpleNamespace(url="https://central.example.test"))
    client._current.set(central)
    with pytest.raises(HTTPException):
        index.assert_legacy_sync_allowed("tenant-a")
    monkeypatch.setattr(central, "call", lambda *args: {"stores": [copy.deepcopy(STORE)]})
    assert central.refresh_stores() == [STORE]
    saved = json.loads((tenant / "lojas_config.json").read_text())
    assert saved[0]["store_id"] == STORE["store_id"]
    assert "private" not in json.dumps(saved)


def test_bad_index_or_unexpected_remote_fields_fail_without_overwrite(tenant):
    path = tenant / "lojas_config.json"
    path.write_text("not json")
    with pytest.raises(HTTPException):
        index.materialize_store_index("tenant-a", [STORE])
    assert path.read_text() == "not json"
    remote = copy.deepcopy(STORE)
    remote["integracoes"]["mercadolivre"]["access_token"] = "must-not-pass"
    with pytest.raises(ValueError):
        index.materialize_store_index("tenant-a", [remote])
    assert path.read_text() == "not json"


def test_index_reads_after_cross_process_lock_preserving_concurrent_local_metadata(tenant, monkeypatch):
    path = tenant / "lojas_config.json"
    path.write_text("[]")
    original_lock = integracoes._integracoes_bloquear_rmw_lojas

    @contextmanager
    def concurrent_writer_before_lock(client_id):
        # Another process commits before our lock acquisition completes.
        path.write_text(json.dumps([{"store_id": STORE["store_id"], "nome": "Local",
                                     "custom": "latest local metadata", "integracoes": {}}]))
        with original_lock(client_id) as acquired:
            yield acquired

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", concurrent_writer_before_lock)
    index.materialize_store_index("tenant-a", [STORE])
    assert json.loads(path.read_text())[0]["custom"] == "latest local metadata"
