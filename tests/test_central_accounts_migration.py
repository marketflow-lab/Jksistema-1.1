from __future__ import annotations

import json
import time

import pytest
from fastapi import HTTPException

from backend.services import central_accounts_migration as migration
from backend.services import integracoes


def stores_fixture(count=11):
    stores = []
    for index in range(count):
        store_id = f"{index + 1:024x}"
        stores.append({
            "store_id": store_id,
            "nome": f"Store {index + 1}",
            "integracoes": {
                "mercadolivre": {
                    "app_id": f"ml-app-{index}", "client_secret": f"ml-secret-{index}",
                    "access_token": f"ml-access-{index}", "refresh_token": f"ml-refresh-{index}",
                    "expires_at": int(time.time()) + 7200, "user_id": f"account-{index}",
                    "site_id": "MLB", "connected": True,
                },
                "bling": {
                    "id": f"bling-app-{index}", "secret": f"bling-secret-{index}",
                    "access_token": f"bling-access-{index}", "refresh_token": f"bling-refresh-{index}",
                    "expires_at": int(time.time()) + 7200, "empresa_id": f"account-{index}",
                    "connected": True,
                },
                **({"mercadoturbo": {"token": "turbo-private", "connected": True}}
                   if index == 0 else {}),
            },
        })
    return stores


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    info = tmp_path / "info"
    tenant = info / "tenant-a"
    tenant.mkdir(parents=True)
    (tenant / "lojas_config.json").write_text(
        json.dumps(stores_fixture(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda client_id: str(info / client_id))
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(info))
    monkeypatch.setattr(migration, "_identity", lambda provider, token: (
        "account-" + token.rsplit("-", 1)[-1], "MLB" if provider == "mercadolivre" else ""))
    secure = {}
    monkeypatch.setattr(migration, "secure_store_available", lambda: True)
    monkeypatch.setattr(migration, "write_scoped_secret", lambda target, value: secure.__setitem__(target, value) is None or True)
    monkeypatch.setattr(migration, "read_scoped_secret", lambda target: secure.get(target, ""))
    monkeypatch.setattr(migration, "delete_scoped_secret",
                        lambda target: (secure.pop(target, None), True)[1])
    return tenant, secure


class Central:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.failures:
            self.failures -= 1
            raise HTTPException(503, {"code": "central_unavailable", "message": "Central indisponível."})
        if method == "GET":
            raise HTTPException(404, {"code": "central_migration_not_found"})
        return {"success": True, "status": "completed", "operation_id": body["operation_id"]}


def test_migration_readiness_verifies_route_without_changing_anything():
    client = Central()
    migration.ensure_available(client)
    assert [call[0] for call in client.calls] == ["GET"]


@pytest.mark.parametrize("status, detail, expected", [(404, {"code": "central_unavailable"}, 503),
                                                      (401, "expired", 401),
                                                      (503, "unavailable", 503)])
def test_migration_readiness_reports_missing_service_or_authorization(status, detail, expected):
    class Missing:
        def call(self, *args):
            raise HTTPException(status, detail)
    with pytest.raises(HTTPException) as error:
        migration.ensure_available(Missing())
    assert error.value.status_code == expected


def test_preview_of_11_stores_and_22_connections_contains_no_credentials(local_config):
    result = migration.preview("tenant-a")
    assert result["stores_total"] == 11
    assert result["connections_total"] == 22
    assert result["turbo_local_total"] == 1
    serialized = json.dumps(result)
    assert "access_token" not in serialized and "refresh_token" not in serialized
    assert "private" not in serialized and "secret-" not in serialized


def test_complete_local_migration_backs_up_then_scrubs_only_ml_and_bling(local_config):
    tenant, secure = local_config
    preview = migration.preview("tenant-a")
    client = Central()
    result = migration.execute(
        "tenant-a", client, operation_id="a" * 32,
        preview_fingerprint=preview["preview_fingerprint"], confirmed=True)
    assert result["logout_required"] is True
    assert result["stores_total"] == 11 and result["connections_total"] == 22
    assert len(secure) == 22 * 4
    assert client.calls[0][0:2] == ("POST", "/migrations/legacy")
    assert len(client.calls[0][2]["stores"]) == 11
    saved = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    assert saved[0]["integracoes"]["mercadoturbo"]["token"] == "turbo-private"
    for store in saved:
        for provider in migration.PROVIDERS:
            config = store["integracoes"][provider]
            assert config["central_migrated"] is True
            assert not set(config) & {"access_token", "refresh_token", "client_secret", "secret", "app_id", "id"}
    state_text = (tenant / "central_migration.json").read_text(encoding="utf-8")
    assert "ml-access" not in state_text and "bling-refresh" not in state_text and "turbo-private" not in state_text


def test_cloud_failure_keeps_local_credentials_and_same_operation_resumes(local_config):
    tenant, secure = local_config
    preview = migration.preview("tenant-a")
    client = Central(failures=1)
    kwargs = {"operation_id": "b" * 32,
              "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}
    with pytest.raises(HTTPException):
        migration.execute("tenant-a", client, **kwargs)
    after_failure = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    assert after_failure[0]["integracoes"]["mercadolivre"]["access_token"] == "ml-access-0"
    assert len(secure) == 88
    result = migration.execute("tenant-a", client, **kwargs)
    assert result["success"] is True and len(client.calls) == 3
    assert client.calls[1][0] == "GET"
    assert client.calls[0][2] == client.calls[2][2]


def test_uncertain_completed_migration_checks_status_before_local_refresh(local_config, monkeypatch):
    tenant, _ = local_config
    preview = migration.preview("tenant-a")
    client = Central(failures=1)
    kwargs = {"operation_id": "f" * 32,
              "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}
    with pytest.raises(HTTPException):
        migration.execute("tenant-a", client, **kwargs)

    def completed(method, path, body=None):
        assert method == "GET"
        return {"operation_id": "f" * 32, "success": True, "status": "completed"}

    client.call = completed
    monkeypatch.setattr(migration, "_prepare_connections", lambda *args: pytest.fail("must not renew"))
    assert migration.execute("tenant-a", client, **kwargs)["logout_required"]
    saved = json.loads((tenant / "lojas_config.json").read_text())
    assert "refresh_token" not in saved[0]["integracoes"]["mercadolivre"]


def test_uncertain_migration_unavailable_status_does_not_resubmit(local_config, monkeypatch):
    preview = migration.preview("tenant-a")
    client = Central(failures=2)
    kwargs = {"operation_id": "f" * 32,
              "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}
    with pytest.raises(HTTPException):
        migration.execute("tenant-a", client, **kwargs)
    monkeypatch.setattr(migration, "_prepare_connections", lambda *args: pytest.fail("must not renew"))
    with pytest.raises(HTTPException):
        migration.execute("tenant-a", client, **kwargs)
    assert [call[0] for call in client.calls] == ["POST", "GET"]


@pytest.mark.parametrize("remote_status", ["completed", "failed", "not_found", "unavailable"])
def test_new_operation_id_checks_previous_uncertain_upload_first(local_config, monkeypatch, remote_status):
    preview = migration.preview("tenant-a")
    client = Central(failures=1)
    first_id = "a" * 32
    with pytest.raises(HTTPException):
        migration.execute("tenant-a", client, operation_id=first_id,
                          preview_fingerprint=preview["preview_fingerprint"], confirmed=True)

    def remote(method, path, body=None):
        assert method == "GET" and path.endswith(first_id)
        if remote_status == "not_found":
            raise HTTPException(404, {"code": "central_migration_not_found"})
        if remote_status == "unavailable":
            raise HTTPException(503, "unavailable")
        return {"success": remote_status == "completed", "operation_id": first_id, "status": remote_status}

    client.call = remote
    monkeypatch.setattr(migration, "_prepare_connections", lambda *args: pytest.fail("must not renew"))
    kwargs = {"operation_id": "b" * 32, "preview_fingerprint": preview["preview_fingerprint"], "confirmed": True}
    if remote_status == "completed":
        result = migration.execute("tenant-a", client, **kwargs)
        assert result["operation_id"] == first_id and result["logout_required"]
    else:
        with pytest.raises(HTTPException) as error:
            migration.execute("tenant-a", client, **kwargs)
        assert error.value.status_code == (503 if remote_status == "unavailable" else 409)


def test_status_restores_original_fingerprint_and_exposes_pending_local_finalize(local_config):
    preview = migration.preview("tenant-a")
    client = Central(failures=1)
    operation_id = "a" * 32
    with pytest.raises(HTTPException):
        migration.execute("tenant-a", client, operation_id=operation_id,
                          preview_fingerprint=preview["preview_fingerprint"], confirmed=True)

    def completed(method, path, body=None):
        assert method == "GET" and path.endswith(operation_id)
        return {"success": True, "status": "completed", "operation_id": operation_id}

    client.call = completed
    result = migration.status("tenant-a", client)
    assert result["preview_fingerprint"] == preview["preview_fingerprint"]
    assert result["status"] == "failed" and result["needs_local_finalize"]
    assert result["remote_error"] is None


def test_local_uncertain_refresh_cannot_replay_token_even_with_new_operation(local_config, monkeypatch):
    import requests

    tenant, _ = local_config
    path = tenant / "lojas_config.json"
    stores = json.loads(path.read_text())
    stores[0]["integracoes"]["mercadolivre"]["expires_at"] = 1
    path.write_text(json.dumps(stores))
    preview = migration.preview("tenant-a")
    calls = []

    def timeout(*args, **kwargs):
        calls.append(True)
        raise requests.Timeout()

    monkeypatch.setattr(migration, "_request", timeout)
    for operation_id in ("a" * 32, "b" * 32):
        with pytest.raises(HTTPException) as error:
            migration.execute("tenant-a", Central(), operation_id=operation_id,
                              preview_fingerprint=preview["preview_fingerprint"], confirmed=True)
        assert error.value.detail["code"] == "legacy_refresh_uncertain"
    assert len(calls) == 1


@pytest.mark.parametrize("status, expected", [(429, "legacy_connection_validation_failed"),
                                              (503, "legacy_connection_validation_failed"),
                                              (401, "legacy_reauthorization_required")])
def test_validation_failure_distinguishes_reauthorization_from_transient(local_config, monkeypatch, status, expected):
    stores = migration._validate_stores(migration._raw_stores("tenant-a"))
    monkeypatch.setattr(migration, "_identity", lambda *args: (_ for _ in ()).throw(HTTPException(401, "expired")))
    monkeypatch.setattr(migration, "_refresh", lambda *args: (_ for _ in ()).throw(HTTPException(status, "failure")))
    with pytest.raises(HTTPException) as error:
        migration._prepare_connections("tenant-a", stores)
    assert error.value.status_code == status
    assert error.value.detail["code"] == expected


def test_backup_failure_aborts_before_upload_and_keeps_credentials(local_config, monkeypatch):
    tenant, _secure = local_config
    preview = migration.preview("tenant-a")
    client = Central()
    monkeypatch.setattr(migration, "write_scoped_secret", lambda *_: (_ for _ in ()).throw(OSError("denied")))
    with pytest.raises(HTTPException, match="backup seguro"):
        migration.execute("tenant-a", client, operation_id="c" * 32,
                          preview_fingerprint=preview["preview_fingerprint"], confirmed=True)
    assert client.calls == []
    stores = json.loads((tenant / "lojas_config.json").read_text(encoding="utf-8"))
    assert stores[0]["integracoes"]["bling"]["refresh_token"] == "bling-refresh-0"


def test_frozen_backup_is_deleted_after_30_days(local_config):
    _tenant, secure = local_config
    preview = migration.preview("tenant-a")
    result = migration.execute("tenant-a", Central(), operation_id="d" * 32,
                               preview_fingerprint=preview["preview_fingerprint"], confirmed=True)
    assert secure
    state = migration.cleanup_expired_backup("tenant-a", now=result["backup_expires_at"] + 1)
    assert state["backup_status"] == "deleted"
    assert not secure


def test_expired_backup_retries_when_windows_rejects_one_deletion(local_config, monkeypatch):
    _tenant, secure = local_config
    preview = migration.preview("tenant-a")
    result = migration.execute("tenant-a", Central(), operation_id="e" * 32,
                               preview_fingerprint=preview["preview_fingerprint"], confirmed=True)
    original_delete = migration.delete_scoped_secret
    attempts = {"failed": False}

    def fail_once(target):
        if not attempts["failed"]:
            attempts["failed"] = True
            return False
        return original_delete(target)

    monkeypatch.setattr(migration, "delete_scoped_secret", fail_once)
    first = migration.cleanup_expired_backup("tenant-a", now=result["backup_expires_at"] + 1)
    assert first["backup_status"] == "delete_failed"
    assert secure
    second = migration.cleanup_expired_backup("tenant-a", now=result["backup_expires_at"] + 2)
    assert second["backup_status"] == "deleted"
    assert not secure


def test_turbo_is_local_to_exact_machine_and_store(local_config, tmp_path, monkeypatch):
    central = [{"store_id": "1".zfill(24), "nome": "Store 1", "owner_client_id": "tenant-a",
                "access": "owner", "integracoes": {"mercadolivre": {"central": True}}}]
    same_machine = integracoes.mesclar_turbo_local("tenant-a", central, incluir_token=True)
    assert same_machine[0]["integracoes"]["mercadoturbo"]["token"] == "turbo-private"
    other_info = tmp_path / "other-info"
    (other_info / "tenant-a").mkdir(parents=True)
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda client_id: str(other_info / client_id))
    other_machine = integracoes.mesclar_turbo_local("tenant-a", central, incluir_token=True)
    assert "mercadoturbo" not in other_machine[0]["integracoes"]
    integracoes.salvar_turbo_local_central("tenant-a", central, central[0]["store_id"], "new-local-token")
    added = integracoes.mesclar_turbo_local("tenant-a", central, incluir_token=True)
    assert added[0]["integracoes"]["mercadoturbo"]["token"] == "new-local-token"
