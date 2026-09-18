from __future__ import annotations

import ast
import copy
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from backend.services import store_public_snapshot as snapshots
from backend.services.mercadolivre_oauth_status import oauth_status


def store(store_id="store-a", nome="Loja A"):
    return {"store_id": store_id, "nome": nome, "integracoes": {"mercadolivre": {
        "user_id": 123, "site_id": "MLB", "app_id": "fixture-app",
        "access_token": "fixture-access", "refresh_token": "fixture-refresh",
        "client_secret": "fixture-secret"}}}


def test_public_projection_omits_credentials_and_raw_provider_messages(tmp_path):
    source = store()
    cfg = source["integracoes"]["mercadolivre"]
    del cfg["refresh_token"]
    cfg["motivo"] = "Upstream exception containing fixture-access"
    cfg["status"] = "fixture-secret"
    source["email"] = "fixture-private-email"
    snapshot = snapshots.build_snapshot([source])
    snapshots.write_snapshot(tmp_path, snapshot)
    data = snapshots.snapshot_path(tmp_path).read_text(encoding="utf-8")
    for private in ("fixture-access", "fixture-app", "fixture-secret", "fixture-private-email", "Upstream"):
        assert private not in data
    assert snapshot["lojas"][0]["mercadolivre_oauth_faltando"] == ["refresh_token"]
    assert snapshot["lojas"][0]["seller_id"] == "123"
    assert snapshots.read_snapshot(tmp_path) == snapshot


def test_central_public_stores_work_without_transport_references():
    source = store()
    source["integracoes"]["mercadolivre"] = {
        "connected": True, "central": True, "user_id": "123", "site_id": "MLB"}
    snapshot = snapshots.build_snapshot([source])
    row = snapshot["lojas"][0]
    assert row["mercadolivre_conectado"] is True
    assert row["mercadolivre_oauth_faltando"] == []
    assert "integracoes" not in row
    assert source["integracoes"]["mercadolivre"] == {
        "connected": True, "central": True, "user_id": "123", "site_id": "MLB"}


def test_independent_generations_and_no_aliasing():
    source = [store()]
    first = snapshots.build_snapshot(source)
    second = snapshots.build_snapshot(source)
    assert first["generation"] != second["generation"]
    source[0]["nome"] = "Changed"
    assert first["lojas"][0]["nome"] == "Loja A"
    assert snapshots.build_snapshot([])["lojas"] == []


@pytest.mark.parametrize("payload", [b"", b'{"schema_version":', b"null", b"[]", b"\xff"])
def test_absent_or_corrupt_snapshot_is_unavailable(tmp_path, payload):
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.read_snapshot(tmp_path)
    snapshots.snapshot_path(tmp_path).write_bytes(payload)
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.read_snapshot(tmp_path)


@pytest.mark.parametrize("mutation", [
    lambda doc: doc.update(access_token="secret"),
    lambda doc: doc.update(schema_version=True),
    lambda doc: doc.update(generation="not-a-generation"),
    lambda doc: doc.update(published_at="2026-09-09T12:00:00"),
    lambda doc: doc["lojas"][0].update(refresh_token="secret"),
    lambda doc: doc["lojas"][0].update(store_id={"token": "secret"}),
    lambda doc: doc["lojas"][0].update(nome=""),
    lambda doc: doc["lojas"][0].update(mercadolivre_conectado=1),
    lambda doc: doc["lojas"][0].update(mercadolivre_status="secret"),
    lambda doc: doc["lojas"][0].update(mercadolivre_motivo="Upstream secret"),
    lambda doc: doc["lojas"][0].update(mercadolivre_oauth_faltando=["secret"]),
    lambda doc: doc["lojas"].append(copy.deepcopy(doc["lojas"][0])),
])
def test_closed_schema_and_identities_rejected_on_both_read_and_write(tmp_path, mutation):
    valid = snapshots.build_snapshot([store()])
    snapshots.write_snapshot(tmp_path, valid)
    invalid = copy.deepcopy(valid)
    mutation(invalid)
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.write_snapshot(tmp_path, invalid)
    assert snapshots.read_snapshot(tmp_path) == valid
    snapshots.snapshot_path(tmp_path).write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.read_snapshot(tmp_path)


def test_duplicate_json_fields_rejected(tmp_path):
    valid = snapshots.build_snapshot([])
    version = valid["schema_version"]
    payload = json.dumps(valid).replace(f'"schema_version": {version}', f'"schema_version": {version}, "schema_version": {version}')
    snapshots.snapshot_path(tmp_path).write_text(payload, encoding="utf-8")
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.read_snapshot(tmp_path)


def test_failed_atomic_replace_preserves_previous_generation_and_cleans_temp(tmp_path, monkeypatch):
    previous = snapshots.build_snapshot([store()])
    snapshots.write_snapshot(tmp_path, previous)
    def fail_replace(*args):
        raise OSError("simulated publication error")
    monkeypatch.setattr(snapshots.os, "replace", fail_replace)
    with pytest.raises(OSError):
        snapshots.write_snapshot(tmp_path, snapshots.build_snapshot([]))
    assert snapshots.read_snapshot(tmp_path) == previous
    assert list(tmp_path.glob(".lojas-public-*.tmp")) == []


def test_failure_to_fsync_never_publishes(tmp_path, monkeypatch):
    previous = snapshots.build_snapshot([store()])
    snapshots.write_snapshot(tmp_path, previous)
    monkeypatch.setattr(snapshots.os, "fsync", lambda *a: (_ for _ in ()).throw(OSError("sync failed")))
    with pytest.raises(OSError):
        snapshots.write_snapshot(tmp_path, snapshots.build_snapshot([]))
    assert snapshots.read_snapshot(tmp_path) == previous


def test_tenant_projections_are_separate(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    snapshots.write_snapshot(a, snapshots.build_snapshot([store("store-a")]))
    snapshots.write_snapshot(b, snapshots.build_snapshot([store("store-b")]))
    assert snapshots.read_snapshot(a)["lojas"][0]["store_id"] == "store-a"
    assert snapshots.read_snapshot(b)["lojas"][0]["store_id"] == "store-b"


def _process_reader(directory, started, errors):
    try:
        started.set()
        for _ in range(300):
            doc = snapshots.read_snapshot(directory)
            names = {row["nome"] for row in doc["lojas"]}
            if len(names) != 1 or len(doc["lojas"]) != 50:
                errors.put("mixed_generation")
                return
        errors.put(None)
    except Exception as exc:
        errors.put(type(exc).__name__)


def test_concurrent_process_reader_observes_only_complete_generations(tmp_path):
    rows = [store(str(i), "Before") for i in range(50)]
    snapshots.write_snapshot(tmp_path, snapshots.build_snapshot(rows))
    context = multiprocessing.get_context("spawn")
    started, errors = context.Event(), context.Queue()
    process = context.Process(target=_process_reader, args=(str(tmp_path), started, errors))
    process.start()
    try:
        assert started.wait(10)
        for i in range(30):
            for row in rows:
                row["nome"] = str(i)
            snapshots.write_snapshot(tmp_path, snapshots.build_snapshot(rows))
        assert errors.get(timeout=10) is None
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


@pytest.mark.parametrize("cfg", [None, {}, {"access_token": "token"},
    {"id": "app", "secret": "secret", "access_token": "token", "refresh_token": "refresh"},
    {"central": True, "connected": True}, {"status": "custom", "motivo": "custom message"}])
def test_legacy_oauth_facade_preserves_contract_without_importing_runtime(cfg):
    # Isolate the facade definition; importing the legacy module initializes runtime.
    source = Path("backend/services/mercadolivre_legacy_api.py").read_text(encoding="utf-8")
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_ml_oauth_status")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "oauth_facade", "exec"), namespace)
    assert namespace["_ml_oauth_status"](cfg) == oauth_status(cfg)

@pytest.mark.skipif(os.name != "nt", reason="Windows readers deny replacement while holding the handle")
def test_long_lived_reader_defers_publication_without_corrupting_previous_generation(tmp_path):
    previous = snapshots.build_snapshot([store()])
    snapshots.write_snapshot(tmp_path, previous)
    latest = snapshots.build_snapshot([])
    with snapshots.snapshot_path(tmp_path).open("rb") as old_handle:
        with pytest.raises(PermissionError):
            snapshots.write_snapshot(tmp_path, latest)
        assert json.load(old_handle) == previous
    assert snapshots.read_snapshot(tmp_path) == previous
    snapshots.write_snapshot(tmp_path, latest)
    assert snapshots.read_snapshot(tmp_path) == latest


def test_leaf_symlink_never_reads_or_replaces_another_tenants_projection(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    other = snapshots.build_snapshot([store("other-store")])
    snapshots.write_snapshot(second, other)
    target = snapshots.snapshot_path(second)
    alias = first / "lojas_public_snapshot.json"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is not permitted on this Windows host")
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.read_snapshot(first)
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.write_snapshot(first, snapshots.build_snapshot([]))
    assert snapshots.read_snapshot(second) == other
    assert alias.is_symlink()


def test_redirected_snapshot_leaf_rejected_even_without_symlink_flag(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    actual = snapshots.os.path.realpath
    def redirected(path, *args, **kwargs):
        candidate = Path(path)
        if candidate == first / "lojas_public_snapshot.json":
            return str(second / "lojas_public_snapshot.json")
        return actual(path, *args, **kwargs)
    monkeypatch.setattr(snapshots.os.path, "realpath", redirected)
    with pytest.raises(snapshots.SnapshotUnavailable):
        snapshots.snapshot_path(first)
