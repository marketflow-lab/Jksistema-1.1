"""Recreate expired user packages using the normal delta builder."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - bind service peers
from backend.services import shared_sync_delta as delta
from backend.services import shared_sync_user_pairs as pairs


SCOPE = "favoritos_planilhas"
DATA = b'{"planilhas": ["fixture"]}'


@pytest.fixture
def sharing(monkeypatch):
    sender = {"client_id": "tenant-fixture", "username": "sender", "is_admin": True}
    receiver = {"client_id": "tenant-fixture", "username": "receiver"}
    link = {
        "id": "link-fixture",
        "source_client_id": sender["client_id"],
        "source_username": sender["username"],
        "source_is_admin": True,
        "target_client_id": receiver["client_id"],
        "target_username": receiver["username"],
    }
    known = set()
    remote = {}
    lookups = []
    pushes = []
    lookup_error = None

    def read_remote(bundle_id, *, strict):
        assert strict is True
        lookups.append(bundle_id)
        if lookup_error:
            raise lookup_error
        return remote.get(bundle_id)

    def push_scope(client_id, scope, session, machine_id, *, bundle_id, known_keys,
                   user_only, allow_empty_delta, **kwargs):
        assert user_only is True
        pushes.append((scope, known_keys))
        if scope == "lojas_integracoes":
            return {"success": True, "id": bundle_id}
        source_entry = {"relative_path": "favoritos_planilhas_lojas.json", "data": DATA, "mtime": 1}
        entries, _, keys = delta._shared_sync_coletar_arquivos_delta(
            client_id, scope, username=session["username"], user_only=user_only,
            known_keys=known_keys, captured_entries=([source_entry], []),
        )
        if not keys and not allow_empty_delta:
            return {"success": True, "skipped": True, "reason": "already_shared"}
        remote[bundle_id] = {entry["relative_path"]: entry["data"] for entry in entries}
        return {"success": True, "id": bundle_id, "item_keys": keys}

    def send(mode, scope=SCOPE):
        if mode == "pair":
            return pairs._shared_sync_push_pair_scope(sender, receiver, scope, link_id=link["id"])
        return pairs._shared_sync_push_link_scope(
            sender, link, scope, skip_if_remote_hash_matches=mode == "auto",
        )

    def fail_lookup():
        nonlocal lookup_error
        lookup_error = HTTPException(status_code=503, detail="Firestore unavailable")

    monkeypatch.setattr(pairs, "_shared_sync_user_share_known_keys", lambda *args: set(known))
    monkeypatch.setattr(pairs, "_shared_sync_user_share_add_known_keys", lambda *args: known.update(args[-1]))
    monkeypatch.setattr(pairs, "_shared_sync_remote_meta_by_id", read_remote)
    monkeypatch.setattr(pairs, "_shared_sync_push_scope", push_scope)
    monkeypatch.setattr(pairs, "_shared_sync_save_link", lambda *args: None)
    monkeypatch.setattr(pairs, "_shared_sync_state_mark_skipped", lambda *args, **kwargs: None)
    return SimpleNamespace(send=send, known=known, remote=remote, lookups=lookups,
                           pushes=pushes, fail_lookup=fail_lookup)


@pytest.mark.parametrize("mode", ["pair", "link"])
def test_manual_submission_recreates_expired_package(sharing, mode):
    first = sharing.send(mode)
    bundle_id = first["id"]
    assert first["item_keys"]
    assert sharing.lookups == []
    initial_files = dict(sharing.remote[bundle_id])
    assert initial_files == {"favoritos_planilhas_lojas.json": DATA}

    sharing.send(mode)
    assert sharing.lookups == [bundle_id]
    assert sharing.pushes[-1][1] == set(first["item_keys"])

    del sharing.remote[bundle_id]
    resent = sharing.send(mode)
    assert resent["id"] == bundle_id
    assert resent["item_keys"] == first["item_keys"]
    assert sharing.lookups == [bundle_id, bundle_id]
    assert sharing.remote[bundle_id] == initial_files


@pytest.mark.parametrize("mode", ["pair", "link"])
def test_lookup_failure_preserves_known_items_and_does_not_publish(sharing, mode):
    first = sharing.send(mode)
    sharing.fail_lookup()

    with pytest.raises(HTTPException) as error:
        sharing.send(mode)

    assert error.value.status_code == 503
    assert sharing.lookups == [first["id"]]
    assert sharing.known == set(first["item_keys"])
    assert len(sharing.pushes) == 1


def test_automatic_submission_does_not_recreate_expired_package(sharing):
    first = sharing.send("link")
    del sharing.remote[first["id"]]

    result = sharing.send("auto")

    assert result["reason"] == "already_shared"
    assert sharing.lookups == []
    assert sharing.remote == {}
    assert sharing.pushes[-1][1] == set(first["item_keys"])


@pytest.mark.parametrize("mode", ["pair", "link"])
def test_store_integrations_keep_their_existing_full_submission(sharing, mode):
    sharing.known.add("ignored")
    sharing.fail_lookup()

    assert sharing.send(mode, "lojas_integracoes")["success"] is True
    assert sharing.lookups == []
    assert sharing.pushes == [("lojas_integracoes", None)]
