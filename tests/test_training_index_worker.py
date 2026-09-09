from __future__ import annotations

import copy
from pathlib import Path
import time

import pytest

from backend.modules.context_hub import api as hub
from backend.modules.context_hub import training_index_worker as worker
from backend.modules.context_hub import training_read_index as index
from backend.modules.context_hub import store_sku_editor as editor
from backend.modules.context_hub.catalog_product_repository import publish_catalog_snapshot
from backend.modules.context_hub.contracts import ContextHubConflictError
from backend.modules.context_hub.paths import _tenant_paths

SCOPE = {"store_ref": "store-a", "store_name": "Loja A", "seller_id": "123", "site_id": "MLB"}


@pytest.fixture
def root(tmp_path, monkeypatch):
    from backend.services import training_read_service
    info = tmp_path / "info"
    info.mkdir()
    hub.configure_context_hub(base_dir=tmp_path, info_root=info, surface="test")
    monkeypatch.setattr(training_read_service, "notify_saved", lambda *args, **kwargs: None)
    yield info
    worker.stop_workers()
    hub.stop_all_context_hub_watchers()


def save(root, text, sku="0007", **extra):
    before = editor.load_store_guidance_editor("tenant-a", SCOPE, info_root=root)
    return editor.save_store_guidance_editor("tenant-a", SCOPE, sku=sku,
        guidance={"notas": text, **extra}, expected_revision=before["editorial"]["revision"], info_root=root)


def note_path(root, snapshot, sku="0007"):
    return _tenant_paths("tenant-a", info_root=root).curated_dir / snapshot["editorial"]["skus"][sku]["relative_path"]


def build(root, cache=None, **kwargs):
    return worker.rebuild_now("tenant-a", SCOPE, info_root=root, cache=cache, **kwargs)


def ficha(root):
    return index.read_ficha("tenant-a", SCOPE, "0007", info_root=root)


def test_worker_current_editor_revision_matches_source_and_unchanged_poll_does_not_parse(root, monkeypatch):
    saved = save(root, "Orientacao inicial")
    cache = worker.NoteCache()
    first = build(root, cache)
    result = ficha(root)
    assert result["notas_sku"] == {"0007": "Orientacao inicial"}
    assert result["expected_revision"] == saved["editorial"]["revision"]
    assert result["sku_details"]["revision"] == result["expected_revision"]
    monkeypatch.setattr(worker, "_read_curated_note", lambda *_args: pytest.fail("Unchanged notes must not be parsed"))
    second = build(root, cache)
    assert second["generation"] == first["generation"]


def test_external_edit_reparse_only_changed_note_and_updates_coherent_revision(root, monkeypatch):
    first = save(root, "Primeira")
    save(root, "Outra nota", sku="0008")
    cache = worker.NoteCache()
    build(root, cache)
    initial = ficha(root)
    target = note_path(root, first)
    target.write_text(target.read_text(encoding="utf-8").replace("Primeira", "Nova externa"), encoding="utf-8")
    original, parsed = worker._read_curated_note, []
    def counted(paths, path):
        parsed.append(path)
        return original(paths, path)
    monkeypatch.setattr(worker, "_read_curated_note", counted)
    build(root, cache)
    updated = ficha(root)
    assert parsed == [target]
    assert updated["notas_sku"]["0007"] == "Nova externa"
    assert updated["expected_revision"] != initial["expected_revision"]
    assert updated["expected_revision"] == editor.load_store_guidance_editor("tenant-a", SCOPE, info_root=root)["editorial"]["revision"]


def test_partial_note_read_does_not_publish_missing_content(root, monkeypatch):
    saved = save(root, "Conteudo valido")
    cache = worker.NoteCache()
    build(root, cache)
    previous = ficha(root)
    target = note_path(root, saved)
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    def unavailable(*args):
        raise PermissionError("temporary note lock")
    monkeypatch.setattr(worker, "_read_curated_note", unavailable)
    with pytest.raises(PermissionError):
        build(root, cache)
    assert ficha(root)["notas_sku"] == previous["notas_sku"]
    assert ficha(root)["expected_revision"] == previous["expected_revision"]


def test_duplicate_and_invalid_notes_remain_visible_as_invalid_states(root):
    saved = save(root, "Unica")
    cache = worker.NoteCache()
    build(root, cache)
    target = note_path(root, saved)
    duplicate = target.parent / "Duplicada.md"
    duplicate.write_bytes(target.read_bytes())
    build(root, cache)
    assert ficha(root)["sku_details"]["editorial_state"] == "conflict"
    assert ficha(root)["notas_sku"]["0007"] == ""
    duplicate.unlink()
    target.write_text("---\nnot valid yaml: [\n---\nTexto", encoding="utf-8")
    build(root, cache)
    assert ficha(root)["sku_details"]["editorial_state"] == "invalid"


def test_catalog_batch_failure_preserves_last_valid_product_and_new_editor(root, monkeypatch):
    save(root, "Primeira")
    publish_catalog_snapshot("tenant-a", SCOPE,
        [{"store_id": "store-a", "sku": "0007", "nome": "Produto original", "marca": "Marca A"}], info_root=root)
    cache = worker.NoteCache()
    build(root, cache)
    previous = ficha(root)
    assert previous["sku_details"]["catalog_found"] is True
    save(root, "Nova orientacao")
    def broken(*args, **kwargs):
        raise OSError("catalog unavailable")
    monkeypatch.setattr(worker, "_catalog_batch", broken)
    build(root, cache)
    current = ficha(root)
    assert current["notas_sku"]["0007"] == "Nova orientacao"
    assert current["sku_details"]["catalog_document"] == previous["sku_details"]["catalog_document"]
    assert current["sku_details"]["catalog_state"] == "unavailable"
    assert current["sku_details"]["editorial_state"] == "ready"


def test_failed_technical_loader_reapplies_current_characteristic_edits(root, monkeypatch):
    key = "catalog:" + "1" * 32
    save(root, "Primeira", caracteristicas={key: "Antes"},
         caracteristicas_fontes={key: {"label": "Modelo", "original_value": "Original"}})
    cache = worker.NoteCache()
    build(root, cache)
    save(root, "Nova", caracteristicas={key: "Depois"},
         caracteristicas_fontes={key: {"label": "Modelo", "original_value": "Original"}})
    monkeypatch.setattr(worker, "load_store_sku_details", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("technical failure")))
    build(root, cache)
    current = ficha(root)
    assert current["notas_sku"]["0007"] == "Nova"
    assert current["sku_details"]["guidance"]["caracteristicas"][key] == "Depois"
    assert next(row for row in current["sku_details"]["characteristics"] if row["key"] == key)["value"] == "Depois"


def test_pending_hub_publication_after_worker_initialization_keeps_previous_generation(root):
    save(root, "Estavel")
    cache = worker.NoteCache()
    build(root, cache)
    before = ficha(root)
    paths = _tenant_paths("tenant-a", info_root=root)
    paths.journal_path.write_text('{"state":"prepared"}', encoding="utf-8")
    with pytest.raises(ContextHubConflictError):
        build(root, cache)
    assert ficha(root)["snapshot"]["generation"] == before["snapshot"]["generation"]
    assert ficha(root)["notas_sku"] == before["notas_sku"]


def test_worker_failure_is_unavailable_instead_of_permanent_initializing(root, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("synthetic failure")
    monkeypatch.setattr(worker, "_rebuild_batch", unavailable)
    worker.request_refresh("tenant-a", SCOPE, info_root=root)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            ficha(root)
        except index.TrainingIndexUnavailable as error:
            if error.code == "training_index_unavailable":
                break
        time.sleep(0.02)
    else:
        pytest.fail("A failed first refresh must expose unavailable")
    worker.stop_workers()
    assert not worker._JOBS

def test_generated_document_edit_invalidates_product_cache_without_database_change(root):
    from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation
    publish_store_sku_generation("tenant-a", SCOPE,
        canonical_documents={"0007": {"sku": "0007", "nome": "Original"}}, store_guidance={},
        sku_guidance={"0007": {"notas": "Orientacao"}},
        bindings=[{"item_id": "MLB100", "sku": "0007"}], info_root=root)
    cache = worker.NoteCache()
    build(root, cache)
    first = ficha(root)
    canonical = next(row for row in first["sku_details"]["documents"] if row["relative_path"].startswith("70_Gerado/"))
    paths = _tenant_paths("tenant-a", info_root=root)
    target = paths.vault_dir / canonical["relative_path"]
    target.write_text(target.read_text(encoding="utf-8").replace("Original", "Changed"), encoding="utf-8")
    build(root, cache)
    current = ficha(root)
    assert current["snapshot"]["generation"] != first["snapshot"]["generation"]
    assert current["sku_details"]["catalog_state"] == "unavailable"
    assert current["sku_details"]["canonical_document"] == first["sku_details"]["canonical_document"]
    assert current["notas_sku"] == first["notas_sku"]


def test_technical_source_changed_during_build_cannot_be_recorded_as_already_processed(root, monkeypatch):
    save(root, "Original")
    publish_catalog_snapshot("tenant-a", SCOPE,
        [{"store_id": "store-a", "sku": "0007", "nome": "Produto original"}], info_root=root)
    cache = worker.NoteCache()
    build(root, cache)
    previous = ficha(root)
    save(root, "Changed editorial")
    paths = _tenant_paths("tenant-a", info_root=root)
    target = next(paths.generated_dir.rglob("Indice.json"))
    original = worker.load_store_sku_details
    def changing_source(*args, **kwargs):
        result = original(*args, **kwargs)
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return result
    monkeypatch.setattr(worker, "load_store_sku_details", changing_source)
    with pytest.raises(worker.TrainingSourceChanged):
        build(root, cache)
    assert ficha(root)["snapshot"]["generation"] == previous["snapshot"]["generation"]
    assert ficha(root)["expected_revision"] == previous["expected_revision"]

def test_store_rename_updates_revision_with_same_scope_identity_and_cache(root):
    save(root, "Permanece")
    cache = worker.NoteCache()
    build(root, cache)
    original = ficha(root)
    renamed = {**SCOPE, "store_name": "Nome atualizado"}
    worker.rebuild_now("tenant-a", renamed, info_root=root, cache=cache)
    current = index.read_ficha("tenant-a", renamed, "0007", info_root=root)
    canonical = editor.load_store_guidance_editor("tenant-a", renamed, info_root=root)
    assert current["loja"] == "Nome atualizado"
    assert current["expected_revision"] != original["expected_revision"]
    assert current["expected_revision"] == canonical["editorial"]["revision"]
    assert current["notas_sku"] == original["notas_sku"]

def test_external_write_debounce_waits_for_stable_note_and_preserves_previous_generation(root):
    import threading
    saved = save(root, "Initial")
    cache = worker.NoteCache()
    build(root, cache)
    before = ficha(root)
    target = note_path(root, saved)
    raw = target.read_text(encoding="utf-8")
    target.write_text(raw.replace("Initial", "Partial"), encoding="utf-8")
    def finish_write():
        time.sleep(0.02)
        target.write_text(raw.replace("Initial", "Finished"), encoding="utf-8")
    writer = threading.Thread(target=finish_write)
    writer.start()
    try:
        with pytest.raises(worker.TrainingSourceChanged):
            build(root, cache, stability_delay=0.1)
    finally:
        writer.join(timeout=2)
    assert ficha(root)["snapshot"]["generation"] == before["snapshot"]["generation"]
    started = time.perf_counter()
    build(root, cache, stability_delay=0.1)
    assert time.perf_counter() - started < 5
    assert ficha(root)["notas_sku"]["0007"] == "Finished"


def test_worker_manual_refresh_forces_verification_and_normal_poll_keeps_generation(root, monkeypatch):
    save(root, "Stable")
    monkeypatch.setattr(worker, "_METADATA_INTERVAL", 0.02)
    monkeypatch.setattr(worker, "_DEBOUNCE", 0.01)
    calls = []
    original = worker._rebuild_batch
    def counted(client_id, entries, **kwargs):
        result = original(client_id, entries, **kwargs)
        calls.append((entries[0]["force"], next(iter(result.values()))["generation"]))
        return result
    monkeypatch.setattr(worker, "_rebuild_batch", counted)
    worker.request_refresh("tenant-a", SCOPE, info_root=root)
    deadline = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) >= 2
    assert calls[0][0] is True and calls[1][0] is False
    assert calls[0][1] == calls[1][1]
    original_generation = calls[0][1]
    worker.request_refresh("tenant-a", SCOPE, info_root=root, immediate=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(force and generation != original_generation for force, generation in calls):
            break
        time.sleep(0.01)
    else:
        pytest.fail("Explicit refresh must force a verified current generation")
    worker.stop_workers()

def test_two_scopes_share_one_tenant_scan_and_never_share_editorial_records(root, monkeypatch):
    save(root, "Store A")
    other = {**SCOPE, "store_ref": "store-b", "seller_id": "456", "store_name": "Loja B"}
    initial = editor.load_store_guidance_editor("tenant-a", other, info_root=root)
    editor.save_store_guidance_editor("tenant-a", other, sku="0007", guidance={"notas": "Store B"},
        expected_revision=initial["editorial"]["revision"], info_root=root)
    walks = []
    original = worker._walk_files
    def counted(folder, paths, suffixes):
        if folder == paths.curated_dir:
            walks.append(folder)
        return original(folder, paths, suffixes)
    monkeypatch.setattr(worker, "_walk_files", counted)
    entries = [{"scope": scope, "cache": worker.NoteCache(), "force": True, "sku": "0007"}
               for scope in (SCOPE, other)]
    worker._rebuild_batch("tenant-a", entries, info_root=root, notes=worker.NoteCache())
    assert len(walks) == 2  # Before and after the entire tenant batch, not per store.
    assert index.read_ficha("tenant-a", SCOPE, "0007", info_root=root)["notas_sku"] == {"0007": "Store A"}
    assert index.read_ficha("tenant-a", other, "0007", info_root=root)["notas_sku"] == {"0007": "Store B"}

def _hold_worker_election(info_root, ready, release):
    from dataclasses import replace
    from backend.modules.context_hub.locking import _exclusive_file_lock
    paths = _tenant_paths("tenant-a", info_root=info_root)
    elected = replace(paths, lock_path=paths.internal_dir / "training-index-worker.lock")
    with _exclusive_file_lock(elected):
        ready.set()
        release.wait(10)


def test_cross_process_updater_election_prevents_duplicate_scan_and_keeps_reads_available(root, monkeypatch):
    import multiprocessing
    save(root, "Available")
    cache = worker.NoteCache()
    build(root, cache)
    before = ficha(root)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_worker_election, args=(str(root), ready, release))
    process.start()
    try:
        assert ready.wait(5)
        with monkeypatch.context() as patch:
            patch.setattr(worker.NoteCache, "scan", lambda *args, **kwargs: pytest.fail("A second process cannot scan under another worker's election"))
            started = time.perf_counter()
            with pytest.raises(ContextHubConflictError):
                build(root, cache)
            assert time.perf_counter() - started < 0.5
            started = time.perf_counter()
            assert ficha(root)["snapshot"]["generation"] == before["snapshot"]["generation"]
            assert time.perf_counter() - started < 0.5
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
    assert process.exitcode == 0
    assert build(root, cache)["generation"] == before["snapshot"]["generation"]

def test_worker_recovers_valid_publish_journal_after_process_crash_with_existing_cache(root):
    import os
    import subprocess
    import sys
    save(root, "Stable editorial")
    paths = _tenant_paths("tenant-a", info_root=root)
    marker = paths.generated_dir / "verified-marker.md"
    marker.write_text("Previous generated source", encoding="utf-8")
    cache = worker.NoteCache()
    build(root, cache)
    previous = ficha(root)
    script = '''
import json, os, sys
from pathlib import Path
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.locking import _exclusive_file_lock
paths = _tenant_paths("tenant-a", info_root=Path(sys.argv[1]))
generation = "a" * 32
with _exclusive_file_lock(paths):
    paths.generated_dir.rename(paths.vault_dir / (".context_hub_backup_" + generation))
    paths.generated_dir.mkdir()
    (paths.generated_dir / "partial.md").write_text("Incomplete replacement")
    paths.journal_path.write_text(json.dumps({"generation_id": generation, "state": "old_moved", "had_previous": True}))
    os._exit(21)
'''
    child = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, timeout=10)
    assert child.returncode == 21, child.stderr.decode(errors="replace")
    assert paths.journal_path.exists() and paths.lock_path.exists()
    # The existing Hub policy only reclaims dead-process locks after five minutes.
    expired = time.time() - 301
    os.utime(paths.lock_path, (expired, expired))
    build(root, cache)
    assert marker.read_text(encoding="utf-8") == "Previous generated source"
    assert not (paths.generated_dir / "partial.md").exists()
    assert not paths.journal_path.exists()
    assert ficha(root)["notas_sku"] == previous["notas_sku"]
    assert ficha(root)["expected_revision"] == previous["expected_revision"]

def test_equivalent_physical_tenant_paths_share_one_updater(root, monkeypatch):
    import os
    monkeypatch.setattr(worker, "_rebuild_batch", lambda *args, **kwargs: {})
    worker.request_refresh("tenant-a", SCOPE, info_root=root)
    alias = str(root).upper() if os.name == "nt" else root.parent / "." / root.name
    worker.request_refresh("tenant-a", SCOPE, info_root=alias)
    with worker._GUARD:
        assert len(worker._JOBS) == 1
        assert len(next(iter(worker._JOBS.values()))["scopes"]) == 1
    worker.stop_workers()

def test_invalid_sku_does_not_leave_a_dead_job_and_next_valid_request_starts(root, monkeypatch):
    from backend.modules.context_hub.contracts import ContextHubValidationError
    monkeypatch.setattr(worker, "_rebuild_batch", lambda *args, **kwargs: {})
    with pytest.raises(ContextHubValidationError):
        worker.request_refresh("tenant-a", SCOPE, sku="A" * 101, info_root=root)
    assert not worker._JOBS
    worker.request_refresh("tenant-a", SCOPE, sku="0007", info_root=root)
    with worker._GUARD:
        assert next(iter(worker._JOBS.values()))["thread"].is_alive()
    worker.stop_workers()


def test_thread_start_failure_allows_next_request_to_start_a_worker(root, monkeypatch):
    monkeypatch.setattr(worker, "_rebuild_batch", lambda *args, **kwargs: {})
    def unavailable(_thread):
        raise RuntimeError("synthetic thread capacity failure")
    with monkeypatch.context() as patch:
        patch.setattr(worker.threading.Thread, "start", unavailable)
        with pytest.raises(RuntimeError):
            worker.request_refresh("tenant-a", SCOPE, info_root=root)
    assert not worker._JOBS
    worker.request_refresh("tenant-a", SCOPE, info_root=root)
    with worker._GUARD:
        assert next(iter(worker._JOBS.values()))["thread"].is_alive()
    worker.stop_workers()
