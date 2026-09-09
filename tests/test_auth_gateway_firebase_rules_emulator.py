"""Real Firestore Rules tests; run with a local Firestore emulator only.

FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 python -m pytest ... -q
The suite uses a demo project and synthetic identities, never production data.
"""
import base64
import json
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from cloud.auth_gateway.app.firebase_sessions import build_grant, resource_hash
from cloud.auth_gateway.app.policy import machine_hash

HOST = os.getenv("FIRESTORE_EMULATOR_HOST", "")
PROJECT = "demo-jk-firebase-scoped"
pytestmark = pytest.mark.skipif(not HOST, reason="Local Firestore emulator required")


def value(raw):
    if raw is None:
        return {"nullValue": None}
    if isinstance(raw, bool):
        return {"booleanValue": raw}
    if isinstance(raw, int):
        return {"integerValue": str(raw)}
    if isinstance(raw, datetime):
        return {"timestampValue": raw.isoformat()}
    if isinstance(raw, str):
        return {"stringValue": raw}
    if isinstance(raw, list):
        return {"arrayValue": {"values": [value(item) for item in raw]}}
    return {"mapValue": {"fields": {key: value(item) for key, item in raw.items()}}}


def token(grant_id, grant, **overrides):
    now = int(time.time())
    claims = {"iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT,
              "sub": grant["uid"], "user_id": grant["uid"], "iat": now, "exp": now + 3600,
              "auth_time": now, "firebase": {"sign_in_provider": "custom", "identities": {}},
              "jk_firebase_v": 1, "jk_firebase_grant": grant_id, "jk_username": grant["username"],
              "jk_client_id": grant["client_id"], "jk_machine_hash": machine_hash(grant["machine_id"])}
    claims.update(overrides)
    encode = lambda item: base64.urlsafe_b64encode(json.dumps(item).encode()).decode().rstrip("=")
    return encode({"alg": "none", "typ": "JWT"}) + "." + encode(claims) + "."


@pytest.fixture
def api():
    assert HOST.startswith(("127.0.0.1:", "localhost:")), "Tests must stay on loopback"
    root = f"http://{HOST}"
    base = f"{root}/v1/projects/{PROJECT}/databases/(default)/documents"
    rules = Path(__file__).resolve().parents[1].joinpath("firestore.rules").read_text(encoding="utf-8")
    result = requests.put(f"{root}/emulator/v1/projects/{PROJECT}:securityRules",
                          json={"rules": {"files": [{"name": "firestore.rules", "content": rules}]}}, timeout=30)
    assert result.ok, result.text
    result = requests.delete(f"{root}/emulator/v1/projects/{PROJECT}/databases/(default)/documents", timeout=30)
    assert result.ok, result.text

    def call(method, path, data=None, auth="owner"):
        body = {"fields": {key: value(item) for key, item in data.items()}} if data is not None else None
        return requests.request(method, f"{base}/{path}", json=body,
                                headers={"Authorization": f"Bearer {auth}"}, timeout=20)

    user = {"username": "ana", "client_id": "empresa-a", "active": True,
            "permissions": {"cadastro": True, "integracao": True}, "machine_ids": ["pc:a", "pc:b"],
            "password_hash": "secret-hash", "max_machines": 2}
    grant = build_grant(user, "ana", "pc:a", "jk_sistema_usuarios")
    grant_id = "a" * 64
    assert call("PATCH", "jk_sistema_usuarios/ana", user).ok
    assert call("PATCH", f"jk_sistema_firebase_grants/{grant_id}", grant).ok
    return call, user, grant, grant_id, token(grant_id, grant), base


def test_own_grant_only_and_no_administrative_data(api):
    call, user, grant, grant_id, auth, base = api
    assert call("GET", f"jk_sistema_firebase_grants/{grant_id}", auth=auth).ok
    for path in ("jk_sistema_usuarios/ana", "users_index/empresa-a", "jk_central_v1_oauth/secret",
                 "jk_sistema_firebase_grants/" + "b" * 64):
        assert call("GET", path, auth=auth).status_code == 403
    assert call("PATCH", f"jk_sistema_firebase_grants/{grant_id}", {**grant, "permissions": {"full": True}}, auth=auth).status_code == 403
    assert call("GET", f"jk_sistema_firebase_grants/{grant_id}", auth=token(grant_id, grant, sub="attacker")).status_code == 403
    assert call("GET", f"jk_sistema_firebase_grants/{grant_id}", auth=token(grant_id, grant, jk_machine_hash="other")).status_code == 403


def test_own_snapshot_chunks_and_transaction_but_no_other_scope_or_user(api):
    call, user, grant, grant_id, auth, base = api
    pointer = grant["scope_pointers"]["cadastro"]
    snapshot = pointer + "__" + "1" * 32
    metadata = {"id": pointer, "pointer_id": pointer, "client_id": "empresa-a", "scope": "cadastro"}
    chunk = {"pointer_id": pointer, "bundle_id": snapshot, "client_id": "empresa-a", "scope": "cadastro", "data": "encrypted"}
    assert call("GET", f"jk_sistema_shared_sync/{pointer}", auth=auth).status_code == 404
    assert call("PATCH", f"jk_sistema_shared_sync/{pointer}", metadata, auth).ok
    assert call("PATCH", f"jk_sistema_shared_sync/{snapshot}", {**metadata, "id": snapshot}, auth).ok
    assert call("PATCH", f"jk_sistema_shared_sync_chunks/{snapshot}_00000", chunk, auth).ok
    assert call("GET", f"jk_sistema_shared_sync_chunks/{snapshot}_00000", auth=auth).ok
    for other in (resource_hash("shared-sync-machine", "empresa-a", "bia", "cadastro"),
                  resource_hash("shared-sync-machine", "empresa-b", "ana", "cadastro"),
                  resource_hash("shared-sync-machine", "empresa-a", "ana", "vendas")):
        assert call("PATCH", f"jk_sistema_shared_sync/{other}", {**metadata, "pointer_id": other}, auth).status_code == 403
        assert call("GET", f"jk_sistema_shared_sync/{other}", auth=auth).status_code == 403
    assert call("PATCH", f"jk_sistema_shared_sync/{pointer}", {**metadata, "client_id": "empresa-b"}, auth).status_code == 403
    # Commit both pointer documents atomically with normal user authentication.
    prefix = f"projects/{PROJECT}/databases/(default)/documents"
    writes = [{"update": {"name": f"{prefix}/jk_sistema_shared_sync/{item}",
                            "fields": {key: value(val) for key, val in metadata.items()}}}
              for item in (pointer, pointer + "__v2_authority")]
    committed = requests.post(base + ":commit", json={"writes": writes},
                              headers={"Authorization": f"Bearer {auth}"}, timeout=20)
    assert committed.ok, committed.text


@pytest.mark.parametrize("mutation", [{"active": False}, {"permissions": {"full": True}},
                                      {"machine_ids": ["pc:b"]}, {"client_id": "empresa-b"},
                                      {"valid_until": "01/01/2000"},
                                      {"password_hash": "new-hash", "updated_at": "changed"}])
def test_live_policy_changes_revoke_existing_token(api, mutation):
    call, user, grant, grant_id, auth, base = api
    assert call("PATCH", "jk_sistema_usuarios/ana", {**user, **mutation}).ok
    assert call("GET", f"jk_sistema_firebase_grants/{grant_id}", auth=auth).status_code == 403


def test_expired_grant_and_anonymous_access_fail_closed(api):
    call, user, grant, grant_id, auth, base = api
    expired = {**grant, "expires_at": datetime.fromtimestamp(1, timezone.utc)}
    assert call("PATCH", f"jk_sistema_firebase_grants/{grant_id}", expired).ok
    assert call("GET", f"jk_sistema_firebase_grants/{grant_id}", auth=auth).status_code == 403
    assert requests.get(f"{base}/jk_sistema_firebase_grants/{grant_id}", timeout=20).status_code == 403


def test_keyring_creation_machine_ownership_and_envelopes(api):
    call, user, grant, grant_id, auth, base = api
    key = {"id": grant["keyring_id"], "schema": 1, "kind": "keyring",
           "owner_client_hash": grant["owner_client_hash"], "owner_user_hash": grant["owner_user_hash"],
           "active_key_id": "a" * 16}
    path = "jk_sistema_shared_sync_keyrings/"
    assert call("GET", path + key["id"], auth=auth).status_code == 404
    assert call("PATCH", path + key["id"], key, auth).ok
    assert call("PATCH", path + key["id"], {**key, "active_key_id": "b" * 16}, auth).status_code == 403
    member = {**key, "id": grant["member_id"], "kind": "member", "keyring_id": grant["keyring_id"], "public_key": "public"}
    assert call("PATCH", path + member["id"], member, auth).ok
    other = resource_hash("shared-sync-keyring-member", "empresa-a", "ana", "pc:b")
    assert call("PATCH", path + other, {**member, "id": other}, auth).status_code == 403
    envelope = {**key, "id": "e" * 64, "kind": "envelope", "keyring_id": grant["keyring_id"],
                "member_id": other, "envelope": {"encrypted": "wrapped"}}
    assert call("PATCH", path + envelope["id"], envelope, auth).ok
    assert call("GET", path + envelope["id"], auth=auth).ok
    assert call("PATCH", path + "f" * 64, {**envelope, "id": "f" * 64, "owner_user_hash": "foreign"}, auth).status_code == 403


def test_real_shared_sync_helpers_roundtrip_and_cas_through_rest_sdk(api, monkeypatch):
    import hashlib
    import logging
    import io
    import zipfile
    from types import SimpleNamespace
    from google.api_core.exceptions import Forbidden
    from google.cloud.firestore_v1.services.firestore.transports.rest import FirestoreRestTransport
    from google.oauth2.credentials import Credentials
    from backend.services import shared_sync  # Initialize the real module facade.
    from backend.services import shared_sync_remote as remote, shared_sync_keyring as keys
    from backend.services import firebase_user_session, shared_sync_config
    from fastapi import HTTPException

    call, user, grant, grant_id, auth, base = api
    # Disable the SDK emulator shortcut: it adds Bearer owner and bypasses Rules.
    # Only the transport host changes; real user bearer credentials remain active.
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST")
    credential = Credentials(token=auth)
    monkeypatch.setattr(firebase_user_session, "FirestoreRestTransport",
        lambda **kwargs: FirestoreRestTransport(host=f"http://{HOST}", **kwargs))
    db = firebase_user_session.UserFirestoreClient(project=PROJECT, credentials=credential)
    with pytest.raises(Forbidden):
        db.collection("jk_sistema_usuarios").document("ana").get(retry=None, timeout=10)

    local_secrets = {}
    session = {"username": "ana", "client_id": "empresa-a", "machine_id": "pc:a", "permissions": user["permissions"]}
    scoped = SimpleNamespace(identity=session, profile=lambda: grant["profile"])
    monkeypatch.setattr(firebase_user_session, "current", lambda *a, **k: scoped)
    for module in (keys, remote, shared_sync_config):
        monkeypatch.setattr(module, "_firebase_db", lambda: db, raising=False)
        monkeypatch.setattr(module, "_firebase_deve_usar", lambda: True, raising=False)
        monkeypatch.setattr(module, "_env_texto", lambda *a: "", raising=False)
        monkeypatch.setattr(module, "logger", logging.getLogger(__name__), raising=False)
    monkeypatch.setattr(keys, "_shared_sync_keyring_read_secret", lambda target: local_secrets.get(target, ""))
    monkeypatch.setattr(keys, "_shared_sync_keyring_write_secret", lambda target, val: local_secrets.update({target: val}))
    monkeypatch.setattr(remote, "_shared_sync_keyring_key_for_push", keys._shared_sync_keyring_key_for_push)
    monkeypatch.setattr(remote, "_shared_sync_keyring_key_for_pull", keys._shared_sync_keyring_key_for_pull)
    monkeypatch.setattr(remote, "_shared_sync_state_update", lambda *a, **kw: None)
    monkeypatch.setattr(remote, "_shared_sync_state_snapshot_id", lambda *a, **kw: "")

    scope = "lojas_integracoes"
    pointer = grant["scope_pointers"][scope]
    data = b'[{"id":"store-a","nome":"Teste"}]'
    manifest = {"schema": 2, "scope": scope, "created_at": "2026-09-09T00:00:00Z",
                "file_count": 1, "snapshot_hash": hashlib.sha256(data).hexdigest(),
                "files": [{"relative_path": "lojas_config.json", "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("files/lojas_config.json", data)
    bundle = buffer.getvalue()
    monkeypatch.setattr(remote, "_shared_sync_montar_pacote", lambda *a, **kw: (bundle, manifest, []))
    # The guard's local file conflict analysis is separate; publication still
    # exercises its real remote transaction, existing revisions and contention.
    monkeypatch.setattr(remote, "_shared_sync_validar_push_lojas_integracoes", lambda *a, **kw: None)
    context = {"sessao": session, "machine_id": "pc:a"}
    result = remote._shared_sync_push_scope("empresa-a", scope, session, "pc:a", bundle_id=pointer, key_context=context)
    assert result["success"]
    meta = remote._shared_sync_remote_meta_by_id(pointer, strict=True)
    assert meta["_guard_pointer_id"] == pointer + "__v2_authority"
    restored, restored_meta = remote._shared_sync_obter_bundle_por_id(pointer, meta=meta, key_context=context)
    assert restored == bundle and restored_meta["encrypted"] is True
    # Real constrained queries used for cleanup/history must pass Rules.
    snapshots = list(remote._shared_sync_user_query(db.collection("jk_sistema_shared_sync"), "pointer_id", pointer).stream())
    chunks = list(remote._shared_sync_user_query(db.collection("jk_sistema_shared_sync_chunks"), "bundle_id", meta["snapshot_id"]).stream())
    assert len(snapshots) >= 3 and chunks
    with pytest.raises(HTTPException) as stale:
        remote._shared_sync_publicar_pointer_cas(db, pointer, meta, None)
    assert stale.value.status_code == 409
    assert remote._shared_sync_remote_meta_by_id(pointer, strict=True)["snapshot_id"] == meta["snapshot_id"]
