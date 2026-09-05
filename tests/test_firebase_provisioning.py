from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jose import JWTError

from backend.routers.firebase_provisioning import (
    FirebaseProvisioningRouterConfig,
    create_firebase_provisioning_router,
)
from backend.services import firebase_provisioning as service


PROJECT_ID = "jksistema-test-project"
RESPONSE_KEYS = {
    "success",
    "configured",
    "ready",
    "source",
    "code",
    "restart_required",
    "can_migrate",
    "replacement_required",
}


def _private_key_pem() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture(scope="module")
def service_account() -> dict:
    return {
        "type": "service_account",
        "project_id": PROJECT_ID,
        "private_key_id": "unit-test-key",
        "private_key": _private_key_pem(),
        "client_email": f"firebase-adminsdk@{PROJECT_ID}.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _encoded(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for key in (
        "JK_FIREBASE_EXPECTED_PROJECT_ID",
        "JK_REMOTE_AUTH_PROJECT_ID",
        "FIREBASE_PROJECT_ID",
        "JK_FIREBASE_PROJECT_ID",
        "FIREBASE_SERVICE_ACCOUNT_FILE",
        "FIREBASE_CREDENTIALS_FILE",
        "FIREBASE_SERVICE_ACCOUNT_JSON",
        "FIREBASE_ADMIN_CREDENTIALS_JSON",
        "FIREBASE_SERVICE_ACCOUNT_BASE64",
        "FIREBASE_ADMIN_CREDENTIALS_BASE64",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JK_FIREBASE_EXPECTED_PROJECT_ID", PROJECT_ID)
    service.reset_firebase_provisioning_runtime_state_for_tests()
    yield
    service.reset_firebase_provisioning_runtime_state_for_tests()


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "local_app"
    info = base / "info"
    info.mkdir(parents=True)
    return base, info


def _probe_ok(monkeypatch):
    calls = []

    def probe(payload, expected_project_id):
        calls.append((payload.get("project_id"), expected_project_id))

    monkeypatch.setattr(service, "_probe_service_account", probe)
    return calls


def test_status_not_configured_has_only_sanitized_contract(tmp_path):
    base, info = _paths(tmp_path)
    result = service.firebase_provisioning_status(base_dir=base, info_dir=info)

    assert result.status_code == 200
    assert set(result.payload) == RESPONSE_KEYS
    assert result.payload == {
        "success": True,
        "configured": False,
        "ready": False,
        "source": "none",
        "code": "not_configured",
        "restart_required": False,
        "can_migrate": False,
        "replacement_required": False,
    }


def test_import_validates_probes_and_writes_only_canonical_file(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    calls = _probe_ok(monkeypatch)

    result = service.firebase_provisioning_import(
        _encoded(service_account),
        replace=False,
        base_dir=base,
        info_dir=info,
    )

    canonical = info / service.CANONICAL_FILENAME
    assert result.status_code == 200
    assert result.payload["code"] == "imported"
    assert result.payload["configured"] is True
    assert result.payload["ready"] is False
    assert result.payload["restart_required"] is True
    assert calls == [(PROJECT_ID, PROJECT_ID)]
    assert canonical.is_file()
    assert json.loads(canonical.read_text(encoding="utf-8"))["project_id"] == PROJECT_ID
    assert sorted(path.name for path in info.iterdir()) == [service.CANONICAL_FILENAME]
    assert set(result.payload) == RESPONSE_KEYS
    response_text = json.dumps(result.payload, ensure_ascii=False)
    assert service_account["private_key"] not in response_text
    assert service_account["client_email"] not in response_text
    assert PROJECT_ID not in response_text


@pytest.mark.parametrize(
    ("raw", "expected_code", "expected_status"),
    [
        (b"not-json", "invalid_json", 400),
        (b"{}", "invalid_service_account", 400),
        (b"x" * (service.MAX_UPLOAD_BYTES + 1), "oversize", 413),
    ],
    ids=("invalid-json", "invalid-object", "oversize"),
)
def test_import_rejects_invalid_or_oversize_without_writing(
    tmp_path,
    raw,
    expected_code,
    expected_status,
):
    base, info = _paths(tmp_path)

    result = service.firebase_provisioning_import(
        raw,
        replace=False,
        base_dir=base,
        info_dir=info,
    )

    assert result.status_code == expected_status
    assert result.payload["code"] == expected_code
    assert not (info / service.CANONICAL_FILENAME).exists()
    assert set(result.payload) == RESPONSE_KEYS


def test_import_rejects_wrong_project_before_probe(tmp_path, monkeypatch, service_account):
    base, info = _paths(tmp_path)
    calls = _probe_ok(monkeypatch)
    wrong = dict(service_account, project_id="another-valid-project")

    result = service.firebase_provisioning_import(
        _encoded(wrong),
        replace=False,
        base_dir=base,
        info_dir=info,
    )

    assert result.status_code == 400
    assert result.payload["code"] == "wrong_project"
    assert calls == []
    assert not (info / service.CANONICAL_FILENAME).exists()


def test_trusted_client_config_allows_replacing_credential_derived_wrong_env_project(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    monkeypatch.delenv("JK_FIREBASE_EXPECTED_PROJECT_ID", raising=False)
    wrong_project = "wrong-derived-project"
    monkeypatch.setenv("FIREBASE_PROJECT_ID", wrong_project)
    client_config = base / "electron_app" / "client-config.json"
    client_config.parent.mkdir(parents=True)
    client_config.write_text(
        json.dumps({"remoteAuth": {"projectId": PROJECT_ID}}),
        encoding="utf-8",
    )
    wrong_credential = dict(
        service_account,
        project_id=wrong_project,
        client_email=f"firebase-adminsdk@{wrong_project}.iam.gserviceaccount.com",
    )
    canonical = info / service.CANONICAL_FILENAME
    canonical.write_bytes(_encoded(wrong_credential))
    calls = _probe_ok(monkeypatch)

    result = service.firebase_provisioning_import(
        _encoded(service_account),
        replace=True,
        base_dir=base,
        info_dir=info,
    )

    assert service._expected_project_id(base, info) == PROJECT_ID
    assert result.status_code == 200
    assert result.payload["code"] == "imported"
    assert calls == [(PROJECT_ID, PROJECT_ID)]
    assert json.loads(canonical.read_text(encoding="utf-8"))["project_id"] == PROJECT_ID


def test_import_rejects_invalid_pem_before_probe(tmp_path, monkeypatch, service_account):
    base, info = _paths(tmp_path)
    calls = _probe_ok(monkeypatch)
    invalid = dict(service_account, private_key="-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----\n")

    result = service.firebase_provisioning_import(
        _encoded(invalid),
        replace=False,
        base_dir=base,
        info_dir=info,
    )

    assert result.status_code == 400
    assert result.payload["code"] == "invalid_service_account"
    assert calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_uri": "https://attacker.invalid/token"},
        {"token_uri": "http://oauth2.googleapis.com/token"},
        {"token_uri": "https://oauth2.googleapis.com/token/"},
        {"universe_domain": "attacker.invalid"},
        {"universe_domain": None},
    ],
)
def test_import_rejects_ssrf_service_account_before_any_probe(
    tmp_path,
    monkeypatch,
    service_account,
    overrides,
):
    base, info = _paths(tmp_path)
    probe_calls = []
    monkeypatch.setattr(
        service,
        "_probe_service_account",
        lambda *_args: probe_calls.append(True),
    )

    result = service.firebase_provisioning_import(
        _encoded(dict(service_account, **overrides)),
        replace=False,
        base_dir=base,
        info_dir=info,
    )

    assert result.status_code == 400
    assert result.payload["code"] == "invalid_service_account"
    assert result.payload["ready"] is False
    assert probe_calls == []
    assert not (info / service.CANONICAL_FILENAME).exists()


def test_import_accepts_explicit_googleapis_universe_domain(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    calls = _probe_ok(monkeypatch)

    result = service.firebase_provisioning_import(
        _encoded(dict(service_account, universe_domain="googleapis.com")),
        replace=False,
        base_dir=base,
        info_dir=info,
    )

    assert result.status_code == 200
    assert result.payload["code"] == "imported"
    assert calls == [(PROJECT_ID, PROJECT_ID)]


def test_replacement_is_explicit_and_failed_probe_preserves_previous_file(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    _probe_ok(monkeypatch)
    first = service.firebase_provisioning_import(
        _encoded(service_account),
        replace=False,
        base_dir=base,
        info_dir=info,
    )
    assert first.status_code == 200
    canonical = info / service.CANONICAL_FILENAME
    before = canonical.read_bytes()
    replacement = dict(service_account, private_key_id="replacement-key")

    conflict = service.firebase_provisioning_import(
        _encoded(replacement),
        replace=False,
        base_dir=base,
        info_dir=info,
    )
    assert conflict.status_code == 409
    assert conflict.payload["code"] == "replacement_required"
    assert conflict.payload["replacement_required"] is True
    assert canonical.read_bytes() == before

    def failed_probe(_payload, _project_id):
        raise service.FirebaseProvisioningError("firebase_test_failed", 503)

    monkeypatch.setattr(service, "_probe_service_account", failed_probe)
    failed = service.firebase_provisioning_import(
        _encoded(replacement),
        replace=True,
        base_dir=base,
        info_dir=info,
    )
    assert failed.status_code == 503
    assert failed.payload["code"] == "firebase_test_failed"
    assert canonical.read_bytes() == before


def test_atomic_install_rolls_back_if_post_write_verification_fails(
    tmp_path,
    monkeypatch,
    service_account,
):
    _base, info = _paths(tmp_path)
    canonical = info / service.CANONICAL_FILENAME
    previous = _encoded(service_account)
    canonical.write_bytes(previous)
    incoming = _encoded(dict(service_account, private_key_id="new-key"))
    original_stage = service._stage_and_replace
    calls = 0

    def fail_after_first_replace(target, encoded):
        nonlocal calls
        calls += 1
        if calls == 1:
            original_stage(target, encoded)
            raise OSError("simulated verification failure")
        return original_stage(target, encoded)

    monkeypatch.setattr(service, "_stage_and_replace", fail_after_first_replace)
    with pytest.raises(service.FirebaseProvisioningError, match="write_failed"):
        service._atomic_install(info, incoming)

    assert canonical.read_bytes() == previous
    assert calls == 2


def test_migration_copies_valid_legacy_and_preserves_source(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    legacy = base / "firebase-service-account.json"
    legacy.write_bytes(_encoded(service_account))
    _probe_ok(monkeypatch)

    before = service.firebase_provisioning_status(base_dir=base, info_dir=info)
    assert before.payload["source"] == "legacy"
    assert before.payload["can_migrate"] is True

    result = service.firebase_provisioning_migrate_legacy(base_dir=base, info_dir=info)

    assert result.status_code == 200
    assert result.payload["code"] == "migrated"
    assert result.payload["source"] == "canonical"
    assert result.payload["ready"] is False
    assert result.payload["restart_required"] is True
    assert legacy.exists()
    assert (info / service.CANONICAL_FILENAME).exists()


def test_status_ready_requires_real_probe_and_runtime_activation(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    (info / service.CANONICAL_FILENAME).write_bytes(_encoded(service_account))
    calls = _probe_ok(monkeypatch)
    monkeypatch.setattr(service, "_runtime_requires_restart", lambda *_args: False)

    result = service.firebase_provisioning_status(base_dir=base, info_dir=info)

    assert result.status_code == 200
    assert result.payload["ready"] is True
    assert result.payload["code"] == "ready"
    assert calls == [(PROJECT_ID, PROJECT_ID)]


def test_status_fails_closed_when_firestore_probe_fails(tmp_path, monkeypatch, service_account):
    base, info = _paths(tmp_path)
    (info / service.CANONICAL_FILENAME).write_bytes(_encoded(service_account))

    def failed_probe(_payload, _project_id):
        raise service.FirebaseProvisioningError("firebase_test_failed", 503)

    monkeypatch.setattr(service, "_probe_service_account", failed_probe)
    result = service.firebase_provisioning_status(base_dir=base, info_dir=info)

    assert result.status_code == 503
    assert result.payload["configured"] is True
    assert result.payload["ready"] is False
    assert result.payload["code"] == "firebase_test_failed"
    assert set(result.payload) == RESPONSE_KEYS


def test_status_fails_closed_when_shared_sync_write_permissions_are_missing(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    (info / service.CANONICAL_FILENAME).write_bytes(_encoded(service_account))

    def insufficient_permissions(_payload, _project_id):
        raise service.FirebaseProvisioningError("firebase_permissions_insufficient", 403)

    monkeypatch.setattr(service, "_probe_service_account", insufficient_permissions)
    result = service.firebase_provisioning_status(base_dir=base, info_dir=info)

    assert result.status_code == 403
    assert result.payload["configured"] is True
    assert result.payload["ready"] is False
    assert result.payload["code"] == "firebase_permissions_insufficient"
    assert set(result.payload) == RESPONSE_KEYS


def test_firestore_probe_is_one_read_without_retry(monkeypatch, service_account):
    import firebase_admin
    from firebase_admin import credentials as firebase_credentials
    from firebase_admin import firestore as firebase_firestore

    calls = []
    fake_app = object()

    class FakeQuery:
        def limit(self, value):
            calls.append(("limit", value))
            return self

        def stream(self, *, retry, timeout):
            calls.append(("stream", retry, timeout))
            return iter(())

    class FakeDatabase:
        def collection(self, name):
            calls.append(("collection", name))
            return FakeQuery()

    monkeypatch.setattr(firebase_credentials, "Certificate", lambda payload: ("certificate", payload["project_id"]))
    monkeypatch.setattr(
        firebase_admin,
        "initialize_app",
        lambda credential, options, name: calls.append(("initialize", credential, options, bool(name))) or fake_app,
    )
    monkeypatch.setattr(firebase_firestore, "client", lambda *, app: calls.append(("client", app)) or FakeDatabase())
    monkeypatch.setattr(firebase_admin, "delete_app", lambda app: calls.append(("delete", app)))
    monkeypatch.setattr(service, "_probe_timeout_seconds", lambda: 3.0)
    monkeypatch.setattr(
        service,
        "_test_required_iam_permissions",
        lambda payload, project_id: calls.append(("iam", payload["project_id"], project_id)),
    )

    service._probe_service_account(service_account, PROJECT_ID)

    assert ("collection", "jk_sistema_shared_sync_keyrings") in calls
    assert ("limit", 1) in calls
    assert ("stream", None, 3.0) in calls
    assert ("iam", PROJECT_ID, PROJECT_ID) in calls
    assert calls[-1] == ("delete", fake_app)


def test_iam_permissions_probe_requires_shared_sync_writes_without_mutation(
    monkeypatch,
    service_account,
):
    from google.auth.transport import requests as google_auth_requests
    from google.oauth2 import service_account as google_service_account

    calls = []

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def json(self):
            return {
                "permissions": [
                    "datastore.entities.get",
                    "datastore.entities.list",
                ]
            }

    class FakeSession:
        def __init__(self, credentials, **kwargs):
            calls.append(("session", credentials, kwargs))

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return FakeResponse()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        google_service_account.Credentials,
        "from_service_account_info",
        staticmethod(lambda payload, scopes: calls.append(("credentials", payload["project_id"], scopes)) or object()),
    )
    monkeypatch.setattr(google_auth_requests, "AuthorizedSession", FakeSession)
    monkeypatch.setattr(service, "_probe_timeout_seconds", lambda: 3.0)

    with pytest.raises(service.FirebaseProvisioningError) as raised:
        service._test_required_iam_permissions(service_account, PROJECT_ID)

    assert raised.value.code == "firebase_permissions_insufficient"
    assert raised.value.status_code == 403
    post = next(call for call in calls if call[0] == "post")
    assert post[1] == (
        f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT_ID}:testIamPermissions"
    )
    assert post[2]["allow_redirects"] is False
    assert post[2]["timeout"] == 3.0
    assert set(post[2]["json"]["permissions"]) == service._REQUIRED_FIRESTORE_PERMISSIONS
    assert calls[-1] == ("close",)


def test_iam_permissions_probe_accepts_all_required_permissions(monkeypatch, service_account):
    from google.auth.transport import requests as google_auth_requests
    from google.oauth2 import service_account as google_service_account

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"permissions": sorted(service._REQUIRED_FIRESTORE_PERMISSIONS)}

    class FakeSession:
        def __init__(self, _credentials, **_kwargs):
            pass

        def post(self, _url, **_kwargs):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(
        google_service_account.Credentials,
        "from_service_account_info",
        staticmethod(lambda _payload, scopes: object()),
    )
    monkeypatch.setattr(google_auth_requests, "AuthorizedSession", FakeSession)

    service._test_required_iam_permissions(service_account, PROJECT_ID)


def test_status_marks_restart_required_when_runtime_flags_are_inactive(
    tmp_path,
    monkeypatch,
    service_account,
):
    from backend.services import admin_usuarios_firebase

    base, info = _paths(tmp_path)
    (info / service.CANONICAL_FILENAME).write_bytes(_encoded(service_account))
    _probe_ok(monkeypatch)
    monkeypatch.setattr(admin_usuarios_firebase, "_firebase_deve_usar", lambda: True)
    monkeypatch.setattr(admin_usuarios_firebase, "_firebase_live_features_ativas", lambda: False)

    result = service.firebase_provisioning_status(base_dir=base, info_dir=info)

    assert result.status_code == 200
    assert result.payload["configured"] is True
    assert result.payload["ready"] is False
    assert result.payload["restart_required"] is True
    assert result.payload["code"] == "restart_required"


def test_status_requires_restart_when_canonical_file_changes_after_startup(
    tmp_path,
    monkeypatch,
    service_account,
):
    from backend.services import admin_usuarios_firebase

    base, info = _paths(tmp_path)
    canonical = info / service.CANONICAL_FILENAME
    canonical.write_bytes(_encoded(service_account))
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_FILE", str(canonical))
    service.initialize_firebase_provisioning_runtime(base_dir=base, info_dir=info)

    replacement = dict(service_account, private_key=_private_key_pem(), private_key_id="replacement")
    canonical.write_bytes(_encoded(replacement))
    _probe_ok(monkeypatch)
    monkeypatch.setattr(admin_usuarios_firebase, "_firebase_deve_usar", lambda: True)
    monkeypatch.setattr(admin_usuarios_firebase, "_firebase_live_features_ativas", lambda: True)

    result = service.firebase_provisioning_status(base_dir=base, info_dir=info)

    assert result.status_code == 200
    assert result.payload["configured"] is True
    assert result.payload["ready"] is False
    assert result.payload["restart_required"] is True
    assert result.payload["code"] == "restart_required"
    assert set(result.payload) == RESPONSE_KEYS
    assert str(canonical) not in json.dumps(result.payload)
    assert replacement["private_key"] not in json.dumps(result.payload)


def test_status_probe_success_is_cached_and_import_invalidates_cache(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    (info / service.CANONICAL_FILENAME).write_bytes(_encoded(service_account))
    calls = _probe_ok(monkeypatch)
    monkeypatch.setattr(service, "_runtime_requires_restart", lambda *_args: False)

    first = service.firebase_provisioning_status(base_dir=base, info_dir=info)
    second = service.firebase_provisioning_status(base_dir=base, info_dir=info)
    assert first.payload["ready"] is True
    assert second.payload["ready"] is True
    assert calls == [(PROJECT_ID, PROJECT_ID)]

    imported = service.firebase_provisioning_import(
        _encoded(service_account),
        replace=False,
        base_dir=base,
        info_dir=info,
    )
    assert imported.payload["code"] == "already_configured"
    assert len(calls) == 2

    third = service.firebase_provisioning_status(base_dir=base, info_dir=info)
    assert third.payload["ready"] is True
    assert len(calls) == 3


def _test_router(tmp_path: Path, *, authorize_recovery, client_host="127.0.0.1"):
    base, info = _paths(tmp_path)

    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=authorize_recovery,
    )))
    return TestClient(app, client=(client_host, 50000)), base, info


def test_router_requires_full_admin(tmp_path):
    def deny(_authorization):
        raise HTTPException(status_code=403, detail="forbidden")

    client, _base, _info = _test_router(tmp_path, authorize_recovery=deny)
    response = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 403


def test_router_rejects_non_loopback_with_sanitized_response(tmp_path):
    client, _base, _info = _test_router(
        tmp_path,
        authorize_recovery=lambda _authorization: {},
        client_host="192.0.2.10",
    )
    response = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "local_only"
    assert set(response.json()) == RESPONSE_KEYS


def test_import_endpoint_never_echoes_secret_filename_or_payload(
    tmp_path,
    monkeypatch,
    service_account,
):
    _probe_ok(monkeypatch)
    client, _base, _info = _test_router(
        tmp_path,
        authorize_recovery=lambda _authorization: {},
    )
    secret_filename = "customer-private-service-account.json"
    response = client.post(
        "/api/admin/firebase-provisioning/import?replace=false",
        headers={"Authorization": "Bearer test"},
        files={"file": (secret_filename, _encoded(service_account), "application/json")},
    )

    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_KEYS
    response_text = response.text
    assert secret_filename not in response_text
    assert service_account["private_key"] not in response_text
    assert service_account["client_email"] not in response_text
    assert PROJECT_ID not in response_text


def test_router_exposes_only_the_three_provisioning_routes(tmp_path):
    base, info = _paths(tmp_path)

    router = create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=lambda _authorization: {},
    ))
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}

    assert routes == {
        ("/api/admin/firebase-provisioning/status", ("GET",)),
        ("/api/admin/firebase-provisioning/import", ("POST",)),
        ("/api/admin/firebase-provisioning/migrate-legacy", ("POST",)),
    }


def _insert_remote_profile(
    monkeypatch,
    info: Path,
    *,
    permissions: dict,
    source: str = "remote-auth-profile",
    client_id: str = "tenant-test",
):
    from backend.services import admin_usuarios as _admin_usuarios_facade  # noqa: F401
    from backend.services import admin_usuarios_common as common
    from backend.services import admin_usuarios_store as store
    from backend.services.remote_auth_contracts import PERMISSION_KEYS

    monkeypatch.setattr(common, "PERMISSION_KEYS", PERMISSION_KEYS, raising=False)
    monkeypatch.setattr(common, "JWTError", JWTError, raising=False)
    monkeypatch.setattr(store, "PASTA_INFO", str(info), raising=False)
    monkeypatch.setattr(store, "ARQUIVO_AUTH_DB", str(info / "auth_users.db"), raising=False)
    store._init_auth_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(info / "auth_users.db"))
    try:
        conn.execute(
            """
            INSERT INTO usuarios_auth (
                username, password, name, client_id, permissions_json, active,
                valid_until, machine_id, max_machines, machine_ids_json, source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "admin",
                "unused",
                "Admin",
                client_id,
                json.dumps(permissions),
                1,
                "31/12/2099",
                "machine-test",
                1,
                json.dumps(["machine-test"]),
                source,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return store


def _configure_recovery_token(monkeypatch, store, payload: dict):
    from backend.services import admin_usuarios_common as common

    def decode(token: str):
        if token != "valid-token":
            raise JWTError("invalid")
        return dict(payload)

    monkeypatch.setattr(common, "decodificar_access_token", decode, raising=False)
    monkeypatch.setattr(store, "_payload_sessao_por_authorization", common._payload_sessao_por_authorization)


def test_recovery_routes_bypass_broken_firebase_with_trusted_full_profile(
    tmp_path,
    monkeypatch,
    service_account,
):
    base, info = _paths(tmp_path)
    store = _insert_remote_profile(monkeypatch, info, permissions={"full": True})
    _configure_recovery_token(
        monkeypatch,
        store,
        {"sub": "admin", "client_id": "tenant-test", "machine_id": "machine-test"},
    )
    firebase_calls = []

    def broken_firebase(*_args, **_kwargs):
        firebase_calls.append(True)
        raise HTTPException(status_code=503, detail="firebase unavailable")

    monkeypatch.setattr(store, "_firebase_obter_usuario", broken_firebase, raising=False)
    _probe_ok(monkeypatch)
    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=store._authorize_full_admin_firebase_provisioning,
    )))
    client = TestClient(app, client=("127.0.0.1", 50000))
    headers = {"Authorization": "Bearer valid-token"}

    status_response = client.get("/api/admin/firebase-provisioning/status", headers=headers)
    import_response = client.post(
        "/api/admin/firebase-provisioning/import",
        headers=headers,
        files={"file": ("credential.json", _encoded(service_account), "application/json")},
    )

    assert status_response.status_code == 200
    assert status_response.json()["code"] == "not_configured"
    assert import_response.status_code == 200
    assert import_response.json()["code"] == "imported"
    assert firebase_calls == []


def test_recovery_routes_reject_non_full_or_non_authoritative_profile(tmp_path, monkeypatch):
    base, info = _paths(tmp_path)
    store = _insert_remote_profile(monkeypatch, info, permissions={"full": False})
    _configure_recovery_token(
        monkeypatch,
        store,
        {"sub": "admin", "client_id": "tenant-test", "machine_id": "machine-test"},
    )
    monkeypatch.setattr(
        store,
        "_require_full_admin_user_management",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=503, detail="firebase unavailable")),
        raising=False,
    )
    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=store._authorize_full_admin_firebase_provisioning,
    )))
    client = TestClient(app, client=("127.0.0.1", 50000))

    non_full = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer valid-token"},
    )
    conn = sqlite3.connect(str(info / "auth_users.db"))
    try:
        conn.execute("UPDATE usuarios_auth SET permissions_json = ?, source = ? WHERE username = ?", (
            json.dumps({"full": True}),
            "local",
            "admin",
        ))
        conn.commit()
    finally:
        conn.close()
    non_authoritative = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert non_full.status_code == 403
    assert non_authoritative.status_code == 403


def test_recovery_routes_reject_invalid_jwt_or_client(tmp_path, monkeypatch):
    base, info = _paths(tmp_path)
    store = _insert_remote_profile(monkeypatch, info, permissions={"full": True})
    _configure_recovery_token(
        monkeypatch,
        store,
        {"sub": "admin", "client_id": "wrong-client", "machine_id": "machine-test"},
    )
    monkeypatch.setattr(
        store,
        "_require_full_admin_user_management",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=403, detail="forbidden")),
        raising=False,
    )
    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=store._authorize_full_admin_firebase_provisioning,
    )))
    client = TestClient(app, client=("127.0.0.1", 50000))

    invalid_jwt = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer invalid-token"},
    )
    invalid_client = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert invalid_jwt.status_code == 401
    assert invalid_client.status_code == 403


def test_recovery_routes_preserve_healthy_normal_admin_fallback(tmp_path, monkeypatch):
    base, info = _paths(tmp_path)
    store = _insert_remote_profile(
        monkeypatch,
        info,
        permissions={"full": False},
        source="local",
    )
    _configure_recovery_token(
        monkeypatch,
        store,
        {"sub": "admin", "client_id": "tenant-test", "machine_id": "machine-test"},
    )
    calls = []

    def healthy_normal_admin(_authorization, client_id):
        calls.append(client_id)
        return {"username": "admin", "client_id": client_id, "permissions": {"full": True}}

    monkeypatch.setattr(store, "_require_full_admin_user_management", healthy_normal_admin, raising=False)
    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=store._authorize_full_admin_firebase_provisioning,
    )))
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert calls == ["tenant-test"]


def test_admin_usuarios_can_read_sanitized_status_but_cannot_change_credential(
    tmp_path,
    monkeypatch,
):
    base, info = _paths(tmp_path)
    store = _insert_remote_profile(
        monkeypatch,
        info,
        permissions={"full": False, "admin_usuarios": True},
    )
    _configure_recovery_token(
        monkeypatch,
        store,
        {"sub": "admin", "client_id": "tenant-test", "machine_id": "machine-test"},
    )
    monkeypatch.setattr(
        store,
        "_require_full_admin_user_management",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=503, detail="firebase unavailable")),
        raising=False,
    )
    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=store._authorize_full_admin_firebase_provisioning,
        authorize_status=store._authorize_admin_firebase_provisioning_status,
    )))
    client = TestClient(app, client=("127.0.0.1", 50000))
    headers = {"Authorization": "Bearer valid-token"}

    status_response = client.get("/api/admin/firebase-provisioning/status", headers=headers)
    import_response = client.post(
        "/api/admin/firebase-provisioning/import",
        headers=headers,
        files={"file": ("credential.json", b"{}", "application/json")},
    )

    assert status_response.status_code == 200
    assert set(status_response.json()) == RESPONSE_KEYS
    assert status_response.json()["code"] == "not_configured"
    assert import_response.status_code == 403
    assert set(import_response.json()) == RESPONSE_KEYS
    assert import_response.json()["code"] == "forbidden"


def test_status_authorization_preserves_healthy_admin_usuarios_fallback(tmp_path, monkeypatch):
    base, info = _paths(tmp_path)
    store = _insert_remote_profile(
        monkeypatch,
        info,
        permissions={"full": False, "admin_usuarios": True},
        source="local",
    )
    _configure_recovery_token(
        monkeypatch,
        store,
        {"sub": "admin", "client_id": "tenant-test", "machine_id": "machine-test"},
    )
    calls = []

    def healthy_admin_access(_authorization, client_id):
        calls.append(client_id)
        return {
            "username": "admin",
            "client_id": client_id,
            "permissions": {"full": False, "admin_usuarios": True},
        }

    monkeypatch.setattr(store, "_require_admin_usuarios_access", healthy_admin_access, raising=False)
    app = FastAPI()
    app.include_router(create_firebase_provisioning_router(FirebaseProvisioningRouterConfig(
        base_dir=str(base),
        info_dir=str(info),
        authorize_recovery=store._authorize_full_admin_firebase_provisioning,
        authorize_status=store._authorize_admin_firebase_provisioning_status,
    )))
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get(
        "/api/admin/firebase-provisioning/status",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert set(response.json()) == RESPONSE_KEYS
    assert calls == ["tenant-test"]
