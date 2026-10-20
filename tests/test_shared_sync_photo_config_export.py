"""Outgoing Cadastro carries its effective photo policy without editing the source."""
import csv
import hashlib
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
import zipfile

import pytest
from fastapi import HTTPException

from backend.services import shared_sync  # noqa: F401 - bind peers
from backend.services import cadastro_fotos, central_accounts_client, integracoes
from backend.services import shared_sync_apply_scope as apply_scope
from backend.services import shared_sync_bundle as bundle
from backend.services import shared_sync_collect_files as collect
from backend.services import shared_sync_delta as delta
from backend.services import shared_sync_operations as operations
from backend.services import shared_sync_remote as remote
from backend.services.store_coordination import store_lock


REL = "cadastro_produtos_lojas.csv"
CONFIG = "cadastro_fotos_config.json"
CONFIG_KEY = "cadastro:file:" + CONFIG
SESSION = {"client_id": "source", "username": "alice"}
DEFAULT = {"schema": "jk.cadastro.fotos.v1", "strict_store_scope": False, "shared_groups": []}


@pytest.fixture
def source(tmp_path, monkeypatch):
    tenant = tmp_path / "source"
    tenant.mkdir()
    stores = [{"store_id": key, "nome": key} for key in ("store-a", "store-b")]
    (tenant / "lojas_config.json").write_text(json.dumps(stores), encoding="utf-8")
    for module in (bundle, collect, operations, apply_scope, cadastro_fotos):
        monkeypatch.setattr(module, "get_tenant_path", lambda client: str(tmp_path / client), raising=False)
    monkeypatch.setattr(cadastro_fotos, "PASTA_INFO", str(tmp_path), raising=False)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _: stores)
    monkeypatch.setattr(central_accounts_client, "current", lambda _: None)

    @contextmanager
    def lock(client):
        with store_lock(str(tmp_path / client)):
            yield
    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", lock)
    catalog = tenant / REL
    catalog.write_text(
        "store_id,sku,nome,foto,row_version,updated_at_utc,deleted_at_utc\n"
        "store-a,001,Produto,,1,2026-10-20T10:00:00Z,\n", encoding="utf-8",
    )
    return SimpleNamespace(root=tmp_path, tenant=tenant, catalog=catalog, config=tenant / CONFIG, stores=stores)


def _build(known=None):
    payload, manifest, warnings = bundle._shared_sync_montar_pacote(
        "source", "cadastro", "alice", user_only=True, known_keys=known,
    )
    assert warnings == []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = {name[6:]: archive.read(name) for name in archive.namelist() if name.startswith("files/")}
    validated, _ = apply_scope._shared_sync_read_validated_bundle(payload, "cadastro")
    assert validated["snapshot_hash"] == bundle._shared_sync_snapshot_hash(validated["files"])
    for entry in manifest["files"]:
        assert hashlib.sha256(files[entry["relative_path"]]).hexdigest() == entry["sha256"]
    return files, manifest


def _preview():
    hashes, _ = operations._shared_sync_local_fingerprint(SESSION, ["cadastro"])
    return hashes["cadastro"]


@pytest.mark.parametrize("known", [None, set()])
def test_absent_source_config_is_materialized_only_in_bundle_and_is_deterministic(source, known):
    before = source.catalog.read_bytes()
    approved = _preview()
    files, manifest = _build(known)
    assert json.loads(files[CONFIG]) == DEFAULT
    assert manifest["source_snapshot_hash"] == approved
    config_entry = next(entry for entry in manifest["files"] if entry["relative_path"] == CONFIG)
    assert config_entry["mtime"] == 0
    repeated, repeated_manifest = _build(known)
    assert repeated[CONFIG] == files[CONFIG]
    assert repeated_manifest["snapshot_hash"] == manifest["snapshot_hash"]
    assert not source.config.exists() and source.catalog.read_bytes() == before
    target = source.root / "target"
    target.mkdir()
    (target / "lojas_config.json").write_text(json.dumps(source.stores), encoding="utf-8")
    plan = apply_scope._shared_sync_cadastro_photo_config_preparar_locked(str(target), files[CONFIG])
    assert plan["action"] == "create" and plan["config"] == DEFAULT
    assert not (target / CONFIG).exists()


@pytest.mark.parametrize("strict", [False, True])
def test_known_config_still_accompanies_canonical_delta_and_keeps_policy(source, strict):
    config = {**DEFAULT, "strict_store_scope": strict,
              "shared_groups": [{"group_id": "grupo", "store_ids": ["store-b", "store-a"]}]}
    source.config.write_text(json.dumps(config, separators=(",", ":")), encoding="utf-8")
    before = source.config.read_bytes()
    approved = _preview()
    files, manifest = _build({CONFIG_KEY})
    assert REL in files and CONFIG in files
    assert json.loads(files[CONFIG]) == {**config, "shared_groups": [{"group_id": "grupo", "store_ids": ["store-a", "store-b"]}]}
    assert manifest["source_snapshot_hash"] == approved
    assert source.config.read_bytes() == before


def test_known_config_and_photo_are_carried_with_changed_product(source):
    config = {**DEFAULT, "strict_store_scope": True}
    source.config.write_text(json.dumps(config), encoding="utf-8")
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segment}/001.jpg"
    photo = source.tenant / photo_rel
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"synthetic-photo")
    source.catalog.write_text(
        "store_id,sku,nome,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,Produto atualizado,{photo_rel},2,2026-10-20T11:00:00Z,\n", encoding="utf-8",
    )
    known = {CONFIG_KEY, "cadastro:file:" + photo_rel + ":revision:" + hashlib.sha256(photo.read_bytes()).hexdigest()}
    files, _ = _build(known)
    assert json.loads(files[CONFIG]) == config
    assert files[photo_rel] == b"synthetic-photo" and REL in files


@pytest.mark.parametrize("strict", [False, True])
def test_photo_policy_follows_emitted_delta_without_bypassing_strict_owner_filter(source, strict):
    if strict:
        source.config.write_text(json.dumps({**DEFAULT, "strict_store_scope": True}), encoding="utf-8")
    segment = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segment}/001.jpg"
    photo = source.tenant / photo_rel
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"updated-photo")
    source.catalog.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,{photo_rel},1,2026-10-20T10:00:00Z,\n", encoding="utf-8",
    )
    _, keys = delta._shared_sync_csv_delta_bytes("cadastro", REL, source.catalog.read_bytes(), set())
    files, manifest = _build(set(keys) | {CONFIG_KEY})
    if strict:
        # Existing strict ownership requires an active row in this delta.
        # A filtered photo must not cause a config-only bundle to appear.
        assert files == {}
    else:
        assert REL not in files and files[photo_rel] == b"updated-photo"
        assert json.loads(files[CONFIG]) == DEFAULT
        assert manifest["item_count"] == 1
    assert source.config.exists() is strict


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_existing_but_uncollected_config_cannot_be_replaced_by_default(source, monkeypatch, kind):
    if kind == "file":
        source.config.write_text(json.dumps({**DEFAULT, "strict_store_scope": True}), encoding="utf-8")
    else:
        source.config.mkdir()
    real_collect = collect._shared_sync_coletar_arquivos

    def omit(*args, **kwargs):
        entries, warnings = real_collect(*args, **kwargs)
        return [entry for entry in entries if entry["relative_path"] != CONFIG], warnings

    monkeypatch.setattr(bundle, "_shared_sync_coletar_arquivos", omit)
    with pytest.raises(HTTPException) as error:
        _build()
    assert error.value.status_code == 409
    assert source.config.exists()


def test_invalid_existing_config_fails_instead_of_using_default(source):
    source.config.write_text('{"schema":"wrong"}', encoding="utf-8")
    before = source.config.read_bytes()
    with pytest.raises(HTTPException) as error:
        _build()
    assert error.value.status_code == 409
    assert source.config.read_bytes() == before


@pytest.mark.parametrize("change", ["create", "change", "format", "remove"])
def test_real_config_change_after_preview_rejects_before_remote_upload(source, monkeypatch, change):
    if change != "create":
        source.config.write_text(json.dumps(DEFAULT), encoding="utf-8")
    approved = _preview()
    if change == "remove":
        source.config.unlink()
    else:
        changed = {**DEFAULT, "strict_store_scope": change in {"create", "change"}}
        source.config.write_text(json.dumps(changed, indent=2), encoding="utf-8")
    monkeypatch.setattr(remote, "_shared_sync_firestore_required", lambda: object())
    with pytest.raises(HTTPException) as error:
        remote._shared_sync_push_scope(
            "source", "cadastro", SESSION, known_keys=set(), expected_snapshot_hash=approved,
        )
    assert error.value.status_code == 409
    assert "mudaram depois da previa" in str(error.value.detail)


def test_captured_policy_is_not_reread_after_lock_released(source, monkeypatch):
    expected = {**DEFAULT, "strict_store_scope": True}
    source.config.write_text(json.dumps(expected), encoding="utf-8")

    @contextmanager
    def change_after_capture(client):
        with store_lock(str(source.tenant)):
            yield
        source.config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", change_after_capture)
    files, _ = _build({CONFIG_KEY})
    assert json.loads(files[CONFIG]) == expected


def test_default_dependency_does_not_turn_empty_delta_into_new_content(source):
    _, keys = delta._shared_sync_csv_delta_bytes("cadastro", REL, source.catalog.read_bytes(), set())
    files, manifest = _build(set(keys) | {CONFIG_KEY})
    assert files == {} and manifest["item_count"] == 0
    assert not source.config.exists()


def test_strict_policy_still_sanitizes_global_photo_references(source):
    source.config.write_text(json.dumps({**DEFAULT, "strict_store_scope": True}), encoding="utf-8")
    legacy = source.tenant / "cadastro_produtos.csv"
    legacy.write_text("sku,nome,foto\n001,Produto,cadastro_fotos/global.jpg\n", encoding="utf-8")
    original = legacy.read_bytes()
    files, _ = _build({CONFIG_KEY})
    rows = list(csv.DictReader(io.StringIO(files["cadastro_produtos.csv"].decode("utf-8-sig"))))
    assert rows[0]["foto"] == ""
    assert json.loads(files[CONFIG])["strict_store_scope"] is True
    assert legacy.read_bytes() == original
