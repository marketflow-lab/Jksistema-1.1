"""Fictional HTTP and transport contracts for native, company-scoped posts."""
import copy
import hashlib
import io
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from backend.modules.cofre import posting, shared_assets, shared_storage, storage
from backend.modules.cofre.shared import SharedCofre
from backend.routers.cofre import create_cofre_router

POST = "/api/cofre/canais/channel/mensagens?servidor_id=guild"
CHANNEL = "/api/cofre/canais/channel?servidor_id=guild"


def actor(user="alice", *, admin=False, company="company-a", remote=False):
    return {"client_id": company, "username": user, "exp": int(time.time()) + 3600,
            "scope": ["cofre:read", *(["cofre:write"] if admin else [])],
            "capability": "fictional-capability" if remote else ""}


def headers(user="alice", *, admin=False, company="company-a"):
    return {"X-Fixture-User": user, "X-Fixture-Admin": str(admin), "X-Fixture-Company": company}


def payload(content="Nova informacao", **extra):
    return {"request_id": str(uuid.uuid4()), "content": content, "links": [], **extra}


def send(client, body=None, *, files=(), **kwargs):
    return client.post(POST, files=[("payload", (None, json.dumps(body or payload()))), *[
        ("arquivos", file) for file in files]], **kwargs)


class Worker:
    """Service adapter fixture; Worker authorization/CAS are tested in Vitest."""
    def __init__(self):
        self.current = {"revision": 0, "state": None,
                        "metadata": {"version": 1, "ownership": [], "deleted_channels": []}}
        self.calls = []
        self.receipts = {}
        self.error = None

    def remote(self, method, path, principal, body=None):
        assert principal["capability"] == "fictional-capability"
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.error:
            raise storage.CofreError("Fixture failure", self.error)
        if method == "GET":
            return (304, None) if self.current["revision"] <= int(path.split("after=")[1]) else (200, copy.deepcopy(self.current))
        if method == "PUT":
            assert "cofre:write" in principal["scope"]
            assert body["expected_revision"] == self.current["revision"]
            self.current = {"revision": self.current["revision"] + 1, "state": copy.deepcopy(body["state"]),
                "metadata": copy.deepcopy(body.get("metadata", self.current["metadata"]))}
            return 200, copy.deepcopy(self.current)
        assert path == "/v1/mutations"
        key = principal["username"], body["request_id"]
        if key in self.receipts:
            return 200, {**copy.deepcopy(self.current), "message_id": self.receipts[key]}
        if body["expected_revision"] != self.current["revision"]:
            raise storage.CofreError("Fixture conflict", 409)
        channel = self.current["state"]["guilds"][0]["channels"][0]
        if body["action"] == "message.create":
            message_id = "jk_msg_" + uuid.UUID(body["request_id"]).hex
            channel["messages"].append({"id": message_id, "author": principal["username"],
                "timestamp": "2026-10-26T12:00:00Z", "content": body["content"], "links": body["links"],
                "attachments": [], "embeds": [], "source_url": "", "deleted": False, "edited_fields": []})
            self.current["metadata"]["ownership"].append({"guild_id": "guild", "channel_id": "channel",
                "message_id": message_id, "owner_username": principal["username"]})
            self.receipts[key] = message_id
        elif body["action"] == "channel.delete":
            assert "cofre:write" in principal["scope"]
            self.current["metadata"]["deleted_channels"] = [{"guild_id": "guild", "channel_id": "channel"}]
            message_id = None
        else:
            message_id = body["message_id"]
            message = next(m for m in channel["messages"] if m["id"] == message_id)
            message["deleted"] = body["action"] == "message.delete"
        self.current["revision"] += 1
        return 200, {**copy.deepcopy(self.current), "message_id": message_id}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    import requests
    def forbidden(*args, **kwargs):
        pytest.fail("An unmocked external transport was attempted")
    monkeypatch.setattr(requests.Session, "request", forbidden)
    shared = SharedCofre(tmp_path, url="https://cofre.example.test")
    worker = Worker()
    shared._remote = worker.remote
    data = {"format": storage.FORMAT, "guild": {"id": "guild", "name": "Acervo ficticio"},
        "exported_at": "2026-10-26T12:00:00Z", "channels": [
        {"id": "channel", "name": "guias", "messages": [{"id": "imported", "author": "alice", "content": "Importada"}]}]}
    storage.import_archive(shared.local_root("company-a"), "company-a", io.BytesIO(json.dumps(data).encode()), "fixture.json")
    context = SimpleNamespace(shared=shared, worker=worker, grant=False, resolver_calls=0)
    def tenant(request: Request):
        if not request.headers.get("X-Fixture-User"):
            raise HTTPException(401, "Fixture JK absent", headers={"WWW-Authenticate": "Bearer"})
        identity = actor(request.headers["X-Fixture-User"], admin=request.headers.get("X-Fixture-Admin") == "True",
                         company=request.headers.get("X-Fixture-Company", "company-a"))
        request.state.cofre_principal = identity
        return identity["client_id"]
    def remote(request):
        context.resolver_calls += 1
        if not context.grant:
            raise HTTPException(503, "Fixture cloud absent")
        return {**request.state.cofre_principal, "capability": "fictional-capability"}
    app = FastAPI()
    app.include_router(create_cofre_router(info_root=tmp_path, tenant_dependency=tenant,
        shared_service=shared, remote_principal=remote))
    with TestClient(app) as client:
        context.client = client
        yield context


def test_local_regular_user_posts_with_jk_only_and_immutable_owner(fixture):
    response = send(fixture.client, headers=headers())
    assert response.status_code == 200, response.text
    item = response.json()["mensagem"]
    assert item["author"] == item["owner_username"] == "alice"
    assert item["can_delete"] and not item["can_edit"]
    assert fixture.resolver_calls == 0 and fixture.worker.calls == []
    listed = fixture.client.get(POST, headers=headers("bob")).json()["mensagens"]
    assert all(not m["can_delete"] for m in listed)
    path = POST.replace("?", "/" + item["id"] + "?")
    assert fixture.client.delete(path, headers=headers("bob")).status_code == 403
    assert fixture.client.delete(path, headers=headers()).json()["mensagem"]["deleted"] is True
    assert fixture.client.post(path.replace("?", "/restaurar?"), headers=headers()).status_code == 200
    assert fixture.client.delete(path, headers=headers("admin", admin=True)).status_code == 200


def test_display_name_is_never_imported_ownership_or_delete_permission(fixture):
    path = POST.replace("?", "/imported?")
    assert fixture.client.delete(path, headers=headers()).status_code == 403
    assert fixture.client.delete(path, headers=headers("admin", admin=True)).status_code == 200


@pytest.mark.parametrize("extra", [{"author": "admin"}, {"owner_username": "admin"},
    {"admin": True}, {"client_id": "company-b"}, {"attachments": []}])
def test_post_rejects_client_authority_and_attachment_urls(fixture, extra):
    assert send(fixture.client, payload(**extra), headers=headers()).status_code == 422
    assert fixture.worker.calls == []


def test_photo_binary_private_download_sniffing_and_cross_company(fixture):
    image = b"\x89PNG\r\n\x1a\nfictional image bytes"
    response = send(fixture.client, payload("Foto"), headers=headers(), files=[("foto.png", image, "text/html")])
    assert response.status_code == 200, response.text
    file = response.json()["mensagem"]["attachments"][0]
    assert file["content_type"] == "image/png"
    downloaded = fixture.client.get(file["local_url"], headers=headers("bob"))
    assert downloaded.status_code == 200 and downloaded.content == image
    assert downloaded.headers["content-security-policy"].startswith("sandbox")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert fixture.client.get(file["local_url"], headers=headers(company="company-b")).status_code == 404
    html = send(fixture.client, files=[("photo.png", b"<script>alert(1)</script>", "image/png")], headers=headers())
    assert html.json()["mensagem"]["attachments"][0]["content_type"] == "application/octet-stream"


def test_upload_limits_empty_payload_invalid_multipart_and_idempotent_retry(fixture):
    body = payload()
    first, second = (send(fixture.client, body, headers=headers()) for _ in range(2))
    assert first.status_code == second.status_code == 200
    assert first.json()["mensagem"]["id"] == second.json()["mensagem"]["id"]
    assert send(fixture.client, {**body, "content": "Diferente"}, headers=headers()).status_code == 409
    assert send(fixture.client, payload(" "), headers=headers()).status_code == 422
    assert send(fixture.client, headers=headers(), files=[(str(i), b"x") for i in range(6)]).status_code == 400
    assert send(fixture.client, headers=headers(), files=[("big.bin", b"x" * (shared_assets.MAX_FILE_BYTES + 1))]).status_code == 413
    assert fixture.client.post(POST, headers=headers(), json=body).status_code == 415
    assert fixture.client.post(POST, headers={**headers(), "Content-Type": "multipart/form-data; boundary=x",
        "Content-Length": str(60 * 1024 * 1024)}, content=b"").status_code == 413
    assert send(fixture.client).status_code == 401


def test_only_admin_can_delete_channel_and_hidden_assets_stay_unavailable(fixture):
    post = send(fixture.client, files=[("report.pdf", b"fictional report")], headers=headers()).json()
    url = post["mensagem"]["attachments"][0]["local_url"]
    assert fixture.client.delete(CHANNEL, headers=headers()).status_code == 403
    result = fixture.client.delete(CHANNEL, headers=headers("admin", admin=True))
    assert result.status_code == 200, result.text
    assert fixture.client.get("/api/cofre/servidores/guild/canais", headers=headers()).json()["canais"] == []
    assert fixture.client.get(POST, headers=headers()).status_code == 404
    assert fixture.client.get(url, headers=headers()).status_code == 404
    assert fixture.resolver_calls == 0


def publish(fixture):
    fixture.shared.publish("company-a", actor("admin", admin=True, remote=True))
    fixture.grant = True


def test_shared_regular_post_syncs_authority_and_owner_delete(fixture):
    publish(fixture)
    body = payload()
    response = send(fixture.client, body, headers=headers())
    assert response.status_code == 200, response.text
    item = response.json()["mensagem"]
    assert item["owner_username"] == "alice" and item["can_delete"]
    assert send(fixture.client, body, headers=headers()).json()["mensagem"]["id"] == item["id"]
    mutation = fixture.worker.calls[-1][2]
    assert not ({"author", "owner_username", "client_id", "admin"} & mutation.keys())
    path = POST.replace("?", "/" + item["id"] + "?")
    assert fixture.client.delete(path, headers=headers("bob")).status_code == 403
    assert fixture.client.delete(path, headers=headers()).json()["mensagem"]["deleted"] is True
    fixture.shared._checks.clear()
    assert fixture.client.post("/api/cofre/compartilhamento/sincronizar", headers=headers("bob")).status_code == 200
    assert fixture.client.get(POST, headers=headers("bob")).json()["total"] == 1


def test_published_cloud_failure_preserves_cache_and_jk_without_offline_mutation(fixture):
    publish(fixture)
    before = shared_storage.export_state(fixture.shared.read_root("company-a"), "company-a")
    fixture.grant = False
    result = send(fixture.client, headers=headers())
    assert result.status_code == 503 and result.json()["detail"]["code"] == "cofre_sync_unavailable"
    assert "www-authenticate" not in result.headers
    assert shared_storage.export_state(fixture.shared.read_root("company-a"), "company-a") == before
    assert fixture.client.get(POST, headers=headers()).status_code == 200
    fixture.grant = True
    fixture.worker.error = 403
    assert send(fixture.client, headers=headers()).status_code == 403
    assert shared_storage.export_state(fixture.shared.read_root("company-a"), "company-a") == before


def test_admin_bootstrap_attests_local_owner_and_channel_tombstone(fixture):
    item = send(fixture.client, headers=headers()).json()["mensagem"]
    assert fixture.client.delete(CHANNEL, headers=headers("admin", admin=True)).status_code == 200
    publish(fixture)
    metadata = fixture.worker.current["metadata"]
    assert metadata["ownership"][0]["message_id"] == item["id"]
    assert metadata["deleted_channels"] == [{"guild_id": "guild", "channel_id": "channel"}]
    assert fixture.client.get("/api/cofre/servidores/guild/canais", headers=headers()).json()["canais"] == []


def test_shared_channel_delete_requires_admin_and_preserves_tombstone(fixture):
    publish(fixture)
    assert fixture.client.delete(CHANNEL, headers=headers()).status_code == 403
    assert fixture.client.delete(CHANNEL, headers=headers("admin", admin=True)).status_code == 200
    assert fixture.client.get(POST, headers=headers()).status_code == 404
    assert shared_storage.export_metadata(fixture.shared.read_root("company-a"), "company-a")["deleted_channels"]


class Response:
    def __init__(self, body=b"", *, status=200, headers=None):
        self.content, self.status_code, self.headers = body, status, headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, size):
        for i in range(0, len(self.content), size): yield self.content[i:i+size]
    def json(self): return json.loads(self.content)


def session(monkeypatch, response, calls):
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            calls.append((args, kwargs))
            return response
        def put(self, *args, **kwargs):
            calls.append((args, kwargs))
            return response
    monkeypatch.setattr(shared_assets.requests, "Session", Session)
    monkeypatch.setattr(shared_assets, "configure_requests_session", lambda value: None)


def remote_attachment(fixture, content=b"fictional file"):
    asset_id, filename = "f" * 64, "documento.pdf"
    data = shared_storage.export_state(fixture.shared.local_root("company-a"), "company-a")
    data["guilds"][0]["channels"][0]["messages"][0]["attachments"] = [
        {"url": shared_assets.cloud_url(fixture.shared, asset_id, filename), "filename": filename,
         "content_type": "application/octet-stream"}]
    shared_storage.store_state(fixture.shared.cache_for("company-a"), "company-a", data, 1)
    return asset_id, filename, content


def test_private_download_validates_hash_then_works_with_jk_cache_only(fixture, monkeypatch):
    asset_id, filename, content = remote_attachment(fixture)
    calls = []
    session(monkeypatch, Response(content, headers={"X-Cofre-SHA256": hashlib.sha256(content).hexdigest()}), calls)
    fixture.grant = True
    response = fixture.client.get(POST, headers=headers()).json()
    url = response["mensagens"][0]["attachments"][0]["local_url"]
    fetched = fixture.client.get(url, headers=headers())
    assert fetched.status_code == 200 and fetched.content == content
    assert calls[0][1]["allow_redirects"] is False
    fixture.grant = False
    assert fixture.client.get(url, headers=headers()).content == content
    assert len(calls) == 1
    assert fixture.client.get(url.replace(asset_id, "e" * 64), headers=headers()).status_code == 404
    assert fixture.client.get(url, headers=headers(company="company-b")).status_code == 404


@pytest.mark.parametrize("kind", ["hash", "size", "redirect", "missing"])
def test_download_never_caches_unconfirmed_or_redirected_file(fixture, monkeypatch, kind):
    asset_id, filename, content = remote_attachment(fixture)
    body = b"x" * (shared_assets.MAX_FILE_BYTES + 1) if kind == "size" else content
    response = Response(body, status=302 if kind == "redirect" else 200,
        headers={} if kind == "missing" else {"X-Cofre-SHA256": "a" * 64})
    session(monkeypatch, response, [])
    with pytest.raises(storage.CofreError):
        shared_assets.fetch(fixture.shared, "company-a", actor(remote=True), "guild", asset_id, filename)
    assert not (fixture.shared.cache_for("company-a") / "private_files" / asset_id).exists()


def test_upload_verifies_ready_record_and_never_trusts_server_filename(fixture, monkeypatch):
    asset_id, filename, content = "a" * 64, "file.txt", b"fictional file"
    record = {"asset_id": asset_id, "filename": filename, "size": len(content),
              "sha256": hashlib.sha256(content).hexdigest(), "ready": False,
              "url": shared_assets.cloud_url(fixture.shared, asset_id, filename)}
    fixture.shared._remote = lambda *args: (201, copy.deepcopy(record))
    calls = []
    session(monkeypatch, Response(json.dumps({**record, "ready": True}).encode()), calls)
    result = shared_assets.upload(fixture.shared, actor(remote=True), io.BytesIO(content), filename,
                                 str(uuid.uuid4()), "guild", "channel")
    assert result["ready"] and len(calls) == 1
    record["filename"] = "other.txt"
    with pytest.raises(storage.CofreError) as error:
        shared_assets.upload(fixture.shared, actor(remote=True), io.BytesIO(content), filename,
                             str(uuid.uuid4()), "guild", "channel")
    assert error.value.status_code == 502 and len(calls) == 1


def test_projection_never_builds_authenticated_path_from_unsafe_imported_filename(fixture):
    name = "../../../../auth/me"
    attachment = {"filename": name, "url": shared_assets.cloud_url(fixture.shared, "a" * 64, name)}
    result = fixture.shared.project_messages("company-a", actor(), "guild", "channel",
        {"mensagens": [{"id": "imported", "attachments": [attachment]}]})
    assert "local_url" not in result["mensagens"][0]["attachments"][0]


def test_local_router_without_cloud_service_still_enforces_owner_and_admin(fixture):
    def tenant(request: Request):
        identity = actor(request.headers.get("X-Fixture-User", "alice"),
                         admin=request.headers.get("X-Fixture-Admin") == "True")
        request.state.cofre_principal = identity
        return identity["client_id"]
    app = FastAPI()
    app.include_router(create_cofre_router(info_root=fixture.shared.local_root("company-a"), tenant_dependency=tenant))
    with TestClient(app) as client:
        result = send(client, headers=headers())
        assert result.status_code == 200, result.text
        path = POST.replace("?", "/" + result.json()["mensagem"]["id"] + "?")
        assert client.delete(path, headers=headers("bob")).status_code == 403
        assert client.patch(path, json={"content": "Forbidden"}, headers=headers()).status_code == 403
        assert client.delete(CHANNEL, headers=headers()).status_code == 403
        assert client.get("/api/cofre/compartilhamento", headers=headers()).json()["is_admin"] is False
        assert client.delete(path, headers=headers()).status_code == 200
        assert client.delete(CHANNEL, headers=headers("admin", admin=True)).status_code == 200


def test_publication_upload_retry_is_idempotent_per_administrator(fixture, monkeypatch):
    assert send(fixture.client, headers=headers(), files=[("file.txt", b"fictional")]).status_code == 200
    attempts = []
    def upload(service, principal, source, filename, request_id, guild, channel):
        attempts.append((principal["username"], request_id))
        return {"url": shared_assets.cloud_url(service, "f" * 64, filename)}
    def unavailable(*args, **kwargs):
        raise storage.CofreError("Fixture publication failure", 503)
    monkeypatch.setattr(shared_assets, "upload", upload)
    monkeypatch.setattr(fixture.shared, "_publish_state", unavailable)
    for user in ("alice", "alice", "bob"):
        with pytest.raises(storage.CofreError):
            fixture.shared.publish("company-a", actor(user, admin=True, remote=True))
    assert attempts[0] == attempts[1]
    assert attempts[0][1] != attempts[2][1]
    assert all(uuid.UUID(request_id).version == 4 for _, request_id in attempts)


@pytest.mark.parametrize("body", [b"null", b"[]", b"invalid", b'{"x":1}', b"x" * 8193])
def test_malformed_binary_upload_confirmation_is_a_controlled_error(fixture, monkeypatch, body):
    content, filename, asset_id = b"fictional", "file.txt", "a" * 64
    record = {"asset_id": asset_id, "filename": filename, "size": len(content), "ready": False,
        "sha256": hashlib.sha256(content).hexdigest(), "url": shared_assets.cloud_url(fixture.shared, asset_id, filename)}
    fixture.shared._remote = lambda *args: (201, record)
    session(monkeypatch, Response(body), [])
    with pytest.raises(storage.CofreError) as error:
        shared_assets.upload(fixture.shared, actor(remote=True), io.BytesIO(content), filename,
                             str(uuid.uuid4()), "guild", "channel")
    assert error.value.status_code in {502, 503}


def test_first_publication_uses_confirmed_binary_type_without_mutating_local_original(fixture, monkeypatch):
    assert send(fixture.client, headers=headers(), files=[("file.pdf", b"%PDF-fictional")]).status_code == 200
    root = fixture.shared.local_root("company-a")
    def upload(service, principal, source, filename, request_id, guild, channel):
        return {"url": shared_assets.cloud_url(service, "f" * 64, filename), "content_type": "application/pdf"}
    monkeypatch.setattr(shared_assets, "upload", upload)
    fixture.shared.publish("company-a", actor("admin", admin=True, remote=True))
    shared_files = [a for m in fixture.worker.current["state"]["guilds"][0]["channels"][0]["messages"] for a in m["attachments"]]
    assert shared_files[0]["content_type"] == "application/pdf"
    local_files = [a for m in storage.list_messages(root, "company-a", "guild", "channel")["mensagens"] for a in m["attachments"]]
    assert local_files[0]["content_type"] == "application/octet-stream"
    assert local_files[0]["local_url"] and local_files[0]["url"] == ""
