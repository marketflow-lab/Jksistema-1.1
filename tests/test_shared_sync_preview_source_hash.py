"""Preview guards compare captured source bytes, not transformed transport bytes."""
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
import zipfile

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - binds service peers only
from backend.services import cadastro_fotos, central_accounts_client, integracoes
from backend.services import shared_sync_ai_context as ai_context
from backend.services import shared_sync_bundle as bundle
from backend.services import shared_sync_collect_files as collect
from backend.services import shared_sync_delta as delta
from backend.services import shared_sync_operations as operations
from backend.services import shared_sync_remote as remote
from backend.services.store_coordination import store_lock


REL = "cadastro_produtos_lojas.csv"
SESSION = {"client_id": "tenant-a", "username": "alice"}


class MemoryDocument:
    def __init__(self, collection, key):
        self.collection, self.key = collection, key

    def set(self, value, merge=False):
        current = self.collection.data.get(self.key, {}) if merge else {}
        self.collection.data[self.key] = {**current, **value}

    def get(self):
        value = self.collection.data.get(self.key)
        return SimpleNamespace(exists=value is not None, to_dict=lambda: dict(value or {}))


class MemoryCollection:
    def __init__(self):
        self.data = {}

    def document(self, key):
        return MemoryDocument(self, key)


class MemoryFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, MemoryCollection())


@pytest.fixture
def source(tmp_path, monkeypatch):
    def tenant(client_id):
        return str(tmp_path / client_id)

    for module in (collect, bundle, operations):
        monkeypatch.setattr(module, "get_tenant_path", tenant, raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_fotos_escopo_estrito", lambda _: False)
    monkeypatch.setattr(central_accounts_client, "current", lambda _: None)

    @contextmanager
    def coordinated(client_id):
        with store_lock(tenant(client_id)):
            yield

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", coordinated)
    tenant_path = tmp_path / SESSION["client_id"]
    tenant_path.mkdir()
    rows = [
        {"store_id": "store-a", "sku": sku, "row_version": "1",
         "updated_at_utc": "2026-10-16T00:00:00Z", "deleted_at_utc": "", "nome": name}
        for sku, name in (("001", "First"), ("002", "Second"))
    ]
    path = tenant_path / REL
    # The first-send regression is portable: transport adds the UTF-8 BOM.
    path.write_bytes(pd.DataFrame(rows).to_csv(index=False, lineterminator="\n").encode("utf-8"))
    return path


@pytest.fixture
def memory_remote(monkeypatch):
    db = MemoryFirestore()
    monkeypatch.setattr(remote, "_shared_sync_firestore_required", lambda: db)
    monkeypatch.setattr(remote, "_firebase_shared_sync_collection_name", lambda: "snapshots")
    monkeypatch.setattr(remote, "_firebase_shared_sync_chunks_collection_name", lambda: "chunks")
    monkeypatch.setattr(remote, "_shared_sync_encryption_secret", lambda: b"synthetic-preview-test-key")
    monkeypatch.setattr(remote, "_shared_sync_state_update", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_shared_sync_cleanup_old_snapshots", lambda *a, **k: None)
    monkeypatch.setattr(remote, "_shared_sync_delete_chunks", lambda *a, **k: None)
    return db


def preview(session=SESSION, include_ai_context=False):
    hashes, _ = operations._shared_sync_local_fingerprint(
        session, ["cadastro"], include_ai_context,
    )
    return hashes["cadastro"]


def push(expected, known_keys, **kwargs):
    return remote._shared_sync_push_scope(
        SESSION["client_id"], "cadastro", SESSION, "machine-a",
        bundle_id="synthetic-bundle", user_only=True,
        known_keys=known_keys, expected_snapshot_hash=expected, **kwargs,
    )


@pytest.mark.parametrize("case", ["first", "partial", "already_shared"])
def test_unchanged_source_accepts_reserialized_partial_and_empty_delta(
    source, memory_remote, monkeypatch, case,
):
    raw = source.read_bytes()
    canonical, all_keys = delta._shared_sync_csv_delta_bytes("cadastro", REL, raw, set())
    if case != "first":
        source.write_bytes(canonical)
    known = set() if case == "first" else set(all_keys[:1] if case == "partial" else all_keys)
    original = source.read_bytes()
    expected = preview()
    captured = []
    real_build = bundle._shared_sync_montar_pacote

    def build(*args, **kwargs):
        result = real_build(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(remote, "_shared_sync_montar_pacote", build)
    result = push(expected, known)
    archive_bytes, manifest, _ = captured[0]
    assert result["success"]
    assert manifest["source_snapshot_hash"] == expected
    assert manifest["snapshot_hash"] != expected
    assert source.read_bytes() == original
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        wire_manifest = json.loads(archive.read("manifest.json"))
        assert "source_snapshot_hash" not in wire_manifest
        assert wire_manifest["snapshot_hash"] == bundle._shared_sync_snapshot_hash(wire_manifest["files"])
        if case == "first":
            sent = archive.read("files/" + REL)
            assert sent != original
            assert pd.read_csv(io.BytesIO(sent), dtype=str).equals(pd.read_csv(io.BytesIO(original), dtype=str))
    if case == "already_shared":
        assert result["skipped"] and result["reason"] == "already_shared"
        assert memory_remote.collections == {}
    else:
        assert result["item_count"] == (2 if case == "first" else 1)
        meta = memory_remote.collection("snapshots").data["synthetic-bundle"]
        assert meta["snapshot_hash"] == manifest["snapshot_hash"]
        assert "source_snapshot_hash" not in meta
        assert memory_remote.collection("chunks").data


def test_real_change_after_preview_still_rejects_before_upload(source, memory_remote):
    expected = preview()
    source.write_bytes(source.read_bytes().replace(b"First", b"Changed"))
    with pytest.raises(HTTPException, match="Os dados locais mudaram") as caught:
        push(expected, set())
    assert caught.value.status_code == 409
    assert memory_remote.collections == {}


def test_source_guard_uses_frozen_capture_when_writer_runs_after_unlock(source, monkeypatch):
    expected = preview()
    original = source.read_bytes()

    @contextmanager
    def replace_after_capture(client_id):
        with store_lock(str(source.parent)):
            yield
        source.write_bytes(original.replace(b"First", b"Changed"))
        monkeypatch.setattr(bundle, "_shared_sync_ler_arquivo_pacote", lambda *_: pytest.fail("live source reopened"))

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", replace_after_capture)
    data, manifest, _ = bundle._shared_sync_montar_pacote(
        SESSION["client_id"], "cadastro", SESSION["username"], known_keys=set(),
    )
    assert manifest["source_snapshot_hash"] == expected
    assert source.read_bytes() != original
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert b"First" in archive.read("files/" + REL)
        assert b"Changed" not in archive.read("files/" + REL)


def test_machine_context_is_captured_once_and_bound_to_tenant_and_user(source, monkeypatch):
    expected = preview(include_ai_context=True)
    assert expected != preview()
    assert expected != preview({**SESSION, "username": "bob"}, include_ai_context=True)
    calls = []
    inside_lock = False
    real_context = ai_context.build_snapshot_bytes

    def capture_context(client_id, username, **kwargs):
        assert inside_lock
        result = real_context(client_id, username, **kwargs)
        calls.append((client_id, username, result[0]))
        return result

    @contextmanager
    def coordinated(client_id):
        nonlocal inside_lock
        with store_lock(str(source.parent)):
            inside_lock = True
            try:
                yield
            finally:
                inside_lock = False

    monkeypatch.setattr(ai_context, "build_snapshot_bytes", capture_context)
    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", coordinated)
    data, manifest, _ = bundle._shared_sync_montar_pacote(
        SESSION["client_id"], "cadastro", SESSION["username"], include_ai_context=True,
    )
    assert len(calls) == 1
    assert calls[0][:2] == (SESSION["client_id"], SESSION["username"])
    assert manifest["source_snapshot_hash"] == expected
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        snapshot = archive.read(ai_context.AI_CONTEXT_EXTENSION_PATH)
        assert snapshot == calls[0][2]
        ai_context.validate_snapshot_bytes(snapshot, expected_client_id=SESSION["client_id"], expected_username=SESSION["username"])
        for identity in (("tenant-b", "alice"), ("tenant-a", "bob")):
            with pytest.raises(HTTPException):
                ai_context.validate_snapshot_bytes(snapshot, expected_client_id=identity[0], expected_username=identity[1])


def test_user_share_omits_machine_ai_context(source, monkeypatch):
    monkeypatch.setattr(ai_context, "build_snapshot_bytes", lambda *a, **k: pytest.fail("user share collected machine context"))
    expected = preview()
    data, manifest, _ = bundle._shared_sync_montar_pacote(
        SESSION["client_id"], "cadastro", SESSION["username"], user_only=True, known_keys=set(),
    )
    assert manifest["source_snapshot_hash"] == expected
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert ai_context.AI_CONTEXT_EXTENSION_PATH not in archive.namelist()


def test_user_scoped_capture_keeps_other_users_and_tenants_out(source, monkeypatch):
    monkeypatch.setattr(collect, "_favoritos_usuario_slug", lambda username: username, raising=False)
    other_tenant = source.parent.parent / "tenant-b"
    other_tenant.mkdir()
    for tenant, user, content in ((source.parent, "alice", b"[1]"), (source.parent, "bob", b"[2]"), (other_tenant, "alice", b"[3]")):
        (tenant / f"favoritos_pesquisas_{user}.json").write_bytes(content)
    hashes, files = operations._shared_sync_local_fingerprint(SESSION, ["sku_campos_pesquisa"])
    data, manifest, _ = bundle._shared_sync_montar_pacote(
        SESSION["client_id"], "sku_campos_pesquisa", SESSION["username"], user_only=True,
    )
    assert manifest["source_snapshot_hash"] == hashes["sku_campos_pesquisa"]
    assert [item["relative_path"] for item in files["sku_campos_pesquisa"]] == ["favoritos_pesquisas_alice.json"]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read("files/favoritos_pesquisas_alice.json") == b"[1]"
        assert not any("bob" in name for name in archive.namelist())


def test_source_hash_precedes_photo_sanitization_and_selection(source, monkeypatch):
    monkeypatch.setattr(cadastro_fotos, "_cadastro_fotos_escopo_estrito", lambda _: True)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _: [{"store_id": "store-a"}])
    (source.parent / "lojas_config.json").write_text(
        json.dumps([{"store_id": "store-a", "nome": "Synthetic"}]), encoding="utf-8",
    )
    config = source.parent / "cadastro_fotos_config.json"
    config.write_text(json.dumps({"schema": "jk.cadastro.fotos.v1", "strict_store_scope": True, "shared_groups": []}), encoding="utf-8")
    legacy = source.parent / "cadastro_produtos.csv"
    legacy.write_bytes(b"sku,nome,foto\n001,Legacy,cadastro_fotos/001.jpg\n")
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    orphan = source.parent / f"cadastro_fotos/lojas/{segment}/999.jpg"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"synthetic-orphan")
    expected = preview()
    originals = {path: path.read_bytes() for path in (source, config, legacy, orphan)}
    data, manifest, _ = bundle._shared_sync_montar_pacote(
        SESSION["client_id"], "cadastro", SESSION["username"], user_only=True,
    )
    assert manifest["source_snapshot_hash"] == expected
    assert manifest["snapshot_hash"] != expected
    assert all(path.read_bytes() == content for path, content in originals.items())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "files/" + orphan.relative_to(source.parent).as_posix() not in archive.namelist()
        sanitized = pd.read_csv(io.BytesIO(archive.read("files/cadastro_produtos.csv")), dtype=str).fillna("")
        assert sanitized.iloc[0]["foto"] == ""
        assert archive.read("files/cadastro_fotos_config.json") != originals[config]
