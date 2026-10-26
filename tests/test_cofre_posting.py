"""Fictional local posts, immutable JK authorship, files and snapshot metadata."""
import copy
import hashlib
import io
import json
import sqlite3
import time
import uuid

import pytest
from pydantic import ValidationError

from backend.modules.cofre import posting, shared_storage, storage


CLIENT, GUILD, CHANNEL = "fixture-company", "guild", "channel"


def actor(username="alice", *, admin=False, client=CLIENT):
    return {"username": username, "client_id": client, "scope": ["cofre:read"] + (["cofre:write"] if admin else []),
            "exp": int(time.time()) + 3600, "capability": ""}


def manifest():
    return {"format": storage.FORMAT, "guild": {"id": GUILD, "name": "Fixture"},
        "exported_at": "2026-01-01T00:00:00Z", "channels": [
            {"id": CHANNEL, "name": "Fixture channel", "messages": [
                {"id": "imported", "author": "alice", "content": "Original", "timestamp": "2020-01-01T00:00:00Z"}]},
            {"id": "other", "name": "Other channel", "messages": []}]}


def import_archive(root, value=None):
    return storage.import_archive(root, CLIENT, io.BytesIO(json.dumps(value or manifest()).encode()), "fixture.json")


@pytest.fixture
def archive(tmp_path):
    import_archive(tmp_path)
    return tmp_path


def payload(content="New post", request_id=None, **kwargs):
    return posting.CreateMessage(request_id=request_id or str(uuid.uuid4()), content=content, **kwargs)


def create(root, body=None, who=None, **kwargs):
    return posting.create_message(root, CLIENT, GUILD, CHANNEL, body or payload(), who or actor(), **kwargs)


def messages(root, **kwargs):
    return storage.list_messages(root, CLIENT, GUILD, CHANNEL, **kwargs)


def test_post_uses_server_identity_and_immutable_owner_not_display_author(archive):
    result = create(archive)
    message = result["mensagem"]
    assert message["id"].startswith("jk_msg_") and message["author"] == "alice"
    assert message["owner_username"] == "alice" and message["can_delete"] is True
    assert message["can_edit"] is False
    storage.change_message(archive, CLIENT, GUILD, CHANNEL, message["id"],
                           patch=storage.MessagePatch(author="bob"))
    alice = posting.per_message_permissions(archive, CLIENT, GUILD, CHANNEL, actor())
    bob = posting.per_message_permissions(archive, CLIENT, GUILD, CHANNEL, actor("bob"))
    admin = posting.per_message_permissions(archive, CLIENT, GUILD, CHANNEL, actor("admin", admin=True))
    assert alice[message["id"]]["owner_username"] == "alice"
    assert alice[message["id"]]["can_delete"] is True and bob[message["id"]]["can_delete"] is False
    assert alice["imported"] == {"owner_username": None, "can_delete": False, "can_edit": False}
    assert all(row["can_delete"] and row["can_edit"] for row in admin.values())
    with storage._connect(storage._tenant_dir(archive, CLIENT), writable=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE message_ownership SET owner_username='bob'")


def test_post_receipt_is_idempotent_and_bound_to_actor_target_and_body(archive):
    body = payload()
    first = create(archive, body)
    assert create(archive, body) == first
    assert messages(archive)["total"] == 2
    for call in (
        lambda: create(archive, payload("Changed", body.request_id)),
        lambda: create(archive, body, actor("bob")),
        lambda: posting.create_message(archive, CLIENT, GUILD, "other", body, actor()),
    ):
        with pytest.raises(storage.CofreError) as error:
            call()
        assert error.value.status_code == 409
    assert messages(archive)["total"] == 2


def test_only_real_owner_or_admin_can_delete_and_restore(archive):
    identifier = create(archive)["mensagem"]["id"]
    for message_id, who in ((identifier, actor("bob")), ("imported", actor())):
        with pytest.raises(storage.CofreError) as error:
            posting.set_message_deleted(archive, CLIENT, GUILD, CHANNEL, message_id, who)
        assert error.value.status_code == 403
    assert posting.set_message_deleted(archive, CLIENT, GUILD, CHANNEL, identifier, actor())["mensagem"]["deleted"] is True
    assert posting.set_message_deleted(archive, CLIENT, GUILD, CHANNEL, identifier, actor(), deleted=False)["mensagem"]["deleted"] is False
    assert posting.set_message_deleted(archive, CLIENT, GUILD, CHANNEL, "imported", actor("admin", admin=True))["mensagem"]["deleted"] is True


@pytest.mark.parametrize("changes", [{"client_id": "other-company"}, {"scope": []}, {"scope": ["cofre:write"]},
    {"exp": 0}, {"exp": True}, {"username": ""}])
def test_invalid_actor_cannot_create_or_delete(archive, changes):
    who = {**actor(), **changes}
    with pytest.raises(storage.CofreError) as error:
        create(archive, who=who)
    assert error.value.status_code == 403
    assert messages(archive)["total"] == 1
    assert not (archive / "other-company").exists()


@pytest.mark.parametrize("change", [{"author": "admin"}, {"owner_username": "admin"}, {"can_delete": True},
    {"request_id": str(uuid.uuid1())}, {"request_id": "invalid"}, {"content": "x" * 32001},
    {"links": [{"url": ""}]}, {"links": [{"url": "https://example.test/space here"}]},
    {"links": [{"url": "https://example.test"}] * 101}])
def test_composer_schema_forbids_claims_and_enforces_bounds(change):
    with pytest.raises(ValidationError):
        posting.CreateMessage.model_validate({"request_id": str(uuid.uuid4()), "content": "Post", **change})


def test_text_and_links_are_literal_searchable_data(archive):
    result = create(archive, payload('<img src=x onerror="alert(1)">', links=[{"url": "https://example.test/guide", "text": "Guide"}]))
    assert result["mensagem"]["content"].startswith("<img")
    assert messages(archive, q="ONERROR")["total"] == 1
    assert messages(archive, q="' OR 1=1 --")["total"] == 0
    with pytest.raises(storage.CofreError):
        create(archive, payload("  "))


def test_owner_cannot_delete_channel_but_admin_hides_children_and_restores_prior_deletions(archive):
    identifier = create(archive)["mensagem"]["id"]
    posting.set_message_deleted(archive, CLIENT, GUILD, CHANNEL, identifier, actor())
    with pytest.raises(storage.CofreError) as error:
        posting.set_channel_deleted(archive, CLIENT, GUILD, CHANNEL, actor())
    assert error.value.status_code == 403
    result = posting.set_channel_deleted(archive, CLIENT, GUILD, CHANNEL, actor("admin", admin=True))
    assert result["servidor"]["message_count"] == 0
    assert result["servidor"]["deleted_message_count"] == 2
    assert result["servidor"]["channel_count"] == 1
    assert result["servidor"]["deleted_channel_count"] == 1
    assert result["canal"]["can_edit"] is False
    assert [row["id"] for row in storage.list_channels(archive, CLIENT, GUILD)["canais"]] == ["other"]
    with pytest.raises(storage.CofreError) as error:
        messages(archive)
    assert error.value.status_code == 404
    trash = messages(archive, excluidos=True, actor=actor("admin", admin=True))
    assert trash["total"] == 2 and trash["can_post"] is False
    assert all(message["deleted"] for message in trash["mensagens"])
    with pytest.raises(storage.CofreError):
        create(archive)
    posting.set_channel_deleted(archive, CLIENT, GUILD, CHANNEL, actor("admin", admin=True), deleted=False)
    assert messages(archive)["total"] == 1
    assert messages(archive, excluidos=True)["mensagens"][0]["id"] == identifier
    assert storage.list_channels(archive, CLIENT, GUILD, actor=actor())["canais"][0]["can_post"] is True


def test_reimport_keeps_channel_tombstones_and_rejects_collisions_with_native_posts(archive):
    identifier = create(archive)["mensagem"]["id"]
    posting.set_channel_deleted(archive, CLIENT, GUILD, CHANNEL, actor("admin", admin=True))
    changed = manifest()
    changed["channels"][0]["messages"].append({"id": "new-imported", "content": "Later capture"})
    import_archive(archive, changed)
    assert storage.list_channels(archive, CLIENT, GUILD)["servidor"]["message_count"] == 0
    assert messages(archive, excluidos=True)["total"] == 3
    changed["channels"][0]["messages"].append({"id": identifier, "author": "bob", "content": "Forged import"})
    with pytest.raises(storage.CofreError) as error:
        import_archive(archive, changed)
    assert error.value.status_code == 409
    assert posting.per_message_permissions(archive, CLIENT, GUILD, CHANNEL, actor())[identifier]["owner_username"] == "alice"


@pytest.mark.parametrize("prefix,expected", [
    (b"\xff\xd8\xfffixture", "image/jpeg"), (b"\x89PNG\r\n\x1a\nfixture", "image/png"),
    (b"GIF89afixture", "image/gif"), (b"RIFFxxxxWEBPfixture", "image/webp"),
    (b"<svg onload='alert(1)'>", "application/octet-stream"), (b"<html>fixture", "application/octet-stream"),
])
def test_attachment_bytes_determine_preview_and_safe_content_addressed_download(archive, prefix, expected):
    upload = posting.PreparedAttachment(io.BytesIO(prefix), "../../unsafe name.html", "image/png")
    result = create(archive, payload(""), attachments=[upload])
    attachment = result["mensagem"]["attachments"][0]
    assert attachment["content_type"] == expected and attachment["url"] == ""
    filename = storage._safe_filename(upload.filename)
    digest = hashlib.sha256(prefix).hexdigest()
    assert attachment["local_url"] == f"/api/cofre/arquivos/{GUILD}/{digest}/{filename}"
    assert storage.get_asset(archive, CLIENT, GUILD, digest, filename).read_bytes() == prefix
    assert create(archive, payload("", result["request_id"]), attachments=[upload])["mensagem"]["id"] == result["mensagem"]["id"]


def test_file_count_and_ten_mib_limit_reject_before_permanent_changes(archive):
    too_many = [posting.PreparedAttachment(io.BytesIO(b"x"), str(index)) for index in range(6)]
    for attachments in (too_many, [posting.PreparedAttachment(io.BytesIO(b"x" * (posting.MAX_FILE_BYTES + 1)), "large.bin")]):
        with pytest.raises(storage.CofreError) as error:
            create(archive, payload(""), attachments=attachments)
        assert error.value.status_code == 413
    assert messages(archive)["total"] == 1
    assert list((storage._tenant_dir(archive, CLIENT) / "assets").iterdir()) == []
    accepted = create(archive, payload(""), attachments=[posting.PreparedAttachment(io.BytesIO(b"x" * posting.MAX_FILE_BYTES), "limit.bin")])
    assert len(accepted["mensagem"]["attachments"]) == 1


@pytest.mark.parametrize("attachment", [
    {"url": "https://fixture.example/file", "filename": "../../another-route"},
    {"url": "https://fixture.example/file", "filename": "safe.bin", "local_url": "/api/auth/me"},
])
def test_composer_remote_attachment_rejects_unsafe_names_or_client_proxy_paths(archive, attachment):
    with pytest.raises(storage.CofreError) as error:
        create(archive, payload(""), attachments=[attachment])
    assert error.value.status_code == 422
    assert messages(archive)["total"] == 1


def test_failed_transaction_rolls_back_post_owner_receipt_and_new_blob(archive, monkeypatch):
    def fail(*args, **kwargs):
        raise storage.CofreError("Fixture transaction failure", 503)
    monkeypatch.setattr(posting, "_message_result", fail)
    with pytest.raises(storage.CofreError):
        create(archive, attachments=[posting.PreparedAttachment(io.BytesIO(b"new fixture bytes"), "fixture.bin")])
    assert messages(archive)["total"] == 1
    with storage._connect(storage._tenant_dir(archive, CLIENT)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM message_ownership").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM posting_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    assert list((storage._tenant_dir(archive, CLIENT) / "assets").iterdir()) == []


def test_message_offset_targets_created_post_despite_future_imported_timestamps(archive):
    value = manifest()
    value["channels"][0]["messages"].extend({"id": f"future{index}", "content": "Future import", "timestamp": "2099-01-01T00:00:00Z"} for index in range(130))
    import_archive(archive, value)
    result = create(archive)
    page = messages(archive, offset=result["message_offset"], limit=100)
    assert any(message["id"] == result["mensagem"]["id"] for message in page["mensagens"])


def test_metadata_round_trip_is_separate_from_snapshot_and_author_cannot_be_reassigned(archive, tmp_path):
    result = create(archive)
    identifier = result["mensagem"]["id"]
    posting.set_channel_deleted(archive, CLIENT, GUILD, CHANNEL, actor("admin", admin=True))
    state = shared_storage.export_state(archive, CLIENT)
    metadata = shared_storage.export_metadata(archive, CLIENT)
    assert metadata == {"version": 1, "ownership": [{"guild_id": GUILD, "channel_id": CHANNEL,
        "message_id": identifier, "owner_username": "alice"}],
        "deleted_channels": [{"guild_id": GUILD, "channel_id": CHANNEL}]}
    assert "owner_username" not in json.dumps(state) and "can_delete" not in json.dumps(state)
    target = tmp_path / "remote-cache"
    shared_storage.store_state(target, CLIENT, state, 1, metadata)
    assert shared_storage.export_metadata(target, CLIENT) == metadata
    assert storage.list_channels(target, CLIENT, GUILD)["servidor"]["message_count"] == 0
    forged = copy.deepcopy(metadata)
    forged["ownership"][0]["owner_username"] = "bob"
    with pytest.raises(storage.CofreError) as error:
        shared_storage.store_state(target, CLIENT, state, 2, forged)
    assert error.value.status_code == 409
    assert shared_storage.read_meta(target, CLIENT)["revision"] == 1
    assert shared_storage.export_metadata(target, CLIENT) == metadata
    assert shared_storage.export_state(target, CLIENT) == state


def test_legacy_snapshot_cannot_erase_native_posts_or_deleted_channel_history(archive):
    identifier = create(archive)["mensagem"]["id"]
    posting.set_channel_deleted(archive, CLIENT, GUILD, CHANNEL, actor("admin", admin=True))
    metadata = shared_storage.export_metadata(archive, CLIENT)
    shared_storage.store_state(archive, CLIENT, {"format": shared_storage.FORMAT, "guilds": []}, 2)
    assert shared_storage.export_metadata(archive, CLIENT) == metadata
    assert messages(archive, excluidos=True)["total"] == 2
    assert any(row["id"] == identifier for row in messages(archive, excluidos=True)["mensagens"])
    # An authenticated explicit metadata snapshot may restore a channel; owner remains immutable.
    restored_metadata = {**metadata, "deleted_channels": []}
    shared_storage.store_state(archive, CLIENT, shared_storage.export_state(archive, CLIENT), 3, restored_metadata)
    assert messages(archive)["total"] == 2


@pytest.mark.parametrize("metadata", [
    {"version": True}, {"version": 1, "extra": True},
    {"version": 1, "ownership": [{"guild_id": GUILD, "channel_id": CHANNEL, "message_id": "imported", "owner_username": "alice"}]},
    {"version": 1, "deleted_channels": [{"guild_id": GUILD, "channel_id": "missing"}]},
])
def test_invalid_metadata_is_rejected_without_changing_snapshot(archive, metadata):
    state = shared_storage.export_state(archive, CLIENT)
    shared_storage.store_state(archive, CLIENT, state, 1)
    with pytest.raises(storage.CofreError):
        shared_storage.store_state(archive, CLIENT, {"format": shared_storage.FORMAT, "guilds": []}, 2, metadata)
    assert shared_storage.export_state(archive, CLIENT) == state
    assert shared_storage.read_meta(archive, CLIENT)["revision"] == 1


def test_asset_resolver_runs_before_export_validation_without_leaking_local_paths(archive):
    create(archive, payload(""), attachments=[posting.PreparedAttachment(io.BytesIO(b"fixture"), "fixture.bin")])
    with pytest.raises(storage.CofreError):
        shared_storage.export_state(archive, CLIENT)
    calls = []
    def resolve(guild, channel, message, attachment):
        calls.append((guild, channel, message, attachment))
        assert attachment["url"] == "" and attachment["local_url"].startswith("/api/cofre/arquivos/")
        return "https://fixture.example/v1/assets/" + hashlib.sha256(b"fixture").hexdigest() + "/fixture.bin"
    state = shared_storage.export_state(archive, CLIENT, asset_url_resolver=resolve)
    assert len(calls) == 1 and calls[0][:2] == (GUILD, CHANNEL)
    assert "local_url" not in json.dumps(state) and "archive_path" not in json.dumps(state)
    assert messages(archive)["mensagens"][-1]["attachments"][0]["url"] == ""
