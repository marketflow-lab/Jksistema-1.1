"""Regression for July checkpoints that observed a hash but stored no snapshot ID."""

import base64
import copy
import hashlib
import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - configure facade
from backend.services import shared_sync_bundle
from backend.services import shared_sync_config
from backend.services import shared_sync_common
from backend.services import shared_sync_merge_integracoes
from backend.services import shared_sync_remote


SCOPE = "lojas_integracoes"
CLIENT = "tenant-a"
USER = "alice"
POINTER = shared_sync_common._shared_sync_pair_doc_id(CLIENT, USER, "tenant-b", "bob", SCOPE)
SNAPSHOT = POINTER + "__july"
STATE_SCOPE = "user-share:link-a:source_to_target:lojas_integracoes"
IDENTITY = {
    "visibility": "user-share",
    "source_client_id": CLIENT,
    "source_username": USER,
    "target_client_id": "tenant-b",
    "target_username": "bob",
    "link_id": "link-a",
    "sync_direction": "source_to_target",
}


class _Snapshot:
    def __init__(self, collection, doc_id):
        self.id = doc_id
        self.reference = _Document(collection, doc_id)
        self.exists = doc_id in collection.data
        self._data = copy.deepcopy(collection.data.get(doc_id, {}))

    def to_dict(self):
        return copy.deepcopy(self._data)


class _Document:
    def __init__(self, collection, doc_id):
        self.collection = collection
        self.id = doc_id

    def get(self):
        return _Snapshot(self.collection, self.id)

    def set(self, value, merge=False, **kwargs):
        original = self.collection.data.get(self.id, {}) if merge else {}
        self.collection.data[self.id] = {**original, **copy.deepcopy(value)}

    def delete(self, **kwargs):
        self.collection.data.pop(self.id, None)


class _Query:
    def __init__(self, collection, field, value):
        self.collection, self.field, self.value = collection, field, value

    def stream(self):
        return [
            _Snapshot(self.collection, doc_id)
            for doc_id, data in list(self.collection.data.items())
            if data.get(self.field) == self.value
        ]


class _Collection:
    def __init__(self):
        self.data = {}

    def document(self, doc_id):
        return _Document(self, doc_id)

    def where(self, field, operator, value):
        assert operator == "=="
        return _Query(self, field, value)


class _Firestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, _Collection())


def _stores(count, revision):
    return [
        {
            "store_id": f"store-{index}",
            "nome": f"Loja {index}",
            "integracoes": {
                "mercadolivre": {
                    "connected": True, "user_id": f"seller-{index}",
                    "app_id": f"app-{index}", "client_secret": f"test-secret-{index}",
                    "access_token": f"test-ml-access-{index}-{revision}",
                    "refresh_token": f"test-ml-refresh-{index}-{revision}",
                },
                "bling": {
                    "connected": True, "client_id": f"bling-{index}",
                    "client_secret": f"test-secret-{index}",
                    "access_token": f"test-bling-access-{index}-{revision}",
                    "refresh_token": f"test-bling-refresh-{index}-{revision}",
                },
            },
        }
        for index in range(count)
    ]


def _bundle(stores, *, manifest_overrides=None, tamper_bytes=False):
    content = json.dumps(stores, ensure_ascii=False).encode("utf-8")
    files = [{
        "relative_path": "lojas_config.json", "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }]
    manifest = {
        "schema": 2, "scope": SCOPE, "client_id": CLIENT, "created_by": USER,
        "created_at": "2026-07-15T12:00:00Z", "file_count": 1, "files": files,
        "snapshot_hash": shared_sync_bundle._shared_sync_snapshot_hash(files),
        **(manifest_overrides or {}),
    }
    manifest = {key: value for key, value in manifest.items() if value is not None}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("files/lojas_config.json", content + (b" " if tamper_bytes else b""))
    return output.getvalue(), manifest


class _Harness:
    def __init__(self, monkeypatch):
        self.db = _Firestore()
        self.local_stores = _stores(11, "current")
        self.remote_stores = _stores(4, "july")
        self.local_bundle, self.local_manifest = _bundle(self.local_stores)
        self.state = {"scopes": {}}
        self.state_writes = []
        self.checkpoint_reads = []
        monkeypatch.setattr(shared_sync_remote, "_shared_sync_firestore_required", lambda: self.db)
        monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_collection_name", lambda: "meta")
        monkeypatch.setattr(shared_sync_remote, "_firebase_shared_sync_chunks_collection_name", lambda: "chunks")
        monkeypatch.setattr(shared_sync_remote, "_shared_sync_encryption_secret", lambda: b"synthetic-test-only")
        monkeypatch.setattr(shared_sync_remote, "_shared_sync_cleanup_old_snapshots", lambda *a: None)
        monkeypatch.setattr(shared_sync_remote, "_shared_sync_user_query", lambda coll, key, value: coll.where(key, "==", value))
        monkeypatch.setattr(shared_sync_remote, "_shared_sync_montar_pacote", lambda *a, **k: (self.local_bundle, self.local_manifest, []))
        monkeypatch.setattr(shared_sync_config, "_shared_sync_state_read", self._read_state)
        monkeypatch.setattr(shared_sync_config, "_shared_sync_state_write", self._write_state)
        self.install_remote()
        self.state["scopes"][STATE_SCOPE] = {
            "snapshot_hash": self.remote_meta["snapshot_hash"], "snapshot_id": "",
            "direction": "push", "synced_at": "2026-07-15T12:00:00Z",
        }

    @property
    def checkpoint(self):
        return self.state["scopes"][STATE_SCOPE]

    def _read_state(self, client_id, username):
        self.checkpoint_reads.append((client_id, username))
        assert (client_id, username) == (CLIENT, USER)
        return copy.deepcopy(self.state)

    def _write_state(self, client_id, username, state):
        assert (client_id, username) == (CLIENT, USER)
        self.state_writes.append(copy.deepcopy(state))
        self.state = copy.deepcopy(state)

    def install_remote(self, *, metadata_overrides=None, manifest_overrides=None, tamper_bytes=False):
        self.remote_bundle, manifest = _bundle(
            self.remote_stores, manifest_overrides=manifest_overrides, tamper_bytes=tamper_bytes,
        )
        meta = {
            "id": POINTER, "pointer_id": POINTER, "snapshot_id": SNAPSHOT,
            "schema": 2, "encrypted": True, "client_id": CLIENT, "scope": SCOPE,
            "snapshot_hash": manifest["snapshot_hash"], **IDENTITY,
            **(metadata_overrides or {}),
        }
        meta = {key: value for key, value in meta.items() if value is not None}
        # Re-encrypt even deliberately wrong pointer metadata so identity tests
        # cannot pass merely because decryption failed with the wrong salt.
        encrypted = shared_sync_remote._shared_sync_encrypt_bundle(meta["pointer_id"], self.remote_bundle)
        meta.update({"bundle_sha256": hashlib.sha256(encrypted).hexdigest(), "chunk_count": 1})
        self.remote_meta = meta
        self.db.collection("meta").data[POINTER] = copy.deepcopy(meta)
        self.db.collection("meta").data[SNAPSHOT] = {**copy.deepcopy(meta), "id": SNAPSHOT}
        self.db.collection("chunks").data[meta["snapshot_id"] + "_00000"] = {
            "bundle_id": meta["snapshot_id"], "data": base64.b64encode(encrypted).decode("ascii"),
        }

    def push(self):
        return shared_sync_remote._shared_sync_push_scope(
            CLIENT, SCOPE, {"username": USER, "client_id": CLIENT},
            bundle_id=POINTER, extra_meta=dict(IDENTITY), user_only=True,
            state_scope=STATE_SCOPE, skip_if_remote_hash_matches=False,
        )

    def assert_rejected_without_publication(self):
        before_meta = copy.deepcopy(self.db.collection("meta").data)
        before_chunks = copy.deepcopy(self.db.collection("chunks").data)
        before_state = copy.deepcopy(self.state)
        with pytest.raises(HTTPException) as error:
            self.push()
        assert error.value.status_code == 409
        assert self.db.collection("meta").data == before_meta
        assert self.db.collection("chunks").data == before_chunks
        assert self.state == before_state
        assert self.state_writes == []


@pytest.fixture
def harness(monkeypatch):
    return _Harness(monkeypatch)


def test_reproduces_legacy_guard_rejecting_refresh_without_causal_base(harness):
    assert len(harness.local_stores) == 11 and len(harness.remote_stores) == 4
    assert {s["store_id"] for s in harness.remote_stores} <= {s["store_id"] for s in harness.local_stores}
    with pytest.raises(HTTPException) as error:
        shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
            POINTER, harness.local_bundle, client_id=CLIENT,
        )
    assert error.value.status_code == 409
    assert shared_sync_merge_integracoes._shared_sync_validar_push_lojas_integracoes(
        POINTER, harness.local_bundle, client_id=CLIENT, base_snapshot_id=SNAPSHOT,
    ) == SNAPSHOT


def test_push_recovers_observed_hash_only_checkpoint_and_keeps_all_stores(harness):
    assert "status" not in harness.remote_meta
    assert "encryption_key_id" not in harness.remote_meta
    assert harness.checkpoint["snapshot_id"] == ""
    result = harness.push()
    assert result["success"] is True and result["stores_count"] == 11
    authority_id = shared_sync_remote._shared_sync_v2_authority_id(POINTER)
    published = harness.db.collection("meta").data[authority_id]
    payload, _ = shared_sync_remote._shared_sync_obter_bundle_por_id(POINTER, meta=published)
    stores = shared_sync_merge_integracoes._shared_sync_lojas_config_from_bundle(payload)
    assert stores == harness.local_stores
    assert harness.checkpoint["snapshot_id"] == published["snapshot_id"]
    assert harness.checkpoint["snapshot_hash"] == harness.local_manifest["snapshot_hash"]
    assert harness.state_writes


@pytest.mark.parametrize("status", [None, "complete"])
def test_older_metadata_without_link_direction_or_manifest_author_is_compatible(harness, status):
    harness.install_remote(
        metadata_overrides={"link_id": None, "sync_direction": None, "status": status},
        manifest_overrides={"client_id": None, "created_by": None},
    )
    result = harness.push()
    assert result["success"] is True and result["stores_count"] == 11
    assert harness.checkpoint["snapshot_id"] != SNAPSHOT


def test_explicit_null_status_remains_compatible_with_legacy_snapshot(harness):
    harness.db.collection("meta").data[POINTER]["status"] = None
    result = harness.push()
    assert result["success"] is True and result["stores_count"] == 11
    assert harness.checkpoint["snapshot_id"] != SNAPSHOT


def test_hash_observed_for_another_scope_does_not_authorize_this_share(harness):
    harness.state["scopes"]["another-share:lojas_integracoes"] = harness.state["scopes"].pop(STATE_SCOPE)
    harness.assert_rejected_without_publication()


@pytest.mark.parametrize("checkpoint_hash", ["", "not-a-hash", "f" * 64])
def test_missing_invalid_or_unobserved_hash_does_not_authorize_refresh(harness, checkpoint_hash):
    harness.checkpoint["snapshot_hash"] = checkpoint_hash
    harness.assert_rejected_without_publication()


def test_explicit_different_snapshot_id_never_falls_back_to_matching_hash(harness):
    harness.checkpoint["snapshot_id"] = "a-different-explicit-snapshot"
    harness.assert_rejected_without_publication()


@pytest.mark.parametrize(
    "field, value",
    [
        ("client_id", "other-tenant"), ("pointer_id", "other-pointer"),
        ("id", "other-pointer"), ("scope", "cadastro"),
        ("source_client_id", "other-source"), ("source_username", "mallory"),
        ("target_client_id", "other-target"), ("target_username", "mallory"),
        ("target_username", ""), ("link_id", "other-link"),
        ("sync_direction", "target_to_source"), ("visibility", "machine-sync"),
        ("status", "pending"), ("status", "failed"),
        ("status", False), ("status", 0), ("status", []), ("status", {"invalid": "status"}),
    ],
)
def test_metadata_from_wrong_identity_or_uncommitted_snapshot_has_no_causal_authority(harness, field, value):
    harness.install_remote(metadata_overrides={field: value})
    harness.assert_rejected_without_publication()


@pytest.mark.parametrize("field, value", [("client_id", "other-tenant"), ("created_by", "mallory")])
def test_manifest_origin_must_match_metadata_and_sender(harness, field, value):
    harness.install_remote(manifest_overrides={field: value})
    harness.assert_rejected_without_publication()


@pytest.mark.parametrize("corruption", ["metadata-hash", "manifest-hash", "file-bytes"])
def test_matching_advertised_hash_never_bypasses_manifest_and_file_validation(harness, corruption):
    if corruption == "metadata-hash":
        harness.install_remote(metadata_overrides={"snapshot_hash": "e" * 64})
    elif corruption == "manifest-hash":
        harness.install_remote(manifest_overrides={"snapshot_hash": "e" * 64})
    else:
        harness.install_remote(tamper_bytes=True)
    harness.checkpoint["snapshot_hash"] = harness.remote_meta["snapshot_hash"]
    harness.assert_rejected_without_publication()


@pytest.mark.parametrize("regression", ["missing-store", "ml-access", "ml-refresh", "bling-access", "bling-refresh", "seller"])
def test_recovered_base_keeps_store_and_connection_protections(harness, regression):
    if regression == "missing-store":
        harness.local_stores.pop(0)
    elif regression == "seller":
        harness.local_stores[0]["integracoes"]["mercadolivre"]["user_id"] = "other-seller"
    else:
        provider, field = regression.split("-")
        provider = "mercadolivre" if provider == "ml" else provider
        harness.local_stores[0]["integracoes"][provider][field + "_token"] = ""
    harness.local_bundle, harness.local_manifest = _bundle(harness.local_stores)
    harness.assert_rejected_without_publication()


def test_competing_publication_after_recovery_is_rejected_by_cas(harness, monkeypatch):
    real_cas = shared_sync_remote._shared_sync_publicar_pointer_cas
    raced = []

    def publish_after_competitor(db, bundle_id, meta, expected_revision):
        raced.append(expected_revision)
        competitor = {**harness.remote_meta, "snapshot_id": POINTER + "__competitor"}
        db.collection("meta").document(POINTER).set(competitor)
        return real_cas(db, bundle_id, meta, expected_revision)

    monkeypatch.setattr(shared_sync_remote, "_shared_sync_publicar_pointer_cas", publish_after_competitor)
    original_state = copy.deepcopy(harness.state)
    with pytest.raises(HTTPException) as error:
        harness.push()
    assert error.value.status_code == 409
    assert len(raced) == 1
    assert raced[0] == {"pointer_id": POINTER, "revision": "v2:" + SNAPSHOT}
    assert harness.db.collection("meta").data[POINTER]["snapshot_id"] == POINTER + "__competitor"
    assert set(harness.db.collection("meta").data) == {POINTER, SNAPSHOT}
    assert set(harness.db.collection("chunks").data) == {SNAPSHOT + "_00000"}
    assert harness.state == original_state and harness.state_writes == []
