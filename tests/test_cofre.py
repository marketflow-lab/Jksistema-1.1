from __future__ import annotations

import copy
import io
import json
import sqlite3
import stat
import warnings
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from backend.modules.cofre import storage
from backend.routers.cofre import create_cofre_router


def manifest():
    return {"format": storage.FORMAT, "guild": {"id": "guild1", "name": "Servidor exemplo"},
            "exported_at": "2026-10-21T16:00:00Z", "channels": [{
                "id": "channel1", "name": "guias", "category": "Estudos", "url": "https://discord.com/channels/guild1/channel1",
                "topic": "Guias do servidor", "kind": "text", "history_complete": False,
                "messages": [{"id": "message1", "author": "Pessoa exemplo", "timestamp": "2026-10-20T10:00:00Z",
                              "content": "Guia de produtos", "links": [{"url": "https://example.org/manual", "text": "Manual"}],
                              "attachments": [], "embeds": [{"text": "Referencia"}],
                              "source_url": "https://discord.com/channels/guild1/channel1/message1"}]}]}


def raw(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def package(payload, assets=None, extra=None):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", raw(payload))
        for name, data in (assets or {}).items():
            archive.writestr(name, data)
        for name, data in (extra or []):
            info = zipfile.ZipInfo("placeholder")
            info.filename = name
            archive.writestr(info, data)
    target.seek(0)
    return target


def import_json(root, payload, tenant="tenant-a"):
    return storage.import_archive(root, tenant, io.BytesIO(raw(payload)), "arquivo.json")


def attachment_manifest():
    payload = manifest()
    payload["channels"][0]["messages"][0]["attachments"] = [{
        "url": "https://cdn.discordapp.com/attachments/example/report.html", "filename": "relatorio.html",
        "content_type": "text/html", "archive_path": "assets/report.html"}]
    return payload


def test_import_round_trip_idempotent_merge_and_partial_history(tmp_path):
    payload = manifest()
    first = import_json(tmp_path, payload)
    assert first["imported_messages"] == 1
    assert first["servidor"]["history_complete"] is False
    assert import_json(tmp_path, payload)["imported_messages"] == 0
    payload["channels"][0]["messages"][0]["content"] = "Texto atualizado"
    payload["channels"][0]["messages"].append({"id": "message2", "content": "Mensagem nova", "timestamp": "2026-10-21T10:00:00Z"})
    assert import_json(tmp_path, payload)["imported_messages"] == 1
    import_json(tmp_path, {**payload, "channels": []})
    channels = storage.list_channels(tmp_path, "tenant-a", "guild1")
    assert channels["canais"][0]["message_count"] == 2
    messages = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")
    assert messages["total"] == 2
    assert messages["mensagens"][0]["content"] == "Texto atualizado"


def test_tenant_and_guild_isolation(tmp_path):
    import_json(tmp_path, manifest())
    assert storage.list_guilds(tmp_path, "tenant-b") == {"servidores": []}
    with pytest.raises(storage.CofreError, match="Canal nao encontrado"):
        storage.list_messages(tmp_path, "tenant-b", "guild1", "channel1")
    with pytest.raises(storage.CofreError, match="Canal nao encontrado"):
        storage.list_messages(tmp_path, "tenant-a", "other-guild", "channel1")
    assert not (tmp_path / "tenant-b").exists()


def test_xss_is_preserved_as_text_and_search_is_parameterized(tmp_path):
    payload = manifest()
    text = '<img src=x onerror="alert(1)"><script>window.pwned=1</script>'
    payload["channels"][0]["messages"][0]["content"] = text
    import_json(tmp_path, payload)
    result = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="ONERROR")
    assert result["total"] == 1
    assert result["mensagens"][0]["content"] == text
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="' OR 1=1 --")["total"] == 0


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(client_id="tenant-b"),
    lambda p: p["channels"][0]["messages"][0]["links"][0].update(url="javascript:alert(1)"),
    lambda p: p["channels"][0]["messages"][0].update(source_url="data:text/html,hello"),
    lambda p: p["channels"][0].update(history_complete="true"),
    lambda p: p["channels"][0].update(id="../outside"),
    lambda p: p["channels"].append(copy.deepcopy(p["channels"][0])),
    lambda p: p["channels"][0]["messages"].append(copy.deepcopy(p["channels"][0]["messages"][0])),
])
def test_invalid_schema_rejected_without_persistence(tmp_path, mutation):
    payload = manifest()
    mutation(payload)
    with pytest.raises(storage.CofreError):
        import_json(tmp_path, payload)
    assert not (tmp_path / "tenant-a").exists()


def test_duplicate_json_keys_are_rejected(tmp_path):
    payload = raw(manifest()).replace(b'"format":', b'"format":"ignored","format":', 1)
    with pytest.raises(storage.CofreError, match="duplicados"):
        storage.import_archive(tmp_path, "tenant-a", io.BytesIO(payload), "a.json")


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "assets/../escape.txt", "assets\\escape.txt", "C:/evil.txt"])
def test_zip_slip_rejected_before_any_write(tmp_path, name):
    with pytest.raises(storage.CofreError):
        storage.import_archive(tmp_path, "tenant-a", package(manifest(), extra=[(name, b"bad")]), "a.zip")
    assert not (tmp_path / "tenant-a").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_zip_duplicate_and_symlink_rejected(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        duplicate = package(manifest(), extra=[("assets/A.txt", b"1"), ("assets/a.txt", b"2")])
    with pytest.raises(storage.CofreError, match="duplicados"):
        storage.import_archive(tmp_path, "tenant-a", duplicate, "a.zip")
    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr("manifest.json", raw(manifest()))
        info = zipfile.ZipInfo("assets/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../outside")
    symlink.seek(0)
    with pytest.raises(storage.CofreError, match="links"):
        storage.import_archive(tmp_path, "tenant-a", symlink, "a.zip")


def test_zip_bomb_and_missing_asset_leave_existing_archive_intact(tmp_path, monkeypatch):
    import_json(tmp_path, manifest())
    with pytest.raises(storage.CofreError, match="ausente"):
        storage.import_archive(tmp_path, "tenant-a", package(attachment_manifest()), "a.zip")
    monkeypatch.setattr(storage, "MAX_EXPANDED_BYTES", 10)
    with pytest.raises(storage.CofreError) as error:
        storage.import_archive(tmp_path, "tenant-a", package(manifest()), "a.zip")
    assert error.value.status_code == 413
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")["total"] == 1


def test_zip_assets_content_addressed_and_preserved_on_json_reimport(tmp_path):
    payload = attachment_manifest()
    storage.import_archive(tmp_path, "tenant-a", package(payload, {"assets/report.html": b"<script>hello</script>"}), "a.zip")
    message = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")["mensagens"][0]
    attachment = message["attachments"][0]
    assert "archive_path" not in attachment
    _, guild_id, digest, filename = attachment["local_url"].rsplit("/", 3)
    assert storage.get_asset(tmp_path, "tenant-a", guild_id, digest, filename).read_bytes() == b"<script>hello</script>"
    with pytest.raises(storage.CofreError):
        storage.get_asset(tmp_path, "tenant-b", guild_id, digest, filename)
    payload["channels"][0]["messages"][0]["attachments"][0].pop("archive_path")
    import_json(tmp_path, payload)
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")["mensagens"][0]["attachments"][0]["local_url"] == attachment["local_url"]
    assert len(list((tmp_path / "tenant-a" / "cofre" / "assets").iterdir())) == 1


def test_search_order_and_pagination(tmp_path):
    payload = manifest()
    payload["channels"][0]["history_complete"] = True
    payload["channels"][0]["messages"] = [
        {"id": "later", "timestamp": "2026-10-21T12:00:00Z", "content": "Guia depois"},
        {"id": "earlier", "timestamp": "2026-10-20T12:00:00Z", "content": "GUIA antes"}]
    import_json(tmp_path, payload)
    result = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="guia", limit=1)
    assert result["mensagens"][0]["id"] == "earlier"
    assert result["has_more"] is True
    second = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", offset=1, limit=1)
    assert second["mensagens"][0]["id"] == "later"
    assert second["has_more"] is False
    assert storage.list_guilds(tmp_path, "tenant-a")["servidores"][0]["history_complete"] is True


@pytest.mark.parametrize("tenant", ["../tenant-a", "CON", ".", "tenant/other", "tenant."])
def test_tenant_path_invalid(tmp_path, tenant):
    with pytest.raises(storage.CofreError):
        import_json(tmp_path, manifest(), tenant)


@pytest.fixture
def client(tmp_path):
    async def authenticated_tenant(authorization: str | None = Header(None)):
        if authorization not in {"Bearer tenant-a", "Bearer tenant-b"}:
            raise HTTPException(401, "Sessao obrigatoria")
        return authorization.split()[-1]
    app = FastAPI()
    app.include_router(create_cofre_router(info_root=tmp_path, tenant_dependency=authenticated_tenant))
    return TestClient(app)


def test_router_auth_upload_queries_and_cross_tenant_asset(client):
    assert client.get("/api/cofre/servidores").status_code == 401
    response = client.post("/api/cofre/importar", headers={"Authorization": "Bearer tenant-a"},
                           files={"arquivo": ("archive.zip", package(attachment_manifest(), {"assets/report.html": b"<script>test</script>"}).getvalue(), "application/zip")})
    assert response.status_code == 200, response.text
    assert response.json()["total_messages"] == 1
    headers = {"Authorization": "Bearer tenant-a"}
    assert client.get("/api/cofre/servidores/guild1/canais", headers=headers).json()["canais"][0]["name"] == "guias"
    result = client.get("/api/cofre/canais/channel1/mensagens?servidor_id=guild1&client_id=tenant-b", headers=headers)
    assert result.status_code == 200
    assert result.headers["cache-control"].startswith("no-store")
    local_url = result.json()["mensagens"][0]["attachments"][0]["local_url"]
    asset = client.get(local_url, headers=headers)
    assert asset.status_code == 200
    assert asset.content == b"<script>test</script>"
    assert asset.headers["content-type"] == "application/octet-stream"
    assert asset.headers["content-disposition"].startswith("attachment;")
    assert asset.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in asset.headers["content-security-policy"]
    assert client.get(local_url, headers={"Authorization": "Bearer tenant-b"}).status_code == 404
    assert client.get("/api/cofre/servidores", headers={"Authorization": "Bearer tenant-b"}).json() == {"servidores": []}
    assert client.get("/api/cofre/canais/channel1/mensagens?servidor_id=guild1&limit=201", headers=headers).status_code == 422


def test_router_upload_limits_and_multipart_shape(client):
    headers = {"Authorization": "Bearer tenant-a"}
    response = client.post("/api/cofre/importar", headers={**headers, "Content-Type": "multipart/form-data; boundary=test", "Content-Length": str(storage.MAX_UPLOAD_BYTES + 100000) }, content=b"")
    assert response.status_code == 413
    assert client.post("/api/cofre/importar", headers=headers, json=manifest()).status_code == 415
    assert client.post("/api/cofre/importar", headers=headers, files={"other": ("a.json", raw(manifest()))}).status_code == 400
    response = client.post("/api/cofre/importar", headers=headers, files={"arquivo": ("a.json", raw(manifest()))}, data={"client_id": "tenant-b"})
    assert response.status_code == 400


def test_permission_contract_is_explicit():
    # Evaluate only the route mapping function, keeping unrelated runtime integrations out of this unit.
    import ast
    source = Path("backend/services/admin_usuarios_common.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    mapping = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_permissao_exigida_por_rota")
    module = ast.Module(body=[mapping], type_ignores=[])
    namespace = {"Optional": __import__("typing").Optional, "Any": __import__("typing").Any}
    exec(compile(module, "permission-mapping", "exec"), namespace)
    assert namespace[mapping.name]("/api/cofre/importar", "POST") == "cofre"
    assert namespace[mapping.name]("/api/cofre/servidores", "GET") == "cofre"
    assert namespace[mapping.name]("/api/cofre-extra", "GET") is None
    from backend.services.remote_auth_contracts import PERMISSION_KEYS
    assert "cofre" in PERMISSION_KEYS
    from cloud.auth_gateway.app.policy import PERMISSION_KEYS as GATEWAY_PERMISSION_KEYS
    assert set(PERMISSION_KEYS) == set(GATEWAY_PERMISSION_KEYS)


def test_router_caps_streamed_upload_without_content_length(client, monkeypatch):
    monkeypatch.setattr(storage, "MAX_UPLOAD_BYTES", 100)
    headers = {"Authorization": "Bearer tenant-a", "Content-Type": "multipart/form-data; boundary=test"}
    response = client.post("/api/cofre/importar", headers=headers,
                           content=iter([b'--test\r\nContent-Disposition: form-data; name="arquivo"; filename="a.json"\r\nContent-Type: application/json\r\n\r\n', b"x" * 70000, b"\r\n--test--\r\n"]))
    assert response.status_code == 413


def test_message_order_uses_actual_time_across_timezone_offsets(tmp_path):
    payload = manifest()
    payload["channels"][0]["messages"] = [
        {"id": "later", "timestamp": "2026-10-21T08:00:00-03:00", "content": "11 UTC"},
        {"id": "earlier", "timestamp": "2026-10-21T10:00:00Z", "content": "10 UTC"}]
    import_json(tmp_path, payload)
    result = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")
    assert [m["id"] for m in result["mensagens"]] == ["earlier", "later"]
    assert result["mensagens"][1]["timestamp"] == "2026-10-21T08:00:00-03:00"


@pytest.mark.parametrize("recapture", ["omitted", "empty", "renewed-discord-query"])
def test_partial_json_recapture_preserves_copied_attachment_search_and_download(client, recapture):
    payload = attachment_manifest()
    attachment = payload["channels"][0]["messages"][0]["attachments"][0]
    attachment["url"] = "https://cdn.discordapp.com/attachments/channel/asset/report.html?ex=old&hm=old"
    headers = {"Authorization": "Bearer tenant-a"}
    copied_bytes = b"<script>archived report</script>"
    first = client.post("/api/cofre/importar", headers=headers, files={
        "arquivo": ("archive.zip", package(payload, {"assets/report.html": copied_bytes}).getvalue(), "application/zip")})
    assert first.status_code == 200
    assert first.json()["servidor"]["attachment_count"] == 1
    messages_url = "/api/cofre/canais/channel1/mensagens?servidor_id=guild1"
    original_attachment = client.get(messages_url, headers=headers).json()["mensagens"][0]["attachments"][0]
    message = payload["channels"][0]["messages"][0]
    message["content"] = "Captura parcial atualizada"
    if recapture == "omitted":
        message.pop("attachments")
    elif recapture == "empty":
        message["attachments"] = []
    else:
        attachment.pop("archive_path")
        attachment["url"] = "https://cdn.discordapp.com/attachments/channel/asset/report.html?ex=new&hm=new"
    # Repeating the same partial recapture must retain exactly one archived attachment.
    for _ in range(2):
        imported = client.post("/api/cofre/importar", headers=headers,
                               files={"arquivo": ("recapture.json", raw(payload), "application/json")})
        assert imported.status_code == 200
        assert imported.json()["imported_messages"] == 0
        assert imported.json()["servidor"]["attachment_count"] == 1
        assert imported.json()["total_messages"] == 1
    result = client.get(messages_url + "&q=relatorio.html", headers=headers).json()
    assert result["total"] == 1
    assert result["mensagens"][0]["content"] == "Captura parcial atualizada"
    final_attachments = result["mensagens"][0]["attachments"]
    assert len(final_attachments) == 1
    assert final_attachments[0]["local_url"] == original_attachment["local_url"]
    assert client.get(final_attachments[0]["local_url"], headers=headers).content == copied_bytes
    if recapture == "renewed-discord-query":
        assert "ex=new" in final_attachments[0]["url"]


def test_recapture_does_not_conflate_attachment_urls_from_different_domains(tmp_path):
    payload = attachment_manifest()
    attachment = payload["channels"][0]["messages"][0]["attachments"][0]
    attachment["url"] = "https://cdn.discordapp.com/attachments/channel/asset/report.html?ex=old"
    storage.import_archive(tmp_path, "tenant-a", package(payload, {"assets/report.html": b"archived"}), "a.zip")
    attachment.pop("archive_path")
    attachment["url"] = "https://example.org/attachments/channel/asset/report.html?ex=new"
    result = import_json(tmp_path, payload)
    assert result["servidor"]["attachment_count"] == 2
    attachments = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")["mensagens"][0]["attachments"]
    assert len(attachments) == 2
    assert "local_url" not in attachments[0]
    assert attachments[1]["local_url"]


def test_local_edits_change_search_without_overwriting_imported_source(tmp_path):
    payload = manifest()
    import_json(tmp_path, payload)
    changed = storage.change_message(tmp_path, "tenant-a", "guild1", "channel1", "message1",
        patch=storage.MessagePatch.model_validate({"content": '<b>Meu texto editado</b>',
            "links": [{"url": "https://example.org/local", "text": "ReferenciaLocal"}],
            "embeds": [{"text": "Detalhe local", "links": [{"url": "https://example.org/anexo"}]}]}))
    assert changed["mensagem"]["edited"] is True
    assert changed["mensagem"]["deleted"] is False
    assert changed["mensagem"]["content"] == '<b>Meu texto editado</b>'
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="referencialocal")["total"] == 1
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="https://example.org/anexo")["total"] == 1
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="Guia de produtos")["total"] == 0
    path = tmp_path / "tenant-a" / "cofre" / "cofre.sqlite3"
    with sqlite3.connect(path) as connection:
        source = json.loads(connection.execute("SELECT payload FROM messages").fetchone()[0])
        assert source["content"] == "Guia de produtos"
        assert source["links"][0]["text"] == "Manual"
    payload["channels"][0]["messages"][0].update(content="Fonte recapturada", author="Autor recapturado")
    import_json(tmp_path, payload)
    effective = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="Autor recapturado")
    assert effective["total"] == 1
    assert effective["mensagens"][0]["content"] == '<b>Meu texto editado</b>'
    assert effective["mensagens"][0]["author"] == "Autor recapturado"
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="Fonte recapturada")["total"] == 0
    # Clearing lists is an explicit local edit and must survive further imports.
    storage.change_message(tmp_path, "tenant-a", "guild1", "channel1", "message1",
                           patch=storage.MessagePatch.model_validate({"links": [], "embeds": []}))
    import_json(tmp_path, payload)
    effective = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")["mensagens"][0]
    assert effective["links"] == []
    assert effective["embeds"] == []


def test_local_channel_edit_preserves_source_and_survives_reimport(tmp_path):
    payload = manifest()
    import_json(tmp_path, payload)
    changed = storage.change_channel(tmp_path, "tenant-a", "guild1", "channel1",
        storage.ChannelPatch.model_validate({"name": "Guias pessoais", "category": "Minha categoria", "topic": None}))
    assert changed["canal"]["edited"] is True
    assert changed["canal"]["name"] == "Guias pessoais"
    assert changed["canal"]["topic"] is None
    payload["channels"][0].update(name="Novo nome original", category="Origem", topic="Novo topico original")
    import_json(tmp_path, payload)
    channel = storage.list_channels(tmp_path, "tenant-a", "guild1")["canais"][0]
    assert (channel["name"], channel["category"], channel["topic"]) == ("Guias pessoais", "Minha categoria", None)
    assert channel["message_count"] == channel["archive_message_count"] == 1
    assert channel["deleted_message_count"] == 0
    with sqlite3.connect(tmp_path / "tenant-a" / "cofre" / "cofre.sqlite3") as connection:
        source = json.loads(connection.execute("SELECT payload FROM channels").fetchone()[0])
        assert source["name"] == "Novo nome original"
        assert source["topic"] == "Novo topico original"


def test_recoverable_delete_counts_and_reimport_keep_local_edits_and_assets(client):
    headers = {"Authorization": "Bearer tenant-a"}
    payload = attachment_manifest()
    copied_bytes = b"archive to restore"
    assert client.post("/api/cofre/importar", headers=headers, files={
        "arquivo": ("archive.zip", package(payload, {"assets/report.html": copied_bytes}).getvalue())}).status_code == 200
    item_path = "/api/cofre/canais/channel1/mensagens/message1"
    item_query = "?servidor_id=guild1"
    messages_url = "/api/cofre/canais/channel1/mensagens?servidor_id=guild1"
    edited = client.patch(item_path + item_query, headers=headers,
                           json={"content": "Rascunho editado", "author": "Autor local"})
    assert edited.status_code == 200
    for _ in range(2):
        removed = client.delete(item_path + item_query, headers=headers)
        assert removed.status_code == 200
        assert removed.json()["total_ativos"] == 0
        assert removed.json()["total_excluidos"] == 1
        assert removed.json()["mensagem"]["deleted"] is True
    visible = client.get(messages_url, headers=headers).json()
    assert visible["total"] == 0
    assert visible["total_ativos"] == 0
    assert visible["total_excluidos"] == 1
    trash = client.get(messages_url + "&excluidos=true&q=rascunho", headers=headers).json()
    assert trash["total"] == 1
    assert trash["mensagens"][0]["edited"] is True
    assert trash["mensagens"][0]["deleted"] is True
    asset_url = trash["mensagens"][0]["attachments"][0]["local_url"]
    assert client.get(asset_url, headers=headers).content == copied_bytes
    summary = client.get("/api/cofre/servidores/guild1/canais", headers=headers).json()
    assert summary["servidor"]["message_count"] == summary["servidor"]["attachment_count"] == 0
    assert summary["servidor"]["deleted_message_count"] == 1
    assert summary["servidor"]["archive_message_count"] == 1
    assert summary["canais"][0]["message_count"] == 0
    assert summary["canais"][0]["deleted_message_count"] == 1
    payload["channels"][0]["messages"][0]["attachments"] = []
    reimported = client.post("/api/cofre/importar", headers=headers,
                            files={"arquivo": ("again.json", raw(payload))})
    assert reimported.status_code == 200
    assert reimported.json()["total_messages"] == 1
    assert reimported.json()["servidor"]["deleted_message_count"] == 1
    assert client.get(messages_url, headers=headers).json()["total"] == 0
    for _ in range(2):
        restored = client.post(item_path + "/restaurar" + item_query, headers=headers)
        assert restored.status_code == 200
        assert restored.json()["mensagem"]["deleted"] is False
        assert restored.json()["mensagem"]["content"] == "Rascunho editado"
        assert restored.json()["total_ativos"] == 1
        assert restored.json()["total_excluidos"] == 0
    active = client.get(messages_url + "&q=autor%20local", headers=headers).json()
    assert active["total"] == 1
    assert client.get(active["mensagens"][0]["attachments"][0]["local_url"], headers=headers).content == copied_bytes
    assert client.get(messages_url + "&excluidos=true", headers=headers).json()["total"] == 0


def test_patch_routes_and_mutations_are_tenant_and_guild_scoped(client):
    headers = {"Authorization": "Bearer tenant-a"}
    assert client.post("/api/cofre/importar", headers=headers,
                       files={"arquivo": ("archive.json", raw(manifest()))}).status_code == 200
    item = "/api/cofre/canais/channel1/mensagens/message1?servidor_id=guild1"
    channel = "/api/cofre/canais/channel1?servidor_id=guild1"
    assert client.patch(item, json={"content": "Changed"}).status_code == 401
    assert client.delete(item).status_code == 401
    assert client.post("/api/cofre/canais/channel1/mensagens/message1/restaurar?servidor_id=guild1").status_code == 401
    wrong_tenant = {"Authorization": "Bearer tenant-b"}
    assert client.patch(item, headers=wrong_tenant, json={"content": "Changed"}).status_code == 404
    assert client.delete(item, headers=wrong_tenant).status_code == 404
    assert client.patch(channel, headers=wrong_tenant, json={"name": "Wrong tenant"}).status_code == 404
    assert client.patch(item.replace("guild1", "wrong-guild"), headers=headers, json={"content": "Changed"}).status_code == 404
    assert client.delete(item.replace("message1", "wrong-message"), headers=headers).status_code == 404
    assert client.patch(item.replace("channel1", "wrong-channel"), headers=headers, json={"content": "Changed"}).status_code == 404
    assert client.patch(channel.replace("channel1", "wrong-channel"), headers=headers, json={"name": "Changed"}).status_code == 404
    changed = client.patch(channel, headers=headers, json={"name": "Canal local", "category": "Local", "topic": "Topico local"})
    assert changed.status_code == 200
    assert changed.json()["canal"]["name"] == "Canal local"
    assert changed.json()["canal"]["edited"] is True
    assert changed.headers["cache-control"].startswith("no-store")
    current = client.get("/api/cofre/canais/channel1/mensagens?servidor_id=guild1", headers=headers).json()["mensagens"][0]
    assert current["content"] == "Guia de produtos"
    assert current["edited"] is False


@pytest.mark.parametrize("patch", [
    {}, {"client_id": "tenant-b"}, {"attachments": []}, {"content": None},
    {"author": "x" * 513}, {"links": [{"url": "javascript:alert(1)"}]},
    {"embeds": [{"text": "sample", "unknown": True}]}, {"deleted": True},
])
def test_message_patch_rejects_invalid_fields(client, patch):
    headers = {"Authorization": "Bearer tenant-a"}
    response = client.patch("/api/cofre/canais/channel1/mensagens/message1?servidor_id=guild1", headers=headers, json=patch)
    assert response.status_code == 422


@pytest.mark.parametrize("patch", [{}, {"name": ""}, {"category": None}, {"kind": "voice"}, {"topic": "x" * 65537}])
def test_channel_patch_rejects_invalid_fields(client, patch):
    response = client.patch("/api/cofre/canais/channel1?servidor_id=guild1", headers={"Authorization": "Bearer tenant-a"}, json=patch)
    assert response.status_code == 422


def test_patch_request_limit_and_duplicate_json_fields(client):
    path = "/api/cofre/canais/channel1/mensagens/message1?servidor_id=guild1"
    headers = {"Authorization": "Bearer tenant-a", "Content-Type": "application/json"}
    assert client.patch(path, headers=headers, content=b'{"content":"a","content":"b"}').status_code == 422
    assert client.patch(path, headers=headers, content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.patch(path, headers={"Authorization": "Bearer tenant-a"}, content=b"text").status_code == 415


def test_additive_migration_reads_old_archive_and_retains_imported_source(tmp_path):
    payload = manifest()
    import_json(tmp_path, payload)
    path = tmp_path / "tenant-a" / "cofre" / "cofre.sqlite3"
    # Emulate the first Cofre schema, before local override/lixeira tables existed.
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT payload FROM messages").fetchone()[0]
        connection.execute("DROP TABLE message_local")
        connection.execute("DROP TABLE channel_local")
    first = storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1")
    assert first["total"] == first["total_ativos"] == 1
    assert first["total_excluidos"] == 0
    assert first["mensagens"][0]["edited"] is False
    assert first["mensagens"][0]["deleted"] is False
    storage.change_message(tmp_path, "tenant-a", "guild1", "channel1", "message1",
                           patch=storage.MessagePatch.model_validate({"content": "Editado apos migracao"}))
    assert storage.list_messages(tmp_path, "tenant-a", "guild1", "channel1", q="apos migracao")["total"] == 1
    with sqlite3.connect(path) as connection:
        after = connection.execute("SELECT payload FROM messages").fetchone()[0]
        assert after == before
        assert connection.execute("SELECT COUNT(*) FROM message_local").fetchone()[0] == 1
    assert storage.list_channels(tmp_path, "tenant-a", "guild1")["canais"][0]["message_count"] == 1
