"""JK-authenticated local archives stay available without a cloud grant."""
from __future__ import annotations

import copy
import io
import json
import time
from types import SimpleNamespace

import jwt
import pytest
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.modules.cofre import shared_storage, storage
from backend.modules.cofre.shared import SharedCofre
from backend.routers.cofre import create_cofre_router


KEY = "fictional-jk-local-session-signature-key-32-bytes"
MESSAGE = "/api/cofre/canais/channel/mensagens/message?servidor_id=guild"


def archive(content="Informacao local", guild="guild"):
    return {"format": storage.FORMAT, "guild": {"id": guild, "name": "Acervo ficticio"},
            "exported_at": "2026-10-26T12:00:00Z", "channels": [
                {"id": "channel", "name": "guias", "messages": [
                    {"id": "message", "author": "Fixture", "content": content}]}]}


def auth(company="company-a", *, write=True, key=KEY):
    token = jwt.encode({"sub": "fixture-user", "client_id": company, "machine_id": "fixture-pc",
                        "exp": int(time.time()) + 3600, "fixture_write": write}, key, algorithm="HS256")
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    import firebase_admin.auth
    import firebase_admin.firestore
    from backend.services.firebase_user_session import FirebaseUserSession

    def forbidden(*_args, **_kwargs):
        pytest.fail("Cofre local actions must not use Firebase or an external transport")

    monkeypatch.setattr(firebase_admin.auth, "verify_id_token", forbidden)
    monkeypatch.setattr(firebase_admin.auth, "create_custom_token", forbidden)
    monkeypatch.setattr(firebase_admin.firestore, "client", forbidden)
    monkeypatch.setattr(FirebaseUserSession, "profile", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    shared = SharedCofre(tmp_path, url="https://cofre.example.test")
    context = SimpleNamespace(shared=shared, grants=False, resolver_calls=0, calls=[], companies={}, worker_error=None)

    def remote(method, path, principal, payload=None):
        assert principal["capability"] == "fixture-verified-remote-capability"
        context.calls.append((method, principal["client_id"]))
        if context.worker_error is not None:
            raise storage.CofreError("Worker fixture failure", context.worker_error)
        company = principal["client_id"]
        current = context.companies.get(company, {"revision": 0, "state": None})
        if method == "GET":
            return (304, None) if current["revision"] == int(path.split("after=")[1]) else (200, copy.deepcopy(current))
        if payload["expected_revision"] != current["revision"]:
            raise storage.CofreError("Fixture conflict", 409)
        updated = {"revision": current["revision"] + 1, "state": copy.deepcopy(payload["state"])}
        context.companies[company] = updated
        return 200, {"revision": updated["revision"]}

    shared._remote = remote

    def tenant(request: Request):
        value = request.headers.get("authorization", "")
        try:
            if not value.startswith("Bearer "):
                raise ValueError("missing fixture authorization")
            claims = jwt.decode(value[len("Bearer "):], KEY, algorithms=["HS256"])
        except (ValueError, jwt.InvalidTokenError):
            raise HTTPException(401, "Sessao JK invalida", headers={"WWW-Authenticate": "Bearer"}) from None
        request.state.cofre_principal = {
            "client_id": claims["client_id"], "username": claims["sub"], "exp": claims["exp"],
            "scope": ["cofre:read", *(["cofre:write"] if claims["fixture_write"] else [])], "capability": ""}
        return claims["client_id"]

    def remote_principal(request: Request):
        context.resolver_calls += 1
        if not context.grants:
            raise HTTPException(503, {"code": "cofre_session_unavailable", "message": "Fixture proof absent"})
        return {**request.state.cofre_principal, "capability": "fixture-verified-remote-capability"}

    context.remote_principal = remote_principal
    app = FastAPI()
    app.include_router(create_cofre_router(info_root=tmp_path, tenant_dependency=tenant,
        shared_service=shared, remote_principal=remote_principal))
    context.client = TestClient(app)
    yield context
    context.client.close()


def import_local(fixture, *, company="company-a", content="Informacao local"):
    return storage.import_archive(fixture.shared.local_root(company), company,
                                 io.BytesIO(json.dumps(archive(content)).encode()), "fixture.json")


def publish_fixture(fixture):
    import_local(fixture)
    fixture.shared.publish("company-a", {"client_id": "company-a", "username": "fixture-user",
        "exp": int(time.time()) + 3600, "scope": ["cofre:read", "cofre:write"],
        "capability": "fixture-verified-remote-capability"})


def sync_error(response):
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "cofre_sync_unavailable"
    assert "www-authenticate" not in response.headers


def test_valid_jk_without_grant_reads_and_edits_unpublished_archive_without_firebase(fixture):
    imported = fixture.client.post("/api/cofre/importar", headers=auth(),
        files={"arquivo": ("fixture.json", json.dumps(archive()).encode(), "application/json")})
    assert imported.status_code == 200
    assert fixture.client.get("/api/cofre/servidores", headers=auth()).json()["servidores"][0]["id"] == "guild"
    assert fixture.client.get("/api/cofre/servidores/guild/canais", headers=auth()).json()["canais"][0]["id"] == "channel"
    data = fixture.client.get("/api/cofre/canais/channel/mensagens?servidor_id=guild", headers=auth()).json()
    assert data["mensagens"][0]["content"] == "Informacao local"
    assert fixture.client.patch(MESSAGE, headers=auth(), json={"content": "Editado localmente"}).status_code == 200
    assert fixture.client.patch("/api/cofre/canais/channel?servidor_id=guild", headers=auth(),
                                json={"name": "Canal local"}).status_code == 200
    assert fixture.client.delete(MESSAGE, headers=auth()).status_code == 200
    assert fixture.client.post(MESSAGE.replace("?", "/restaurar?"), headers=auth()).status_code == 200
    stored = storage.list_messages(fixture.shared.read_root("company-a"), "company-a", "guild", "channel")
    assert stored["mensagens"][0]["content"] == "Editado localmente"
    status = fixture.client.get("/api/cofre/compartilhamento", headers=auth()).json()
    assert status["configured"] is True and status["enabled"] is False and status["can_publish"] is True
    assert fixture.resolver_calls == 0 and fixture.calls == [] and fixture.companies == {}


def test_local_grantless_session_cannot_sync_publish_or_read_another_tenant(fixture):
    import_local(fixture)
    for operation in ("sincronizar", "publicar"):
        sync_error(fixture.client.post("/api/cofre/compartilhamento/" + operation, headers=auth()))
    assert fixture.calls == []
    assert fixture.client.get("/api/cofre/servidores", headers=auth("company-b")).json() == {"servidores": []}
    assert fixture.client.get("/api/cofre/canais/channel/mensagens?servidor_id=guild",
                              headers=auth("company-b")).status_code == 404
    assert fixture.client.get("/api/cofre/servidores", headers=auth()).status_code == 200


def test_remote_proof_recovery_requires_explicit_publication_and_then_confirms_shared_edits(fixture):
    import_local(fixture)
    assert fixture.client.patch(MESSAGE, headers=auth(), json={"content": "Local antes de publicar"}).status_code == 200
    sync_error(fixture.client.post("/api/cofre/compartilhamento/publicar", headers=auth()))
    assert fixture.calls == [] and fixture.companies == {}
    fixture.grants = True
    # Obtaining proof and reading status alone cannot publish local changes.
    assert fixture.client.get("/api/cofre/compartilhamento", headers=auth()).json()["enabled"] is False
    assert fixture.calls == []
    published = fixture.client.post("/api/cofre/compartilhamento/publicar", headers=auth())
    assert published.status_code == 200 and published.json()["revision"] == 1
    assert fixture.client.patch(MESSAGE, headers=auth(), json={"content": "Edicao compartilhada"}).status_code == 200
    assert fixture.calls == [("PUT", "company-a"), ("PUT", "company-a")]
    state = fixture.companies["company-a"]
    assert state["revision"] == 2
    assert state["state"]["guilds"][0]["channels"][0]["messages"][0]["content"] == "Edicao compartilhada"
    originals = storage.list_messages(fixture.shared.local_root("company-a"), "company-a", "guild", "channel")
    assert originals["mensagens"][0]["content"] == "Local antes de publicar"


def test_local_reader_cannot_edit_unpublished_archive_or_publish(fixture):
    import_local(fixture)
    assert fixture.client.get("/api/cofre/servidores", headers=auth(write=False)).status_code == 200
    assert fixture.client.patch(MESSAGE, headers=auth(write=False), json={"content": "Denied"}).status_code == 403
    assert fixture.client.post("/api/cofre/compartilhamento/publicar", headers=auth(write=False)).status_code == 403
    assert fixture.resolver_calls == 0 and fixture.calls == []


def test_missing_cloud_proof_never_mutates_shared_cache_or_local_originals(fixture):
    publish_fixture(fixture)
    shared = fixture.shared
    before = shared_storage.export_state(shared.cache_for("company-a"), "company-a")
    original = shared_storage.export_state(shared.local_root("company-a"), "company-a")
    calls = len(fixture.calls)
    assert fixture.client.get("/api/cofre/compartilhamento", headers=auth()).json()["enabled"] is True
    attempts = (
        lambda: fixture.client.patch(MESSAGE, headers=auth(), json={"content": "Denied"}),
        lambda: fixture.client.delete(MESSAGE, headers=auth()),
        lambda: fixture.client.post(MESSAGE.replace("?", "/restaurar?"), headers=auth()),
        lambda: fixture.client.patch("/api/cofre/canais/channel?servidor_id=guild", headers=auth(), json={"name": "Denied"}),
        lambda: fixture.client.post("/api/cofre/importar", headers=auth(),
            files={"arquivo": ("new.json", json.dumps(archive("Denied", guild="new-guild")).encode(), "application/json")}),
    )
    for mutation in attempts:
        sync_error(mutation())
    assert shared_storage.export_state(shared.cache_for("company-a"), "company-a") == before
    assert shared_storage.export_state(shared.local_root("company-a"), "company-a") == original
    assert len(fixture.calls) == calls
    assert fixture.client.get("/api/cofre/canais/channel/mensagens?servidor_id=guild", headers=auth()).status_code == 200


@pytest.mark.parametrize("status", [401, 403, 503])
def test_worker_auth_or_availability_error_does_not_invalidate_jk_or_replay_mutations(fixture, status):
    publish_fixture(fixture)
    fixture.grants = True
    fixture.worker_error = status
    before = shared_storage.export_state(fixture.shared.cache_for("company-a"), "company-a")
    for operation in ("sincronizar", "publicar"):
        fixture.shared._checks.clear()
        sync_error(fixture.client.post("/api/cofre/compartilhamento/" + operation, headers=auth()))
    sync_error(fixture.client.patch(MESSAGE, headers=auth(), json={"content": "Unconfirmed"}))
    assert shared_storage.export_state(fixture.shared.cache_for("company-a"), "company-a") == before
    assert fixture.client.get("/api/cofre/servidores", headers=auth()).status_code == 200


def test_invalid_jk_jwt_retains_bearer_401_before_any_cloud_resolution(fixture):
    response = fixture.client.get("/api/cofre/servidores", headers=auth(key="other-fixture-key-at-least-32-bytes"))
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert fixture.resolver_calls == 0 and fixture.calls == []


def test_publication_racing_a_local_edit_cannot_modify_either_copy_without_cloud_proof(fixture, monkeypatch):
    import_local(fixture)
    change = fixture.shared.change_message

    def publish_then_edit(*args, **kwargs):
        publish_fixture(fixture)
        return change(*args, **kwargs)

    monkeypatch.setattr(fixture.shared, "change_message", publish_then_edit)
    sync_error(fixture.client.patch(MESSAGE, headers=auth(), json={"content": "Racing edit"}))
    assert len(fixture.calls) == 1 and fixture.calls[0][0] == "PUT"
    assert fixture.resolver_calls == 0
    for root in (fixture.shared.read_root("company-a"), fixture.shared.local_root("company-a")):
        assert storage.list_messages(root, "company-a", "guild", "channel")["mensagens"][0]["content"] == "Informacao local"
