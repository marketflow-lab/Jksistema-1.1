"""Two-device synchronization, concurrency, privacy, and offline behavior."""
import copy
import hashlib
import io
import json
import time
import zipfile

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.modules.cofre import shared_storage, storage
from backend.modules.cofre.shared import CofreSyncUnavailable, SharedCofre
from backend.routers.cofre import create_cofre_router


def archive(content="Informacao da empresa"):
    return {"format": storage.FORMAT, "guild": {"id": "guild", "name": "Acervo"},
            "exported_at": "2026-10-22T12:00:00Z", "channels": [
                {"id": "channel", "name": "guias", "messages": [
                    {"id": "message", "content": content, "author": "Fixture"}]}]}


def identity(company="company-a", *, write=True):
    return {"client_id": company, "username": "fixture", "exp": int(time.time()) + 3600,
            "scope": ["cofre:read", *(["cofre:write"] if write else [])], "capability": "test-capability"}


def import_archive(root, company="company-a", content="Informacao da empresa"):
    return storage.import_archive(root, company, io.BytesIO(json.dumps(archive(content)).encode()), "archive.json")


class MemoryD1:
    def __init__(self):
        self.companies = {}
        self.calls = []
        self.offline = False

    def remote(self, method, path, principal, payload=None):
        if self.offline:
            raise storage.CofreError("Conexao indisponivel.", 503)
        company = principal["client_id"]
        self.calls.append((company, method))
        current = self.companies.get(company, {"revision": 0, "state": None})
        if method == "GET":
            after = int(path.split("after=")[1])
            if current["revision"] <= after:
                return 304, None
            return 200, copy.deepcopy(current)
        if current["revision"] != payload["expected_revision"]:
            raise storage.CofreError("Sincronize antes de publicar.", 409)
        updated = {"revision": current["revision"] + 1, "state": copy.deepcopy(payload["state"])}
        self.companies[company] = updated
        return 200, {"revision": updated["revision"]}


def service(root, remote):
    result = SharedCofre(root, url="https://cofre.example.test")
    result._remote = remote.remote
    return result


def messages(shared, company="company-a", *, trash=False):
    return storage.list_messages(shared.read_root(company), company, "guild", "channel", excluidos=trash)


def test_unpublished_archive_accepts_local_jk_write_without_cloud_capability(tmp_path):
    remote = MemoryD1()
    shared = service(tmp_path, remote)
    local = {**identity(), "capability": ""}
    shared.import_archive("company-a", local, io.BytesIO(json.dumps(archive()).encode()), "local.json")
    assert shared.status("company-a", local)["enabled"] is False
    shared.change_message("company-a", local, "guild", "channel", "message",
                          patch=storage.MessagePatch(content="Edicao local"))
    shared.change_channel("company-a", local, "guild", "channel", storage.ChannelPatch(name="Canal local"))
    shared.change_message("company-a", local, "guild", "channel", "message", deleted=True)
    assert messages(shared)["total"] == 0
    shared.change_message("company-a", local, "guild", "channel", "message", deleted=False)
    assert messages(shared)["mensagens"][0]["content"] == "Edicao local"
    assert remote.calls == [] and remote.companies == {}
    with pytest.raises(CofreSyncUnavailable):
        shared.publish("company-a", local)


@pytest.mark.parametrize("changes", [
    {"client_id": "company-b"}, {"exp": 0}, {"exp": True},
    {"scope": []}, {"scope": ["cofre:write"]},
])
def test_local_status_still_requires_tenant_read_scope_and_live_session(tmp_path, changes):
    shared = service(tmp_path, MemoryD1())
    with pytest.raises(storage.CofreError) as denied:
        shared.status("company-a", {**identity(), "capability": "", **changes})
    assert denied.value.status_code == 403


def test_published_archive_never_falls_back_to_local_mutations_without_cloud_proof(tmp_path):
    remote = MemoryD1()
    shared = service(tmp_path, remote)
    import_archive(tmp_path)
    shared.publish("company-a", identity())
    before = shared_storage.export_state(shared.cache_for("company-a"), "company-a")
    local = {**identity(), "capability": ""}
    count = len(remote.calls)
    for mutation in (
        lambda: shared.change_message("company-a", local, "guild", "channel", "message", deleted=True),
        lambda: shared.change_channel("company-a", local, "guild", "channel", storage.ChannelPatch(name="Denied")),
        lambda: shared.import_archive("company-a", local, io.BytesIO(json.dumps(archive("Denied")).encode()), "denied.json"),
    ):
        with pytest.raises(CofreSyncUnavailable):
            mutation()
    assert shared_storage.export_state(shared.cache_for("company-a"), "company-a") == before
    assert storage.list_messages(tmp_path, "company-a", "guild", "channel")["mensagens"][0]["content"] == "Informacao da empresa"
    assert len(remote.calls) == count


@pytest.mark.parametrize("status", [401, 403])
def test_worker_authentication_error_is_sync_unavailable_not_jk_logout(tmp_path, monkeypatch, status):
    from backend.modules.cofre import shared as shared_module

    class Response:
        status_code = status
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def iter_content(self, *_):
            return iter([b'{"error":"invalid_token"}'])

    class Transport:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def request(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(shared_module.requests, "Session", Transport)
    monkeypatch.setattr(shared_module, "configure_requests_session", lambda *_: None)
    with pytest.raises(CofreSyncUnavailable) as unavailable:
        SharedCofre(tmp_path, url="https://cofre.example.test").sync("company-a", identity())
    assert unavailable.value.status_code == 503


def test_two_devices_publish_pull_search_and_read_without_network(tmp_path):
    remote = MemoryD1()
    first = service(tmp_path / "first", remote)
    second = service(tmp_path / "second", remote)
    import_archive(first.root)
    original = (first.root / "company-a/cofre/cofre.sqlite3").read_bytes()
    assert first.publish("company-a", identity())["revision"] == 1
    assert second.sync("company-a", identity(write=False))["changed"] is True
    before = len(remote.calls)
    for _ in range(10):
        assert messages(second)["mensagens"][0]["content"] == "Informacao da empresa"
        assert second.sync("company-a", identity(write=False))["changed"] is False
        assert second.status("company-a", identity(write=False))["can_publish"] is False
    assert len(remote.calls) == before
    assert (first.root / "company-a/cofre/cofre.sqlite3").read_bytes() == original


def test_company_and_publisher_isolation(tmp_path):
    remote = MemoryD1()
    shared = service(tmp_path, remote)
    import_archive(tmp_path)
    with pytest.raises(storage.CofreError) as denied:
        shared.change_message("company-a", identity(write=False), "guild", "channel", "message", deleted=True)
    assert denied.value.status_code == 403
    assert storage.list_messages(tmp_path, "company-a", "guild", "channel")["total"] == 1
    shared.publish("company-a", identity())
    assert shared.sync("company-b", identity("company-b"))["enabled"] is False
    assert storage.list_guilds(shared.read_root("company-b"), "company-b") == {"servidores": []}
    with pytest.raises(storage.CofreError) as denied:
        shared.publish("company-a", identity(write=False))
    assert denied.value.status_code == 403
    with pytest.raises(storage.CofreError):
        shared.sync("company-a", identity("company-b"))
    expired = {**identity(), "exp": int(time.time()) - 1}
    with pytest.raises(storage.CofreError):
        shared.sync("company-a", expired)


def test_conflicting_edits_preserve_local_copy_and_allow_immediate_pull(tmp_path):
    remote = MemoryD1()
    first = service(tmp_path / "first", remote)
    second = service(tmp_path / "second", remote)
    import_archive(first.root)
    first.publish("company-a", identity())
    second.sync("company-a", identity())
    first.change_message("company-a", identity(), "guild", "channel", "message",
                         patch=storage.MessagePatch(content="Primeira edicao"))
    with pytest.raises(storage.CofreError) as conflict:
        second.change_message("company-a", identity(), "guild", "channel", "message",
                              patch=storage.MessagePatch(content="Edicao concorrente"))
    assert conflict.value.status_code == 409
    assert messages(second)["mensagens"][0]["content"] == "Informacao da empresa"
    assert second.sync("company-a", identity())["revision"] == 2
    assert messages(second)["mensagens"][0]["content"] == "Primeira edicao"
    second.change_message("company-a", identity(), "guild", "channel", "message", deleted=True)
    first._checks.clear()
    first.sync("company-a", identity())
    assert messages(first)["total"] == 0
    assert messages(first, trash=True)["total"] == 1
    first.change_message("company-a", identity(), "guild", "channel", "message", deleted=False)
    assert messages(first)["total"] == 1


def test_offline_and_failed_publish_keep_cached_and_original_data(tmp_path):
    remote = MemoryD1()
    shared = service(tmp_path, remote)
    import_archive(tmp_path)
    shared.publish("company-a", identity())
    shared._checks.clear()
    remote.offline = True
    with pytest.raises(storage.CofreError):
        shared.sync("company-a", identity())
    with pytest.raises(storage.CofreError):
        shared.change_message("company-a", identity(), "guild", "channel", "message",
                              patch=storage.MessagePatch(content="Nao confirmado"))
    assert messages(shared)["mensagens"][0]["content"] == "Informacao da empresa"
    assert storage.list_messages(tmp_path, "company-a", "guild", "channel")["total"] == 1


def test_snapshot_validation_and_atomic_cache(tmp_path, monkeypatch):
    import_archive(tmp_path)
    state = shared_storage.export_state(tmp_path, "company-a")
    cache = tmp_path / "cache"
    shared_storage.store_state(cache, "company-a", state, 1)
    malformed = copy.deepcopy(state)
    malformed["client_id"] = "company-b"
    with pytest.raises(storage.CofreError):
        shared_storage.store_state(cache, "company-a", malformed, 2)
    assert shared_storage.read_meta(cache, "company-a")["revision"] == 1
    monkeypatch.setattr(storage, "_searchable_message", lambda _: (_ for _ in ()).throw(RuntimeError("disk failure")))
    with pytest.raises(RuntimeError):
        shared_storage.store_state(cache, "company-a", state, 2)
    assert shared_storage.read_meta(cache, "company-a")["revision"] == 1
    assert storage.list_messages(cache, "company-a", "guild", "channel")["total"] == 1


@pytest.mark.parametrize("url", ["", "https://example.test/a b", "https://example.test/\x7f"])
def test_shared_links_follow_worker_contract(tmp_path, url):
    import_archive(tmp_path)
    state = shared_storage.export_state(tmp_path, "company-a")
    state["guilds"][0]["channels"][0]["messages"][0]["links"] = [{"url": url}]
    with pytest.raises(storage.CofreError) as invalid:
        shared_storage.validate_state(state)
    assert invalid.value.status_code == 422


def test_invalid_remote_snapshot_does_not_throttle_recovery(tmp_path):
    remote = MemoryD1()
    publisher = service(tmp_path / "publisher", remote)
    reader = service(tmp_path / "reader", remote)
    import_archive(publisher.root)
    publisher.publish("company-a", identity())
    malformed = copy.deepcopy(remote.companies["company-a"])
    malformed["state"]["client_id"] = "other-company"
    reader._remote = lambda *args: (200, malformed)
    with pytest.raises(storage.CofreError):
        reader.sync("company-a", identity(write=False))
    reader._remote = remote.remote
    assert reader.sync("company-a", identity(write=False))["revision"] == 1


def test_existing_routes_share_edits_and_reader_cannot_mutate(tmp_path):
    remote = MemoryD1()
    shared = service(tmp_path, remote)
    import_archive(tmp_path)
    principal = identity()

    def tenant(request: Request):
        request.state.cofre_principal = principal
        return principal["client_id"]

    app = FastAPI()
    app.include_router(create_cofre_router(info_root=tmp_path, tenant_dependency=tenant, shared_service=shared))
    client = TestClient(app)
    assert client.post("/api/cofre/compartilhamento/publicar").json()["revision"] == 1
    before = len(remote.calls)
    assert client.get("/api/cofre/servidores").status_code == 200
    assert client.get("/api/cofre/compartilhamento").json()["enabled"] is True
    assert len(remote.calls) == before
    principal = identity(write=False)
    path = "/api/cofre/canais/channel/mensagens/message?servidor_id=guild"
    assert client.patch(path, json={"content": "Sem permissao"}).status_code == 403
    assert client.delete(path).status_code == 403
    assert client.post("/api/cofre/compartilhamento/publicar").status_code == 403
    principal = identity()
    assert client.patch(path, json={"content": "Publicada"}).status_code == 200
    assert remote.companies["company-a"]["state"]["guilds"][0]["channels"][0]["messages"][0]["content"] == "Publicada"


def test_reimport_on_second_device_preserves_published_edits_and_tombstones(tmp_path):
    remote = MemoryD1()
    first = service(tmp_path / "first", remote)
    second = service(tmp_path / "second", remote)
    original = archive()
    original["channels"][0].update(category="Original category", topic="Original topic")
    original["channels"][0]["messages"].append({"id": "deleted-message", "content": "Deleted source"})
    storage.import_archive(first.root, "company-a", io.BytesIO(json.dumps(original).encode()), "fixture.json")
    source_bytes = (first.root / "company-a/cofre/cofre.sqlite3").read_bytes()
    first.publish("company-a", identity())
    edited_links = [{"url": "https://example.test/edited", "text": "Edited link"}]
    edited_embeds = [{"text": "Edited embed", "links": []}]
    first.change_message("company-a", identity(), "guild", "channel", "message",
        patch=storage.MessagePatch(content="Published edit", author="Edited author",
                                   links=edited_links, embeds=edited_embeds))
    first.change_channel("company-a", identity(), "guild", "channel",
        storage.ChannelPatch(name="Edited channel", category="Edited category", topic=None))
    first.change_message("company-a", identity(), "guild", "channel", "deleted-message", deleted=True)
    assert second.sync("company-a", identity())["changed"] is True

    newer = copy.deepcopy(original)
    newer["channels"][0].update(name="Fresh source name", category="Fresh source category", topic="Fresh source topic")
    newer["channels"][0]["messages"][0].update(content="Fresh source text", author="Fresh source author", links=[], embeds=[])
    newer["channels"][0]["messages"][1]["content"] = "Fresh deleted source"
    newer["channels"][0]["messages"].append({"id": "new-message", "content": "New imported text"})
    second.import_archive("company-a", identity(), io.BytesIO(json.dumps(newer).encode()), "fresh.json")
    first._checks.clear()
    assert first.sync("company-a", identity())["changed"] is True
    for device in (first, second):
        active = messages(device)
        assert active["total"] == 2
        edited = next(item for item in active["mensagens"] if item["id"] == "message")
        assert edited["content"] == "Published edit"
        assert edited["author"] == "Edited author"
        assert edited["links"] == edited_links
        assert edited["embeds"] == edited_embeds
        assert next(item for item in active["mensagens"] if item["id"] == "new-message")["content"] == "New imported text"
        deleted = messages(device, trash=True)
        assert deleted["total"] == 1
        assert deleted["mensagens"][0]["id"] == "deleted-message"
        assert deleted["mensagens"][0]["content"] == "Fresh deleted source"
        channel = storage.list_channels(device.read_root("company-a"), "company-a", "guild")["canais"][0]
        assert channel["name"] == "Edited channel"
        assert channel["category"] == "Edited category"
        assert channel.get("topic") is None

    first.change_message("company-a", identity(), "guild", "channel", "deleted-message", deleted=False)
    second._checks.clear()
    second.sync("company-a", identity())
    assert messages(second)["total"] == 3
    assert messages(second, trash=True)["total"] == 0
    assert (first.root / "company-a/cofre/cofre.sqlite3").read_bytes() == source_bytes


def test_case_distinct_companies_publish_pull_and_mutate_without_crossing(tmp_path):
    remote = MemoryD1()
    upper, lower = "Company-A", "company-a"
    send_upper = service(tmp_path / "sender", remote)
    send_lower = service(tmp_path / "sender", remote)
    for device, company, text in ((send_upper, upper, "UPPER source"), (send_lower, lower, "LOWER source")):
        device.import_archive(company, identity(company), io.BytesIO(json.dumps(archive(text)).encode()), "fixture.json")
        device.publish(company, identity(company))

    receive_upper = service(tmp_path / "receiver", remote)
    receive_lower = service(tmp_path / "receiver", remote)
    assert receive_upper.sync(upper, identity(upper))["changed"] is True
    assert receive_lower.sync(lower, identity(lower))["changed"] is True
    assert messages(receive_upper, upper)["mensagens"][0]["content"] == "UPPER source"
    assert messages(receive_lower, lower)["mensagens"][0]["content"] == "LOWER source"

    receive_upper.change_message(upper, identity(upper), "guild", "channel", "message",
                                patch=storage.MessagePatch(content="UPPER edited"))
    receive_lower.change_channel(lower, identity(lower), "guild", "channel", storage.ChannelPatch(name="LOWER channel"))
    receive_lower.change_message(lower, identity(lower), "guild", "channel", "message", deleted=True)
    assert messages(receive_upper, upper)["mensagens"][0]["content"] == "UPPER edited"
    assert messages(receive_lower, lower)["total"] == 0
    assert messages(receive_lower, lower, trash=True)["mensagens"][0]["content"] == "LOWER source"

    for device, company in ((send_upper, upper), (send_lower, lower)):
        device._checks.clear()
        assert device.sync(company, identity(company))["changed"] is True
    assert messages(send_upper, upper)["mensagens"][0]["content"] == "UPPER edited"
    assert messages(send_lower, lower)["total"] == 0
    upper_channel = storage.list_channels(send_upper.read_root(upper), upper, "guild")["canais"][0]
    lower_channel = storage.list_channels(send_lower.read_root(lower), lower, "guild")["canais"][0]
    assert upper_channel["name"] == "guias"
    assert lower_channel["name"] == "LOWER channel"

    send_lower.change_message(lower, identity(lower), "guild", "channel", "message", deleted=False)
    receive_lower._checks.clear()
    receive_lower.sync(lower, identity(lower))
    assert messages(receive_lower, lower)["mensagens"][0]["content"] == "LOWER source"
    assert messages(receive_upper, upper)["mensagens"][0]["content"] == "UPPER edited"
    assert set(remote.companies) == {upper, lower}
    assert storage.list_messages(send_upper.local_root(upper), upper, "guild", "channel")["mensagens"][0]["content"] == "UPPER source"
    assert storage.list_messages(send_lower.local_root(lower), lower, "guild", "channel")["mensagens"][0]["content"] == "LOWER source"


def test_conflicting_legacy_case_is_never_read_published_or_downloaded(tmp_path):
    remote = MemoryD1()
    upper, lower = "Company-A", "company-a"
    original = archive("UPPER legacy information")
    asset = b"UPPER_LEGACY_ASSET_FIXTURE"
    original["channels"][0]["messages"][0]["attachments"] = [{
        "filename": "upper.txt", "url": "https://example.test/upper.txt", "archive_path": "assets/upper.txt"}]
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as zipped:
        zipped.writestr("manifest.json", json.dumps(original))
        zipped.writestr("assets/upper.txt", asset)
    package.seek(0)
    storage.import_archive(tmp_path, upper, package, "fixture.zip")
    original_bytes = (tmp_path / upper / "cofre/cofre.sqlite3").read_bytes()
    shared = service(tmp_path, remote)

    def tenant(request: Request):
        company = request.headers["x-fixture-company"]
        request.state.cofre_principal = identity(company)
        return company

    app = FastAPI()
    app.include_router(create_cofre_router(info_root=tmp_path, tenant_dependency=tenant, shared_service=shared))
    client = TestClient(app)
    upper_headers = {"x-fixture-company": upper}
    lower_headers = {"x-fixture-company": lower}
    assert client.get("/api/cofre/servidores", headers=lower_headers).json() == {"servidores": []}
    assert len(client.get("/api/cofre/servidores", headers=upper_headers).json()["servidores"]) == 1
    asset_url = "/api/cofre/arquivos/guild/" + hashlib.sha256(asset).hexdigest() + "/upper.txt"
    assert client.get(asset_url, headers=upper_headers).content == asset
    assert client.get(asset_url, headers=lower_headers).status_code == 404

    assert shared.publish(lower, identity(lower))["revision"] == 1
    assert remote.companies[lower]["state"]["guilds"] == []
    assert shared.publish(upper, identity(upper))["revision"] == 1
    assert remote.companies[upper]["state"]["guilds"][0]["channels"][0]["messages"][0]["content"] == "UPPER legacy information"
    shared.import_archive(lower, identity(lower), io.BytesIO(json.dumps(archive("LOWER own information")).encode()), "lower.json")
    assert messages(shared, lower)["mensagens"][0]["content"] == "LOWER own information"
    assert messages(shared, upper)["mensagens"][0]["content"] == "UPPER legacy information"
    assert client.get(asset_url, headers=lower_headers).status_code == 404
    assert (tmp_path / upper / "cofre/cofre.sqlite3").read_bytes() == original_bytes
